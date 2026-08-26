#include "vibratalkie_bitmap_eye_display.h"
#include <esp_log.h>
#include <esp_lcd_panel_ops.h>
#include <esp_lcd_panel_io.h>
#include <esp_random.h>
#include <cstdlib>
#include <cstring>

static const char* TAG = "BitmapEye";

// ── 表情 → 眼球位置映射表 ──────────────────────────────
// x,y 范围 0-1023, 512 为中心
static const EmotionPosition kEmotionTable[] = {
    {"neutral",      512, 512, true,  true },
    {"idle",         512, 512, true,  true },
    {"relaxed",      512, 480, true,  true },
    {"happy",        512, 400, true,  true },
    {"laughing",     512, 350, true,  false},
    {"funny",        512, 420, true,  true },
    {"loving",       512, 450, true,  true },
    {"confident",    512, 480, true,  true },
    {"cool",         512, 480, true,  false},
    {"sad",          512, 650, true,  true },
    {"crying",       512, 700, true,  true },
    {"angry",        512, 550, false, true },
    {"surprised",    512, 300, true,  true },
    {"shocked",      512, 250, true,  true },
    {"thinking",     700, 350, true,  true },
    {"confused",     300, 400, true,  true },
    {"embarrassed",  600, 512, true,  false},
    {"sleepy",       512, 600, true,  false},
    {"winking",      512, 512, true,  false},
    {"silly",        400, 350, true,  true },
    {"delicious",    512, 400, true,  true },
    {"kissy",        512, 480, true,  true },
    {"microchip_ai", 512, 512, true,  true },
};

// ── 构造 / 析构 ──────────────────────────────────────
// LVGL 空 flush 回调: 不向 LCD 写入任何数据, SPI 总线留给 eye 任务独占
static void dummy_flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map) {
    lv_display_flush_ready(disp);
}

// SPI 传输完成空回调: 替换 esp_lvgl_port 注册的回调,
// 阻止 eye 任务的每次 SPI DMA 完成都触发 lv_display_flush_ready()
static bool noop_color_trans_done(esp_lcd_panel_io_handle_t panel_io,
                                  esp_lcd_panel_io_event_data_t *edata,
                                  void *user_ctx) {
    return false;
}

static uint16_t BlendRgb565(uint16_t bg, uint16_t fg, uint8_t alpha) {
    uint32_t bg_r = (bg >> 11) & 0x1F;
    uint32_t bg_g = (bg >> 5) & 0x3F;
    uint32_t bg_b = bg & 0x1F;
    uint32_t fg_r = (fg >> 11) & 0x1F;
    uint32_t fg_g = (fg >> 5) & 0x3F;
    uint32_t fg_b = fg & 0x1F;

    uint32_t out_r = (bg_r * (255 - alpha) + fg_r * alpha + 127) / 255;
    uint32_t out_g = (bg_g * (255 - alpha) + fg_g * alpha + 127) / 255;
    uint32_t out_b = (bg_b * (255 - alpha) + fg_b * alpha + 127) / 255;
    return (out_r << 11) | (out_g << 5) | out_b;
}

