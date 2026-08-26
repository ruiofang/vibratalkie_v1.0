#!/usr/bin/env python3
"""
RUIO 眼睛位图数据生成工具
=========================
将用户的 PNG 眼睛素材转换为 C 头文件，用于 VibratalkieBitmapEyeDisplay 渲染。

支持两种输入模式:
  A. 完整眼睛图片模式: 输入一张正面眼睛照片，工具自动提取各部分
  B. 分离素材模式: 分别输入巩膜、虹膜、眼睑等独立素材

用法:
    python3 eye_data_generator.py            # 启动 GUI (双模式 Tab 切换)
    python3 eye_data_generator.py --help     # 查看命令行用法

    # 完整眼睛图片模式 (自动裁剪):
    python3 eye_data_generator.py --cli --eye full_eye.png --screen 240 --output ./eye_data/240/

    # 分离素材模式 (手动指定):
    python3 eye_data_generator.py --cli \
        --sclera sclera.png --iris iris.png \
        --upper upper.png --lower lower.png \
        --screen 240 --output ./eye_data/240/

依赖: pip install Pillow numpy
"""

import argparse
import math
import os
import re
import sys

# numpy 和 PIL 延迟导入 (避免 OpenBLAS 与 tkinter 的线程库冲突导致段错误)
np = None
Image = None

def _ensure_imports():
    """首次调用转换函数时才导入 numpy/PIL"""
    global np, Image
    if np is None:
        import numpy
        np = numpy
    if Image is None:
        try:
            from PIL import Image as _Image
            Image = _Image
        except ImportError:
            print("错误: 需要安装 Pillow 库: pip install Pillow numpy")
            sys.exit(1)


def load_rgb_image(image_path):
    """读取图片并统一转为 RGB。"""
    _ensure_imports()
    img = Image.open(image_path)
    if img.mode == 'RGBA':
        bg = Image.new('RGB', img.size, (0, 0, 0))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    return img


def normalize_crop_box(crop_box, image_size):
    """将用户框选区域规范化为图像内的正方形裁剪框。"""
    img_w, img_h = image_size
    if crop_box is None:
        side = min(img_w, img_h)
        left = (img_w - side) // 2
        top = (img_h - side) // 2
        return (left, top, left + side, top + side)

    left, top, right, bottom = crop_box
    left, right = sorted((int(round(left)), int(round(right))))
    top, bottom = sorted((int(round(top)), int(round(bottom))))

    width = max(1, right - left)
    height = max(1, bottom - top)
    side = min(max(width, height), min(img_w, img_h))

    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    left = int(round(center_x - side / 2.0))
    top = int(round(center_y - side / 2.0))

    left = max(0, min(left, img_w - side))
    top = max(0, min(top, img_h - side))
    right = left + side
    bottom = top + side
    return (left, top, right, bottom)


def apply_circular_alpha(img):
    """对正方形图片应用圆形 alpha，圆外透明。"""
    _ensure_imports()
    rgba = img.convert('RGBA')
    arr = np.array(rgba)
    height, width = arr.shape[:2]
    center_x = (width - 1) / 2.0
    center_y = (height - 1) / 2.0
    radius = min(width, height) / 2.0

    yy, xx = np.ogrid[:height, :width]
    dist_sq = (xx - center_x) ** 2 + (yy - center_y) ** 2
    mask = dist_sq <= radius ** 2
    arr[:, :, 3] = np.where(mask, arr[:, :, 3], 0)
    return Image.fromarray(arr)


def hex_to_rgb(color_hex):
    """将 #RRGGBB 转为 RGB 元组。"""
    color_hex = (color_hex or '').strip().lstrip('#')
    if len(color_hex) != 6:
        return (216, 200, 184)
    return tuple(int(color_hex[index:index + 2], 16) for index in (0, 2, 4))


def create_eyelid_material(size, eyelid_color='#d8c8b8', material_path=None):
    """生成眼皮材质层，支持纯色或材质图。"""
    _ensure_imports()
    width, height = size
    color = np.array(hex_to_rgb(eyelid_color), dtype=np.float32)

    if material_path and os.path.exists(material_path):
        material = load_rgb_image(material_path).resize((width, height), Image.LANCZOS)
        material_arr = np.array(material, dtype=np.float32)
        tinted = material_arr * (color / 255.0)
        return np.clip(tinted, 0, 255).astype(np.uint8)

    material = np.empty((height, width, 3), dtype=np.uint8)
    material[:, :] = color.astype(np.uint8)
    return material


def apply_feathered_circle_alpha(img, feather_ratio=0.08):
    """给图片应用圆形 alpha，并在外缘做轻微羽化。"""
    _ensure_imports()
    rgba = img.convert('RGBA')
    arr = np.array(rgba, dtype=np.uint8)
    height, width = arr.shape[:2]
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    outer_r = min(width, height) / 2.0
    feather = max(1.0, outer_r * float(max(feather_ratio, 0.0)))

    yy, xx = np.mgrid[:height, :width]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)

    arr[:, :, 3] = np.where(dist <= (outer_r - feather), arr[:, :, 3], 0)
    if feather > 0:
        band_mask = (dist > (outer_r - feather)) & (dist < outer_r)
        if band_mask.any():
            alpha = np.clip((outer_r - dist[band_mask]) / feather, 0.0, 1.0)
            arr[band_mask, 3] = np.minimum(arr[band_mask, 3], np.round(alpha * 255.0).astype(np.uint8))

    return Image.fromarray(arr)


def build_pupil_texture(source_img, out_size, pupil_ratio=0.42, custom_pupil_img=None):
    """构建可缩放的瞳孔贴图纹理。"""
    _ensure_imports()
    if custom_pupil_img is not None:
        return custom_pupil_img.convert('RGB').resize(out_size, Image.LANCZOS)

    src = source_img.convert('RGB')
    src_w, src_h = src.size
    cx = (src_w - 1) / 2.0
    cy = (src_h - 1) / 2.0
    crop_r = max(4.0, min(src_w, src_h) * max(0.18, min(0.75, pupil_ratio)) * 0.6)
    left = max(0, int(round(cx - crop_r)))
    top = max(0, int(round(cy - crop_r)))
    right = min(src_w, int(round(cx + crop_r)))
    bottom = min(src_h, int(round(cy + crop_r)))
    if right <= left or bottom <= top:
        return src.resize(out_size, Image.LANCZOS)
    return src.crop((left, top, right, bottom)).resize(out_size, Image.LANCZOS)


def build_pupil_sticker(source_img, out_size, pupil_ratio=0.42, custom_pupil_img=None, feather_ratio=0.04):
    """构建用于预览/导出的圆形瞳孔贴图。"""
    _ensure_imports()
    if custom_pupil_img is not None:
        base = custom_pupil_img.convert('RGB').resize(out_size, Image.LANCZOS)
    else:
        src = source_img.convert('RGB')
        src_w, src_h = src.size
        cx = (src_w - 1) / 2.0
        cy = (src_h - 1) / 2.0
        crop_r = max(4.0, min(src_w, src_h) * max(0.18, min(0.75, pupil_ratio)) * 0.6)
        left = max(0, int(round(cx - crop_r)))
        top = max(0, int(round(cy - crop_r)))
        right = min(src_w, int(round(cx + crop_r)))
        bottom = min(src_h, int(round(cy + crop_r)))
        if right <= left or bottom <= top:
            base = src.resize(out_size, Image.LANCZOS)
        else:
            base = src.crop((left, top, right, bottom)).resize(out_size, Image.LANCZOS)
    return apply_feathered_circle_alpha(base, feather_ratio=feather_ratio)


def build_iris_sticker(source_img, out_size, custom_sticker_img=None, feather_ratio=0.02, inner_ratio=0.0):
    """构建带内孔的圆形虹膜贴图，用于导出检查图。"""
    _ensure_imports()
    if custom_sticker_img is not None:
        base = custom_sticker_img.convert('RGB').resize(out_size, Image.LANCZOS)
    else:
        base = source_img.convert('RGB').resize(out_size, Image.LANCZOS)

    rgba = apply_feathered_circle_alpha(base, feather_ratio=feather_ratio)
    inner_ratio = float(max(0.0, min(0.95, inner_ratio)))
    if inner_ratio <= 0.0:
        return rgba

    arr = np.array(rgba, dtype=np.uint8)
    height, width = arr.shape[:2]
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    inner_r = min(width, height) * 0.5 * inner_ratio
    yy, xx = np.mgrid[:height, :width]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    arr[dist <= inner_r, 3] = 0
    return Image.fromarray(arr)


def compose_center_overlay_on_iris(iris_crop, pupil_ratio=0.42, custom_pupil_img=None, feather_ratio=0.04):
    """将自定义中心图合成到完整虹膜圆图中，供整圈虹膜缩放使用。"""
    _ensure_imports()
    base = iris_crop.convert('RGBA').copy()
    if custom_pupil_img is None:
        return base.convert('RGB')

    iris_w, iris_h = base.size
    pupil_diam = max(4, int(round(min(iris_w, iris_h) * max(0.05, min(0.95, pupil_ratio)))))
    sticker = build_pupil_sticker(
        custom_pupil_img,
        (pupil_diam, pupil_diam),
        pupil_ratio=1.0,
        custom_pupil_img=custom_pupil_img,
        feather_ratio=feather_ratio,
    )
    paste_x = (iris_w - pupil_diam) // 2
    paste_y = (iris_h - pupil_diam) // 2
    base.paste(sticker.convert('RGB'), (paste_x, paste_y), sticker.split()[3])
    return base.convert('RGB')


def compose_pupil_on_sclera(sclera_img, pupil_img, preset, pupil_ratio=0.42, pupil_feather_ratio=0.04):
    """将圆形瞳孔贴图合成到巩膜中央，生成便于检查的导出/预览图。"""
    _ensure_imports()
    base = sclera_img.convert('RGBA').copy()
    if pupil_img is None:
        return base.convert('RGB')

    iris_w = preset['IRIS_WIDTH']
    scl_w = preset['SCLERA_WIDTH']
    scl_h = preset['SCLERA_HEIGHT']
    pupil_diam = max(4, int(round(iris_w * max(0.05, min(0.95, pupil_ratio)))))

    if pupil_img.mode != 'RGBA':
        sticker = apply_feathered_circle_alpha(
            pupil_img.convert('RGB').resize((pupil_diam, pupil_diam), Image.LANCZOS),
            feather_ratio=pupil_feather_ratio,
        )
    else:
        sticker = pupil_img.resize((pupil_diam, pupil_diam), Image.LANCZOS)

    paste_x = (scl_w - pupil_diam) // 2
    paste_y = (scl_h - pupil_diam) // 2
    base.paste(sticker.convert('RGB'), (paste_x, paste_y), sticker.split()[3])
    return base.convert('RGB')


def compose_iris_on_sclera(sclera_img, iris_sticker_img, preset):
    """将完整圆形虹膜贴图合成到巩膜中央，用于导出检查图。"""
    _ensure_imports()
    base = sclera_img.convert('RGBA').copy()
    if iris_sticker_img is None:
        return base.convert('RGB')

    iris_diam = max(4, int(round(preset['IRIS_WIDTH'])))
    sticker = iris_sticker_img.resize((iris_diam, iris_diam), Image.LANCZOS)
    paste_x = (preset['SCLERA_WIDTH'] - iris_diam) // 2
    paste_y = (preset['SCLERA_HEIGHT'] - iris_diam) // 2
    base.paste(sticker.convert('RGB'), (paste_x, paste_y), sticker.split()[3])
    return base.convert('RGB')


def create_sclera_from_user_crop(eye_sq_img, preset):
    """将用户选区扩展后的正方形原图直接生成巩膜底图。

    保留完整原图内容（包括虹膜/瞳孔区域），运行时虹膜纹理会覆盖中心区域。
    仅对正方形图像的圆外角落做辐射填充。
    """
    _ensure_imports()
    sclera_w = preset['SCLERA_WIDTH']
    sclera_h = preset['SCLERA_HEIGHT']

    sclera_rgb = eye_sq_img.resize((sclera_w, sclera_h), Image.LANCZOS)

    arr = np.array(sclera_rgb, dtype=np.uint8)
    height, width = arr.shape[:2]
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    outer_radius = min(width, height) / 2.0

    yy, xx = np.mgrid[:height, :width]
    dx = (xx - cx).astype(np.float64)
    dy = (yy - cy).astype(np.float64)
    dist = np.sqrt(dx ** 2 + dy ** 2)

    # 圆外角落：沿射线方向取圆边界颜色向外辐射填充
    outside_mask = dist > outer_radius
    if outside_mask.any():
        dist_safe = np.where(dist > 0, dist, 1.0)
        norm = outer_radius / dist_safe
        edge_x = np.clip(np.round(cx + dx * norm).astype(int), 0, width - 1)
        edge_y = np.clip(np.round(cy + dy * norm).astype(int), 0, height - 1)
        arr[outside_mask] = arr[edge_y[outside_mask], edge_x[outside_mask]]

    return Image.fromarray(arr)


