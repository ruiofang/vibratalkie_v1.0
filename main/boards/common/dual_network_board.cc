#include "dual_network_board.h"
#include "application.h"
#include "display.h"
#include "assets/lang_config.h"
#include "settings.h"
#include <esp_log.h>
#include <font_awesome.h>
#include <cstring>

static const char *TAG = "DualNetworkBoard";

DualNetworkBoard::DualNetworkBoard(gpio_num_t ml307_tx_pin, gpio_num_t ml307_rx_pin, gpio_num_t ml307_dtr_pin, int32_t default_net_type) 
    : Board(), 
      ml307_tx_pin_(ml307_tx_pin), 
      ml307_rx_pin_(ml307_rx_pin), 
      ml307_dtr_pin_(ml307_dtr_pin) {
    
    // 从Settings加载网络类型
    network_type_ = LoadNetworkTypeFromSettings(default_net_type);
    
    // 只初始化当前网络类型对应的板卡
    InitializeCurrentBoard();
}

NetworkType DualNetworkBoard::LoadNetworkTypeFromSettings(int32_t default_net_type) {
    Settings settings("network", true);
    int network_type = settings.GetInt("type", default_net_type); // 默认使用ML307 (1)
    return network_type == 1 ? NetworkType::ML307 : NetworkType::WIFI;
}

void DualNetworkBoard::SaveNetworkTypeToSettings(NetworkType type) {
    Settings settings("network", true);
    int network_type = (type == NetworkType::ML307) ? 1 : 0;
    settings.SetInt("type", network_type);
}

void DualNetworkBoard::InitializeCurrentBoard() {
    if (network_type_ == NetworkType::ML307) {
        ESP_LOGI(TAG, "Initialize ML307 board");
        current_board_ = std::make_unique<Ml307Board>(ml307_tx_pin_, ml307_rx_pin_, ml307_dtr_pin_);
    } else {
        ESP_LOGI(TAG, "Initialize WiFi board");
        current_board_ = std::make_unique<WifiBoard>();
    }
}

void DualNetworkBoard::SwitchNetworkType() {
    auto display = GetDisplay();
    if (network_type_ == NetworkType::WIFI) {    
        SaveNetworkTypeToSettings(NetworkType::ML307);
        display->ShowNotification(Lang::Strings::SWITCH_TO_4G_NETWORK);
    } else {
        SaveNetworkTypeToSettings(NetworkType::WIFI);
        display->ShowNotification(Lang::Strings::SWITCH_TO_WIFI_NETWORK);
    }
    vTaskDelay(pdMS_TO_TICKS(1000));
    auto& app = Application::GetInstance();
    app.Reboot();
}

 
std::string DualNetworkBoard::GetBoardType() {
    return current_board_->GetBoardType();
}

void DualNetworkBoard::StartNetwork() {
    auto display = Board::GetInstance().GetDisplay();
    
    if (network_type_ == NetworkType::WIFI) {
        display->SetStatus(Lang::Strings::CONNECTING);
    } else {
        display->SetStatus(Lang::Strings::DETECTING_MODULE);
    }
    
    current_board_->StartNetwork();
    
    // 创建异步任务检查网络连接状态，如果4G连接失败，自动切换到WiFi
    if (network_type_ == NetworkType::ML307) {
        xTaskCreate([](void* arg) {
            DualNetworkBoard* board = static_cast<DualNetworkBoard*>(arg);
            board->CheckNetworkAndAutoSwitch();
            vTaskDelete(NULL);
        }, "network_check", 4096, this, 5, NULL);
    }
}

void DualNetworkBoard::CheckNetworkAndAutoSwitch() {
    // 延迟较长时间检查网络连接状态，给4G模块足够时间初始化和连接
    vTaskDelay(pdMS_TO_TICKS(30000)); // 等待30秒让4G网络尝试连接
    
    bool connected = false;
    
    // 根据当前网络类型检查连接状态
    if (network_type_ == NetworkType::ML307) {
        // 检查4G网络状态
        auto ml307_board = dynamic_cast<Ml307Board*>(current_board_.get());
        if (ml307_board && ml307_board->GetNetwork()) {
            // 使用modem的network_ready方法检查连接状态
            auto network_state_icon = ml307_board->GetNetworkStateIcon();
            // 如果图标不是信号关闭，说明连接成功
            connected = (strcmp(network_state_icon, FONT_AWESOME_SIGNAL_OFF) != 0);
        }
        ESP_LOGI(TAG, "4G network status check: connected=%d", connected);
    } else {
        // WiFi网络状态检查
        auto wifi_board = dynamic_cast<WifiBoard*>(current_board_.get());
        if (wifi_board && wifi_board->GetNetwork()) {
            auto network_state_icon = wifi_board->GetNetworkStateIcon();
            // 如果图标不是WiFi关闭，说明连接成功
            connected = (strcmp(network_state_icon, FONT_AWESOME_WIFI_SLASH) != 0);
        }
        ESP_LOGI(TAG, "WiFi network status check: connected=%d", connected);
    }
    
    if (network_type_ == NetworkType::ML307 && !connected) {
        ESP_LOGI(TAG, "4G connection failed after 30 seconds, switching to WiFi");
        auto display = Board::GetInstance().GetDisplay();
        display->ShowNotification("4G连接失败，切换到WiFi");
        
        // 切换到WiFi
        SaveNetworkTypeToSettings(NetworkType::WIFI);
        vTaskDelay(pdMS_TO_TICKS(3000)); // 显示提示3秒
        
        auto& app = Application::GetInstance();
        app.Reboot();
    } else if (network_type_ == NetworkType::ML307 && connected) {
        ESP_LOGI(TAG, "4G connection successful");
        auto display = Board::GetInstance().GetDisplay();
        display->ShowNotification("4G连接成功");
    }
}

NetworkInterface* DualNetworkBoard::GetNetwork() {
    return current_board_->GetNetwork();
}

const char* DualNetworkBoard::GetNetworkStateIcon() {
    return current_board_->GetNetworkStateIcon();
}

void DualNetworkBoard::SetPowerSaveMode(bool enabled) {
    current_board_->SetPowerSaveMode(enabled);
}

std::string DualNetworkBoard::GetBoardJson() {   
    return current_board_->GetBoardJson();
}

std::string DualNetworkBoard::GetDeviceStatusJson() {
    return current_board_->GetDeviceStatusJson();
}