VibratalkieBitmapEyeDisplay::VibratalkieBitmapEyeDisplay(
    esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel,
    int width, int height, int offset_x, int offset_y,
    bool mirror_x, bool mirror_y, bool swap_xy)
    : SpiLcdDisplay(panel_io, panel, width, height, offset_x, offset_y,
                    mirror_x, mirror_y, swap_xy)
{
    // 替换 LVGL flush 回调为空操作, 阻止 LVGL 任务向 SPI 总线写数据
    lv_display_set_flush_cb(display_, dummy_flush_cb);

    // 取消 esp_lvgl_port 注册的 SPI 传输完成回调
    // 否则 eye 任务每次 draw_bitmap 完成都会触发 lv_display_flush_ready(),
    // 导致 LVGL 任务被高频唤醒, 卡死 SPI 流水线
    const esp_lcd_panel_io_callbacks_t io_cbs = {
        .on_color_trans_done = noop_color_trans_done,
    };
    esp_lcd_panel_io_register_event_callbacks(panel_io, &io_cbs, nullptr);

    // 预分配 DMA 双缓冲 (避免每帧 malloc/free)
    line_buf_[0] = (uint16_t*)heap_caps_malloc(EYE_LINES_PER_BATCH * SCREEN_WIDTH * sizeof(uint16_t), MALLOC_CAP_DMA);
    line_buf_[1] = (uint16_t*)heap_caps_malloc(EYE_LINES_PER_BATCH * SCREEN_WIDTH * sizeof(uint16_t), MALLOC_CAP_DMA);
    if (!line_buf_[0] || !line_buf_[1]) {
        ESP_LOGE(TAG, "Failed to allocate DMA line buffers");
    }

    // 启动眼球渲染任务 (固定到 Core 0 以避免与 Core 1 上的音频任务抢占)
    // 优先级 8: 高于普通应用任务, 低于音频 DSP/I2S (通常 10+)
    // 栈 12288: Split 最深 6 层递归 + Frame + DrawEye, 留足余量
    eye_running_ = true;
    xTaskCreatePinnedToCore(EyeLoopTask, "eye_loop", 12288, this, 8, &eye_task_, 0);
    ESP_LOGI(TAG, "Bitmap eye display started (%dx%d)", SCREEN_WIDTH, SCREEN_HEIGHT);
}

VibratalkieBitmapEyeDisplay::~VibratalkieBitmapEyeDisplay() {
    eye_running_ = false;
    if (eye_task_) {
        vTaskDelay(pdMS_TO_TICKS(200));
        vTaskDelete(eye_task_);
        eye_task_ = nullptr;
    }
    if (line_buf_[0]) free(line_buf_[0]);
    if (line_buf_[1]) free(line_buf_[1]);
}

// ── 辅助函数 ────────────────────────────────────────
int VibratalkieBitmapEyeDisplay::LinearMap(int x, int in_min, int in_max, int out_min, int out_max) {
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min;
}

int VibratalkieBitmapEyeDisplay::RandomRange(int min, int max) {
    return min + esp_random() % (max - min + 1);
}

int VibratalkieBitmapEyeDisplay::RandomMax(int max) {
    return esp_random() % max;
}

// ── SetEmotion ──────────────────────────────────────
void VibratalkieBitmapEyeDisplay::SetEmotion(const char* emotion) {
    if (!emotion) return;

    for (const auto& e : kEmotionTable) {
        if (strcmp(e.name, emotion) == 0) {
            eye_new_x_ = e.x;
            eye_new_y_ = e.y;
            is_blink_ = e.blink;
            is_track_ = e.track;
            return;
        }
    }
    // 未知表情, 使用默认
    eye_new_x_ = 512;
    eye_new_y_ = 512;
}

// ── SetChatMessage ──────────────────────────────────
// 位图眼睛模式下不使用 LVGL 显示文字, 仅记录状态
void VibratalkieBitmapEyeDisplay::SetChatMessage(const char* role, const char* content) {
    if (!content || strlen(content) == 0) {
        show_message_ = false;
        return;
    }
    show_message_ = true;
}

