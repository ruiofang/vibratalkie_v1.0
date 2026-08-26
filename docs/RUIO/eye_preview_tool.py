#!/usr/bin/env python3
"""
RUIO 猫咪眼睛预览与调参工具

功能:
- 可视化预览所有 22 种表情
- 实时滑动条调整 EyeParams 参数
- 模拟眨眼动画
- 一键导出修改后的表情参数 C 代码
- 支持单眼/双眼模式切换

用法:
    python eye_preview_tool.py

依赖:
    pip install tkinter  (通常 Python 自带)
"""

import tkinter as tk
from tkinter import ttk, messagebox
import math
import random
import copy

# ── 默认表情参数 ────────────────────────────────────
# 与 vibratalkie_eye_display.cc 中 kExpressionTable 完全一致

DEFAULT_EXPRESSIONS = [
    ("neutral",      1.0, 1.0,  1.0, 1.0,   0.0,  0.0,  0.0,  0.0),
    ("idle",         1.0, 1.0,  1.0, 1.0,   0.0,  0.0,  0.0,  0.0),
    ("relaxed",      1.0, 0.85, 1.0, 0.85,  0.0,  0.0,  0.0,  0.0),
    ("microchip_ai", 1.0, 1.0,  1.0, 1.0,   0.0,  0.0,  0.0,  0.0),
    ("happy",        1.1, 0.40, 1.1, 0.40,  0.0,  0.0,  0.0,  0.0),
    ("laughing",     1.15,0.25, 1.15,0.25,  0.0,  0.0,  0.0,  0.0),
    ("funny",        1.05,0.35, 1.05,0.35,  0.0,  0.0,  0.0,  0.0),
    ("loving",       1.0, 0.45, 1.0, 0.45,  0.0,  0.0,  0.0,  0.0),
    ("confident",    0.95,0.70, 0.95,0.70,  0.0,  0.0,  0.0,  0.0),
    ("delicious",    1.0, 0.30, 1.0, 0.30,  0.0,  0.0,  0.0,  0.0),
    ("kissy",        0.85,0.50, 0.85,0.50,  0.0,  0.0,  0.0,  0.0),
    ("cool",         1.2, 0.35, 1.2, 0.35,  0.0,  0.0,  0.0,  0.0),
    ("sad",          1.0, 0.70, 1.0, 0.70,  0.0,  0.35, 0.0,  0.35),
    ("crying",       1.0, 0.80, 1.0, 0.80,  0.0,  0.40, 0.0,  0.40),
    ("angry",        1.1, 0.50, 1.1, 0.50,  0.0,  0.2,  0.0,  0.2),
    ("surprised",    1.2, 1.25, 1.2, 1.25,  0.0,  0.0,  0.0,  0.0),
    ("shocked",      1.3, 1.35, 1.3, 1.35,  0.0,  0.0,  0.0,  0.0),
    ("thinking",     0.8, 0.7,  1.1, 1.05,  0.25,-0.3,  0.25,-0.3),
    ("confused",     0.85,0.8,  1.1, 1.1,  -0.25, 0.0,  0.3,  0.0),
    ("embarrassed",  0.9, 0.60, 0.9, 0.60,  0.3,  0.0,  0.3,  0.0),
    ("sleepy",       1.0, 0.15, 1.0, 0.15,  0.0,  0.0,  0.0,  0.0),
    ("winking",      1.0, 0.06, 1.0, 1.0,   0.0,  0.0,  0.0,  0.0),
    ("silly",        1.1, 1.1,  0.85,0.06, -0.2,  0.2,  0.0,  0.0),
]

# ── 颜色配置 ────────────────────────────────────────

COLORS = {
    "background": "#000000",
    "eye":        "#F0F0F0",
    "outline":    "#3A3A3A",
    "pupil":      "#111111",
    "highlight":  "#FAFAFA",
}

# ── 屏幕模拟参数 ────────────────────────────────────

SCREEN_SIZE = 240  # 模拟 240x240 圆屏


