#ifndef VIBRATALKIE_EYE_DISPLAY_H
#define VIBRATALKIE_EYE_DISPLAY_H

#include "display/lcd_display.h"
#include <lvgl.h>
#include <string>

// 眼睛表情参数
struct EyeParams {
    float lw, lh, rw, rh;          // 左右眼宽高缩放 (1.0=标准)
    float lpdx, lpdy, rpdx, rpdy;  // 左右瞳孔偏移 (-1.0~1.0)
};

// 猫咪风格动态卡通眼睛显示
// 支持慢眨眼、瞳孔漂移、文字临时显示后自动恢复表情
// 支持单眼模式: 两块屏幕烧同一固件, 各显示一只大眼睛
class VibratalkieEyeDisplay : public SpiLcdDisplay {
public:
    VibratalkieEyeDisplay(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel,
                   int width, int height, int offset_x, int offset_y,
                   bool mirror_x, bool mirror_y, bool swap_xy,
                   bool single_eye = false);
    ~VibratalkieEyeDisplay();

    void SetEmotion(const char* emotion) override;
    void SetChatMessage(const char* role, const char* content) override;
    void SetTheme(Theme* theme) override;

private:
    void CreateFace();
    void ApplyExpression(const EyeParams& p);
    void DoBlink();
    void DoSlowBlink();
    void ScheduleNextBlink();
    void StartPupilDrift();
    void ShowFace();
    void HideMessageShowFace();
    void UpdateHighlights(int lpdx, int lpdy, int lw, int lh,
                          int rpdx, int rpdy, int rw, int rh);

    static void BlinkTimerCb(lv_timer_t* timer);
    static void OnBlinkCloseDone(lv_anim_t* anim);
    static void OnSlowBlinkCloseDone(lv_anim_t* anim);
    static void PupilDriftCb(lv_timer_t* timer);
    static void MessageTimeoutCb(lv_timer_t* timer);

    // LVGL对象
    lv_obj_t* eye_container_ = nullptr;
    lv_obj_t* left_eye_ = nullptr;
    lv_obj_t* right_eye_ = nullptr;
    lv_obj_t* left_pupil_ = nullptr;
    lv_obj_t* right_pupil_ = nullptr;
    lv_obj_t* left_highlight_ = nullptr;
    lv_obj_t* right_highlight_ = nullptr;
    lv_obj_t* overlay_label_ = nullptr;  // 半透明浮层文字
    lv_obj_t* overlay_box_ = nullptr;    // 浮层背景框
    int hl_size_ = 4;  // 高光点尺寸

    lv_timer_t* blink_timer_ = nullptr;
    lv_timer_t* pupil_drift_timer_ = nullptr;
    lv_timer_t* message_timeout_timer_ = nullptr;

    // 基础尺寸
    int eye_w_;
    int eye_h_;
    int pupil_w_;      // 瞳孔宽度 (竖瞳: 窄)
    int pupil_h_;      // 瞳孔高度 (竖瞳: 高)
    int eye_gap_;

    // 当前表情的目标高度
    int left_target_h_;
    int right_target_h_;

    // 瞳孔漂移目标偏移
    int pupil_drift_lx_ = 0;
    int pupil_drift_ly_ = 0;
    int pupil_drift_rx_ = 0;
    int pupil_drift_ry_ = 0;

    // 当前表情参数 (瞳孔偏移)
    EyeParams cur_params_ = {1.0f, 1.0f, 1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f};

    bool is_blinking_ = false;
    int blink_count_ = 0;  // 统计普通眨眼次数, 用于穿插慢眨眼

    bool single_eye_ = false;  // 单眼模式 (双屏并联)
};

#endif // VIBRATALKIE_EYE_DISPLAY_H
