#pragma once

#include <functional>

#include <esp_timer.h>
#include <esp_pm.h>

class PowerSaveTimer {
public:
    PowerSaveTimer(int cpu_max_freq, int seconds_to_sleep = 20, int seconds_to_shutdown = -1);
    ~PowerSaveTimer();

    void SetEnabled(bool enabled);
    void OnEnterSleepMode(std::function<void()> callback);
    void OnExitSleepMode(std::function<void()> callback);
    void OnShutdownRequest(std::function<void()> callback);
    void WakeUp();

    // 新增：MCP节能定时器控制接口
    void SetTimeout(int seconds_to_sleep, int seconds_to_shutdown);  // 设置休眠和关机超时时间
    bool IsEnabled() const { return enabled_; }                      // 获取是否启用状态
    int GetSleepTimeout() const { return seconds_to_sleep_; }        // 获取休眠超时时间（秒）
    int GetShutdownTimeout() const { return seconds_to_shutdown_; }  // 获取关机超时时间（秒）

private:
    void PowerSaveCheck();

    esp_timer_handle_t power_save_timer_ = nullptr;
    bool enabled_ = false;
    bool in_sleep_mode_ = false;
    bool is_wake_word_running_ = false;
    int ticks_ = 0;
    int cpu_max_freq_;
    int seconds_to_sleep_;
    int seconds_to_shutdown_;

    std::function<void()> on_enter_sleep_mode_;
    std::function<void()> on_exit_sleep_mode_;
    std::function<void()> on_shutdown_request_;
};