// ── 像素级眼球渲染 ──────────────────────────────────
// 五层合成: 巩膜 → 整圈虹膜(极坐标展开图) → 上眼皮遮罩 → 下眼皮遮罩
//
// 参数说明:
//   iScale  - 整圈虹膜缩放比例, 值越大可见虹膜越小
//   scleraX - 巩膜纹理的水平偏移 (眼球左右看时巩膜跟着移动)
//   scleraY - 巩膜纹理的垂直偏移 (眼球上下看时巩膜跟着移动)
//   uT      - 上眼皮阈值 (0-255), 值越大上眼皮遮挡面积越大
//   lT      - 下眼皮阈值 (0-255), 值越大下眼皮遮挡面积越大
//
// 位图尺寸关系 (240x240 屏幕):
//   巩膜: 375x375 (比屏幕大135像素, 留出眼球移动空间)
//   虹膜: 150x150 (眼球中央区域, 用于极坐标定位)
//   眼睑: 240x240 (和屏幕一样大, 每个像素是0-255灰度遮罩)
//   极坐标表: 150x150 (虹膜大小, 存储每像素的角度+半径)
//   虹膜贴图: 471x75 (整圈虹膜极坐标纹理, 宽=角度, 高=半径)
void VibratalkieBitmapEyeDisplay::DrawEye(
    uint32_t iScale, uint32_t scleraX, uint32_t scleraY,
    uint32_t uT, uint32_t lT)
{
    uint8_t screenX;
    uint16_t p;       // 当前像素颜色 (RGB565)
    uint16_t scleraPixel;
    uint16_t irisPixel;
    uint16_t polarValue;
    uint32_t rawRadius;
    uint32_t d;       // 虹膜半径缩放后的值
    uint32_t irisFeather;
    int16_t irisX, irisY;  // 当前像素在虹膜坐标系中的位置

    uint32_t scleraXsave = scleraX;
    irisFeather = EYE_IRIS_FEATHER;
    if (irisFeather >= IRIS_MAP_HEIGHT) irisFeather = IRIS_MAP_HEIGHT > 0 ? (IRIS_MAP_HEIGHT - 1) : 0;
    // 虹膜在巩膜中居中, 计算偏移
    irisY = scleraY - (SCLERA_HEIGHT - IRIS_HEIGHT) / 2;

    if (!line_buf_[0] || !line_buf_[1]) return;

    uint8_t bufIdx = 0;  // DMA 双缓冲切换索引

    // 逐批处理 (每批 EYE_LINES_PER_BATCH=10 行, 减少 DMA 调用次数)
    for (uint16_t screenY = 0; screenY < SCREEN_HEIGHT; screenY += EYE_LINES_PER_BATCH) {
        uint16_t* currentBuf = line_buf_[bufIdx];
        bufIdx ^= 1;  // 切换到另一个缓冲区

        uint8_t linesToProcess = ((SCREEN_HEIGHT - screenY) < EYE_LINES_PER_BATCH)
                                 ? (SCREEN_HEIGHT - screenY) : EYE_LINES_PER_BATCH;

        // 逐行像素合成
        for (uint8_t line = 0; line < linesToProcess; line++, scleraY++, irisY++) {
            scleraX = scleraXsave;
            irisX = scleraX - (SCLERA_WIDTH - IRIS_WIDTH) / 2;

            // 逐像素判断: 眼皮 > 虹膜 > 巩膜
            for (screenX = 0; screenX < SCREEN_WIDTH; screenX++, scleraX++, irisX++) {
                // screenIdx: 眼睑遮罩数组索引 (240x240)
                uint32_t screenIdx = (screenY + line) * SCREEN_WIDTH + screenX;
                // pixelIdx: 当前批次缓冲区索引
                uint32_t pixelIdx = line * SCREEN_WIDTH + screenX;

                // 第1层: 眼皮遮罩判断
                // lower_data_[]: 下眼皮灰度遮罩, 值小=眼皮边缘区域
                // upper_data_[]: 上眼皮灰度遮罩, 值小=眼皮边缘区域
                // 当像素灰度值 <= 阈值时, 该像素被眼皮盖住 → 显示黑色
                if ((lower_data_[screenIdx] <= lT) || (upper_data_[screenIdx] <= uT)) {
                    p = 0;  // 黑色 (被眼皮遮挡)

                // 第2层: 不在虹膜区域内 → 直接显示巩膜
                } else if ((irisY < 0) || (irisY >= IRIS_HEIGHT) ||
                           (irisX < 0) || (irisX >= IRIS_WIDTH)) {
                    p = sclera_data_[scleraY * SCLERA_WIDTH + scleraX];

                // 第3层: 在虹膜区域内 → 按比例采样整圈虹膜纹理
                } else {
                    scleraPixel = sclera_data_[scleraY * SCLERA_WIDTH + scleraX];
                    polarValue = polar_data_[irisY * IRIS_WIDTH + irisX];
                    rawRadius = polarValue & 0x7F;
                    d = (iScale * rawRadius) / 240;
                    if (d < IRIS_MAP_HEIGHT) {
                        uint16_t a = (IRIS_MAP_WIDTH * (polarValue >> 7)) / 512;
                        if (a >= IRIS_MAP_WIDTH) a = IRIS_MAP_WIDTH - 1;
                        if (d >= (uint32_t)IRIS_MAP_HEIGHT) d = IRIS_MAP_HEIGHT - 1;
                        irisPixel = iris_data_[d * IRIS_MAP_WIDTH + a];

                        if ((irisFeather > 0) && (d + irisFeather >= (uint32_t)IRIS_MAP_HEIGHT)) {
                            uint32_t distToEdge = IRIS_MAP_HEIGHT - 1 - d;
                            uint8_t alpha = (uint8_t)((distToEdge * 255) / irisFeather);
                            p = BlendRgb565(scleraPixel, irisPixel, alpha);
                        } else {
                            p = irisPixel;
                        }
                    } else {
                        p = scleraPixel;
                    }
                }
                // SPI LCD 需要大端字节序, 交换高低字节
                currentBuf[pixelIdx] = (p >> 8) | (p << 8);
            }
        }

        // 将当前批次写入 LCD (位图眼睛独占SPI, 无需加锁)
        esp_lcd_panel_draw_bitmap(panel_, 0, screenY,
                                  SCREEN_WIDTH, screenY + linesToProcess,
                                  currentBuf);
        // 每 4 批次让出 CPU 一次, 给 Core 0 上的网络/系统任务运行机会
        if ((screenY & 0x28) == 0) taskYIELD();
    }
}