def load_reference_common_layers(preset, log_fn=print):
    """加载与当前渲染器兼容的公共巩膜和眼睑遮罩。"""
    _ensure_imports()
    builtin_eye_data_dir = get_builtin_eye_data_dir(preset['SCREEN_WIDTH'])
    if builtin_eye_data_dir:
        try:
            sclera_values = parse_c_array_values(
                os.path.join(builtin_eye_data_dir, 'sclera.h'),
                'sclera_default',
            )
            upper_values = parse_c_array_values(
                os.path.join(builtin_eye_data_dir, 'eyelid.h'),
                'upper_default',
            )
            lower_values = parse_c_array_values(
                os.path.join(builtin_eye_data_dir, 'eyelid.h'),
                'lower_default',
            )
            sclera_img = rgb565_array_to_image(
                sclera_values,
                (preset['SCLERA_WIDTH'], preset['SCLERA_HEIGHT']),
            )
            upper_img = gray8_array_to_image(
                upper_values,
                (preset['SCREEN_WIDTH'], preset['SCREEN_HEIGHT']),
            )
            lower_img = gray8_array_to_image(
                lower_values,
                (preset['SCREEN_WIDTH'], preset['SCREEN_HEIGHT']),
            )
            log_fn("  参考公共层: 使用项目当前 eye_data 默认资源")
            return sclera_img, upper_img, lower_img
        except Exception as exc:
            log_fn(f"  ⚠ 解析项目默认 eye_data 失败: {exc}，回退到 docs/RUIO/240x240")

    base_dir = os.path.join(os.path.dirname(__file__), '240x240')
    paths = {
        'sclera': os.path.join(base_dir, 'sclera_common', 'sclera_default.png'),
        'upper': os.path.join(base_dir, 'upper_lower_common', 'upper_default.png'),
        'lower': os.path.join(base_dir, 'upper_lower_common', 'lower_default.png'),
    }

    missing = [name for name, path in paths.items() if not os.path.exists(path)]
    if missing:
        log_fn(f"  ⚠ 参考公共层缺失: {', '.join(missing)}，回退为自动生成")
        return None

    sclera_img = load_rgb_image(paths['sclera']).resize(
        (preset['SCLERA_WIDTH'], preset['SCLERA_HEIGHT']),
        Image.LANCZOS,
    )
    upper_img = Image.open(paths['upper']).convert('L').resize(
        (preset['SCREEN_WIDTH'], preset['SCREEN_HEIGHT']),
        Image.LANCZOS,
    )
    lower_img = Image.open(paths['lower']).convert('L').resize(
        (preset['SCREEN_WIDTH'], preset['SCREEN_HEIGHT']),
        Image.LANCZOS,
    )
    return sclera_img, upper_img, lower_img


def parse_c_array_values(header_path, array_name):
    """从 .h 文件中提取指定 C 数组的十六进制数据。"""
    with open(header_path, 'r') as f:
        content = f.read()
    pattern = rf'const\s+\w+\s+{re.escape(array_name)}\[[^\]]+\]\s*=\s*\{{(.*?)\}};'
    match = re.search(pattern, content, re.S)
    if not match:
        raise ValueError(f'未找到数组 {array_name}')
    return [int(token, 16) for token in re.findall(r'0x[0-9A-Fa-f]+', match.group(1))]


def rgb565_array_to_image(data, size):
    """将 RGB565 数组解码回 RGB 图像。"""
    _ensure_imports()
    width, height = size
    arr = np.array(data, dtype=np.uint16).reshape((height, width))
    r5 = (arr >> 11) & 0x1F
    g6 = (arr >> 5) & 0x3F
    b5 = arr & 0x1F
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[:, :, 0] = (r5 << 3) | (r5 >> 2)
    rgb[:, :, 1] = (g6 << 2) | (g6 >> 4)
    rgb[:, :, 2] = (b5 << 3) | (b5 >> 2)
    return Image.fromarray(rgb)


def gray8_array_to_image(data, size):
    """将灰度数组解码回 L 图像。"""
    _ensure_imports()
    width, height = size
    arr = np.array(data, dtype=np.uint8).reshape((height, width))
    return Image.fromarray(arr)


def get_builtin_eye_data_dir(screen_size):
    """返回项目内已验证可用的默认 eye_data 目录。"""
    base_dir = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            '..', '..', 'main', 'boards', 'vibratalkie', 'eye_data', str(screen_size)
        )
    )
    required = ['common.h', 'sclera.h', 'eyelid.h']
    if all(os.path.exists(os.path.join(base_dir, name)) for name in required):
        return base_dir
    return None


def copy_builtin_header(output_dir, source_dir, file_name):
    """复制项目内置的默认头文件。"""
    src = os.path.join(source_dir, file_name)
    dst = os.path.join(output_dir, file_name)
    with open(src, 'r') as fsrc:
        content = fsrc.read()
    with open(dst, 'w') as fdst:
        fdst.write(content)


