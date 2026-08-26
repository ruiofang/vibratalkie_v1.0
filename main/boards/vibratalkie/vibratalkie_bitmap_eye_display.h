#ifndef VIBRATALKIE_BITMAP_EYE_DISPLAY_H
#define VIBRATALKIE_BITMAP_EYE_DISPLAY_H

#include "display/lcd_display.h"
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_timer.h>
#include <esp_random.h>
#include <cstdint>
#include <cstring>
#include <atomic>

// 根据 Kconfig 选择的屏幕尺寸引入对应的眼睛位图数据
#if defined(CONFIG_DISPLAY_USE_GC9D01)
    #include "eye_data/160/common.h"
    #include "eye_data/160/sclera.h"
    #include "eye_data/160/iris.h"
    #include "eye_data/160/eyelid.h"
#else
    #include "eye_data/240/common.h"
    #include "eye_data/240/sclera.h"
    #include "eye_data/240/iris.h"
    #include "eye_data/240/eyelid.h"
#endif

// 眼球动画常量
// EYE_IRIS_MIN/MAX 控制整圈虹膜缩放；值越大，屏幕上可见虹膜越小
#define EYE_IRIS_MIN      600
#define EYE_IRIS_MAX      900
// 虹膜边缘羽化宽度：单位为 IRIS_MAP_HEIGHT 径向采样行数，建议 2-6
#define EYE_IRIS_FEATHER  4
#define EYE_LINES_PER_BATCH 10
#define EYE_NOBLINK 0
#define EYE_ENBLINK 1
#define EYE_DEBLINK 2

// 缓动曲线 3*t^2-2*t^3
static const uint8_t kEyeEase[] = {
    0,  0,  0,  0,  0,  0,  0,  1,  1,  1,  1,  1,  2,  2,  2,  3,
    3,  3,  4,  4,  4,  5,  5,  6,  6,  7,  7,  8,  9,  9, 10, 10,
   11, 12, 12, 13, 14, 15, 15, 16, 17, 18, 18, 19, 20, 21, 22, 23,
   24, 25, 26, 27, 27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38, 39,
   40, 41, 42, 44, 45, 46, 47, 48, 50, 51, 52, 53, 54, 56, 57, 58,
   60, 61, 62, 63, 65, 66, 67, 69, 70, 72, 73, 74, 76, 77, 78, 80,
   81, 83, 84, 85, 87, 88, 90, 91, 93, 94, 96, 97, 98,100,101,103,
  104,106,107,109,110,112,113,115,116,118,119,121,122,124,125,127,
  128,130,131,133,134,136,137,139,140,142,143,145,146,148,149,151,
  152,154,155,157,158,159,161,162,164,165,167,168,170,171,172,174,
  175,177,178,179,181,182,183,185,186,188,189,190,192,193,194,195,
  197,198,199,201,202,203,204,205,207,208,209,210,211,213,214,215,
  216,217,218,219,220,221,222,224,225,226,227,228,228,229,230,231,
  232,233,234,235,236,237,237,238,239,240,240,241,242,243,243,244,
  245,245,246,246,247,248,248,249,249,250,250,251,251,251,252,252,
  252,253,253,253,254,254,254,254,254,255,255,255,255,255,255,255
};

// 表情到眼球位置的映射
struct EmotionPosition {
    const char* name;
    int16_t x;  // 0-1023
    int16_t y;  // 0-1023
    bool blink;
    bool track;
};

// 像素级位图合成写实眼睛显示
// 移植自 RoPet 项目的 5 层位图渲染: 巩膜→虹膜(极坐标查表)→上下眼睑遮罩
// 支持用户通过 PNG→C 头文件工具链自定义眼睛外观
class VibratalkieBitmapEyeDisplay : public SpiLcdDisplay {
public:
    VibratalkieBitmapEyeDisplay(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel,
                         int width, int height, int offset_x, int offset_y,
                         bool mirror_x, bool mirror_y, bool swap_xy);
    ~VibratalkieBitmapEyeDisplay();

    void SetEmotion(const char* emotion) override;
    void SetChatMessage(const char* role, const char* content) override;

    // 位图眼睛模式完全绕过 LVGL, 以下方法重写为空操作
    void SetStatus(const char* status) override {}
    void ShowNotification(const char* notification, int duration_ms = 3000) override {}
    void SetTheme(Theme* theme) override {}
    void UpdateStatusBar(bool update_all = false) override {}

private:
    // 眼球渲染 (直接写 panel_, 绕过 LVGL)
    void DrawEye(uint32_t iScale, uint32_t scleraX, uint32_t scleraY,
                 uint32_t uT, uint32_t lT);
    void Frame(uint16_t iScale);
    void Split(int16_t startValue, int16_t endValue,
               uint64_t startTime, int32_t duration, int16_t range);
    void EyeLoop();
    static void EyeLoopTask(void* arg);

    // 辅助函数
    static int LinearMap(int x, int in_min, int in_max, int out_min, int out_max);
    static int RandomRange(int min, int max);
    static int RandomMax(int max);

    // 眼球状态
    struct BlinkState {
        uint8_t state = EYE_NOBLINK;
        int32_t duration = 0;
        uint32_t startTime = 0;
    };

    BlinkState blink_;
    std::atomic<bool> is_blink_{true};
    std::atomic<bool> is_track_{true};
    std::atomic<int16_t> eye_new_x_{512};
    std::atomic<int16_t> eye_new_y_{512};

    uint16_t old_iris_ = 0;
    uint16_t new_iris_ = 0;
    uint32_t time_of_last_blink_ = 0;
    uint32_t time_to_next_blink_ = 0;

    // 位图数据指针 (允许运行时切换样式)
    const uint16_t* sclera_data_ = sclera_default;
    const uint16_t* iris_data_ = iris_default;
    const uint8_t*  upper_data_ = upper_default;
    const uint8_t*  lower_data_ = lower_default;
    const uint16_t* polar_data_ = polar_default;

    TaskHandle_t eye_task_ = nullptr;
    std::atomic<bool> eye_running_{false};

    // 预分配 DMA 双缓冲 (避免每帧 malloc)
    uint16_t* line_buf_[2] = {nullptr, nullptr};

    // 消息浮层
    std::atomic<bool> show_message_{false};
    char msg_buf_[256] = {};
};

#endif // VIBRATALKIE_BITMAP_EYE_DISPLAY_H