// ── 动画帧 ──────────────────────────────────────────
// 每帧计算: 眼球位置(缓动) + 眨眼状态 + 眼睑跟踪 → 调用 DrawEye 渲染
// iScale: 虹膜缩放值 (由 Split 递归控制, 产生瞳孔微颤效果)
void VibratalkieBitmapEyeDisplay::Frame(uint16_t iScale) {
    int16_t eyeX, eyeY;          // 当前帧的眼球位置 (0-1023 逻辑坐标)
    uint32_t t = esp_timer_get_time();  // 微秒级时间戳

    // ─── 眼球位置缓动 ───
    // 眼球不是瞬间移动到目标位置, 而是用 ease 曲线平滑过渡
    static bool eyeInMotion = false;      // 是否正在移动中
    static int16_t eyeOldX = 512, eyeOldY = 512;  // 移动起点
    static uint32_t eyeMoveStartTime = 0;  // 移动开始时间
    static int32_t eyeMoveDuration = 0;    // 移动持续时间 (微秒)

    int16_t targetX = eye_new_x_.load();  // 目标位置 (由 SetEmotion 设置)
    int16_t targetY = eye_new_y_.load();

    int32_t dt = t - eyeMoveStartTime;

    if (eyeInMotion) {
        if (dt >= eyeMoveDuration) {
            // 移动完成, 进入静止等待期
            eyeInMotion = false;
            eyeMoveDuration = RandomMax(100000);  // 静止 0-100ms 后再移动
            eyeMoveStartTime = t;
            eyeX = eyeOldX = targetX;
            eyeY = eyeOldY = targetY;
        } else {
            // 移动中: 用 ease 曲线插值 (先慢后快再慢)
            int16_t e = kEyeEase[255 * dt / eyeMoveDuration] + 1;
            eyeX = eyeOldX + (((targetX - eyeOldX) * e) / 256);
            eyeY = eyeOldY + (((targetY - eyeOldY) * e) / 256);
        }
    } else {
        eyeX = eyeOldX;
        eyeY = eyeOldY;
        if (dt > eyeMoveDuration) {
            // 静止期结束, 开始新的移动
            int16_t dx, dy;
            uint32_t d;
            do {
                dx = (targetX * 2) - 1023;
                dy = (targetY * 2) - 1023;
            } while ((d = (dx * dx + dy * dy)) > (1023 * 1023));

            eyeMoveDuration = RandomRange(72000, 144000);  // 移动耗时 72-144ms
            eyeMoveStartTime = t;
            eyeInMotion = true;
        }
    }

    // ─── 眨眼控制 ───
    // 三个状态: NOBLINK(睁眼) → ENBLINK(闭眼中) → DEBLINK(睁眼中) → NOBLINK
    if (is_blink_.load()) {
        if ((t - time_of_last_blink_) >= time_to_next_blink_) {
            time_of_last_blink_ = t;
            // 眨眼速度: 36-72ms 闭合, 之后 ×2 = 72-144ms 张开
            uint32_t blinkDuration = RandomRange(36000, 72000);
            if (blink_.state == EYE_NOBLINK) {
                blink_.state = EYE_ENBLINK;
                blink_.startTime = t;
                blink_.duration = blinkDuration;
            }
            // 下次眨眼间隔: 眨眼时长×3 + 随机0-4秒
            time_to_next_blink_ = blinkDuration * 3 + RandomMax(4000000);
        }
    }

    if (blink_.state) {
        if ((t - blink_.startTime) >= (uint32_t)blink_.duration) {
            if (++blink_.state > EYE_DEBLINK) {
                blink_.state = EYE_NOBLINK;  // 眨眼结束
            } else {
                blink_.duration *= 2;  // 张开阶段比闭合慢一倍
                blink_.startTime = t;
            }
        }
    }

    // ─── 逻辑坐标 → 巩膜像素坐标 ───
    // 将 0-1023 映射到巩膜可滚动范围 (375-240=135 像素)
    eyeX = LinearMap(eyeX, 0, 1023, 0, SCLERA_WIDTH  - SCREEN_WIDTH);
    eyeY = LinearMap(eyeY, 0, 1023, 0, SCLERA_HEIGHT - SCREEN_HEIGHT);

    if (eyeX > (SCLERA_WIDTH - SCREEN_WIDTH))
        eyeX = (SCLERA_WIDTH - SCREEN_WIDTH);

    // ─── 眼睑跟踪 ───
    // 上眼皮会跟随眼球移动: 眼球往下看时上眼皮自然下垂
    static uint8_t uThreshold = 0;  // 上眼皮阈值 (带平滑, 持久保存)
    uint8_t lThreshold = 0, n = 0;  // 下眼皮阈值, 临时采样值

    if (is_track_.load()) {
        // 根据眼球位置采样 upper_data_ 确定上眼皮自然下垂程度
        int16_t sampleX = SCLERA_WIDTH / 2 - (eyeX / 3);
        int16_t sampleY = SCLERA_HEIGHT / 2 - (eyeY + IRIS_HEIGHT / 6);
        if (sampleY < 0) {
            n = 0;  // 眼球看很下方时, 上眼皮不额外下垂
        } else {
            // 采样左右两侧取平均, 让上眼皮对称
            n = upper_data_[sampleY * SCREEN_WIDTH + sampleX] +
                upper_data_[sampleY * SCREEN_WIDTH + (SCREEN_WIDTH - 1 - sampleX)] / 2;
        }
        // ★ 上眼皮阈值: 7/8 平滑滤波, 防止抖动
        // n 越大 → uThreshold 越大 → 上眼皮遮挡越多
        uThreshold = (uThreshold * 7 + n) / 8;
        // ★ 下眼皮阈值: 与上眼皮互补 (上眼皮下垂多, 下眼皮就收少)
        lThreshold = 250 - uThreshold;
        uThreshold = uThreshold / 2;
        lThreshold = 0;  // 下眼皮整体更收敛, 不如上眼皮明显
        // ★ 如果想单独缩小上眼皮, 在这里加: uThreshold = uThreshold / 3;
        //   不要在 lThreshold 计算之前改 uThreshold, 否则下眼皮会变大
    } else {
        uThreshold = 0;  // 不跟踪时上下眼皮都完全收起
        lThreshold = 0;
    }

    // ─── 眨眼动画叠加 ───
    // 眨眼时临时增大上下眼皮阈值 → 眼皮合拢
    if (blink_.state) {
        uint32_t s = (t - blink_.startTime);
        if (s >= (uint32_t)blink_.duration) {
            s = 255;  // 完全闭合
        } else {
            s = 255 * s / blink_.duration;  // 0→255 线性进度
        }
        // ENBLINK: 256→1 (闭合), DEBLINK: 1→256 (张开)
        s = (blink_.state == EYE_DEBLINK) ? 1 + s : 256 - s;
        // 将眨眼进度混合到眼皮阈值: s小→阈值大→眼皮闭合
        n = (uThreshold * s + 254 * (257 - s)) / 256;
        lThreshold = (lThreshold * s + 254 * (257 - s)) / 256;
    } else {
        n = uThreshold;  // 不眨眼时直接用跟踪值
    }

    // 最终渲染: n=上眼皮阈值, lThreshold=下眼皮阈值
    DrawEye(iScale, eyeX, eyeY, n, lThreshold);
}