def generate_eyelid_masks_from_source(sclera_rgb_img, preset, log_fn=print):
    """从原图估算上下眼睑遮罩，作为参考公共层缺失时的回退方案。"""
    _ensure_imports()
    screen_w = preset['SCREEN_WIDTH']
    screen_h = preset['SCREEN_HEIGHT']
    sclera_w = preset['SCLERA_WIDTH']
    sclera_h = preset['SCLERA_HEIGHT']

    screen_left = (sclera_w - screen_w) // 2
    screen_top = (sclera_h - screen_h) // 2
    screen_crop = sclera_rgb_img.crop((screen_left, screen_top,
                                       screen_left + screen_w,
                                       screen_top + screen_h))
    gray = np.array(screen_crop.convert('L'), dtype=np.float32)

    row_avg = gray.mean(axis=1)
    eye_center_y = int(np.argmax(row_avg))

    upper_mask = np.zeros((screen_h, screen_w), dtype=np.uint8)
    for y in range(screen_h):
        for x in range(screen_w):
            brightness = gray[y, x]
            dist_from_center = abs(x - screen_w // 2) / (screen_w / 2)
            dist_from_top = y / max(eye_center_y, 1)
            if y <= eye_center_y:
                val = brightness * min(dist_from_top, 1.0)
                edge = 1.0 - dist_from_center * dist_from_center * 0.5
                val *= max(edge, 0.0)
            else:
                val = 255
            upper_mask[y, x] = int(max(0, min(255, val)))

    lower_mask = np.zeros((screen_h, screen_w), dtype=np.uint8)
    for y in range(screen_h):
        for x in range(screen_w):
            brightness = gray[y, x]
            dist_from_center = abs(x - screen_w // 2) / (screen_w / 2)
            dist_from_bottom = (screen_h - 1 - y) / max(screen_h - 1 - eye_center_y, 1)
            if y >= eye_center_y:
                val = brightness * min(dist_from_bottom, 1.0)
                edge = 1.0 - dist_from_center * dist_from_center * 0.5
                val *= max(edge, 0.0)
            else:
                val = 255
            lower_mask[y, x] = int(max(0, min(255, val)))

    log_fn(f"  眼睑遮罩: 自动生成 {screen_w}×{screen_h} (眼裂中心 y={eye_center_y})")
    return Image.fromarray(upper_mask), Image.fromarray(lower_mask)


def make_renderer_preview(sclera_img, iris_src_img, preset, upper_img, lower_img,
                          eyelid_color='#d8c8b8', material_path=None,
                          eye_x=512, eye_y=512,
                          close_ratio=None,
                          pupil_ratio=0.42,
                          pupil_feather_ratio=0.04):
    """精确模拟渲染器合成效果的预览图。

    渲染流程与 VibratalkieBitmapEyeDisplay::DrawEye 完全对应:
    1. sclera_img 为底
    2. iris_src_img (圆形整圈虹膜图) 贴入中心
      3. 按 eye_x/y 计算 scleraX/Y，裁切 240×240 视口
      4. 圆形 alpha (圆屏)
      5. 眼睑遮罩叠加 (可选 close_ratio 模拟眨眼)
    """
    _ensure_imports()
    scl_w    = preset['SCLERA_WIDTH']
    scl_h    = preset['SCLERA_HEIGHT']
    screen_w = preset['SCREEN_WIDTH']
    screen_h = preset['SCREEN_HEIGHT']
    iris_w   = preset['IRIS_WIDTH']
    iris_h   = preset['IRIS_HEIGHT']

    # ── 1. 巩膜底图 + 瞳孔贴图合成 ──
    base = compose_iris_on_sclera(sclera_img, iris_src_img, preset)

    # ── 2. 逻辑坐标 → 巩膜像素偏移 (同 LinearMap) ──
    scl_ox = round(eye_x * (scl_w - screen_w) / 1023)
    scl_oy = round(eye_y * (scl_h - screen_h) / 1023)
    scl_ox = max(0, min(scl_w - screen_w, scl_ox))
    scl_oy = max(0, min(scl_h - screen_h, scl_oy))

    # ── 3. 裁切视口 + 圆屏 alpha ──
    viewport = base.crop((scl_ox, scl_oy, scl_ox + screen_w, scl_oy + screen_h))
    viewport = apply_circular_alpha(viewport)   # RGBA

    # ── 4. 眼睑遮罩：按渲染器的“阈值裁切”思路生成可见度，而不是直接拿灰度当 alpha ──
    upper_mask = np.array(upper_img.convert('L'), dtype=np.float32)
    lower_mask = np.array(lower_img.convert('L'), dtype=np.float32)
    threshold = 0.0 if close_ratio is None else float(np.clip(close_ratio, 0.0, 1.0) * 254.0)
    feather_px = 20.0
    upper_visible = np.clip((upper_mask - threshold) / feather_px, 0.0, 1.0)
    lower_visible = np.clip((lower_mask - threshold) / feather_px, 0.0, 1.0)
    visible = np.minimum(upper_visible, lower_visible)

    eyelid_rgb = create_eyelid_material((screen_w, screen_h), eyelid_color, material_path).astype(np.float32)
    composed   = np.array(viewport, dtype=np.uint8)
    eye_rgb    = composed[:, :, :3].astype(np.float32)
    composed[:, :, :3] = np.clip(
        eye_rgb * visible[:, :, None] + eyelid_rgb * (1.0 - visible[:, :, None]),
        0, 255,
    ).astype(np.uint8)
    return Image.fromarray(composed)


# 以下两个函数保留展名层，内部转发到 make_renderer_preview
def make_composed_eye_preview(sclera_img, iris_src_img, preset, upper_img, lower_img,
                              eyelid_color='#d8c8b8', material_path=None,
                              pupil_ratio=0.42,
                              pupil_feather_ratio=0.04):
    return make_renderer_preview(sclera_img, iris_src_img, preset, upper_img, lower_img,
                                  eyelid_color=eyelid_color, material_path=material_path,
                                  pupil_ratio=pupil_ratio, pupil_feather_ratio=pupil_feather_ratio)


def make_blink_preview(sclera_img, iris_src_img, preset, upper_img, lower_img,
                       close_ratio=0.88, eyelid_color='#d8c8b8', material_path=None,
                       pupil_ratio=0.42,
                       pupil_feather_ratio=0.04):
    return make_renderer_preview(sclera_img, iris_src_img, preset, upper_img, lower_img,
                                  eyelid_color=eyelid_color, material_path=material_path,
                                  close_ratio=close_ratio,
                                  pupil_ratio=pupil_ratio,
                                  pupil_feather_ratio=pupil_feather_ratio)

# ── 屏幕尺寸预设 ────────────────────────────────────
# ─── 尺寸参数说明 ──────────────────────────────────────────
# IRIS_WIDTH / IRIS_HEIGHT : 虹膜圆在巩膜坐标系中的大小（像素）
#   虹膜外径 = IRIS_WIDTH / 2
#   例：IRIS_WIDTH=150 → 外径75px；IRIS_WIDTH=200 → 外径100px
#   ⚠ 改完必须重新生成所有 .h 文件并重新编译固件
#
# IRIS_MAP_WIDTH / IRIS_MAP_HEIGHT : 极坐标展开图尺寸
#   IRIS_MAP_WIDTH  = 角度方向采样数（建议 ≈ IRIS_WIDTH * π）
#   IRIS_MAP_HEIGHT = 径向方向采样数（建议 ≈ IRIS_WIDTH / 2）
#                    同时决定极坐标查表 r 编码的校准点：
#                    r_max = IRIS_MAP_HEIGHT * 240 / 500（iScale 中心处虹膜纹理恰好铺满）
# ────────────────────────────────────────────────────────────
PRESETS = {
    240: {
        "SCREEN_WIDTH": 240, "SCREEN_HEIGHT": 240,
        "SCLERA_WIDTH": 375, "SCLERA_HEIGHT": 375,
        "IRIS_MAP_WIDTH": 471, "IRIS_MAP_HEIGHT": 75,
        "IRIS_WIDTH": 150, "IRIS_HEIGHT": 150,
        # 虹膜外径 = 150/2 = 75px（在375×375巩膜坐标中）
        # 对应屏幕上约 150/375*240 = 96px 宽
    },
    160: {
        "SCREEN_WIDTH": 160, "SCREEN_HEIGHT": 160,
        "SCLERA_WIDTH": 250, "SCLERA_HEIGHT": 250,
        "IRIS_MAP_WIDTH": 314, "IRIS_MAP_HEIGHT": 50,
        "IRIS_WIDTH": 100, "IRIS_HEIGHT": 100,
        # 虹膜外径 = 100/2 = 50px（在250×250巩膜坐标中）
    },
}


def png_to_rgb565(img):
    """将 PIL Image 转为 RGB565 uint16 数组"""
    if img.mode == 'RGBA':
        bg = Image.new('RGB', img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    arr = np.array(img)
    r = (arr[:, :, 0].astype(np.uint16) >> 3) << 11
    g = (arr[:, :, 1].astype(np.uint16) >> 2) << 5
    b = arr[:, :, 2].astype(np.uint16) >> 3
    return (r | g | b).flatten()


def png_to_gray8(img):
    """将 PIL Image 转为灰度 uint8 数组"""
    if img.mode != 'L':
        img = img.convert('L')
    return np.array(img).flatten()


def format_uint16_array(name, data, width_name, height_name):
    """格式化为 C const uint16_t 数组"""
    lines = [f'#include "common.h"\n']
    lines.append(f'const uint16_t {name}[{width_name}*{height_name}] = {{')
    for i in range(0, len(data), 8):
        chunk = data[i:i+8]
        hex_vals = ', '.join(f'0x{v:04X}' for v in chunk)
        suffix = ',' if (i + 8) < len(data) else ''
        lines.append(f'  {hex_vals}{suffix}')
    lines.append('};')
    return '\n'.join(lines) + '\n'


def format_uint8_array(name, data, width_name, height_name):
    """格式化为 C const uint8_t 数组"""
    lines = [f'const uint8_t {name}[{width_name} * {height_name}] = {{']
    for i in range(0, len(data), 12):
        chunk = data[i:i+12]
        hex_vals = ', '.join(f'0x{v:02X}' for v in chunk)
        suffix = ',' if (i + 12) < len(data) else ''
        lines.append(f'  {hex_vals}{suffix}')
    lines.append('};')
    return '\n'.join(lines) + '\n'


def generate_common_h(preset):
    """生成 common.h"""
    return (
        f'#define IRIS_MAP_WIDTH  {preset["IRIS_MAP_WIDTH"]}\n'
        f'#define IRIS_MAP_HEIGHT {preset["IRIS_MAP_HEIGHT"]}\n\n'
        f'#define SCLERA_WIDTH {preset["SCLERA_WIDTH"]}\n'
        f'#define SCLERA_HEIGHT {preset["SCLERA_HEIGHT"]}\n\n'
        f'#define SCREEN_WIDTH {preset["SCREEN_WIDTH"]}\n'
        f'#define SCREEN_HEIGHT {preset["SCREEN_HEIGHT"]}\n\n'
        f'#define IRIS_WIDTH  {preset["IRIS_WIDTH"]}\n'
        f'#define IRIS_HEIGHT {preset["IRIS_HEIGHT"]}\n\n'
        f'#define SYMMETRICAL_EYELID\n'
    )


def generate_polar_map(iris_w, iris_h, iris_map_h=75, draw_divisor=240, iscale_center=500):
    """自动生成极坐标查表，r 编码按 DrawEye 公式校准。

    DrawEye 采用 d = (iScale \u00d7 r) / draw_divisor；
    令 iscale_center 处虹膜外径对应 d = iris_map_h，就能推导：
        r_max = iris_map_h \u00d7 draw_divisor / iscale_center

    效果：
      iScale < iscale_center  \u2192 完整虹膜纹理可见（兜孔最小）
      iScale = iscale_center  \u2192 虹膜纹理恰好铺满整个虹膜圆（正常相)
      iScale > iscale_center  \u2192 外圈退回巩膜黑洞 \u2192 石孔放大效果

    参数说明：
      iris_w / iris_h   : 虹膜方形尺寸 (= IRIS_WIDTH x IRIS_HEIGHT)
      iris_map_h        : 径向采样数 (= IRIS_MAP_HEIGHT)
      draw_divisor      : DrawEye 公式除数 (实际固定为 SCREEN_WIDTH=240)
      iscale_center     : 中心 iScale，建议 = (EYE_IRIS_MIN+EYE_IRIS_MAX)/2 = 500
    """
    # 校准后的虹膜外径对应的编码値：
    #   DrawEye 在 iscale_center 时 d = iscale_center * r_max / draw_divisor = iris_map_h
    #   => r_max = iris_map_h * draw_divisor / iscale_center
    r_max_encoded = max(1, min(127, int(iris_map_h * draw_divisor / iscale_center)))

    cx, cy = iris_w / 2.0, iris_h / 2.0
    max_radius = min(cx, cy)   # 虹膜圆半径（像素）
    polar = np.zeros((iris_h, iris_w), dtype=np.uint16)
    for y in range(iris_h):
        for x in range(iris_w):
            dx = x - cx
            dy = y - cy
            dist = np.sqrt(dx*dx + dy*dy)
            angle = np.arctan2(dy, dx)
            if angle < 0:
                angle += 2 * np.pi
            # 有效辐射范围: 0 -> r_max_encoded，圆外钉位到 127
            r = min(int(dist * r_max_encoded / max_radius), 127)
            a = int(angle * 512 / (2 * np.pi)) & 0x1FF
            polar[y, x] = (a << 7) | r
    return polar.flatten()


def estimate_auto_pupil_ratio(iris_crop_img, fallback_ratio=0.42):
    """从圆形虹膜裁片中估算默认瞳孔半径占比。

    当用户未显式设置内径时，希望得到“外圈虹膜 + 中间瞳孔”的结构，
    而不是从圆心开始把整块都展开成虹膜纹理。
    """
    arr = np.array(iris_crop_img.convert('RGB'), dtype=np.float32)
    height, width = arr.shape[:2]
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    yy, xx = np.mgrid[:height, :width]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    max_r = min(cx, cy)
    if max_r <= 4:
        return fallback_ratio

    rgb_max = arr.max(axis=2)
    rgb_min = arr.min(axis=2)
    saturation = np.where(rgb_max > 0, (rgb_max - rgb_min) / rgb_max, 0.0)
    gray = arr.mean(axis=2)

    ring_gray = []
    ring_sat = []
    for radius in range(int(max_r) + 1):
        mask = (dist >= radius) & (dist < radius + 1)
        if not mask.any():
            ring_gray.append(0.0)
            ring_sat.append(0.0)
            continue
        ring_gray.append(float(gray[mask].mean()))
        ring_sat.append(float(saturation[mask].mean()))

    kernel = np.array([1, 2, 3, 2, 1], dtype=np.float32)
    kernel /= kernel.sum()
    gray_s = np.convolve(np.array(ring_gray, dtype=np.float32), kernel, mode='same')
    sat_s = np.convolve(np.array(ring_sat, dtype=np.float32), kernel, mode='same')
    score = gray_s + sat_s * 96.0
    grad = np.diff(score, prepend=score[0])

    min_idx = max(2, int(max_r * 0.18))
    max_idx = min(int(max_r * 0.62), len(grad) - 1)
    if max_idx <= min_idx:
        return fallback_ratio

    center_score = float(score[:max(2, int(max_r * 0.08))].mean())
    candidate = min_idx + int(np.argmax(grad[min_idx:max_idx + 1]))
    boundary_score = float(score[candidate])
    if grad[candidate] < 2.0 or boundary_score < center_score + 8.0:
        return fallback_ratio

    ratio = candidate / max_r
    return float(np.clip(ratio, 0.28, 0.52))


def build_iris_ring_map(iris_crop, iris_map_w, iris_map_h, inner_r_in_crop):
    """从圆形虹膜裁片生成纯虹膜展开纹理（不烘焙瞳孔）。"""
    iris_arr = np.array(iris_crop.convert('RGB'), dtype=np.uint8)
    iris_h, iris_w = iris_arr.shape[:2]
    icx, icy = iris_w / 2.0, iris_h / 2.0
    max_r = min(icx, icy)

    iris_map = np.zeros((iris_map_h, iris_map_w, 3), dtype=np.uint8)
    for r_idx in range(iris_map_h):
        radius = inner_r_in_crop + r_idx * (max_r - inner_r_in_crop) / max(iris_map_h - 1, 1)
        for a_idx in range(iris_map_w):
            angle = a_idx * 2 * np.pi / iris_map_w
            sx = int(icx + radius * np.cos(angle))
            sy = int(icy + radius * np.sin(angle))
            sx = max(0, min(iris_w - 1, sx))
            sy = max(0, min(iris_h - 1, sy))
            iris_map[r_idx, a_idx] = iris_arr[sy, sx]
    return Image.fromarray(iris_map)


def extract_from_full_eye(eye_path, preset, log_fn=print, crop_box=None, iris_crop_box=None, center_overlay_path=None):
    """从一张完整的眼睛照片中提取巩膜、虹膜、上下眼睑素材。

    处理流程:
    1. 将图片裁剪为正方形 (取中心区域)
    2. 缩放到巩膜尺寸 (375×375 或 250×250) 作为巩膜
    3. 从指定位置提取虹膜区域, 进行极坐标展开生成虹膜纹理
    4. 根据亮度阈值自动生成上下眼睑遮罩

    iris_crop_box: (cx, cy, r) 在原图坐标系中虹膜圆的中心和半径，
                   None 时默认取巩膜选区中心。

    返回: (sclera_img, iris_img, iris_sticker_img, pupil_texture_img, pupil_preview_img, upper_img, lower_img, pupil_ratio)
    """
    log_fn(f"  打开完整眼睛图片: {eye_path}")
    eye_img = load_rgb_image(eye_path)

    w, h = eye_img.size
    log_fn(f"  原始尺寸: {w}×{h}")

    # ── 1. 裁剪为正方形 ──
    left, top, right, bottom = normalize_crop_box(crop_box, eye_img.size)
    eye_sq = eye_img.crop((left, top, right, bottom))
    log_fn(f"  裁剪区域: ({left}, {top}) - ({right}, {bottom})")
    log_fn(f"  裁剪为正方形: {right - left}×{bottom - top}")

    sclera_w = preset['SCLERA_WIDTH']
    sclera_h = preset['SCLERA_HEIGHT']
    screen_w = preset['SCREEN_WIDTH']
    screen_h = preset['SCREEN_HEIGHT']
    iris_w = preset['IRIS_WIDTH']
    iris_h = preset['IRIS_HEIGHT']
    iris_map_w = preset['IRIS_MAP_WIDTH']
    iris_map_h = preset['IRIS_MAP_HEIGHT']

    # ── 2. 原图缩放到 SCLERA 尺寸，用于提取虹膜 ──
    sclera_rgb_img = eye_sq.resize((sclera_w, sclera_h), Image.LANCZOS)

    # 完整眼睛照片并不适合作为最终 sclera 层直接输出。
    # 渲染器会再次叠加虹膜与眼睑，如果这里保留整张照片，最终效果会双重叠层。
    common_layers = load_reference_common_layers(preset, log_fn=log_fn)
    if common_layers is not None:
        _base_sclera_img, upper_img, lower_img = common_layers
        log_fn(f"  眼睑遮罩: 使用参考公共遮罩 {screen_w}×{screen_h}")
    else:
        log_fn(f"  眼睑遮罩: 未找到参考公共遮罩，使用自动生成")

    center_overlay_img = None
    if center_overlay_path:
        try:
            center_overlay_img = load_rgb_image(center_overlay_path)
            log_fn(f"  瞳孔贴图: {center_overlay_path}")
        except Exception as e:
            log_fn(f"  警告: 无法加载瞳孔贴图: {e}")
    # ── 3. 虹膜: 从用户指定位置提取圆形区域, 做极坐标展开 ──
    # iris_crop_box = (cx, cy, r_outer) 或 (cx, cy, r_outer, r_inner)
    #   r_outer: 外圆半径 (虹膜外边界), r_inner: 内圆半径 (瞳孔边界, 0=自动估算)
    #   坐标系: 原图坐标
    # 未指定时默认取 eye_sq 中心
    sq_side = right - left  # eye_sq 的边长 (已是正方形)
    if iris_crop_box is not None:
        # 将原图坐标转换到 eye_sq 坐标系，再映射到 sclera 坐标系
        if len(iris_crop_box) == 4:
            orig_icx, orig_icy, orig_ir, orig_inner_r = iris_crop_box
        else:
            orig_icx, orig_icy, orig_ir = iris_crop_box
            orig_inner_r = 0.0
        # eye_sq 坐标 = 原图坐标 - crop_box 左上角
        sq_icx = orig_icx - left
        sq_icy = orig_icy - top
        sq_ir  = orig_ir
        # 映射到 sclera 坐标系
        scale = sclera_w / sq_side
        scl_icx    = sq_icx * scale
        scl_icy    = sq_icy * scale
        scl_ir     = sq_ir  * scale
        scl_inner_r = orig_inner_r * scale
        log_fn(f"  虹膜选区: 原图中心({orig_icx:.0f},{orig_icy:.0f}) 外径{orig_ir:.0f} 内径{orig_inner_r:.0f}")
        log_fn(f"  虹膜选区→巩膜坐标: 中心({scl_icx:.0f},{scl_icy:.0f}) 外径{scl_ir:.0f} 内径{scl_inner_r:.0f}")
    else:
        scl_icx     = sclera_w / 2.0
        scl_icy     = sclera_h / 2.0
        scl_ir      = iris_w / 2.0
        scl_inner_r = 0.0
        log_fn(f"  虹膜选区: 默认取巩膜中心，外径 {scl_ir:.0f}")

    # 从 sclera_rgb_img 中按圆形裁取虹膜区域，缩放到 iris_w×iris_h
    i_left = int(round(scl_icx - scl_ir))
    i_top  = int(round(scl_icy - scl_ir))
    i_side = max(4, int(round(scl_ir * 2)))
    i_left = max(0, min(i_left, sclera_w - 1))
    i_top  = max(0, min(i_top,  sclera_h - 1))
    i_side = min(i_side, sclera_w - i_left, sclera_h - i_top)
    iris_crop = sclera_rgb_img.crop((i_left, i_top, i_left + i_side, i_top + i_side))
    iris_crop = iris_crop.resize((iris_w, iris_h), Image.LANCZOS)

    # 极坐标展开: 将虹膜环形区域展开为矩形纹理
    # 宽 = 角度 (0-2π → 0-iris_map_w), 高 = 半径 (瞳孔→虹膜外边界 → 0-iris_map_h)
    max_r = min(iris_w / 2.0, iris_h / 2.0)

    if scl_ir > 0 and scl_inner_r > 0:
        pupil_ratio = min(scl_inner_r / scl_ir, 0.95)
        inner_r_in_crop = min(scl_inner_r / scl_ir * max_r, max_r * 0.95)
        log_fn(f"  瞳孔建议: 使用用户指定内径，约占虹膜半径 {pupil_ratio:.0%}")
    else:
        pupil_ratio = estimate_auto_pupil_ratio(iris_crop)
        inner_r_in_crop = min(max_r * pupil_ratio, max_r * 0.95)
        scl_inner_r = scl_ir * pupil_ratio
        log_fn(f"  瞳孔建议: 未指定内径，自动估算为外径的 {pupil_ratio:.0%}（巩膜坐标内径 {scl_inner_r:.0f}px）")

    sclera_img = create_sclera_from_user_crop(eye_sq, preset)
    log_fn(f"  巩膜: 用户选区扩展为正方形后缩放到 {sclera_w}×{sclera_h}（保留完整原图）")

    # 若有自定义中心贴图，先合成到虹膜裁片再做极坐标展开
    iris_runtime_src = compose_center_overlay_on_iris(
        iris_crop,
        pupil_ratio=pupil_ratio,
        custom_pupil_img=center_overlay_img,
        feather_ratio=0.04,
    )

    # 极坐标展开: inner_r = 0，从圆心到外缘全覆盖
    iris_img = build_iris_ring_map(
        iris_runtime_src,
        iris_map_w,
        iris_map_h,
        0,  # 从圆心开始展开，运行时直接用 d 采样
    )
    log_fn(f"  虹膜展开图: 生成 {iris_map_w}×{iris_map_h}（从圆心到外缘全覆盖）")

    iris_sticker_img = build_iris_sticker(
        iris_runtime_src,
        (iris_w, iris_h),
        feather_ratio=0.02,
    )
    log_fn(f"  圆形虹膜: 生成 {iris_w}×{iris_h}")

    pupil_texture_img = iris_img.copy()
    pupil_preview_img = build_pupil_sticker(
        iris_runtime_src,
        (iris_w, iris_h),
        pupil_ratio=pupil_ratio,
        custom_pupil_img=center_overlay_img,
        feather_ratio=0.04,
    )
    log_fn(f"  中心贴图: 生成圆形贴图 {iris_w}×{iris_h}（仅用于检查中心内容）")

    # ── 4. 眼睑遮罩: 优先使用与渲染器匹配的公共遮罩 ──
    if common_layers is None:
        upper_img, lower_img = generate_eyelid_masks_from_source(sclera_rgb_img, preset, log_fn=log_fn)

    return sclera_img, iris_img, iris_sticker_img, pupil_texture_img, pupil_preview_img, upper_img, lower_img, pupil_ratio


def convert_from_full_eye(screen_size, output_dir, eye_path, log_fn=print, crop_box=None, iris_crop_box=None, center_overlay_path=None, preset_override=None):
    """从完整眼睛图片一键生成所有数据"""
    _ensure_imports()
    preset = preset_override or PRESETS.get(screen_size)
    if not preset:
        log_fn(f"错误: 不支持的屏幕尺寸 {screen_size}, 仅支持 160 或 240")
        return False

    os.makedirs(output_dir, exist_ok=True)
    log_fn(f"═══ 从完整眼睛图片生成数据 ═══")
    log_fn(f"屏幕: {screen_size}×{screen_size} | 虹膜直径: {preset['IRIS_WIDTH']}px | 展开图: {preset['IRIS_MAP_WIDTH']}×{preset['IRIS_MAP_HEIGHT']}")

    # 提取各部分
    sclera_img, iris_img, iris_sticker_img, pupil_texture_img, pupil_preview_img, upper_img, lower_img, pupil_ratio = \
        extract_from_full_eye(eye_path, preset, log_fn, crop_box=crop_box, iris_crop_box=iris_crop_box, center_overlay_path=center_overlay_path)

    sclera_export_img = compose_iris_on_sclera(
        sclera_img,
        iris_sticker_img,
        preset,
    )

    builtin_eye_data_dir = get_builtin_eye_data_dir(screen_size)

    # 判断是否使用了自定义尺寸（与内置默认值不同）
    default_preset = PRESETS.get(screen_size, {})
    use_custom_size = (
        preset_override is not None and (
            preset.get('IRIS_WIDTH')      != default_preset.get('IRIS_WIDTH') or
            preset.get('IRIS_MAP_WIDTH')  != default_preset.get('IRIS_MAP_WIDTH') or
            preset.get('IRIS_MAP_HEIGHT') != default_preset.get('IRIS_MAP_HEIGHT')
        )
    )
    # 有自定义尺寸时必须重新生成 common.h，不能复制内置（否则 #define 与实际数组大小不匹配）
    use_builtin_headers = builtin_eye_data_dir and not use_custom_size

    # 1. common.h
    if use_builtin_headers:
        copy_builtin_header(output_dir, builtin_eye_data_dir, 'common.h')
        log_fn(f"✓ 复制内置 common.h")
    else:
        with open(os.path.join(output_dir, 'common.h'), 'w') as f:
            f.write(generate_common_h(preset))
        if use_custom_size:
            log_fn(f"✓ 生成 common.h (自定义尺寸: IRIS_WIDTH={preset['IRIS_WIDTH']}, IRIS_MAP={preset['IRIS_MAP_WIDTH']}×{preset['IRIS_MAP_HEIGHT']})")
        else:
            log_fn(f"✓ 生成 common.h")

    # 2. sclera.h
    sclera_data = png_to_rgb565(sclera_img)
    with open(os.path.join(output_dir, 'sclera.h'), 'w') as f:
        f.write(format_uint16_array('sclera_default', sclera_data,
                                     'SCLERA_WIDTH', 'SCLERA_HEIGHT'))
    log_fn(f"✓ 生成 sclera.h ({len(sclera_data)*2//1024}KB)")

    # 3. iris.h
    iris_data = png_to_rgb565(iris_img)
    with open(os.path.join(output_dir, 'iris.h'), 'w') as f:
        f.write(format_uint16_array('iris_default', iris_data,
                                     'IRIS_MAP_WIDTH', 'IRIS_MAP_HEIGHT'))
    log_fn(f"✓ 生成 iris.h ({len(iris_data)*2//1024}KB，当前承载完整虹膜展开纹理)")

    # 4. eyelid.h
    upper_data = png_to_gray8(upper_img)
    lower_data = png_to_gray8(lower_img)
    iris_sz = (preset['IRIS_WIDTH'], preset['IRIS_HEIGHT'])
    log_fn(f"  自动生成极坐标查表 ({iris_sz[0]}×{iris_sz[1]})，根据 IRIS_MAP_HEIGHT={preset['IRIS_MAP_HEIGHT']} 自动校准 r 编码")
    polar_data = generate_polar_map(iris_sz[0], iris_sz[1], iris_map_h=preset['IRIS_MAP_HEIGHT'])

    if use_builtin_headers:
        copy_builtin_header(output_dir, builtin_eye_data_dir, 'eyelid.h')
        log_fn(f"✓ 复制内置 eyelid.h")
    else:
        eyelid_content = '#include "common.h"\n\n'
        eyelid_content += '#ifdef SYMMETRICAL_EYELID\n\n'
        eyelid_content += format_uint8_array('upper_default', upper_data,
                                              'SCREEN_WIDTH', 'SCREEN_HEIGHT')
        eyelid_content += '\n'
        eyelid_content += format_uint8_array('lower_default', lower_data,
                                              'SCREEN_WIDTH', 'SCREEN_HEIGHT')
        eyelid_content += '\n#else\n\n'
        eyelid_content += format_uint8_array('upper_default', upper_data,
                                              'SCREEN_WIDTH', 'SCREEN_HEIGHT')
        eyelid_content += '\n'
        eyelid_content += format_uint8_array('lower_default', lower_data,
                                              'SCREEN_WIDTH', 'SCREEN_HEIGHT')
        eyelid_content += '\n#endif\n\n'
        eyelid_content += format_uint16_array('polar_default', polar_data,
                                               'IRIS_WIDTH', 'IRIS_HEIGHT')

        with open(os.path.join(output_dir, 'eyelid.h'), 'w') as f:
            f.write(eyelid_content)
    total_kb = (len(upper_data) + len(lower_data) + len(polar_data)*2) // 1024
    if not builtin_eye_data_dir:
        log_fn(f"✓ 生成 eyelid.h ({total_kb}KB)")

    # 保存中间素材到输出目录 (方便检查/手动微调)
    sclera_export_img.save(os.path.join(output_dir, '_extracted_sclera.png'))
    sclera_img.save(os.path.join(output_dir, '_extracted_sclera_base.png'))
    iris_img.save(os.path.join(output_dir, '_extracted_iris.png'))
    iris_sticker_img.save(os.path.join(output_dir, '_extracted_iris_circle.png'))
    pupil_preview_img.save(os.path.join(output_dir, '_extracted_pupil.png'))
    pupil_texture_img.save(os.path.join(output_dir, '_extracted_pupil_map.png'))
    upper_img.save(os.path.join(output_dir, '_extracted_upper.png'))
    lower_img.save(os.path.join(output_dir, '_extracted_lower.png'))
    log_fn(f"✓ 提取的中间素材已保存 (_extracted_*.png，含已合成中心贴图的 _extracted_sclera.png)")

    total_bytes = (len(sclera_data)*2 + len(iris_data)*2 +
                   len(upper_data) + len(lower_data) + len(polar_data)*2)
    log_fn(f"\n完成! 总数据量: {total_bytes//1024}KB")
    log_fn(f"输出目录: {os.path.abspath(output_dir)}")
    log_fn(f"\n提示: 如果效果不理想, 可以手动修改 _extracted_*.png 后用分离素材模式重新生成")
    return True


def convert_eye_data(screen_size, output_dir,
                     sclera_path, iris_path, upper_path, lower_path,
                     polar_path=None, log_fn=print):
    """核心转换逻辑"""
    _ensure_imports()
    preset = PRESETS.get(screen_size)
    if not preset:
        log_fn(f"错误: 不支持的屏幕尺寸 {screen_size}, 仅支持 160 或 240")
        return False

    os.makedirs(output_dir, exist_ok=True)

    # 1. common.h
    common_path = os.path.join(output_dir, 'common.h')
    with open(common_path, 'w') as f:
        f.write(generate_common_h(preset))
    log_fn(f"✓ 生成 common.h")

    # 2. sclera.h
    log_fn(f"  转换巩膜: {sclera_path}")
    sclera_img = Image.open(sclera_path)
    expected = (preset['SCLERA_WIDTH'], preset['SCLERA_HEIGHT'])
    if sclera_img.size != expected:
        log_fn(f"  ⚠ 巩膜图片尺寸 {sclera_img.size} != 预期 {expected}, 自动缩放")
        sclera_img = sclera_img.resize(expected, Image.LANCZOS)
    sclera_data = png_to_rgb565(sclera_img)
    with open(os.path.join(output_dir, 'sclera.h'), 'w') as f:
        f.write(format_uint16_array('sclera_default', sclera_data,
                                     'SCLERA_WIDTH', 'SCLERA_HEIGHT'))
    log_fn(f"✓ 生成 sclera.h ({len(sclera_data)*2//1024}KB)")

    # 3. iris.h
    log_fn(f"  转换虹膜: {iris_path}")
    iris_img = Image.open(iris_path)
    expected = (preset['IRIS_MAP_WIDTH'], preset['IRIS_MAP_HEIGHT'])
    if iris_img.size != expected:
        log_fn(f"  ⚠ 虹膜图片尺寸 {iris_img.size} != 预期 {expected}, 自动缩放")
        iris_img = iris_img.resize(expected, Image.LANCZOS)
    iris_data = png_to_rgb565(iris_img)
    with open(os.path.join(output_dir, 'iris.h'), 'w') as f:
        f.write(format_uint16_array('iris_default', iris_data,
                                     'IRIS_MAP_WIDTH', 'IRIS_MAP_HEIGHT'))
    log_fn(f"✓ 生成 iris.h ({len(iris_data)*2//1024}KB)")

    # 4. eyelid.h (upper + lower + polar)
    log_fn(f"  转换上眼睑: {upper_path}")
    upper_img = Image.open(upper_path)
    screen_sz = (preset['SCREEN_WIDTH'], preset['SCREEN_HEIGHT'])
    if upper_img.size != screen_sz:
        log_fn(f"  ⚠ 上眼睑尺寸 {upper_img.size} != 预期 {screen_sz}, 自动缩放")
        upper_img = upper_img.resize(screen_sz, Image.LANCZOS)
    upper_data = png_to_gray8(upper_img)

    log_fn(f"  转换下眼睑: {lower_path}")
    lower_img = Image.open(lower_path)
    if lower_img.size != screen_sz:
        log_fn(f"  ⚠ 下眼睑尺寸 {lower_img.size} != 预期 {screen_sz}, 自动缩放")
        lower_img = lower_img.resize(screen_sz, Image.LANCZOS)
    lower_data = png_to_gray8(lower_img)

    # Polar map
    iris_sz = (preset['IRIS_WIDTH'], preset['IRIS_HEIGHT'])
    if polar_path and os.path.exists(polar_path):
        log_fn(f"  转换极坐标: {polar_path}")
        polar_img = Image.open(polar_path)
        if polar_img.size != iris_sz:
            polar_img = polar_img.resize(iris_sz, Image.LANCZOS)
        polar_data = png_to_rgb565(polar_img)
    else:
        log_fn(f"  自动生成极坐标查表 ({iris_sz[0]}×{iris_sz[1]})，根据 IRIS_MAP_HEIGHT={preset['IRIS_MAP_HEIGHT']} 自动校准 r 编码")
        polar_data = generate_polar_map(iris_sz[0], iris_sz[1], iris_map_h=preset['IRIS_MAP_HEIGHT'])

    eyelid_content = '#include "common.h"\n\n'
    eyelid_content += '#ifdef SYMMETRICAL_EYELID\n\n'
    eyelid_content += format_uint8_array('upper_default', upper_data,
                                          'SCREEN_WIDTH', 'SCREEN_HEIGHT')
    eyelid_content += '\n'
    eyelid_content += format_uint8_array('lower_default', lower_data,
                                          'SCREEN_WIDTH', 'SCREEN_HEIGHT')
    eyelid_content += '\n#else\n\n'
    eyelid_content += format_uint8_array('upper_default', upper_data,
                                          'SCREEN_WIDTH', 'SCREEN_HEIGHT')
    eyelid_content += '\n'
    eyelid_content += format_uint8_array('lower_default', lower_data,
                                          'SCREEN_WIDTH', 'SCREEN_HEIGHT')
    eyelid_content += '\n#endif\n\n'
    eyelid_content += format_uint16_array('polar_default', polar_data,
                                           'IRIS_WIDTH', 'IRIS_HEIGHT')

    with open(os.path.join(output_dir, 'eyelid.h'), 'w') as f:
        f.write(eyelid_content)
    total_kb = (len(upper_data) + len(lower_data) + len(polar_data)*2) // 1024
    log_fn(f"✓ 生成 eyelid.h ({total_kb}KB)")

    total_bytes = (len(sclera_data)*2 + len(iris_data)*2 +
                   len(upper_data) + len(lower_data) + len(polar_data)*2)
    log_fn(f"\n完成! 总数据量: {total_bytes//1024}KB")
    log_fn(f"输出目录: {os.path.abspath(output_dir)}")
    return True


# ── GUI 模式 ─────────────────────────────────────────
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, colorchooser
    root = tk.Tk()
    root.title("RUIO 眼睛位图生成工具")
    root.geometry("1800x1500")
    root.resizable(True, True)

    screen_size = tk.IntVar(value=240)
    output_dir = tk.StringVar(value=os.path.abspath("eye_data_output"))
    iris_width_var = tk.IntVar(value=150)  # 虹膜直径（IRIS_WIDTH），默认150

    eye_path = tk.StringVar()
    sclera_path = tk.StringVar()
    iris_path = tk.StringVar()
    upper_path = tk.StringVar()
    lower_path = tk.StringVar()
    polar_path = tk.StringVar()
    blink_amount = tk.DoubleVar(value=88.0)
    eyelid_color = tk.StringVar(value='#d8c8b8')
    eyelid_material_path = tk.StringVar()
    center_overlay_path = tk.StringVar()
    image_tk = None
    preview_refs = {}
    full_eye_crop_box = None
    full_iris_crop_box = None  # (cx, cy, r) 在原图坐标系；None=默认取中心
    sclera_cx_var = tk.IntVar(value=0)  # 巩膜选区中心X (0=未设定)
    sclera_cy_var = tk.IntVar(value=0)  # 巩膜选区中心Y (0=未设定)
    sclera_r_var  = tk.IntVar(value=0)  # 巩膜选区半径  (0=未设定)
    _sclera_sync  = [False]             # 防止 spinbox↔canvas 循环触发
    source_canvas_image = None
    source_canvas_rect = None
    source_canvas_drag = None
    source_canvas_meta = {
        'image_size': None,
        'preview_size': None,
        'offset': (0, 0),
    }

    def ensure_preview_support():
        nonlocal image_tk
        _ensure_imports()
        if image_tk is None:
            from PIL import ImageTk
            image_tk = ImageTk

    def select_file(var):
        path = filedialog.askopenfilename(
            filetypes=[("PNG files", "*.png"), ("JPEG files", "*.jpg;*.jpeg"), ("All", "*.*")]
        )
        if path:
            var.set(path)

    def select_full_eye_file():
        nonlocal full_eye_crop_box, full_iris_crop_box
        select_file(eye_path)
        full_eye_crop_box = None
        full_iris_crop_box = None
        _sclera_sync[0] = True
        sclera_cx_var.set(0); sclera_cy_var.set(0); sclera_r_var.set(0)
        _sclera_sync[0] = False
        refresh_full_preview()

    def select_dir(var):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def choose_eyelid_color():
        result = colorchooser.askcolor(color=eyelid_color.get(), title='选择眼皮颜色')
        if result and result[1]:
            eyelid_color.set(result[1])

    def select_eyelid_material():
        path = filedialog.askopenfilename(
            filetypes=[("Image files", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"), ("All", "*.*")]
        )
        if path:
            eyelid_material_path.set(path)

    def clear_eyelid_material():
        eyelid_material_path.set('')

    def select_center_overlay():
        path = filedialog.askopenfilename(
            filetypes=[("Image files", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"), ("All", "*.*")]
        )
        if path:
            center_overlay_path.set(path)

    def clear_center_overlay():
        center_overlay_path.set('')

    def get_current_preset():
        """返回当前屏幕尺寸的 preset，并用用户自定义虹膜直径覆盖相关字段。"""
        base = dict(PRESETS.get(screen_size.get(), PRESETS[240]))
        iw = max(4, iris_width_var.get())
        # IRIS_MAP_WIDTH  ≈ IRIS_WIDTH × π（角度方向采样密度）
        # IRIS_MAP_HEIGHT ≈ IRIS_WIDTH / 2（径向方向采样密度）
        base['IRIS_WIDTH']      = iw
        base['IRIS_HEIGHT']     = iw
        base['IRIS_MAP_WIDTH']  = max(4, round(iw * math.pi))
        base['IRIS_MAP_HEIGHT'] = max(4, round(iw / 2))
        return base

    size_frame = ttk.LabelFrame(root, text="屏幕尺寸")
    size_frame.pack(fill='x', padx=10, pady=5)
    ttk.Radiobutton(size_frame, text="240×240 (GC9A01/ST7789)", variable=screen_size, value=240).pack(
        side='left', padx=10, pady=5
    )
    ttk.Radiobutton(size_frame, text="160×160 (GC9D01)", variable=screen_size, value=160).pack(
        side='left', padx=10, pady=5
    )
    # ── 虹膜尺寸自定义 ──
    ttk.Separator(size_frame, orient='vertical').pack(side='left', fill='y', padx=8, pady=4)
    ttk.Label(size_frame, text="虹膜直径 (IRIS_WIDTH):").pack(side='left')
    iris_width_spin = ttk.Spinbox(size_frame, from_=20, to=400, textvariable=iris_width_var, width=6)
    iris_width_spin.pack(side='left', padx=(2, 4))
    iris_size_hint = ttk.Label(size_frame, text="", foreground='gray')
    iris_size_hint.pack(side='left', padx=(0, 8))

    def update_iris_size_hint(*_):
        p = get_current_preset()
        iw = p['IRIS_WIDTH']
        screen_px = round(iw / p['SCLERA_WIDTH'] * p['SCREEN_WIDTH'])
        # 安全上限: 虹膜直径不应超过巩膜的 56% (否则会覆盖整个屏幕导致眼白消失)
        max_safe_iw = int(p['SCLERA_WIDTH'] * 0.56)
        if iw > max_safe_iw:
            iris_size_hint.config(
                text=f"⚠ 过大({screen_px}px)！虹膜会覆盖全屏，眼白消失。建议 ≤ {max_safe_iw}",
                foreground='red',
            )
        else:
            iris_size_hint.config(
                text=f"外径 {iw//2}px → 屏幕约 {screen_px}px | 展开图 {p['IRIS_MAP_WIDTH']}×{p['IRIS_MAP_HEIGHT']}",
                foreground='gray',
            )
        if eye_path.get():
            refresh_full_preview()

    def reset_iris_width(*_):
        defaults = {240: 150, 160: 100}
        iris_width_var.set(defaults.get(screen_size.get(), 150))

    iris_width_var.trace_add('write', update_iris_size_hint)
    ttk.Button(size_frame, text="重置默认", command=reset_iris_width).pack(side='left', padx=(0, 10))
    update_iris_size_hint()

    # 切换屏幕尺寸时自动重置虹膜直径为该尺寸的默认值
    def on_screen_size_changed(*_):
        reset_iris_width()
    screen_size.trace_add('write', on_screen_size_changed)

    out_frame = ttk.LabelFrame(root, text="输出目录")
    out_frame.pack(fill='x', padx=10, pady=5)
    ttk.Entry(out_frame, textvariable=output_dir, width=60).pack(
        side='left', padx=5, pady=5, fill='x', expand=True
    )
    ttk.Button(out_frame, text="浏览", command=lambda: select_dir(output_dir)).pack(side='right', padx=5, pady=5)

    notebook = ttk.Notebook(root)
    notebook.pack(fill='both', expand=False, padx=10, pady=5)

    tab_full = ttk.Frame(notebook)
    notebook.add(tab_full, text=" 📷 完整眼睛图片 (自动裁剪) ")

    desc = ttk.Label(
        tab_full,
        text="输入正面眼睛照片，自动提取巩膜/虹膜/眼睑。建议正方形、正面、睁大。生成后同时保存中间素材 (_extracted_*.png)。",
        foreground='gray',
    )
    desc.pack(padx=10, pady=(4, 2), anchor='w')

    eye_row = ttk.Frame(tab_full)
    eye_row.pack(fill='x', padx=10, pady=2)
    ttk.Label(eye_row, text="眼睛图片:", width=12).pack(side='left')
    ttk.Entry(eye_row, textvariable=eye_path, width=50).pack(side='left', padx=5, fill='x', expand=True)
    ttk.Button(eye_row, text="选择", command=select_full_eye_file).pack(side='right', padx=5)

    preview_frame = ttk.LabelFrame(tab_full, text="提取预览")
    preview_frame.pack(fill='x', padx=10, pady=2)

    preview_top = ttk.Frame(preview_frame)
    preview_top.pack(fill='x', padx=4, pady=4)

    source_frame = ttk.Frame(preview_top)
    source_frame.pack(side='left', fill='both', expand=True, padx=(0, 10))
    ttk.Label(source_frame, text="原图").pack(anchor='w')
    source_canvas = tk.Canvas(source_frame, width=420, height=220, bg='#202020', highlightthickness=1)
    source_canvas.pack(fill='both', expand=True, pady=(2, 0))
    source_canvas.create_text(210, 110, text="未选择图片", fill="#d0d0d0", tags="placeholder")

    extracted_frame = ttk.Frame(preview_top)
    extracted_frame.pack(side='left', fill='both', expand=True)
    ttk.Label(extracted_frame, text="自动提取结果 / 对应预览").grid(row=0, column=0, columnspan=2, sticky='w')

    crop_preview = ttk.Label(extracted_frame, text="选区圆形预览", anchor='center')
    composed_preview = ttk.Label(extracted_frame, text="组合效果", anchor='center')
    blink_preview = ttk.Label(extracted_frame, text="眨眼效果", anchor='center')
    sclera_preview = ttk.Label(extracted_frame, text="巩膜（全图）", anchor='center')
    iris_preview = ttk.Label(extracted_frame, text="圆形眼珠贴图", anchor='center')
    upper_preview = ttk.Label(extracted_frame, text="上眼睑遮罩", anchor='center')
    lower_preview = ttk.Label(extracted_frame, text="下眼睑遮罩", anchor='center')

    crop_preview.grid(row=1, column=0, padx=4, pady=2, sticky='nsew')
    composed_preview.grid(row=1, column=1, padx=4, pady=2, sticky='nsew')
    blink_preview.grid(row=2, column=0, padx=4, pady=2, sticky='nsew')
    sclera_preview.grid(row=2, column=1, padx=4, pady=2, sticky='nsew')
    iris_preview.grid(row=3, column=0, padx=4, pady=2, sticky='nsew')
    upper_preview.grid(row=3, column=1, padx=4, pady=2, sticky='nsew')
    lower_preview.grid(row=4, column=0, padx=4, pady=2, sticky='nsew')
    extracted_frame.columnconfigure(0, weight=1)
    extracted_frame.columnconfigure(1, weight=1)

    preview_status = ttk.Label(
        preview_frame,
        text="选择完整眼睛图片后，可在这里预览自动裁剪和提取结果。",
        foreground='gray',
        justify='left',
    )
    preview_status.pack(fill='x', padx=8, pady=(0, 3))

    selection_button_row = ttk.Frame(tab_full)
    selection_button_row.pack(fill='x', padx=10, pady=(0, 2))
    ttk.Label(
        selection_button_row,
        text="先点击眼珠中心，再拖拽决定圆形半径；未框选时默认取图片中心圆形区域。",
        foreground='gray',
    ).pack(side='left')

    def clear_full_eye_selection():
        nonlocal full_eye_crop_box, full_iris_crop_box
        full_eye_crop_box = None
        full_iris_crop_box = None
        _sclera_sync[0] = True
        sclera_cx_var.set(0); sclera_cy_var.set(0); sclera_r_var.set(0)
        _sclera_sync[0] = False
        refresh_full_preview()

    ttk.Button(selection_button_row, text="清除选区", command=clear_full_eye_selection).pack(side='right')

    # ── 巩膜选区数值微调 ──
    sclera_sel_row = ttk.Frame(tab_full)
    sclera_sel_row.pack(fill='x', padx=10, pady=(0, 2))
    ttk.Label(sclera_sel_row, text="巩膜选区:", width=12).pack(side='left')
    ttk.Label(sclera_sel_row, text="中心X:").pack(side='left')
    ttk.Spinbox(sclera_sel_row, from_=0, to=9999, textvariable=sclera_cx_var, width=7).pack(side='left', padx=(2, 8))
    ttk.Label(sclera_sel_row, text="中心Y:").pack(side='left')
    ttk.Spinbox(sclera_sel_row, from_=0, to=9999, textvariable=sclera_cy_var, width=7).pack(side='left', padx=(2, 8))
    ttk.Label(sclera_sel_row, text="半径:").pack(side='left')
    ttk.Spinbox(sclera_sel_row, from_=0, to=9999, textvariable=sclera_r_var, width=7).pack(side='left', padx=(2, 8))
    sclera_sel_status = ttk.Label(sclera_sel_row, text="(0=默认取图片中心)", foreground='gray')
    sclera_sel_status.pack(side='left', padx=(0, 8))
    ttk.Button(sclera_sel_row, text="重置", command=lambda: [sclera_cx_var.set(0), sclera_cy_var.set(0), sclera_r_var.set(0)]).pack(side='left')

    def apply_sclera_selection(*_):
        nonlocal full_eye_crop_box
        if _sclera_sync[0]:
            return
        cx = sclera_cx_var.get()
        cy = sclera_cy_var.get()
        r  = sclera_r_var.get()
        if cx == 0 and cy == 0 and r == 0:
            full_eye_crop_box = None
            sclera_sel_status.config(text="(0=默认取图片中心)", foreground='gray')
        elif r < 4:
            sclera_sel_status.config(text="半径过小（需 ≥ 4）", foreground='orange')
            return
        else:
            img_size = source_canvas_meta['image_size']
            full_eye_crop_box = normalize_crop_box(
                (cx - r, cy - r, cx + r, cy + r), img_size
            )
            sclera_sel_status.config(
                text=f"中心({cx},{cy}) 半径{r}", foreground='#00aa44'
            )
        draw_source_selection()
        if eye_path.get():
            refresh_full_preview()

    def sync_sclera_spinboxes():
        """将 full_eye_crop_box 同步回三个 Spinbox，期间屏蔽 trace 回调。"""
        _sclera_sync[0] = True
        try:
            if full_eye_crop_box:
                cx = int(round((full_eye_crop_box[0] + full_eye_crop_box[2]) / 2))
                cy = int(round((full_eye_crop_box[1] + full_eye_crop_box[3]) / 2))
                r  = int(round((full_eye_crop_box[2] - full_eye_crop_box[0]) / 2))
                sclera_cx_var.set(cx)
                sclera_cy_var.set(cy)
                sclera_r_var.set(r)
                sclera_sel_status.config(text=f"中心({cx},{cy}) 半径{r}", foreground='#00aa44')
            else:
                sclera_cx_var.set(0)
                sclera_cy_var.set(0)
                sclera_r_var.set(0)
                sclera_sel_status.config(text="(0=默认取图片中心)", foreground='gray')
        finally:
            _sclera_sync[0] = False

    sclera_cx_var.trace_add('write', apply_sclera_selection)
    sclera_cy_var.trace_add('write', apply_sclera_selection)
    sclera_r_var.trace_add('write', apply_sclera_selection)

    # ── 虹膜选区控件 ──
    iris_sel_row = ttk.Frame(tab_full)
    iris_sel_row.pack(fill='x', padx=10, pady=(0, 2))
    ttk.Label(iris_sel_row, text="虹膜选区:", width=12).pack(side='left')
    ttk.Label(iris_sel_row, text="X偏移:").pack(side='left')
    iris_offset_x_var = tk.IntVar(value=0)
    iris_offset_x_spin = ttk.Spinbox(
        iris_sel_row, from_=-999, to=999, textvariable=iris_offset_x_var, width=6
    )
    iris_offset_x_spin.pack(side='left', padx=(2, 8))
    ttk.Label(iris_sel_row, text="Y偏移:").pack(side='left')
    iris_offset_y_var = tk.IntVar(value=0)
    iris_offset_y_spin = ttk.Spinbox(
        iris_sel_row, from_=-999, to=999, textvariable=iris_offset_y_var, width=6
    )
    iris_offset_y_spin.pack(side='left', padx=(2, 8))
    ttk.Label(iris_sel_row, text="外径:").pack(side='left')
    iris_radius_var = tk.IntVar(value=0)  # 0 = 自动
    iris_radius_spin = ttk.Spinbox(
        iris_sel_row, from_=0, to=9999, textvariable=iris_radius_var, width=6
    )
    iris_radius_spin.pack(side='left', padx=(2, 8))
    ttk.Label(iris_sel_row, text="内径:").pack(side='left')
    iris_inner_r_var = tk.IntVar(value=0)  # 0 = 自动估算瞳孔半径
    iris_inner_r_spin = ttk.Spinbox(
        iris_sel_row, from_=0, to=9999, textvariable=iris_inner_r_var, width=6
    )
    iris_inner_r_spin.pack(side='left', padx=(2, 8))
    iris_sel_status = ttk.Label(iris_sel_row, text="(默认: 巩膜选区中心)", foreground='gray')
    iris_sel_status.pack(side='left', padx=(0, 8))

    def apply_iris_selection(*_args):
        nonlocal full_iris_crop_box
        ox      = iris_offset_x_var.get()
        oy      = iris_offset_y_var.get()
        r       = iris_radius_var.get()
        r_inner = iris_inner_r_var.get()
        if ox == 0 and oy == 0 and r == 0 and r_inner == 0:
            full_iris_crop_box = None
            iris_sel_status.config(text="(默认: 巩膜选区中心)", foreground='gray')
        else:
            # 以巩膜选区中心为基准点，加上偏移量
            if full_eye_crop_box:
                base_cx = (full_eye_crop_box[0] + full_eye_crop_box[2]) / 2
                base_cy = (full_eye_crop_box[1] + full_eye_crop_box[3]) / 2
                base_r  = (full_eye_crop_box[2] - full_eye_crop_box[0]) / 2
            else:
                base_cx = base_cy = base_r = None
            if base_cx is not None:
                icx      = base_cx + ox
                icy      = base_cy + oy
                ir_outer = r if r > 0 else max(4, int(base_r * 0.4))
                full_iris_crop_box = (icx, icy, ir_outer, r_inner)
                inner_text = f"内径{r_inner}" if r_inner > 0 else "内径自动"
                iris_sel_status.config(
                    text=f"中心({icx:.0f},{icy:.0f}) 外径{ir_outer} {inner_text}",
                    foreground='#00aa44',
                )
            else:
                full_iris_crop_box = None
                iris_sel_status.config(text="(请先框选巩膜区域)", foreground='orange')
        draw_source_selection()
        if eye_path.get():
            refresh_full_preview()

    def reset_iris_selection():
        nonlocal full_iris_crop_box
        iris_offset_x_var.set(0)
        iris_offset_y_var.set(0)
        iris_radius_var.set(0)
        iris_inner_r_var.set(0)
        full_iris_crop_box = None
        iris_sel_status.config(text="(默认: 巩膜选区中心)", foreground='gray')
        draw_source_selection()
        if eye_path.get():
            refresh_full_preview()

    iris_offset_x_var.trace_add('write', apply_iris_selection)
    iris_offset_y_var.trace_add('write', apply_iris_selection)
    iris_radius_var.trace_add('write', apply_iris_selection)
    iris_inner_r_var.trace_add('write', apply_iris_selection)
    ttk.Button(iris_sel_row, text="重置", command=reset_iris_selection).pack(side='left')
    ttk.Label(
        tab_full,
        text="外径: 虹膜外边界；内径: 瞳孔边界(0=自动估算)；X/Y: 相对巩膜中心偏移(原图像素)。眼皮颜色/材质仅影响效果预览。",
        foreground='gray',
    ).pack(fill='x', padx=10, pady=(0, 2))

    # ── 中心叠加图控件 ──
    center_overlay_row = ttk.Frame(tab_full)
    center_overlay_row.pack(fill='x', padx=10, pady=(0, 2))
    ttk.Label(center_overlay_row, text="瞳孔贴图:", width=12).pack(side='left')
    ttk.Entry(center_overlay_row, textvariable=center_overlay_path, width=40).pack(side='left', padx=(0, 6), fill='x', expand=True)
    ttk.Button(center_overlay_row, text="选择图片", command=select_center_overlay).pack(side='left', padx=(0, 6))
    ttk.Button(center_overlay_row, text="清除", command=clear_center_overlay).pack(side='left')
    ttk.Label(
        tab_full,
        text="瞳孔贴图: 可选，自定义中心瞳孔纹理；运行时会按代码设置进行缩放与羽化。",
        foreground='gray',
    ).pack(fill='x', padx=10, pady=(0, 2))

    blink_control_row = ttk.Frame(tab_full)
    blink_control_row.pack(fill='x', padx=10, pady=(0, 2))
    ttk.Label(blink_control_row, text="闭眼程度:", width=12).pack(side='left')
    blink_scale = ttk.Scale(
        blink_control_row,
        from_=0.0,
        to=100.0,
        variable=blink_amount,
        orient='horizontal',
        length=260,
    )
    blink_scale.pack(side='left', padx=(0, 8))
    blink_value_label = ttk.Label(blink_control_row, text="88%")
    blink_value_label.pack(side='left')

    eyelid_style_row = ttk.Frame(tab_full)
    eyelid_style_row.pack(fill='x', padx=10, pady=(0, 2))
    ttk.Label(eyelid_style_row, text="眼皮颜色:", width=12).pack(side='left')
    eyelid_color_swatch = tk.Canvas(eyelid_style_row, width=24, height=18, highlightthickness=1)
    eyelid_color_swatch.pack(side='left', padx=(0, 6))
    ttk.Button(eyelid_style_row, text="选择颜色", command=choose_eyelid_color).pack(side='left', padx=(0, 12))
    ttk.Label(eyelid_style_row, text="眼皮材质:", width=12).pack(side='left')
    ttk.Entry(eyelid_style_row, textvariable=eyelid_material_path, width=36).pack(side='left', padx=(0, 6), fill='x', expand=True)
    ttk.Button(eyelid_style_row, text="选择材质", command=select_eyelid_material).pack(side='left', padx=(0, 6))
    ttk.Button(eyelid_style_row, text="清除材质", command=clear_eyelid_material).pack(side='left')


    def make_preview_image(img, max_size):
        ensure_preview_support()
        preview = img.copy()
        preview.thumbnail(max_size, Image.LANCZOS)
        return image_tk.PhotoImage(preview)

    def clear_preview(label, text):
        label.configure(image='', text=text)

    def canvas_to_image_coords(canvas_x, canvas_y):
        preview_size = source_canvas_meta['preview_size']
        image_size = source_canvas_meta['image_size']
        offset_x, offset_y = source_canvas_meta['offset']
        if not preview_size or not image_size:
            return None

        preview_w, preview_h = preview_size
        image_w, image_h = image_size
        rel_x = min(max(canvas_x - offset_x, 0), preview_w)
        rel_y = min(max(canvas_y - offset_y, 0), preview_h)
        return (
            rel_x * image_w / preview_w,
            rel_y * image_h / preview_h,
        )

    def draw_source_selection():
        source_canvas.delete('selection')
        if not full_eye_crop_box:
            return

        preview_size = source_canvas_meta['preview_size']
        image_size = source_canvas_meta['image_size']
        if not preview_size or not image_size:
            return

        preview_w, preview_h = preview_size
        image_w, image_h = image_size
        offset_x, offset_y = source_canvas_meta['offset']
        left, top, right, bottom = full_eye_crop_box
        x1 = offset_x + left * preview_w / image_w
        y1 = offset_y + top * preview_h / image_h
        x2 = offset_x + right * preview_w / image_w
        y2 = offset_y + bottom * preview_h / image_h
        # 青色圆: 巩膜选区
        source_canvas.create_oval(x1, y1, x2, y2, outline='#00d0ff', width=2, tags='selection')
        center_x = (x1 + x2) / 2.0
        center_y = (y1 + y2) / 2.0
        source_canvas.create_line(center_x - 8, center_y, center_x + 8, center_y, fill='#00d0ff', width=1, tags='selection')
        source_canvas.create_line(center_x, center_y - 8, center_x, center_y + 8, fill='#00d0ff', width=1, tags='selection')

        # 黄色圆: 虹膜选区 (外圆 + 内圆)
        is_default = full_iris_crop_box is None
        if not is_default:
            if len(full_iris_crop_box) == 4:
                icx, icy, ir_outer, ir_inner = full_iris_crop_box
            else:
                icx, icy, ir_outer = full_iris_crop_box
                ir_inner = 0
            # 外圆
            ox1 = offset_x + (icx - ir_outer) * preview_w / image_w
            oy1 = offset_y + (icy - ir_outer) * preview_h / image_h
            ox2 = offset_x + (icx + ir_outer) * preview_w / image_w
            oy2 = offset_y + (icy + ir_outer) * preview_h / image_h
            # 内圆 (ir_inner=0 时用估算值)
            eff_inner = ir_inner if ir_inner > 0 else ir_outer * 0.45
            ix1 = offset_x + (icx - eff_inner) * preview_w / image_w
            iy1 = offset_y + (icy - eff_inner) * preview_h / image_h
            ix2 = offset_x + (icx + eff_inner) * preview_w / image_w
            iy2 = offset_y + (icy + eff_inner) * preview_h / image_h
            icx_px = offset_x + icx * preview_w / image_w
            icy_px = offset_y + icy * preview_h / image_h
        else:
            # 默认: 估算虹膜位置
            sclera_r = (x2 - x1) / 2.0
            def_outer_px = sclera_r * 0.4
            def_inner_px = sclera_r * 0.18
            ox1, oy1 = center_x - def_outer_px, center_y - def_outer_px
            ox2, oy2 = center_x + def_outer_px, center_y + def_outer_px
            ix1, iy1 = center_x - def_inner_px, center_y - def_inner_px
            ix2, iy2 = center_x + def_inner_px, center_y + def_inner_px
            icx_px, icy_px = center_x, center_y
            ir_inner = 0  # 用于判断内圆样式
        # 绘制外圆
        source_canvas.create_oval(
            ox1, oy1, ox2, oy2,
            outline='#ffd400', width=2,
            dash=(6, 3) if is_default else (),
            tags='selection',
        )
        # 绘制内圆
        source_canvas.create_oval(
            ix1, iy1, ix2, iy2,
            outline='#ffd400', width=2,
            dash=(6, 3) if (is_default or ir_inner == 0) else (),
            tags='selection',
        )
        # 中心十字准星
        source_canvas.create_line(icx_px - 6, icy_px, icx_px + 6, icy_px, fill='#ffd400', width=1, tags='selection')
        source_canvas.create_line(icx_px, icy_px - 6, icx_px, icy_px + 6, fill='#ffd400', width=1, tags='selection')

    def on_source_press(event):
        nonlocal source_canvas_drag
        coords = canvas_to_image_coords(event.x, event.y)
        if coords is None:
            return
        source_canvas_drag = coords

    def on_source_drag(event):
        coords = canvas_to_image_coords(event.x, event.y)
        if coords is None or source_canvas_drag is None:
            return

        center_x, center_y = source_canvas_drag
        end_x, end_y = coords
        source_canvas.delete('drag')

        preview_size = source_canvas_meta['preview_size']
        image_size = source_canvas_meta['image_size']
        offset_x, offset_y = source_canvas_meta['offset']
        preview_w, preview_h = preview_size
        image_w, image_h = image_size
        radius = max(abs(end_x - center_x), abs(end_y - center_y))
        x1 = offset_x + (center_x - radius) * preview_w / image_w
        y1 = offset_y + (center_y - radius) * preview_h / image_h
        x2 = offset_x + (center_x + radius) * preview_w / image_w
        y2 = offset_y + (center_y + radius) * preview_h / image_h
        source_canvas.create_oval(x1, y1, x2, y2, outline='#ffd400', width=1, dash=(4, 2), tags='drag')
        center_canvas_x = offset_x + center_x * preview_w / image_w
        center_canvas_y = offset_y + center_y * preview_h / image_h
        source_canvas.create_line(center_canvas_x - 8, center_canvas_y, center_canvas_x + 8, center_canvas_y, fill='#ffd400', width=1, tags='drag')
        source_canvas.create_line(center_canvas_x, center_canvas_y - 8, center_canvas_x, center_canvas_y + 8, fill='#ffd400', width=1, tags='drag')

    def on_source_release(event):
        nonlocal full_eye_crop_box, source_canvas_drag
        coords = canvas_to_image_coords(event.x, event.y)
        if coords is None or source_canvas_drag is None:
            source_canvas_drag = None
            source_canvas.delete('drag')
            return

        center_x, center_y = source_canvas_drag
        end_x, end_y = coords
        source_canvas_drag = None
        source_canvas.delete('drag')

        radius = max(abs(end_x - center_x), abs(end_y - center_y))
        if radius < 4:
            return

        full_eye_crop_box = normalize_crop_box(
            (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
            source_canvas_meta['image_size'],
        )
        sync_sclera_spinboxes()
        refresh_full_preview()

    source_canvas.bind('<ButtonPress-1>', on_source_press)
    source_canvas.bind('<B1-Motion>', on_source_drag)
    source_canvas.bind('<ButtonRelease-1>', on_source_release)

    def update_source_canvas(source_img):
        ensure_preview_support()
        canvas_w = int(source_canvas['width'])
        canvas_h = int(source_canvas['height'])
        preview = source_img.copy()
        preview.thumbnail((canvas_w - 8, canvas_h - 8), Image.LANCZOS)
        preview_refs['source'] = image_tk.PhotoImage(preview)
        preview_w, preview_h = preview.size
        offset_x = (canvas_w - preview_w) // 2
        offset_y = (canvas_h - preview_h) // 2
        source_canvas_meta['image_size'] = source_img.size
        source_canvas_meta['preview_size'] = preview.size
        source_canvas_meta['offset'] = (offset_x, offset_y)

        source_canvas.delete('all')
        source_canvas.create_image(offset_x, offset_y, anchor='nw', image=preview_refs['source'], tags='source-image')
        draw_source_selection()

    def on_blink_amount_changed(*_args):
        blink_value_label.config(text=f"{int(round(blink_amount.get()))}%")
        if eye_path.get():
            refresh_full_preview()

    blink_amount.trace_add('write', on_blink_amount_changed)
    on_blink_amount_changed()

    def update_eyelid_color_swatch(*_args):
        eyelid_color_swatch.delete('all')
        eyelid_color_swatch.create_rectangle(0, 0, 24, 18, fill=eyelid_color.get(), outline='')
        if eye_path.get():
            refresh_full_preview()

    def on_eyelid_material_changed(*_args):
        if eye_path.get():
            refresh_full_preview()

    def on_center_overlay_changed(*_args):
        if eye_path.get():
            refresh_full_preview()

    eyelid_color.trace_add('write', update_eyelid_color_swatch)
    eyelid_material_path.trace_add('write', on_eyelid_material_changed)
    center_overlay_path.trace_add('write', on_center_overlay_changed)

    def refresh_full_preview():
        if not eye_path.get():
            source_canvas.delete('all')
            source_canvas.create_text(210, 130, text="未选择图片", fill="#d0d0d0")
            clear_preview(crop_preview, "选区圆形预览")
            clear_preview(composed_preview, "组合效果")
            clear_preview(blink_preview, "眨眼效果")
            clear_preview(sclera_preview, "巩膜（全图）")
            clear_preview(iris_preview, "圆形眼珠贴图")
            clear_preview(upper_preview, "上眼睑")
            clear_preview(lower_preview, "下眼睑")
            preview_status.config(text="选择完整眼睛图片后，可在这里预览自动裁剪和提取结果。", foreground='gray')
            preview_refs.clear()
            return

        try:
            ensure_preview_support()
            source_img = load_rgb_image(eye_path.get())
            actual_crop_box = normalize_crop_box(full_eye_crop_box, source_img.size)

            preset = get_current_preset()
            sclera_img, iris_img, iris_sticker_img, pupil_texture_img, pupil_preview_img, upper_img, lower_img, extracted_pupil_ratio = extract_from_full_eye(
                eye_path.get(),
                preset,
                log_fn=lambda *_args, **_kwargs: None,
                crop_box=actual_crop_box,
                iris_crop_box=full_iris_crop_box,
                center_overlay_path=center_overlay_path.get() or None,
            )

            update_source_canvas(source_img)

            sclera_preview_img = compose_iris_on_sclera(
                sclera_img,
                iris_sticker_img,
                preset,
            )

            crop_img = apply_circular_alpha(
                source_img.crop(actual_crop_box).resize(
                    (preset['SCREEN_WIDTH'], preset['SCREEN_HEIGHT']),
                    Image.LANCZOS,
                )
            )
            composed_img = make_composed_eye_preview(
                sclera_img,
                iris_sticker_img,
                preset,
                upper_img,
                lower_img,
                eyelid_color=eyelid_color.get(),
                material_path=eyelid_material_path.get() or None,
                pupil_ratio=extracted_pupil_ratio,
                pupil_feather_ratio=0.04,
            )
            blink_img = make_blink_preview(
                sclera_img,
                iris_sticker_img,
                preset,
                upper_img,
                lower_img,
                close_ratio=blink_amount.get() / 100.0,
                eyelid_color=eyelid_color.get(),
                material_path=eyelid_material_path.get() or None,
                pupil_ratio=extracted_pupil_ratio,
                pupil_feather_ratio=0.04,
            )

            preview_refs['crop'] = make_preview_image(crop_img, (160, 160))
            preview_refs['composed'] = make_preview_image(composed_img, (160, 160))
            preview_refs['blink'] = make_preview_image(blink_img, (160, 160))
            preview_refs['sclera'] = make_preview_image(sclera_preview_img, (160, 160))
            preview_refs['iris'] = make_preview_image(iris_sticker_img, (160, 160))
            preview_refs['upper'] = make_preview_image(upper_img.convert('RGB'), (160, 160))
            preview_refs['lower'] = make_preview_image(lower_img.convert('RGB'), (160, 160))

            crop_preview.configure(image=preview_refs['crop'], text='')
            composed_preview.configure(image=preview_refs['composed'], text='')
            blink_preview.configure(image=preview_refs['blink'], text='')
            sclera_preview.configure(image=preview_refs['sclera'], text='')
            iris_preview.configure(image=preview_refs['iris'], text='')
            upper_preview.configure(image=preview_refs['upper'], text='')
            lower_preview.configure(image=preview_refs['lower'], text='')

            center_x = (actual_crop_box[0] + actual_crop_box[2]) // 2
            center_y = (actual_crop_box[1] + actual_crop_box[3]) // 2
            radius = (actual_crop_box[2] - actual_crop_box[0]) // 2
            preview_status.config(
                text=(
                    f"原图: {source_img.size[0]}×{source_img.size[1]} | "
                    f"屏幕模式: {screen_size.get()}×{screen_size.get()} | "
                    f"圆心: ({center_x}, {center_y}) | 半径: {radius} | 闭眼程度: {int(round(blink_amount.get()))}% | "
                    f"眼皮颜色: {eyelid_color.get()}"
                ),
                foreground='gray',
            )
        except Exception as exc:
            source_canvas.delete('all')
            source_canvas.create_text(210, 130, text="预览失败", fill="#ff8080")
            clear_preview(crop_preview, "选区圆形预览")
            clear_preview(composed_preview, "组合效果")
            clear_preview(blink_preview, "眨眼效果")
            clear_preview(sclera_preview, "巩膜（全图）")
            clear_preview(iris_preview, "虹膜")
            clear_preview(upper_preview, "上眼睑")
            clear_preview(lower_preview, "下眼睑")
            preview_refs.clear()
            preview_status.config(text=f"预览失败: {exc}", foreground='red')

    update_eyelid_color_swatch()

    preview_button_row = ttk.Frame(tab_full)
    preview_button_row.pack(fill='x', padx=10, pady=(0, 5))
    ttk.Button(preview_button_row, text="刷新预览", command=refresh_full_preview).pack(side='left')

    log_frame = ttk.LabelFrame(root, text="日志")
    log_frame.pack(fill='both', expand=True, padx=10, pady=5)
    log_text = tk.Text(log_frame, height=12, wrap='word')
    scrollbar = ttk.Scrollbar(log_frame, command=log_text.yview)
    log_text.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right', fill='y')
    log_text.pack(fill='both', expand=True, padx=5, pady=5)

    def log(msg):
        log_text.insert('end', msg + '\n')
        log_text.see('end')
        root.update_idletasks()

    def convert_full():
        log_text.delete('1.0', 'end')
        if not eye_path.get():
            messagebox.showerror("缺少图片", "请选择一张完整的眼睛图片")
            return

        ok = convert_from_full_eye(
            screen_size=screen_size.get(),
            output_dir=output_dir.get(),
            eye_path=eye_path.get(),
            log_fn=log,
            crop_box=full_eye_crop_box,
            iris_crop_box=full_iris_crop_box,
            center_overlay_path=center_overlay_path.get() or None,
            preset_override=get_current_preset(),
        )
        if ok:
            messagebox.showinfo(
                "完成",
                f"眼睛数据已生成到:\n{output_dir.get()}\n\n"
                "提取的中间素材 (_extracted_*.png) 也已保存。\n"
                "如需微调，可修改中间素材后用「分离素材」模式重新生成。\n\n"
                "将生成的 .h 文件复制到项目的\n"
                "main/boards/vibratalkie/eye_data/240/ (或 160/)\n"
                "然后重新编译固件即可。",
            )

    ttk.Button(tab_full, text="🔄 从完整图片生成眼睛数据", command=convert_full).pack(padx=10, pady=10)

    tab_sep = ttk.Frame(notebook)
    notebook.add(tab_sep, text=" 🎨 分离素材 (手动指定) ")

    files_frame = ttk.Frame(tab_sep)
    files_frame.pack(fill='x', padx=10, pady=5)
    files_frame.columnconfigure(1, weight=1)

    ttk.Label(files_frame, text="巩膜 (sclera):", width=18).grid(row=0, column=0, padx=5, pady=2, sticky='w')
    ttk.Entry(files_frame, textvariable=sclera_path, width=40).grid(row=0, column=1, padx=5, pady=2, sticky='ew')
    ttk.Button(files_frame, text="选择", command=lambda: select_file(sclera_path)).grid(row=0, column=2, padx=2, pady=2)
    ttk.Label(files_frame, text="白眼球背景, 375×375 或 250×250", foreground='gray').grid(row=0, column=3, padx=5, pady=2, sticky='w')

    ttk.Label(files_frame, text="虹膜 (iris):", width=18).grid(row=1, column=0, padx=5, pady=2, sticky='w')
    ttk.Entry(files_frame, textvariable=iris_path, width=40).grid(row=1, column=1, padx=5, pady=2, sticky='ew')
    ttk.Button(files_frame, text="选择", command=lambda: select_file(iris_path)).grid(row=1, column=2, padx=2, pady=2)
    ttk.Label(files_frame, text="极坐标展开图, 471×75 或 314×50", foreground='gray').grid(row=1, column=3, padx=5, pady=2, sticky='w')

    ttk.Label(files_frame, text="上眼睑 (upper):", width=18).grid(row=2, column=0, padx=5, pady=2, sticky='w')
    ttk.Entry(files_frame, textvariable=upper_path, width=40).grid(row=2, column=1, padx=5, pady=2, sticky='ew')
    ttk.Button(files_frame, text="选择", command=lambda: select_file(upper_path)).grid(row=2, column=2, padx=2, pady=2)
    ttk.Label(files_frame, text="灰度遮罩, 与屏幕同尺寸", foreground='gray').grid(row=2, column=3, padx=5, pady=2, sticky='w')

    ttk.Label(files_frame, text="下眼睑 (lower):", width=18).grid(row=3, column=0, padx=5, pady=2, sticky='w')
    ttk.Entry(files_frame, textvariable=lower_path, width=40).grid(row=3, column=1, padx=5, pady=2, sticky='ew')
    ttk.Button(files_frame, text="选择", command=lambda: select_file(lower_path)).grid(row=3, column=2, padx=2, pady=2)
    ttk.Label(files_frame, text="灰度遮罩, 与屏幕同尺寸", foreground='gray').grid(row=3, column=3, padx=5, pady=2, sticky='w')

    ttk.Label(files_frame, text="极坐标 (polar):", width=18).grid(row=4, column=0, padx=5, pady=2, sticky='w')
    ttk.Entry(files_frame, textvariable=polar_path, width=40).grid(row=4, column=1, padx=5, pady=2, sticky='ew')
    ttk.Button(files_frame, text="选择", command=lambda: select_file(polar_path)).grid(row=4, column=2, padx=2, pady=2)
    ttk.Label(files_frame, text="可选, 留空自动生成", foreground='gray').grid(row=4, column=3, padx=5, pady=2, sticky='w')

    ref_frame = ttk.LabelFrame(tab_sep, text="素材尺寸参考")
    ref_frame.pack(fill='x', padx=10, pady=5)
    ref_label = ttk.Label(ref_frame, text="", wraplength=700, justify='left')
    ref_label.pack(padx=10, pady=5)

    def update_ref(*_args):
        preset = get_current_preset()
        ref_label.config(
            text=(
                f"巩膜: {preset['SCLERA_WIDTH']}×{preset['SCLERA_HEIGHT']} | "
                f"虹膜展开图: {preset['IRIS_MAP_WIDTH']}×{preset['IRIS_MAP_HEIGHT']} | "
                f"眼睑: {preset['SCREEN_WIDTH']}×{preset['SCREEN_HEIGHT']} | "
                f"极坐标: {preset['IRIS_WIDTH']}×{preset['IRIS_HEIGHT']}"
            )
        )
        if eye_path.get():
            refresh_full_preview()

    iris_width_var.trace_add('write', update_ref)
    update_ref()

    def convert_separate():
        log_text.delete('1.0', 'end')

        for name, var in [
            ("巩膜", sclera_path),
            ("虹膜", iris_path),
            ("上眼睑", upper_path),
            ("下眼睑", lower_path),
        ]:
            if not var.get():
                messagebox.showerror("缺少素材", f"请选择{name}文件")
                return

        ok = convert_eye_data(
            screen_size=screen_size.get(),
            output_dir=output_dir.get(),
            sclera_path=sclera_path.get(),
            iris_path=iris_path.get(),
            upper_path=upper_path.get(),
            lower_path=lower_path.get(),
            polar_path=polar_path.get() or None,
            log_fn=log,
        )
        if ok:
            messagebox.showinfo(
                "完成",
                f"眼睛数据已生成到:\n{output_dir.get()}\n\n"
                "将生成的文件复制到项目的\n"
                "main/boards/vibratalkie/eye_data/240/ (或 160/)\n"
                "然后重新编译固件即可。",
            )

    ttk.Button(tab_sep, text="🔄 从分离素材生成眼睛数据", command=convert_separate).pack(padx=10, pady=10)

    root.mainloop()


# ── CLI 模式 ─────────────────────────────────────────
def run_cli_full(args):
    """CLI: 完整眼睛图片模式"""
    ok = convert_from_full_eye(
        screen_size=args.screen,
        output_dir=args.output,
        eye_path=args.eye,
    )
    sys.exit(0 if ok else 1)


def run_cli_separate(args):
    """CLI: 分离素材模式"""
    ok = convert_eye_data(
        screen_size=args.screen,
        output_dir=args.output,
        sclera_path=args.sclera,
        iris_path=args.iris,
        upper_path=args.upper,
        lower_path=args.lower,
        polar_path=args.polar,
    )
    sys.exit(0 if ok else 1)


# ── 入口 ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="RUIO 眼睛位图数据生成工具 - 将 PNG 素材转为 C 头文件")
    parser.add_argument('--cli', action='store_true',
                       help='命令行模式 (不启动 GUI)')
    parser.add_argument('--screen', type=int, default=240, choices=[160, 240],
                       help='屏幕尺寸 (默认 240)')
    parser.add_argument('--output', default='eye_data_output',
                       help='输出目录 (默认 eye_data_output)')

    # 模式 A: 完整眼睛图片 (自动提取)
    parser.add_argument('--eye', help='完整眼睛图片路径 (自动裁剪提取巩膜/虹膜/眼睑)')

    # 模式 B: 分离素材
    parser.add_argument('--sclera', help='巩膜 PNG 文件路径')
    parser.add_argument('--iris', help='虹膜 PNG 文件路径')
    parser.add_argument('--upper', help='上眼睑 PNG 文件路径')
    parser.add_argument('--lower', help='下眼睑 PNG 文件路径')
    parser.add_argument('--polar', help='极坐标 PNG 文件路径 (可选)')

    args = parser.parse_args()

    if args.cli:
        if args.eye:
            # 完整图片模式
            run_cli_full(args)
        else:
            # 分离素材模式
            for name, val in [('sclera', args.sclera), ('iris', args.iris),
                              ('upper', args.upper), ('lower', args.lower)]:
                if not val:
                    parser.error(f'CLI 模式下需要 --eye 或同时指定 --sclera/--iris/--upper/--lower')
            run_cli_separate(args)
    else:
        run_gui()


if __name__ == '__main__':
    main()