class EyeParams:
    """与 C 代码的 EyeParams 结构体对应"""
    def __init__(self, lw=1.0, lh=1.0, rw=1.0, rh=1.0,
                 lpdx=0.0, lpdy=0.0, rpdx=0.0, rpdy=0.0):
        self.lw = lw
        self.lh = lh
        self.rw = rw
        self.rh = rh
        self.lpdx = lpdx
        self.lpdy = lpdy
        self.rpdx = rpdx
        self.rpdy = rpdy


class EyePreviewApp:
    def __init__(self, root):
        self.root = root
        self.root.title("RUIO 猫咪眼睛预览工具")
        self.root.resizable(True, True)

        # 表情数据（可修改）
        self.expressions = []
        for e in DEFAULT_EXPRESSIONS:
            self.expressions.append({
                "name": e[0],
                "params": EyeParams(*e[1:])
            })

        self.current_idx = 0
        self.single_eye = False
        self.blink_phase = 0  # 0=normal, 1=closing, 2=opening
        self.blink_progress = 0.0

        self._build_ui()
        self._select_expression(0)

    def _build_ui(self):
        # ── 主布局 ──
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 左侧: 表情列表
        left = ttk.LabelFrame(main_frame, text="表情列表", padding=5)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        self.expr_listbox = tk.Listbox(left, width=16, height=24,
                                       font=("Consolas", 11))
        self.expr_listbox.pack(fill=tk.Y, expand=True)
        for e in self.expressions:
            self.expr_listbox.insert(tk.END, e["name"])
        self.expr_listbox.bind("<<ListboxSelect>>", self._on_list_select)

        # 中间: 画布
        center = ttk.Frame(main_frame)
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        canvas_size = 320
        self.canvas = tk.Canvas(center, width=canvas_size, height=canvas_size,
                                bg=COLORS["background"], highlightthickness=1,
                                highlightbackground="#333")
        self.canvas.pack(pady=(0, 5))

        # 模式切换 + 动画按钮
        btn_frame = ttk.Frame(center)
        btn_frame.pack(fill=tk.X)

        self.mode_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(btn_frame, text="单眼模式", variable=self.mode_var,
                        command=self._on_mode_change).pack(side=tk.LEFT)

        ttk.Button(btn_frame, text="快速眨眼", command=self._do_blink).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="慢眨眼", command=self._do_slow_blink).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="导出 C 代码", command=self._export_code).pack(side=tk.RIGHT)

        # 名称标签
        self.name_label = ttk.Label(center, text="neutral", font=("", 14, "bold"))
        self.name_label.pack(pady=(5, 0))

        # 右侧: 参数滑动条
        right = ttk.LabelFrame(main_frame, text="参数调节", padding=5)
        right.pack(side=tk.LEFT, fill=tk.Y)

        self.sliders = {}
        slider_defs = [
            ("lw",   "左眼宽", 0.3, 1.5),
            ("lh",   "左眼高", 0.05, 1.5),
            ("rw",   "右眼宽", 0.3, 1.5),
            ("rh",   "右眼高", 0.05, 1.5),
            ("lpdx", "左瞳X",  -1.0, 1.0),
            ("lpdy", "左瞳Y",  -1.0, 1.0),
            ("rpdx", "右瞳X",  -1.0, 1.0),
            ("rpdy", "右瞳Y",  -1.0, 1.0),
        ]

        for key, label, vmin, vmax in slider_defs:
            frame = ttk.Frame(right)
            frame.pack(fill=tk.X, pady=1)
            ttk.Label(frame, text=label, width=6).pack(side=tk.LEFT)
            var = tk.DoubleVar(value=1.0 if "w" in key or "h" in key else 0.0)
            scale = ttk.Scale(frame, from_=vmin, to=vmax, variable=var,
                              orient=tk.HORIZONTAL, length=180,
                              command=lambda v, k=key: self._on_slider(k))
            scale.pack(side=tk.LEFT, padx=2)
            val_label = ttk.Label(frame, text="1.00", width=5)
            val_label.pack(side=tk.LEFT)
            self.sliders[key] = (var, val_label)

        # 颜色编辑区
        color_frame = ttk.LabelFrame(right, text="颜色 (十六进制)", padding=5)
        color_frame.pack(fill=tk.X, pady=(10, 0))

        self.color_entries = {}
        for cname, cval in COLORS.items():
            if cname == "background":
                continue
            f = ttk.Frame(color_frame)
            f.pack(fill=tk.X, pady=1)
            ttk.Label(f, text=cname, width=10).pack(side=tk.LEFT)
            sv = tk.StringVar(value=cval)
            entry = ttk.Entry(f, textvariable=sv, width=10)
            entry.pack(side=tk.LEFT)
            sv.trace_add("write", lambda *a: self._draw())
            self.color_entries[cname] = sv

        ttk.Label(right, text="提示: 拖动滑块即时预览",
                  foreground="gray").pack(pady=(10, 0))

    def _on_list_select(self, event):
        sel = self.expr_listbox.curselection()
        if sel:
            self._select_expression(sel[0])

    def _select_expression(self, idx):
        self.current_idx = idx
        p = self.expressions[idx]["params"]
        name = self.expressions[idx]["name"]

        self.name_label.config(text=name)

        # 更新滑动条
        for key in ["lw", "lh", "rw", "rh", "lpdx", "lpdy", "rpdx", "rpdy"]:
            self.sliders[key][0].set(getattr(p, key))
            self.sliders[key][1].config(text=f"{getattr(p, key):.2f}")

        self._draw()

    def _on_slider(self, key):
        val = self.sliders[key][0].get()
        self.sliders[key][1].config(text=f"{val:.2f}")

        # 更新当前表情参数
        p = self.expressions[self.current_idx]["params"]
        setattr(p, key, val)

        self._draw()

    def _on_mode_change(self):
        self.single_eye = self.mode_var.get()
        self._draw()

    def _get_colors(self):
        colors = dict(COLORS)
        for cname, sv in self.color_entries.items():
            v = sv.get().strip()
            if v.startswith("#") and len(v) == 7:
                colors[cname] = v
        return colors

    def _draw(self, blink_scale_l=1.0, blink_scale_r=1.0):
        """绘制眼睛到画布"""
        c = self.canvas
        c.delete("all")

        colors = self._get_colors()
        cw = int(c["width"])
        ch = int(c["height"])
        ds = min(cw, ch)
        cx, cy = cw // 2, ch // 2

        # 绘制圆屏边界参考
        r = ds // 2 - 5
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      outline="#222", width=1, dash=(3, 3))

        p = self.expressions[self.current_idx]["params"]

        if self.single_eye:
            ew = int(ds * 75 / 100 * p.lw)
            eh = int(ds * 78 / 100 * p.lh * blink_scale_l)
            pw = int(ds * 22 / 100)
            ph = int(ds * 45 / 100)
            self._draw_one_eye(c, cx, cy, ew, max(eh, 3), pw, ph,
                               p.lpdx, p.lpdy, colors)
        else:
            ew_base = int(ds * 38 / 100)
            eh_base = int(ds * 40 / 100)
            pw = int(ds * 14 / 100)
            ph = int(ds * 28 / 100)
            gap = int(ds * 10 / 100)

            # 左眼
            lw = int(ew_base * p.lw)
            lh = max(int(eh_base * p.lh * blink_scale_l), 3)
            lx = cx - gap // 2 - lw // 2
            self._draw_one_eye(c, lx, cy, lw, lh, pw, ph,
                               p.lpdx, p.lpdy, colors)

            # 右眼
            rw = int(ew_base * p.rw)
            rh = max(int(eh_base * p.rh * blink_scale_r), 3)
            rx = cx + gap // 2 + rw // 2
            self._draw_one_eye(c, rx, cy, rw, rh, pw, ph,
                               p.rpdx, p.rpdy, colors)

    def _draw_one_eye(self, c, cx, cy, ew, eh, pw, ph, pdx, pdy, colors):
        """绘制单只眼睛"""
        # 眼球（圆角矩形，用椭圆模拟）
        c.create_oval(cx - ew // 2, cy - eh // 2,
                      cx + ew // 2, cy + eh // 2,
                      fill=colors["eye"], outline=colors["outline"], width=2)

        # 瞳孔偏移
        max_dx = (ew - pw) // 2
        max_dy = (eh - ph) // 2
        off_x = int(pdx * max_dx) if max_dx > 0 else 0
        off_y = int(pdy * max_dy) if max_dy > 0 else 0
        pcx = cx + off_x
        pcy = cy + off_y

        # 竖瞳
        actual_ph = min(ph, eh - 4)
        if actual_ph > 2:
            c.create_oval(pcx - pw // 2, pcy - actual_ph // 2,
                          pcx + pw // 2, pcy + actual_ph // 2,
                          fill=colors["pupil"], outline="")

        # 高光
        hl_size = max(pw // 2, 4)
        hl_x = cx + pw // 3 + int(off_x * 0.3)
        hl_y = cy - ph // 4 + int(off_y * 0.3)
        # 限制高光在眼球内
        hl_x = max(cx - ew // 2 + hl_size, min(cx + ew // 2 - hl_size, hl_x))
        hl_y = max(cy - eh // 2 + hl_size, min(cy + eh // 2 - hl_size, hl_y))
        if eh > hl_size * 2:
            c.create_oval(hl_x - hl_size // 2, hl_y - hl_size // 2,
                          hl_x + hl_size // 2, hl_y + hl_size // 2,
                          fill=colors["highlight"], outline="")

    # ── 眨眼动画 ──

    def _do_blink(self):
        self._animate_blink(close_ms=100, open_ms=150)

    def _do_slow_blink(self):
        self._animate_blink(close_ms=300, open_ms=400)

    def _animate_blink(self, close_ms=100, open_ms=150, step=20):
        frames_close = max(close_ms // step, 1)
        frames_open = max(open_ms // step, 1)
        total = frames_close + frames_open
        self._blink_frame(0, total, frames_close, step)

    def _blink_frame(self, frame, total, close_frames, step):
        if frame >= total:
            self._draw()
            return

        if frame < close_frames:
            # 闭合阶段
            progress = frame / close_frames
            scale = 1.0 - progress * 0.97  # 最小 0.03
        else:
            # 睁开阶段
            open_frame = frame - close_frames
            open_total = total - close_frames
            progress = open_frame / open_total
            scale = 0.03 + progress * 0.97

        self._draw(blink_scale_l=scale, blink_scale_r=scale)
        self.root.after(step, lambda: self._blink_frame(frame + 1, total, close_frames, step))

    # ── 导出 ──

    def _export_code(self):
        lines = ['static const struct { const char* name; EyeParams p; } kExpressionTable[] = {']
        for e in self.expressions:
            p = e["params"]
            lines.append(
                f'    {{"{e["name"]}",'
                f' {{{p.lw:.2f}f, {p.lh:.2f}f, {p.rw:.2f}f, {p.rh:.2f}f,'
                f'  {p.lpdx:.2f}f, {p.lpdy:.2f}f, {p.rpdx:.2f}f, {p.rpdy:.2f}f}}}},'
            )
        lines.append('};')

        code = "\n".join(lines)

        # 显示在新窗口
        win = tk.Toplevel(self.root)
        win.title("导出 C 代码 - 复制到 vibratalkie_eye_display.cc")
        win.geometry("780x520")
        text = tk.Text(win, font=("Consolas", 11), wrap=tk.NONE)
        text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        text.insert("1.0", code)
        text.config(state=tk.NORMAL)

        # 复制按钮
        def copy_all():
            win.clipboard_clear()
            win.clipboard_append(code)
            messagebox.showinfo("已复制", "C 代码已复制到剪贴板", parent=win)

        ttk.Button(win, text="复制到剪贴板", command=copy_all).pack(pady=5)


def main():
    root = tk.Tk()
    EyePreviewApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