// ── 虹膜缩放递归分裂 ──────────────────────────────────
// 用二分递归制造虹膜大小的随机微颤效果 (模拟瞳孔对光反射)
// 从 startValue 渐变到 endValue, 中间插入随机波动
// range 控制波动幅度, 每次递归减半直到 <8 进入线性插值
void VibratalkieBitmapEyeDisplay::Split(
    int16_t startValue, int16_t endValue,
    uint64_t startTime, int32_t duration, int16_t range)
{
    if (!eye_running_) return;

    if (range >= 8) {
        range /= 2;
        duration /= 2;
        // 在中点附加随机偏移
        int16_t midValue = (startValue + endValue - range) / 2 + (esp_random() % range);
        uint64_t midTime = startTime + duration;
        Split(startValue, midValue, startTime, duration, range);  // 前半段
        Split(midValue, endValue, midTime, duration, range);      // 后半段
    } else {
        // 递归到最小粒度: 线性插值 + 逐帧渲染
        int32_t dt;
        int16_t v;
        while ((dt = (esp_timer_get_time() - startTime)) < duration) {
            if (!eye_running_) return;
            v = startValue + (((endValue - startValue) * dt) / duration);
            if (v < EYE_IRIS_MIN) v = EYE_IRIS_MIN;    // 钳位到 300
            else if (v > EYE_IRIS_MAX) v = EYE_IRIS_MAX;  // 钳位到 700
            Frame(v);           // 渲染一帧
            vTaskDelay(1);      // 让出 CPU, 喂看门狗 (~10ms)
        }
    }
}

// ── 主循环 FreeRTOS 任务 ────────────────────────────
void VibratalkieBitmapEyeDisplay::EyeLoopTask(void* arg) {
    auto* self = static_cast<VibratalkieBitmapEyeDisplay*>(arg);
    self->EyeLoop();
    vTaskDelete(nullptr);
}

// 无限循环: 不断随机生成新的虹膜大小目标, 用 Split 过渡
// 每轮 Split 耗时约 5 秒, 期间持续渲染眼球动画
void VibratalkieBitmapEyeDisplay::EyeLoop() {
    blink_.state = EYE_NOBLINK;

    while (eye_running_) {
        new_iris_ = RandomRange(EYE_IRIS_MIN, EYE_IRIS_MAX);  // 随机目标 300-700
        // 5秒内从当前虹膜大小过渡到新目标, 带随机微颤
        Split(old_iris_, new_iris_, esp_timer_get_time(), 5000000L, EYE_IRIS_MAX - EYE_IRIS_MIN);
        old_iris_ = new_iris_;
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
