#include "vibratalkie_eye_display.h"
#include "display/lvgl_display/lvgl_theme.h"
#include <esp_log.h>
#include <esp_lvgl_port.h>
#include <esp_random.h>
#include <cstdlib>
#include <cstring>
#include <algorithm>

#define TAG "VibratalkieEyeDisplay"

LV_FONT_DECLARE(BUILTIN_TEXT_FONT);

// 文字显示持续时间 (毫秒)
#define MESSAGE_DISPLAY_MS 3000

// ── 表情参数表 ──────────────────────────────────────
// static const struct { const char* name; EyeParams p; } kExpressionTable[] = {
//     // 中性 - 猫咪默认圆眼
//     {"neutral",      {1.0f, 1.0f,  1.0f, 1.0f,   0.0f,  0.0f,  0.0f,  0.0f}},
//     {"idle",         {1.0f, 1.0f,  1.0f, 1.0f,   0.0f,  0.0f,  0.0f,  0.0f}},
//     {"relaxed",      {1.0f, 0.85f, 1.0f, 0.85f,  0.0f,  0.0f,  0.0f,  0.0f}},
//     {"microchip_ai", {1.0f, 1.0f,  1.0f, 1.0f,   0.0f,  0.0f,  0.0f,  0.0f}},
//     // 开心 - 猫咪眯眼
//     {"happy",        {1.1f, 0.40f, 1.1f, 0.40f,  0.0f,  0.0f,  0.0f,  0.0f}},
//     {"laughing",     {1.15f,0.25f, 1.15f,0.25f,  0.0f,  0.0f,  0.0f,  0.0f}},
//     {"funny",        {1.05f,0.35f, 1.05f,0.35f,  0.0f,  0.0f,  0.0f,  0.0f}},
//     {"loving",       {1.0f, 0.45f, 1.0f, 0.45f,  0.0f,  0.0f,  0.0f,  0.0f}},
//     {"confident",    {0.95f,0.70f, 0.95f,0.70f,  0.0f,  0.0f,  0.0f,  0.0f}},
//     {"delicious",    {1.0f, 0.30f, 1.0f, 0.30f,  0.0f,  0.0f,  0.0f,  0.0f}},
//     {"kissy",        {0.85f,0.50f, 0.85f,0.50f,  0.0f,  0.0f,  0.0f,  0.0f}},
//     {"cool",         {1.2f, 0.35f, 1.2f, 0.35f,  0.0f,  0.0f,  0.0f,  0.0f}},
//     // 悲伤 - 瞳孔下垂
//     {"sad",          {1.0f, 0.70f, 1.0f, 0.70f,  0.0f,  0.35f, 0.0f,  0.35f}},
//     {"crying",       {1.0f, 0.80f, 1.0f, 0.80f,  0.0f,  0.40f, 0.0f,  0.40f}},
//     // 生气 - 眯缝
//     {"angry",        {1.1f, 0.50f, 1.1f, 0.50f,  0.0f,  0.2f,  0.0f,  0.2f}},
//     // 惊讶 - 猫咪瞪大圆眼
//     {"surprised",    {1.2f, 1.25f, 1.2f, 1.25f,  0.0f,  0.0f,  0.0f,  0.0f}},
//     {"shocked",      {1.3f, 1.35f, 1.3f, 1.35f,  0.0f,  0.0f,  0.0f,  0.0f}},
//     // 思考 - 不对称猫眼
//     {"thinking",     {0.8f, 0.7f,  1.1f, 1.05f,  0.25f,-0.3f,  0.25f,-0.3f}},
//     {"confused",     {0.85f,0.8f,  1.1f, 1.1f,  -0.25f, 0.0f,  0.3f,  0.0f}},
//     {"embarrassed",  {0.9f, 0.60f, 0.9f, 0.60f,  0.3f,  0.0f,  0.3f,  0.0f}},
//     // 困倦 - 几乎闭眼
//     {"sleepy",       {1.0f, 0.15f, 1.0f, 0.15f,  0.0f,  0.0f,  0.0f,  0.0f}},
//     // 眨眼/调皮
//     {"winking",      {1.0f, 0.06f, 1.0f, 1.0f,   0.0f,  0.0f,  0.0f,  0.0f}},
//     {"silly",        {1.1f, 1.1f,  0.85f,0.06f, -0.2f,  0.2f,  0.0f,  0.0f}},
// };

