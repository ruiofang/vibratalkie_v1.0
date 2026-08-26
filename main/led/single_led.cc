#include "single_led.h"
#include "application.h"
#include <esp_log.h> 

#define TAG "SingleLed"

#define DEFAULT_BRIGHTNESS 4
#define HIGH_BRIGHTNESS 16
#define LOW_BRIGHTNESS 2

#define BLINK_INFINITE -1


SingleLed::SingleLed(gpio_num_t gpio) {
    // If the gpio is not connected, you should use NoLed class
    assert(gpio != GPIO_NUM_NC);

    led_strip_config_t strip_config = {};
    strip_config.strip_gpio_num = gpio;
    strip_config.max_leds = 1;
    strip_config.color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB;
    strip_config.led_model = LED_MODEL_WS2812;

    led_strip_rmt_config_t rmt_config = {};
    rmt_config.resolution_hz = 10 * 1000 * 1000; // 10MHz

    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &rmt_config, &led_strip_));
    led_strip_clear(led_strip_);

    esp_timer_create_args_t blink_timer_args = {
        .callback = [](void *arg) {
            auto led = static_cast<SingleLed*>(arg);
            led->OnBlinkTimer();
        },
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "blink_timer",
        .skip_unhandled_events = false,
    };
    ESP_ERROR_CHECK(esp_timer_create(&blink_timer_args, &blink_timer_));
}

SingleLed::~SingleLed() {
    esp_timer_stop(blink_timer_);
    if (led_strip_ != nullptr) {
        led_strip_del(led_strip_);
    }
}


void SingleLed::SetColor(uint8_t r, uint8_t g, uint8_t b) {
    r_ = r;
    g_ = g;
    b_ = b;
}

void SingleLed::TurnOn() {
    if (led_strip_ == nullptr) {
        return;
    }
    
    std::lock_guard<std::mutex> lock(mutex_);
    esp_timer_stop(blink_timer_);
    led_strip_set_pixel(led_strip_, 0, r_, g_, b_);
    led_strip_refresh(led_strip_);
}

void SingleLed::TurnOff() {
    if (led_strip_ == nullptr) {
        return;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    esp_timer_stop(blink_timer_);
    led_strip_clear(led_strip_);
}

void SingleLed::Shutdown() {
    if (led_strip_ == nullptr) {
        return;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    esp_timer_stop(blink_timer_);
    is_breathing_ = false;
    blink_counter_ = 0;
    r_ = 0;
    g_ = 0;
    b_ = 0;
    led_strip_clear(led_strip_);
}

void SingleLed::BlinkOnce() {
    Blink(1, 100);
}

void SingleLed::Blink(int times, int interval_ms) {
    StartBlinkTask(times, interval_ms);
}

void SingleLed::StartContinuousBlink(int interval_ms) {
    StartBlinkTask(BLINK_INFINITE, interval_ms);
}

void SingleLed::StartBlinkTask(int times, int interval_ms) {
    if (led_strip_ == nullptr) {
        return;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    esp_timer_stop(blink_timer_);
    
    blink_counter_ = times * 2;
    blink_interval_ms_ = interval_ms;
    esp_timer_start_periodic(blink_timer_, interval_ms * 1000);
}

void SingleLed::OnBlinkTimer() {
    std::lock_guard<std::mutex> lock(mutex_);

    // jlfuture检查是否在呼吸灯模式
    if (is_breathing_) {
        // 呼吸灯效果：渐变亮度
        breath_value_ += breath_step_;
        
        // 亮度范围 0-32，到达边界时反转方向
        if (breath_value_ >= 32) {
            breath_value_ = 32;
            breath_step_ = -1; // 开始减少亮度
        } else if (breath_value_ <= 0) {
            breath_value_ = 0;
            breath_step_ = 1; // 开始增加亮度
        }
        
        // 设置绿色LED亮度
        g_ = breath_value_;
        led_strip_set_pixel(led_strip_, 0, r_, g_, b_);
        led_strip_refresh(led_strip_);
        return;
    }
    
    // 原有的闪烁逻辑
    blink_counter_--;
    if (blink_counter_ & 1) {
        led_strip_set_pixel(led_strip_, 0, r_, g_, b_);
        led_strip_refresh(led_strip_);
    } else {
        led_strip_clear(led_strip_);

        if (blink_counter_ == 0) {
            esp_timer_stop(blink_timer_);
        }
    }
}


void SingleLed::OnStateChanged() {
    auto& app = Application::GetInstance();
    auto device_state = app.GetDeviceState();
    switch (device_state) {
        case kDeviceStateStarting:
            SetColor(0, 0, DEFAULT_BRIGHTNESS);
            StartContinuousBlink(100);
            break;
        case kDeviceStateWifiConfiguring:
            SetColor(0, 0, DEFAULT_BRIGHTNESS);
            StartContinuousBlink(500);
            break;
        case kDeviceStateIdle:
            TurnOff();
            break;
        case kDeviceStateConnecting:
            SetColor(0, 0, DEFAULT_BRIGHTNESS);
            TurnOn();
            break;
        case kDeviceStateListening:
        case kDeviceStateAudioTesting:
            if (app.IsVoiceDetected()) {
                SetColor(HIGH_BRIGHTNESS, 0, 0);
            } else {
                SetColor(LOW_BRIGHTNESS, 0, 0);
            }
            TurnOn();
            break;
        case kDeviceStateSpeaking:
            SetColor(0, DEFAULT_BRIGHTNESS, 0);
            TurnOn();
            break;
        case kDeviceStateUpgrading:
            SetColor(0, DEFAULT_BRIGHTNESS, 0);
            StartContinuousBlink(100);
            break;
        case kDeviceStateActivating:
            SetColor(0, DEFAULT_BRIGHTNESS, 0);
            StartContinuousBlink(500);
            break;
        default:
            ESP_LOGW(TAG, "Unknown led strip event: %d", device_state);
            return;
    }
}

// jlfuture新增：专门用于设置LED休眠状态的方法
void SingleLed::SetSleepMode(bool enabled) {
    std::lock_guard<std::mutex> lock(mutex_);
    esp_timer_stop(blink_timer_);
    
    if (enabled) {
        // 休眠模式：绿色呼吸灯
        r_ = 0;
        g_ = 0; // 呼吸灯开始时亮度为0
        b_ = 0;
        
        // 设置呼吸灯模式
        is_breathing_ = true;
        breath_step_ = 1; // 开始是增加亮度
        breath_value_ = 0; // 从0开始
        
        // 使用较短的间隔来实现平滑呼吸效果
        blink_interval_ms_ = 50; // 50ms更新一次亮度
        esp_timer_start_periodic(blink_timer_, blink_interval_ms_ * 1000);
        ESP_LOGI("SingleLed", "进入休眠模式 LED 绿色呼吸灯");
    } else {
        // 关闭呼吸灯模式
        is_breathing_ = false;
        // 恢复正常状态，通过OnStateChanged重设LED
        OnStateChanged();
    }
}