static const struct { const char* name; EyeParams p; } kExpressionTable[] = {
    {"neutral", {1.00f, 1.00f, 1.00f, 1.00f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"idle", {1.00f, 1.00f, 1.00f, 1.00f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"relaxed", {1.00f, 0.85f, 1.00f, 0.85f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"microchip_ai", {1.00f, 1.00f, 1.00f, 1.00f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"happy", {1.10f, 0.40f, 1.10f, 0.40f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"laughing", {1.15f, 0.25f, 1.15f, 0.25f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"funny", {1.05f, 0.35f, 1.05f, 0.35f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"loving", {1.00f, 0.45f, 1.00f, 0.45f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"confident", {0.95f, 0.70f, 0.95f, 0.70f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"delicious", {1.00f, 0.30f, 1.00f, 0.30f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"kissy", {0.85f, 0.50f, 0.85f, 0.50f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"cool", {1.20f, 0.35f, 1.20f, 0.35f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"sad", {1.00f, 0.70f, 1.00f, 0.70f,  0.00f, 0.35f, 0.00f, 0.35f}},
    {"crying", {1.00f, 0.80f, 1.00f, 0.80f,  0.00f, 0.40f, 0.00f, 0.40f}},
    {"angry", {1.10f, 0.50f, 1.10f, 0.50f,  0.00f, 0.20f, 0.00f, 0.20f}},
    {"surprised", {1.20f, 1.25f, 1.20f, 1.25f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"shocked", {1.30f, 1.35f, 1.30f, 1.35f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"thinking", {0.80f, 0.70f, 1.10f, 1.05f,  0.25f, -0.30f, 0.25f, -0.30f}},
    {"confused", {0.85f, 0.80f, 1.10f, 1.10f,  -0.25f, 0.00f, 0.30f, 0.00f}},
    {"embarrassed", {0.90f, 0.60f, 0.90f, 0.60f,  0.30f, 0.00f, 0.30f, 0.00f}},
    {"sleepy", {1.00f, 0.15f, 1.00f, 0.15f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"winking", {1.00f, 0.06f, 1.00f, 1.00f,  0.00f, 0.00f, 0.00f, 0.00f}},
    {"silly", {1.10f, 1.10f, 0.85f, 0.06f,  -0.20f, 0.20f, 0.00f, 0.00f}},
};

static EyeParams GetEyeParams(const char* emotion) {
    if (emotion) {
        for (const auto& e : kExpressionTable) {
            if (strcmp(e.name, emotion) == 0) return e.p;
        }
    }
    return {1.0f, 1.0f, 1.0f, 1.0f, 0.0f, 0.0f, 0.0f, 0.0f};
}

// ── 构造/析构 ───────────────────────────────────────

VibratalkieEyeDisplay::VibratalkieEyeDisplay(
    esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel,
    int width, int height, int offset_x, int offset_y,
    bool mirror_x, bool mirror_y, bool swap_xy,
    bool single_eye)
    : SpiLcdDisplay(panel_io, panel, width, height, offset_x, offset_y, mirror_x, mirror_y, swap_xy),
      single_eye_(single_eye)
{
    int ds = std::min(width_, height_);

    if (single_eye_) {
        // 单眼模式: 一只大眼睛占满整个屏幕
        eye_w_   = ds * 75 / 100;   // 180px @ 240
        eye_h_   = ds * 78 / 100;   // 187px @ 240
        pupil_w_ = ds * 22 / 100;   // 53px @ 240
        pupil_h_ = ds * 45 / 100;   // 108px @ 240
        eye_gap_ = 0;
    } else {
        // 双眼模式: 两只眼睛并排
        eye_w_   = ds * 38 / 100;
        eye_h_   = ds * 40 / 100;
        pupil_w_ = ds * 14 / 100;
        pupil_h_ = ds * 28 / 100;
        eye_gap_ = ds * 10 / 100;
    }

    left_target_h_  = eye_h_;
    right_target_h_ = eye_h_;

    CreateFace();
    ESP_LOGI(TAG, "猫咪表情初始化完成 (display %dx%d, eye %dx%d, pupil %dx%d, %s)",
             width_, height_, eye_w_, eye_h_, pupil_w_, pupil_h_,
             single_eye_ ? "单眼模式" : "双眼模式");
}

VibratalkieEyeDisplay::~VibratalkieEyeDisplay() {
    if (blink_timer_) { lv_timer_delete(blink_timer_); blink_timer_ = nullptr; }
    if (pupil_drift_timer_) { lv_timer_delete(pupil_drift_timer_); pupil_drift_timer_ = nullptr; }
    if (message_timeout_timer_) { lv_timer_delete(message_timeout_timer_); message_timeout_timer_ = nullptr; }
}

// ── 创建猫咪眼睛 ───────────────────────────────────

void VibratalkieEyeDisplay::CreateFace() {
    DisplayLockGuard lock(this);
    auto screen = lv_screen_active();

    // ━━ 1. 完全隐藏 SetupUI() 创建的所有UI层 ━━
    // container_ 包含 status_bar_/content_ 等, 直接整个隐藏
    if (container_) lv_obj_add_flag(container_, LV_OBJ_FLAG_HIDDEN);

    // ━━ 2. 屏幕纯黑背景 ━━
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);

    // ━━ 3. 眼睛容器 (透明, 直接挂在 screen 上) ━━
    int cw, ch;
    if (single_eye_) {
        cw = eye_w_ + 4;   // 单眼: 容器刚好包裹一只眼
        ch = eye_h_ + 4;
    } else {
        cw = eye_w_ * 2 + eye_gap_;  // 双眼: 容器包裹两只眼
        ch = eye_h_ * 3 / 2;
    }
    eye_container_ = lv_obj_create(screen);
    lv_obj_remove_style_all(eye_container_);   // ◆ 清除所有主题样式
    lv_obj_set_size(eye_container_, cw, ch);
    lv_obj_center(eye_container_);

    // 颜色定义: 黑底 + 深灰眼球 + 灰色轮廓
    lv_color_t eye_color = lv_color_hex(0xF0F0F0);      // 眼球
    lv_color_t outline_color = lv_color_hex(0x3A3A3A);   // 轮廓
    lv_color_t pupil_color = lv_color_hex(0x111111);     // 近黑瞳孔
    lv_color_t highlight_color = lv_color_hex(0xFAFAFA); // 柔和高光

    // ━━ 4. 左眼 (单眼模式下为唯一的眼睛, 居中显示) ━━
    left_eye_ = lv_obj_create(eye_container_);
    lv_obj_remove_style_all(left_eye_);
    lv_obj_set_size(left_eye_, eye_w_, eye_h_);
    lv_obj_set_style_bg_color(left_eye_, eye_color, 0);
    lv_obj_set_style_bg_opa(left_eye_, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(left_eye_, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_color(left_eye_, outline_color, 0);
    lv_obj_set_style_border_width(left_eye_, 2, 0);
    lv_obj_set_style_border_opa(left_eye_, LV_OPA_COVER, 0);
    if (single_eye_) {
        lv_obj_center(left_eye_);  // 单眼居中
    } else {
        lv_obj_align(left_eye_, LV_ALIGN_LEFT_MID, 0, 0);
    }

    // 左竖瞳 (近黑细长椭圆)
    left_pupil_ = lv_obj_create(left_eye_);
    lv_obj_remove_style_all(left_pupil_);
    lv_obj_set_size(left_pupil_, pupil_w_, pupil_h_);
    lv_obj_set_style_bg_color(left_pupil_, pupil_color, 0);
    lv_obj_set_style_bg_opa(left_pupil_, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(left_pupil_, LV_RADIUS_CIRCLE, 0);
    lv_obj_center(left_pupil_);

    // 左眼高光 (挂在眼球上, 按瞳孔30%比例跟随, 不超出眼球)
    hl_size_ = std::max(pupil_w_ / 2, 4);
    left_highlight_ = lv_obj_create(left_eye_);
    lv_obj_remove_style_all(left_highlight_);
    lv_obj_set_size(left_highlight_, hl_size_, hl_size_);
    lv_obj_set_style_bg_color(left_highlight_, highlight_color, 0);
    lv_obj_set_style_bg_opa(left_highlight_, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(left_highlight_, LV_RADIUS_CIRCLE, 0);
    lv_obj_align(left_highlight_, LV_ALIGN_CENTER, pupil_w_ / 3, -pupil_h_ / 4);

    // ━━ 5. 右眼 (仅双眼模式创建) ━━
    if (!single_eye_) {
        right_eye_ = lv_obj_create(eye_container_);
        lv_obj_remove_style_all(right_eye_);
        lv_obj_set_size(right_eye_, eye_w_, eye_h_);
        lv_obj_set_style_bg_color(right_eye_, eye_color, 0);
        lv_obj_set_style_bg_opa(right_eye_, LV_OPA_COVER, 0);
        lv_obj_set_style_radius(right_eye_, LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_border_color(right_eye_, outline_color, 0);
        lv_obj_set_style_border_width(right_eye_, 2, 0);
        lv_obj_set_style_border_opa(right_eye_, LV_OPA_COVER, 0);
        lv_obj_align(right_eye_, LV_ALIGN_RIGHT_MID, 0, 0);

        // 右竖瞳
        right_pupil_ = lv_obj_create(right_eye_);
        lv_obj_remove_style_all(right_pupil_);
        lv_obj_set_size(right_pupil_, pupil_w_, pupil_h_);
        lv_obj_set_style_bg_color(right_pupil_, pupil_color, 0);
        lv_obj_set_style_bg_opa(right_pupil_, LV_OPA_COVER, 0);
        lv_obj_set_style_radius(right_pupil_, LV_RADIUS_CIRCLE, 0);
        lv_obj_center(right_pupil_);

        // 右眼高光
        right_highlight_ = lv_obj_create(right_eye_);
        lv_obj_remove_style_all(right_highlight_);
        lv_obj_set_size(right_highlight_, hl_size_, hl_size_);
        lv_obj_set_style_bg_color(right_highlight_, highlight_color, 0);
        lv_obj_set_style_bg_opa(right_highlight_, LV_OPA_COVER, 0);
        lv_obj_set_style_radius(right_highlight_, LV_RADIUS_CIRCLE, 0);
        lv_obj_align(right_highlight_, LV_ALIGN_CENTER, pupil_w_ / 3, -pupil_h_ / 4);
    }

    // ━━ 6. 半透明浮层文字 (叠在眼睛上, 屏幕中央显示) ━━
    // 半透明背景容器 - 放在屏幕中央(圆屏最宽处)
    overlay_box_ = lv_obj_create(screen);
    lv_obj_remove_style_all(overlay_box_);
    lv_obj_set_size(overlay_box_, LV_HOR_RES - 20, LV_SIZE_CONTENT);
    lv_obj_set_style_max_height(overlay_box_, LV_VER_RES * 2 / 3, 0);
    lv_obj_set_style_bg_color(overlay_box_, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(overlay_box_, LV_OPA_50, 0);
    lv_obj_set_style_radius(overlay_box_, 10, 0);
    lv_obj_set_style_pad_hor(overlay_box_, 8, 0);
    lv_obj_set_style_pad_ver(overlay_box_, 6, 0);
    lv_obj_set_style_clip_corner(overlay_box_, true, 0);
    lv_obj_set_scrollbar_mode(overlay_box_, LV_SCROLLBAR_MODE_OFF);
    lv_obj_remove_flag(overlay_box_, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_center(overlay_box_);  // 屏幕正中央
    lv_obj_add_flag(overlay_box_, LV_OBJ_FLAG_HIDDEN);

    // 文字标签 - 多行自动换行
    overlay_label_ = lv_label_create(overlay_box_);
    lv_obj_remove_style_all(overlay_label_);
    lv_obj_set_width(overlay_label_, LV_HOR_RES - 36);
    lv_label_set_long_mode(overlay_label_, LV_LABEL_LONG_WRAP);  // 自动换行
    lv_obj_set_style_text_color(overlay_label_, lv_color_white(), 0);
    lv_obj_set_style_text_opa(overlay_label_, LV_OPA_90, 0);
    lv_obj_set_style_text_align(overlay_label_, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_font(overlay_label_, &BUILTIN_TEXT_FONT, 0);
    lv_obj_center(overlay_label_);
    lv_label_set_text(overlay_label_, "");

    // ━━ 7. 定时器 ━━
    blink_timer_ = lv_timer_create(BlinkTimerCb, 2000 + (esp_random() % 3000), this);
    pupil_drift_timer_ = lv_timer_create(PupilDriftCb, 1500 + (esp_random() % 1500), this);
}

// ── 表情应用 ────────────────────────────────────────

void VibratalkieEyeDisplay::ApplyExpression(const EyeParams& p) {
    cur_params_ = p;

    int lw = (int)(eye_w_ * p.lw);
    int lh = (int)(eye_h_ * p.lh);
    if (lh < 3) lh = 3;
    left_target_h_  = lh;
    lv_obj_set_size(left_eye_, lw, lh);

    // 瞳孔偏移 = 表情偏移 + 漂移偏移
    int lpdx = (int)(p.lpdx * (lw - pupil_w_) / 2) + pupil_drift_lx_;
    int lpdy = (int)(p.lpdy * (lh - pupil_h_) / 2) + pupil_drift_ly_;
    lv_obj_align(left_pupil_,  LV_ALIGN_CENTER, lpdx, lpdy);

    int rw = lw, rh = lh, rpdx = lpdx, rpdy = lpdy;  // 单眼模式下用左眼参数
    if (right_eye_) {
        rw = (int)(eye_w_ * p.rw);
        rh = (int)(eye_h_ * p.rh);
        if (rh < 3) rh = 3;
        right_target_h_ = rh;
        lv_obj_set_size(right_eye_, rw, rh);

        rpdx = (int)(p.rpdx * (rw - pupil_w_) / 2) + pupil_drift_rx_;
        rpdy = (int)(p.rpdy * (rh - pupil_h_) / 2) + pupil_drift_ry_;
        lv_obj_align(right_pupil_, LV_ALIGN_CENTER, rpdx, rpdy);
    }

    // 高光跟随瞳孔
    UpdateHighlights(lpdx, lpdy, lw, lh, rpdx, rpdy, rw, rh);
}

void VibratalkieEyeDisplay::UpdateHighlights(int lpdx, int lpdy, int lw, int lh,
                                       int rpdx, int rpdy, int rw, int rh) {
    // 基础偏置: 右上方
    int base_dx = pupil_w_ / 3;
    int base_dy = -pupil_h_ / 4;
    // 跟随比例 30%
    int hl_lx = base_dx + lpdx * 3 / 10;
    int hl_ly = base_dy + lpdy * 3 / 10;
    int hl_rx = base_dx + rpdx * 3 / 10;
    int hl_ry = base_dy + rpdy * 3 / 10;
    // 限制在眼球内 (半径 - 高光半径 - 边框)
    int max_lx = (lw - hl_size_) / 2 - 2;
    int max_ly = (lh - hl_size_) / 2 - 2;
    int max_rx = (rw - hl_size_) / 2 - 2;
    int max_ry = (rh - hl_size_) / 2 - 2;
    if (max_lx < 0) max_lx = 0;
    if (max_ly < 0) max_ly = 0;
    if (max_rx < 0) max_rx = 0;
    if (max_ry < 0) max_ry = 0;
    hl_lx = std::max(-max_lx, std::min(max_lx, hl_lx));
    hl_ly = std::max(-max_ly, std::min(max_ly, hl_ly));
    hl_rx = std::max(-max_rx, std::min(max_rx, hl_rx));
    hl_ry = std::max(-max_ry, std::min(max_ry, hl_ry));

    if (left_highlight_)  lv_obj_align(left_highlight_,  LV_ALIGN_CENTER, hl_lx, hl_ly);
    if (right_highlight_) lv_obj_align(right_highlight_, LV_ALIGN_CENTER, hl_rx, hl_ry);
}

// ── SetTheme: 当 assets 加载 common 字体后更新 overlay 字体 ──

void VibratalkieEyeDisplay::SetTheme(Theme* theme) {
    // 先调用父类, 更新状态栏等标准元素
    SpiLcdDisplay::SetTheme(theme);

    DisplayLockGuard lock(this);
    if (overlay_label_ && theme) {
        auto lvgl_theme = static_cast<LvglTheme*>(theme);
        auto text_font = lvgl_theme->text_font()->font();
        if (text_font) {
            lv_obj_set_style_text_font(overlay_label_, text_font, 0);
            ESP_LOGI(TAG, "Overlay font updated from theme (line_height=%d)", text_font->line_height);
        }
    }
}

// ── SetEmotion ──────────────────────────────────────

void VibratalkieEyeDisplay::SetEmotion(const char* emotion) {
    if (!emotion) return;
    DisplayLockGuard lock(this);

    auto params = GetEyeParams(emotion);
    ApplyExpression(params);
    ShowFace();
}

// ── SetChatMessage: 临时显示文字，自动恢复表情 ─────

void VibratalkieEyeDisplay::SetChatMessage(const char* role, const char* content) {
    DisplayLockGuard lock(this);
    if (!overlay_label_ || !overlay_box_) return;

    // 空内容则隐藏
    if (!content || strlen(content) == 0) {
        lv_obj_add_flag(overlay_box_, LV_OBJ_FLAG_HIDDEN);
        return;
    }

    // 直接显示完整文字, 由LVGL自动换行+容器max_height裁剪
    lv_label_set_text(overlay_label_, content);
    lv_obj_update_layout(overlay_box_);

    // 显示浮层
    lv_obj_remove_flag(overlay_box_, LV_OBJ_FLAG_HIDDEN);

    // 启动/重置自动消失定时器 (每次新消息重新计时)
    if (message_timeout_timer_) {
        lv_timer_reset(message_timeout_timer_);
    } else {
        message_timeout_timer_ = lv_timer_create(MessageTimeoutCb, MESSAGE_DISPLAY_MS, this);
        lv_timer_set_repeat_count(message_timeout_timer_, 1);
    }
}

void VibratalkieEyeDisplay::MessageTimeoutCb(lv_timer_t* timer) {
    auto* self = static_cast<VibratalkieEyeDisplay*>(lv_timer_get_user_data(timer));
    self->HideMessageShowFace();
    self->message_timeout_timer_ = nullptr;
}

void VibratalkieEyeDisplay::HideMessageShowFace() {
    if (overlay_box_) lv_obj_add_flag(overlay_box_, LV_OBJ_FLAG_HIDDEN);
}

void VibratalkieEyeDisplay::ShowFace() {
    if (eye_container_) lv_obj_remove_flag(eye_container_, LV_OBJ_FLAG_HIDDEN);
    if (emoji_box_)     lv_obj_add_flag(emoji_box_, LV_OBJ_FLAG_HIDDEN);
    if (emoji_label_)   lv_obj_add_flag(emoji_label_, LV_OBJ_FLAG_HIDDEN);
    if (emoji_image_)   lv_obj_add_flag(emoji_image_, LV_OBJ_FLAG_HIDDEN);
}

// ── 瞳孔漂移 (猫咪四处张望) ────────────────────────

void VibratalkieEyeDisplay::PupilDriftCb(lv_timer_t* timer) {
    auto* self = static_cast<VibratalkieEyeDisplay*>(lv_timer_get_user_data(timer));

    int range = self->pupil_w_;  // 漂移范围与瞳孔宽度相当
    // 两眼联动, 偶尔有微小差异
    int dx = (int)(esp_random() % (range * 2 + 1)) - range;
    int dy = (int)(esp_random() % (range + 1)) - range / 2;
    int diff = (int)(esp_random() % 3) - 1;  // -1, 0, +1

    self->pupil_drift_lx_ = dx;
    self->pupil_drift_ly_ = dy;
    self->pupil_drift_rx_ = dx + diff;
    self->pupil_drift_ry_ = dy;

    // 创建瞳孔平滑移动动画 (左瞳)
    lv_anim_t a_lx;
    lv_anim_init(&a_lx);
    lv_anim_set_var(&a_lx, self->left_pupil_);
    int cur_lx = lv_obj_get_x_aligned(self->left_pupil_);
    int target_lx = (int)(self->cur_params_.lpdx * (lv_obj_get_width(self->left_eye_) - self->pupil_w_) / 2) + self->pupil_drift_lx_;
    lv_anim_set_values(&a_lx, cur_lx, target_lx);
    lv_anim_set_duration(&a_lx, 400 + (esp_random() % 300));
    lv_anim_set_exec_cb(&a_lx, [](void* obj, int32_t v) {
        lv_obj_align(static_cast<lv_obj_t*>(obj), LV_ALIGN_CENTER, v, lv_obj_get_y_aligned(static_cast<lv_obj_t*>(obj)));
    });
    lv_anim_set_path_cb(&a_lx, lv_anim_path_ease_in_out);
    lv_anim_start(&a_lx);

    // 右瞳 (仅双眼模式)
    if (self->right_pupil_) {
        lv_anim_t a_rx;
        lv_anim_init(&a_rx);
        lv_anim_set_var(&a_rx, self->right_pupil_);
        int cur_rx = lv_obj_get_x_aligned(self->right_pupil_);
        int target_rx = (int)(self->cur_params_.rpdx * (lv_obj_get_width(self->right_eye_) - self->pupil_w_) / 2) + self->pupil_drift_rx_;
        lv_anim_set_values(&a_rx, cur_rx, target_rx);
        lv_anim_set_duration(&a_rx, 400 + (esp_random() % 300));
        lv_anim_set_exec_cb(&a_rx, [](void* obj, int32_t v) {
            lv_obj_align(static_cast<lv_obj_t*>(obj), LV_ALIGN_CENTER, v, lv_obj_get_y_aligned(static_cast<lv_obj_t*>(obj)));
        });
        lv_anim_set_path_cb(&a_rx, lv_anim_path_ease_in_out);
        lv_anim_start(&a_rx);
    }

    // 下次漂移间隔 1.5~4秒
    lv_timer_set_period(timer, 1500 + (esp_random() % 2500));
}

// ── 眨眼动画 (猫咪风格: 慢眨眼+快眨眼交替) ────────

void VibratalkieEyeDisplay::BlinkTimerCb(lv_timer_t* timer) {
    auto* self = static_cast<VibratalkieEyeDisplay*>(lv_timer_get_user_data(timer));
    if (self->is_blinking_) return;

    self->blink_count_++;
    // 每3~5次普通眨眼后做一次猫咪慢眨眼 (表示信任/放松)
    if (self->blink_count_ >= 3 + (int)(esp_random() % 3)) {
        self->DoSlowBlink();
        self->blink_count_ = 0;
    } else {
        self->DoBlink();
    }
    self->ScheduleNextBlink();
}

void VibratalkieEyeDisplay::ScheduleNextBlink() {
    if (blink_timer_) {
        lv_timer_set_period(blink_timer_, 2000 + (esp_random() % 3000));
    }
}

// ── 普通快速眨眼 ───────────────────────────────────

void VibratalkieEyeDisplay::DoBlink() {
    is_blinking_ = true;

    // 左眼闭合 (100ms)
    lv_anim_t close_l;
    lv_anim_init(&close_l);
    lv_anim_set_var(&close_l, left_eye_);
    lv_anim_set_values(&close_l, left_target_h_, 3);
    lv_anim_set_duration(&close_l, 100);
    lv_anim_set_exec_cb(&close_l, [](void* obj, int32_t v) {
        lv_obj_set_height(static_cast<lv_obj_t*>(obj), v);
    });
    lv_anim_set_path_cb(&close_l, lv_anim_path_ease_in);

    if (right_eye_) {
        // 双眼模式: 左眼不带回调, 右眼完成后触发睁眼
        lv_anim_start(&close_l);

        lv_anim_t close_r;
        lv_anim_init(&close_r);
        lv_anim_set_var(&close_r, right_eye_);
        lv_anim_set_values(&close_r, right_target_h_, 3);
        lv_anim_set_duration(&close_r, 100);
        lv_anim_set_exec_cb(&close_r, [](void* obj, int32_t v) {
            lv_obj_set_height(static_cast<lv_obj_t*>(obj), v);
        });
        lv_anim_set_path_cb(&close_r, lv_anim_path_ease_in);
        lv_anim_set_completed_cb(&close_r, OnBlinkCloseDone);
        lv_anim_set_user_data(&close_r, this);
        lv_anim_start(&close_r);
    } else {
        // 单眼模式: 左眼自己带回调
        lv_anim_set_completed_cb(&close_l, OnBlinkCloseDone);
        lv_anim_set_user_data(&close_l, this);
        lv_anim_start(&close_l);
    }
}

void VibratalkieEyeDisplay::OnBlinkCloseDone(lv_anim_t* anim) {
    auto* self = static_cast<VibratalkieEyeDisplay*>(lv_anim_get_user_data(anim));
    if (!self) return;

    // 左眼睁开 (150ms)
    lv_anim_t open_l;
    lv_anim_init(&open_l);
    lv_anim_set_var(&open_l, self->left_eye_);
    lv_anim_set_values(&open_l, 3, self->left_target_h_);
    lv_anim_set_duration(&open_l, 150);
    lv_anim_set_exec_cb(&open_l, [](void* obj, int32_t v) {
        lv_obj_set_height(static_cast<lv_obj_t*>(obj), v);
    });
    lv_anim_set_path_cb(&open_l, lv_anim_path_ease_out);

    if (self->right_eye_) {
        // 双眼模式: 右眼带完成回调
        lv_anim_start(&open_l);

        lv_anim_t open_r;
        lv_anim_init(&open_r);
        lv_anim_set_var(&open_r, self->right_eye_);
        lv_anim_set_values(&open_r, 3, self->right_target_h_);
        lv_anim_set_duration(&open_r, 150);
        lv_anim_set_exec_cb(&open_r, [](void* obj, int32_t v) {
            lv_obj_set_height(static_cast<lv_obj_t*>(obj), v);
        });
        lv_anim_set_path_cb(&open_r, lv_anim_path_ease_out);
        lv_anim_set_completed_cb(&open_r, [](lv_anim_t* a) {
            auto* s = static_cast<VibratalkieEyeDisplay*>(lv_anim_get_user_data(a));
            if (s) s->is_blinking_ = false;
        });
        lv_anim_set_user_data(&open_r, self);
        lv_anim_start(&open_r);
    } else {
        // 单眼模式: 左眼自己带完成回调
        lv_anim_set_completed_cb(&open_l, [](lv_anim_t* a) {
            auto* s = static_cast<VibratalkieEyeDisplay*>(lv_anim_get_user_data(a));
            if (s) s->is_blinking_ = false;
        });
        lv_anim_set_user_data(&open_l, self);
        lv_anim_start(&open_l);
    }
}

// ── 猫咪慢眨眼 (信任眨眼, 缓慢闭合再缓慢睁开) ────

void VibratalkieEyeDisplay::DoSlowBlink() {
    is_blinking_ = true;

    // 左眼缓慢闭合 (300ms)
    lv_anim_t close_l;
    lv_anim_init(&close_l);
    lv_anim_set_var(&close_l, left_eye_);
    lv_anim_set_values(&close_l, left_target_h_, 3);
    lv_anim_set_duration(&close_l, 300);
    lv_anim_set_exec_cb(&close_l, [](void* obj, int32_t v) {
        lv_obj_set_height(static_cast<lv_obj_t*>(obj), v);
    });
    lv_anim_set_path_cb(&close_l, lv_anim_path_ease_in_out);

    if (right_eye_) {
        lv_anim_start(&close_l);

        lv_anim_t close_r;
        lv_anim_init(&close_r);
        lv_anim_set_var(&close_r, right_eye_);
        lv_anim_set_values(&close_r, right_target_h_, 3);
        lv_anim_set_duration(&close_r, 300);
        lv_anim_set_exec_cb(&close_r, [](void* obj, int32_t v) {
            lv_obj_set_height(static_cast<lv_obj_t*>(obj), v);
        });
        lv_anim_set_path_cb(&close_r, lv_anim_path_ease_in_out);
        lv_anim_set_completed_cb(&close_r, OnSlowBlinkCloseDone);
        lv_anim_set_user_data(&close_r, this);
        lv_anim_start(&close_r);
    } else {
        lv_anim_set_completed_cb(&close_l, OnSlowBlinkCloseDone);
        lv_anim_set_user_data(&close_l, this);
        lv_anim_start(&close_l);
    }
}

void VibratalkieEyeDisplay::OnSlowBlinkCloseDone(lv_anim_t* anim) {
    auto* self = static_cast<VibratalkieEyeDisplay*>(lv_anim_get_user_data(anim));
    if (!self) return;

    // 左眼缓慢睁开 (400ms)
    lv_anim_t open_l;
    lv_anim_init(&open_l);
    lv_anim_set_var(&open_l, self->left_eye_);
    lv_anim_set_values(&open_l, 3, self->left_target_h_);
    lv_anim_set_duration(&open_l, 400);
    lv_anim_set_exec_cb(&open_l, [](void* obj, int32_t v) {
        lv_obj_set_height(static_cast<lv_obj_t*>(obj), v);
    });
    lv_anim_set_path_cb(&open_l, lv_anim_path_ease_in_out);

    if (self->right_eye_) {
        lv_anim_start(&open_l);

        lv_anim_t open_r;
        lv_anim_init(&open_r);
        lv_anim_set_var(&open_r, self->right_eye_);
        lv_anim_set_values(&open_r, 3, self->right_target_h_);
        lv_anim_set_duration(&open_r, 400);
        lv_anim_set_exec_cb(&open_r, [](void* obj, int32_t v) {
            lv_obj_set_height(static_cast<lv_obj_t*>(obj), v);
        });
        lv_anim_set_path_cb(&open_r, lv_anim_path_ease_in_out);
        lv_anim_set_completed_cb(&open_r, [](lv_anim_t* a) {
            auto* s = static_cast<VibratalkieEyeDisplay*>(lv_anim_get_user_data(a));
            if (s) s->is_blinking_ = false;
        });
        lv_anim_set_user_data(&open_r, self);
        lv_anim_start(&open_r);
    } else {
        lv_anim_set_completed_cb(&open_l, [](lv_anim_t* a) {
            auto* s = static_cast<VibratalkieEyeDisplay*>(lv_anim_get_user_data(a));
            if (s) s->is_blinking_ = false;
        });
        lv_anim_set_user_data(&open_l, self);
        lv_anim_start(&open_l);
    }
}
