#include "wifi_board.h"
#include "dual_network_board.h"
#include "codecs/box_audio_codec.h"
#include "display/lcd_display.h"
#include "application.h"
#include "button.h"
#include "config.h"
#include "i2c_device.h"
#ifdef CONFIG_VIBRATALKIE_ANIMATED_FACE
    #ifdef CONFIG_VIBRATALKIE_EYE_BITMAP
        #include "vibratalkie_bitmap_eye_display.h"
    #else
        #include "vibratalkie_eye_display.h"
    #endif
#endif

#include <esp_log.h>
#include <esp_lcd_panel_vendor.h>
#if defined(CONFIG_DISPLAY_USE_GC9D01)
#include "esp_lcd_gc9d01n.h"
#elif defined(CONFIG_DISPLAY_USE_GC9A01)
#include <esp_lcd_gc9a01.h>
#endif
#include <driver/i2c_master.h>
#include <driver/spi_common.h>
#include <wifi_station.h>
#include <ssid_manager.h>
#include <esp_wifi.h>
#include <esp_lvgl_port.h>
#include <lvgl.h>
#include <string>

#include "axp173.h"
#include "led/single_led.h"
#include "power_save_timer.h"
#include "settings.h"
#include "assets/lang_config.h"
#include "freertos/queue.h"
#include "driver/gpio.h"
#include "mcp_tools.h"

// 全局pmic指针定义，供功放/电源等全局控制使用
Axp173* g_pmic_ptr = nullptr;

// 前向声明
class VibratalkieBoard;

// 全局事件队列
static QueueHandle_t axp173_evt_queue = nullptr;

// ISR回调
static void IRAM_ATTR axp173_irq_isr_handler(void* arg) {
    uint32_t gpio_num = (uint32_t)arg;
    xQueueSendFromISR(axp173_evt_queue, &gpio_num, NULL);
}

// 任务处理函数声明（实现放到类定义后）
static void axp173_irq_task(void* arg);

#define TAG "VibratalkieBoard"

LV_FONT_DECLARE(font_puhui_basic_20_4);
LV_FONT_DECLARE(font_awesome_20_4);

class Pmic : public Axp173 {
public:
    Pmic(i2c_master_bus_handle_t i2c_bus, uint8_t addr) : Axp173(i2c_bus, addr)
    {
        // ===== 第1步：先设置各路输出电压（在使能输出之前） =====
        WriteReg(0x26, (3300 - 700) / 25); // REG 26H: DC-DC1输出电压设置 3.3V
        WriteReg(0x27, (3300 - 700) / 25); // REG 27H: LDO4输出电压设置 3.3V
        WriteReg(0x28, 0xFF);               // REG 28H: LDO2/LDO3 电压档位均设为 3.3V（仅使能 LDO3）

        // 回读验证DC-DC1电压寄存器是否写入成功
        uint8_t reg26 = Axp173::ReadReg(0x26);
        ESP_LOGI(TAG, "DC-DC1电压寄存器(0x26) 回读=0x%02X, 期望=0x%02X", reg26, (uint8_t)((3300 - 700) / 25));
        if (reg26 != (uint8_t)((3300 - 700) / 25)) {
            ESP_LOGW(TAG, "DC-DC1电压寄存器写入不匹配！重试写入...");
            WriteReg(0x26, (3300 - 700) / 25);
            vTaskDelay(pdMS_TO_TICKS(5));
            reg26 = Axp173::ReadReg(0x26);
            ESP_LOGI(TAG, "DC-DC1电压寄存器(0x26) 第二次回读=0x%02X", reg26);
        }

        vTaskDelay(pdMS_TO_TICKS(10));     // 等待电压设置锁存稳定

        // ===== 第2步：使能电源输出 =====
        WriteReg(0x12, 0b00001011);        // REG 12H: 开启 LDO3(3.3V)、LDO4(3.3V) 和 DC-DC1，LDO2 保持关闭

        vTaskDelay(pdMS_TO_TICKS(50));     // 等待DC-DC1输出爬升并稳定（50ms）

        // ===== 第3步：再次确认DC-DC1电压（防止使能后被复位） =====
        reg26 = Axp173::ReadReg(0x26);
        if (reg26 != (uint8_t)((3300 - 700) / 25)) {
            ESP_LOGW(TAG, "使能后DC-DC1电压被复位(0x%02X)，重新设置！", reg26);
            WriteReg(0x26, (3300 - 700) / 25);
        }

        // ===== 第4步：其余配置 =====
        WriteReg(0x33, 0b11001011);        // REG 33H: 充电控制1 4.2V 1000mA
        WriteReg(0x36, 0b01101100);        // REG 36H: PEK按键参数设置短按开机，长按4s关机
        WriteReg(0x10, 0b00000000);        // REG 10H: EXTEN 默认关闭，由EnableOutput控制
        //写一遍默认值避免遇到定制芯片
        WriteReg(0x30, 0b01001000);        // REG 30H: VBUS-IPSOUT通路管理
        WriteReg(0x31, 0b00000001);        // REG 31H: VOFF关机电压设置2.7V
        WriteReg(0x32, 0b01000000);        // REG 32H: 关机设置、电池检测以及CHGLED管脚控制
        WriteReg(0x3A, 0x68);              // REG 3AH: APS 低电级别1
        WriteReg(0x3B, 0x5F);              // REG 3BH: APS 低电级别2
        WriteReg(0x84, 0b00110100);        // REG 84H: ADC采样速率设置，TS管脚控制 
        WriteReg(0x8A, 0b00000000);        // REG 8AH: 定时器控制  
        WriteReg(0x8B, 0b00000000);        // REG 8BH: VBUS管脚监测SRP功能控制
        WriteReg(0x8F, 0b00000100);        // REG 8FH: 过温关机等功能设置0b00000100开启
        WriteReg(0x40, 0b11011110);        // REG 40H: IRQ使能1
        WriteReg(0x41, 0b11111111);        // REG 41H: IRQ使能2
        WriteReg(0x42, 0b10111011);        // REG 42H: IRQ使能3
        WriteReg(0x43, 0b11110011);        // REG 43H: IRQ使能4

        // 打印最终电源状态
        ESP_LOGI(TAG, "AXP173电源配置完成 - DCDC1=0x%02X, LDO4=0x%02X, LDO2/3=0x%02X, REG12=0x%02X",
                 Axp173::ReadReg(0x26), Axp173::ReadReg(0x27),
                 Axp173::ReadReg(0x28), Axp173::ReadReg(0x12));
    }

    // 公开读取reg46的方法
    uint8_t ReadIrqReg46() { return Axp173::ReadReg(0x46); }

};

class CustomAudioCodec : public BoxAudioCodec {
public:
    CustomAudioCodec(i2c_master_bus_handle_t i2c_bus) 
        : BoxAudioCodec(i2c_bus, 
                        AUDIO_INPUT_SAMPLE_RATE, 
                        AUDIO_OUTPUT_SAMPLE_RATE,
                        AUDIO_I2S_GPIO_MCLK, 
                        AUDIO_I2S_GPIO_BCLK, 
                        AUDIO_I2S_GPIO_WS, 
                        AUDIO_I2S_GPIO_DOUT, 
                        AUDIO_I2S_GPIO_DIN,
                        GPIO_NUM_NC, // 实际功放由AXP173 EXTEN控制
                        AUDIO_CODEC_ES8311_ADDR, 
                        AUDIO_CODEC_ES7210_ADDR, 
                        AUDIO_INPUT_REFERENCE,
                        2) {
        // 麦克风增益设置（默认30dB），AGC会在AFE处理后补偿正常语音电平
        input_gain_ = 30.0f;
        // NVS中音量可能为0，强制设置合理默认值
        if (output_volume_ <= 10) {
            output_volume_ = 50;
            ESP_LOGW(TAG, "音量过低(%d)，重置为默认值50", output_volume_);
        }
        ESP_LOGI(TAG, "CustomAudioCodec初始化完成 - ES8311 addr=0x%02X, ES7210 addr=0x%02X, volume=%d", 
                 AUDIO_CODEC_ES8311_ADDR, AUDIO_CODEC_ES7210_ADDR, output_volume_);
        ESP_LOGI(TAG, "I2S引脚: MCLK=%d, BCLK=%d, WS=%d, DOUT=%d, DIN=%d",
                 AUDIO_I2S_GPIO_MCLK, AUDIO_I2S_GPIO_BCLK, AUDIO_I2S_GPIO_WS,
                 AUDIO_I2S_GPIO_DOUT, AUDIO_I2S_GPIO_DIN);
    }

    virtual void EnableOutput(bool enable) override {
        ESP_LOGI(TAG, "EnableOutput(%s)", enable ? "ON" : "OFF");
        BoxAudioCodec::EnableOutput(enable);
        if (g_pmic_ptr) {
            g_pmic_ptr->SetExten(enable ? 1 : 0);
        }
    }
};

class VibratalkieBoard : public DualNetworkBoard
{
private:
    i2c_master_bus_handle_t i2c_bus_;
    i2c_master_dev_handle_t ads1115_device_ = nullptr;
    Pmic* pmic_ = nullptr;
    Button boot_button_;
    Button volume_up_button_;
    LcdDisplay* display_;
    PowerSaveTimer* power_save_timer_;

    void PowerOff() {
        auto* led = static_cast<SingleLed*>(GetLed());
        if (led) {
            led->Shutdown();
            // WS2812 needs the zero frame to latch before the PMIC turns rails off.
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        if (pmic_) {
            pmic_->PowerOff();
        }
    }

    // 电池供电时定时自动关机，节省电量
    void InitializePowerSaveTimer() {
        // 从持久化存储读取设置
        Settings settings("powersave", true);
        bool enabled = settings.GetBool("enabled", true);
        int sleep_timeout = settings.GetInt("sleep_to", 600);  // 休眠时间（秒）
        int shutdown_timeout = settings.GetInt("shut_to", -1);  // 关机时间（秒）

        // 参数：CPU频率(MHz), 休眠时间(秒), 关机时间(秒)
        power_save_timer_ = new PowerSaveTimer(120, sleep_timeout, shutdown_timeout);
        power_save_timer_->OnEnterSleepMode([this]() {
            ESP_LOGI(TAG, "节能休眠");
            auto display = GetDisplay();
            display->SetChatMessage("system", "");
            display->SetEmotion("sleepy");
            GetBacklight()->SetBrightness(0);
            auto single_led = static_cast<SingleLed*>(GetLed());
            if (single_led) {
                single_led->SetSleepMode(true); // 使用专门的呼吸灯模式
            }
        });
        power_save_timer_->OnExitSleepMode([this]() {
            auto display = GetDisplay();
            display->SetChatMessage("system", "");
            display->SetEmotion("neutral");
            GetBacklight()->RestoreBrightness();
            auto single_led = static_cast<SingleLed*>(GetLed());
            if (single_led) {
                single_led->OnStateChanged(); // 重新设置LED状态
            }
        });
        power_save_timer_->OnShutdownRequest([this]() {
            ESP_LOGI(TAG, "节能关机");
            PowerOff();
        });
        // 设置节能定时器启用状态
        power_save_timer_->SetEnabled(enabled);
    }

    // Test the physical I2C lines before the peripheral takes ownership of
    // the pins. A released line must become high with the internal pull-up.
    void TestAndRecoverI2cLines() {
        const gpio_num_t sda = AUDIO_CODEC_I2C_SDA_PIN;
        const gpio_num_t scl = AUDIO_CODEC_I2C_SCL_PIN;

        gpio_reset_pin(sda);
        gpio_reset_pin(scl);
        gpio_set_direction(sda, GPIO_MODE_INPUT);
        gpio_set_direction(scl, GPIO_MODE_INPUT);
        gpio_set_pull_mode(sda, GPIO_FLOATING);
        gpio_set_pull_mode(scl, GPIO_FLOATING);
        vTaskDelay(pdMS_TO_TICKS(5));
        int floating_sda = gpio_get_level(sda);
        int floating_scl = gpio_get_level(scl);

        gpio_set_pull_mode(sda, GPIO_PULLUP_ONLY);
        gpio_set_pull_mode(scl, GPIO_PULLUP_ONLY);
        vTaskDelay(pdMS_TO_TICKS(10));
        int pulled_sda = gpio_get_level(sda);
        int pulled_scl = gpio_get_level(scl);
        ESP_LOGI(TAG, "I2C pull-up test: floating SDA=%d SCL=%d, pull-up SDA=%d SCL=%d",
                 floating_sda, floating_scl, pulled_sda, pulled_scl);

        // Open-drain high means release, never drive a line high against a
        // target that may be holding it low.
        gpio_set_direction(sda, GPIO_MODE_INPUT_OUTPUT_OD);
        gpio_set_direction(scl, GPIO_MODE_INPUT_OUTPUT_OD);
        gpio_set_level(sda, 1);
        gpio_set_level(scl, 1);
        vTaskDelay(pdMS_TO_TICKS(2));

        if (!gpio_get_level(sda) || !gpio_get_level(scl)) {
            ESP_LOGW(TAG, "I2C lines are held low; sending 9 open-drain recovery clocks");
            for (int i = 0; i < 9; ++i) {
                gpio_set_level(scl, 0);
                vTaskDelay(pdMS_TO_TICKS(1));
                gpio_set_level(scl, 1);
                vTaskDelay(pdMS_TO_TICKS(1));
            }

            // Generate an open-drain STOP: SDA low, release SCL, release SDA.
            gpio_set_level(sda, 0);
            vTaskDelay(pdMS_TO_TICKS(1));
            gpio_set_level(scl, 1);
            vTaskDelay(pdMS_TO_TICKS(1));
            gpio_set_level(sda, 1);
            vTaskDelay(pdMS_TO_TICKS(2));
        }

        ESP_LOGI(TAG, "I2C recovery result: SDA=%d SCL=%d",
                 gpio_get_level(sda), gpio_get_level(scl));
    }

    // I2C初始化
    void InitializeI2c() {
        TestAndRecoverI2cLines();

        // Initialize I2C peripheral
        i2c_master_bus_config_t i2c_bus_cfg = {
            .i2c_port = I2C_NUM_0,
            .sda_io_num = AUDIO_CODEC_I2C_SDA_PIN,
            .scl_io_num = AUDIO_CODEC_I2C_SCL_PIN,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .intr_priority = 0,
            .trans_queue_depth = 0,
            .flags = {
                .enable_internal_pullup = 1,
            },
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&i2c_bus_cfg, &i2c_bus_));

        // Give all always-on I2C devices time to leave power-on reset, then
        // clear a potentially interrupted transaction left by a warm reboot.
        vTaskDelay(pdMS_TO_TICKS(20));
        esp_err_t reset_ret = i2c_master_bus_reset(i2c_bus_);
        if (reset_ret != ESP_OK) {
            ESP_LOGW(TAG, "I2C bus initial reset failed: %s", esp_err_to_name(reset_ret));
        }

        esp_err_t probe_ret = ESP_FAIL;
        for (int attempt = 1; attempt <= 3; ++attempt) {
            probe_ret = i2c_master_probe(i2c_bus_, AXP173_I2C_ADDR, 200);
            if (probe_ret == ESP_OK) {
                ESP_LOGI(TAG, "AXP173 detected at 0x%02X on I2C0", AXP173_I2C_ADDR);
                break;
            }
            ESP_LOGW(TAG, "AXP173 probe failed (%s), attempt %d/3, SDA=%d SCL=%d",
                     esp_err_to_name(probe_ret), attempt,
                     gpio_get_level(AUDIO_CODEC_I2C_SDA_PIN),
                     gpio_get_level(AUDIO_CODEC_I2C_SCL_PIN));
            i2c_master_bus_reset(i2c_bus_);
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        ESP_ERROR_CHECK(probe_ret);
    }

    void InitializeAxp173()
    {
        ESP_LOGI(TAG, "Init AXP173");
        pmic_ = new Pmic(i2c_bus_, AXP173_I2C_ADDR);
        g_pmic_ptr = pmic_;
        // 新增：打印AXP173中断状态寄存器
        pmic_->PrintIrqStatusRegs();
    }

    static void Ads1115PrintTask(void* arg) {
        auto* board = static_cast<VibratalkieBoard*>(arg);
        // At 128 SPS the first continuous-conversion result is ready well
        // before this initial delay expires.
        vTaskDelay(pdMS_TO_TICKS(20));
        while (true) {
            uint8_t conversion_reg = 0x00;
            uint8_t data[2] = {};
            esp_err_t ret = i2c_master_transmit_receive(
                board->ads1115_device_, &conversion_reg, 1, data, sizeof(data), 200);
            if (ret == ESP_OK) {
                int16_t raw = static_cast<int16_t>(
                    (static_cast<uint16_t>(data[0]) << 8) | data[1]);
                float voltage = static_cast<float>(raw) * 4.096f / 32768.0f;
                ESP_LOGI(TAG, "ADS1115 AIN0: raw=%d, voltage=%.4f V", raw, voltage);
            } else {
                ESP_LOGW(TAG, "ADS1115 AIN0 read failed: %s", esp_err_to_name(ret));
            }
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }

    void InitializeAds1115() {
        constexpr uint8_t kAds1115Address = 0x48; // ADDR connected to GND

        esp_err_t ret = i2c_master_probe(i2c_bus_, kAds1115Address, 200);
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "ADS1115 not detected at 0x%02X: %s",
                     kAds1115Address, esp_err_to_name(ret));
            return;
        }

        i2c_device_config_t device_config = {
            .dev_addr_length = I2C_ADDR_BIT_LEN_7,
            .device_address = kAds1115Address,
            .scl_speed_hz = 100 * 1000,
            .scl_wait_us = 0,
            .flags = {
                .disable_ack_check = 0,
            },
        };
        ESP_ERROR_CHECK(i2c_master_bus_add_device(
            i2c_bus_, &device_config, &ads1115_device_));

        // Config 0xC283:
        // AIN0-GND, +/-4.096V PGA, continuous mode, 128 SPS, comparator off.
        uint8_t config[] = {0x01, 0xC2, 0x83};
        ret = i2c_master_transmit(ads1115_device_, config, sizeof(config), 200);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "ADS1115 configuration failed: %s", esp_err_to_name(ret));
            ESP_ERROR_CHECK(i2c_master_bus_rm_device(ads1115_device_));
            ads1115_device_ = nullptr;
            return;
        }

        ESP_LOGI(TAG, "ADS1115 initialized at 0x%02X, AIN0 continuous sampling enabled",
                 kAds1115Address);
        xTaskCreate(Ads1115PrintTask, "ads1115_print", 3072, this, 5, nullptr);
    }

    // SPI初始化（用于显示屏）
    void InitializeSpi() {
        spi_bus_config_t buscfg = {};
        buscfg.mosi_io_num = DISPLAY_MOSI_PIN;
        buscfg.miso_io_num = GPIO_NUM_NC;
        buscfg.sclk_io_num = DISPLAY_CLK_PIN;
        buscfg.quadwp_io_num = GPIO_NUM_NC;
        buscfg.quadhd_io_num = GPIO_NUM_NC;
        buscfg.max_transfer_sz = DISPLAY_WIDTH * DISPLAY_HEIGHT * sizeof(uint16_t);
        ESP_ERROR_CHECK(spi_bus_initialize(SPI3_HOST, &buscfg, SPI_DMA_CH_AUTO));
    }
    // 按钮初始化
    void InitializeButtons() {
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateStarting) {
                // 在启动状态时，可以切换网络类型
                SwitchNetworkType();
                return; // 切换网络类型后直接返回，不执行聊天状态切换
            }
            app.ToggleChatState(); 
        });

        // 长按BOOT键：恢复出厂设置（清除WiFi配置、网络类型选择，重启进入配网模式）
        boot_button_.OnLongPress([this]() {
            ESP_LOGW(TAG, "长按BOOT键：恢复出厂设置，清除WiFi配置...");
            power_save_timer_->WakeUp();

            auto display = GetDisplay();
            display->ShowNotification("恢复出厂设置...");

            // 1. 清除所有已保存的 WiFi SSID
            SsidManager::GetInstance().Clear();

            // 2. 清除网络类型选择，恢复默认（4G优先）
            {
                Settings net_settings("network", true);
                net_settings.SetInt("type", 1);
            }

            // 3. 清除 force_ap 标志，确保下次以干净状态进入配网
            {
                Settings wifi_settings("wifi", true);
                wifi_settings.SetInt("force_ap", 1); // 强制进入配网AP模式
            }

            ESP_LOGW(TAG, "出厂设置已清除，3秒后重启...");
            vTaskDelay(pdMS_TO_TICKS(3000));
            esp_restart();
        });

#if CONFIG_USE_DEVICE_AEC
        boot_button_.OnDoubleClick([this]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateIdle) {
                app.SetAecMode(app.GetAecMode() == kAecOff ? kAecOnDeviceSide : kAecOff);
            }
        });
#endif

        // 右按钮音量+
        volume_up_button_.OnClick([this]() {
            power_save_timer_->WakeUp();
            auto codec = GetAudioCodec();
            auto volume = codec->output_volume() + 10;
            if (volume > 100) {
                volume = 100;
            }
            codec->SetOutputVolume(volume);
            GetDisplay()->ShowNotification(Lang::Strings::VOLUME + std::to_string(volume));
        });
        
        // 长按右按钮静音
        volume_up_button_.OnLongPress([this]() {
            power_save_timer_->WakeUp();
            GetAudioCodec()->SetOutputVolume(0);
            GetDisplay()->ShowNotification(Lang::Strings::MUTED); 
        });
        // 音量-，由AXP173中断处理
    }

    // 显示屏初始化（通用SPI IO + panel创建）
    void InitializeDisplay() {
        esp_lcd_panel_io_handle_t panel_io = nullptr;
        esp_lcd_panel_handle_t panel = nullptr;

        ESP_LOGD(TAG, "Install panel IO");
        esp_lcd_panel_io_spi_config_t io_config = {};
        io_config.cs_gpio_num = DISPLAY_CS_PIN;
        io_config.dc_gpio_num = DISPLAY_DC_PIN;
        io_config.spi_mode = DISPLAY_SPI_MODE;
        io_config.pclk_hz = DISPLAY_PCLK_HZ;
        io_config.trans_queue_depth = 10;
        io_config.lcd_cmd_bits = 8;
        io_config.lcd_param_bits = 8;
        ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(SPI3_HOST, &io_config, &panel_io));

        ESP_LOGD(TAG, "Install LCD driver");
        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = DISPLAY_RST_PIN;
        panel_config.rgb_ele_order = DISPLAY_RGB_ORDER;
        panel_config.bits_per_pixel = 16;

#if defined(CONFIG_DISPLAY_USE_GC9D01)
        ESP_LOGI(TAG, "使用 GC9D01 160x160 显示屏");
        ESP_ERROR_CHECK(esp_lcd_new_panel_gc9d01n(panel_io, &panel_config, &panel));
#elif defined(CONFIG_DISPLAY_USE_GC9A01)
        ESP_LOGI(TAG, "使用 GC9A01 240x240 显示屏");
        ESP_ERROR_CHECK(esp_lcd_new_panel_gc9a01(panel_io, &panel_config, &panel));
#else
        ESP_LOGI(TAG, "使用 ST7789 240x240 显示屏");
        ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(panel_io, &panel_config, &panel));
#endif

        esp_lcd_panel_reset(panel);
        esp_lcd_panel_init(panel);
        esp_lcd_panel_invert_color(panel, DISPLAY_INVERT_COLOR);
        esp_lcd_panel_swap_xy(panel, DISPLAY_SWAP_XY);
        esp_lcd_panel_mirror(panel, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);

#ifdef CONFIG_VIBRATALKIE_ANIMATED_FACE
#ifdef CONFIG_VIBRATALKIE_EYE_BITMAP
        display_ = new VibratalkieBitmapEyeDisplay(panel_io, panel,
                                    DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
#else
        display_ = new VibratalkieEyeDisplay(panel_io, panel,
                                    DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY,
#ifdef CONFIG_VIBRATALKIE_SINGLE_EYE
                                    true   // 单眼模式
#else
                                    false  // 双眼模式
#endif
                                    );
#endif
#else
        display_ = new SpiLcdDisplay(panel_io, panel,
                                    DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
#endif
    }

public:
    // 构造函数
    VibratalkieBoard() : DualNetworkBoard(Module_4G_TX_PIN, Module_4G_RX_PIN),
                           boot_button_(BOOT_BUTTON_GPIO),
                           volume_up_button_(VOLUME_UP_BUTTON_GPIO) {
        InitializeI2c();
        InitializeAxp173();
        InitializeAds1115();
        InitializePowerSaveTimer();
        InitializeSpi();
        InitializeDisplay();
        InitializeButtons();
        RegisterVibratalkieMcpTools(*this);
        GetBacklight()->RestoreBrightness();

        // 新增：初始化AXP173 IRQ GPIO
        gpio_config_t io_conf = {};
        io_conf.intr_type = GPIO_INTR_NEGEDGE;
        io_conf.mode = GPIO_MODE_INPUT;
        io_conf.pin_bit_mask = (1ULL << AXP173_IRQ_GPIO);
        io_conf.pull_up_en = GPIO_PULLUP_ENABLE;
        io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
        gpio_config(&io_conf);

        axp173_evt_queue = xQueueCreate(2, sizeof(uint32_t));
        gpio_install_isr_service(0);
        gpio_isr_handler_add((gpio_num_t)AXP173_IRQ_GPIO, axp173_irq_isr_handler, (void*)AXP173_IRQ_GPIO);

        // 创建中断处理任务
        xTaskCreate(axp173_irq_task, "axp173_irq_task", 4096, this, 10, NULL);
    }

    // 获取音频编码器
    virtual AudioCodec* GetAudioCodec() override {
        static AudioCodec* audio_codec = nullptr;
        if (!audio_codec) {
            // 等待音频编解码器上电稳定后再初始化，避免刚上电立即访问I2C导致NACK
            vTaskDelay(pdMS_TO_TICKS(200));
            audio_codec = new CustomAudioCodec(i2c_bus_);
        }
        return audio_codec;
    }

    // 获取LED
    virtual Led* GetLed() override {
        static SingleLed led(BUILTIN_LED_GPIO);
        return &led;
    }

    // 获取显示屏
    virtual Display* GetDisplay() override {
        return display_;
    }
    // 获取背光控制
    virtual Backlight* GetBacklight() override {
        static PwmBacklight backlight(DISPLAY_BACKLIGHT_PIN, DISPLAY_BACKLIGHT_OUTPUT_INVERT);
        return &backlight;
    }
    // 获取pmic指针
    Pmic* GetPmic() { return pmic_; }

    // 获取PowerSaveTimer指针
    virtual PowerSaveTimer* GetPowerSaveTimer() override
    {
        return power_save_timer_;
    }
    // 更新电量与控制休眠
    virtual bool GetBatteryLevel(int &level, bool& charging, bool& discharging) override {
        //static bool last_discharging = false;
        charging = pmic_->IsCharging(); // 判断是否正在充电
        discharging = pmic_->IsDischarging(); // 判断是否正在放电
        if (discharging == false && power_save_timer_->IsEnabled())
        {
            //power_save_timer_->SetEnabled(discharging);
            power_save_timer_->WakeUp();
            //ESP_LOGI(TAG, "未处于放电状态且节能定时器启用，阻止休眠");
            //last_discharging = discharging;
        }

        level = pmic_->GetBatteryLevel();
        //ESP_LOGI(TAG, "Battery: level=%d%%, charging=%d, discharging=%d, timer_enabled=%d", level, charging, discharging, power_save_timer_->IsEnabled());
        return true;
    }

    virtual void SetPowerSaveMode(bool enabled) override {
        if (!enabled) {
            power_save_timer_->WakeUp();
        }
        DualNetworkBoard::SetPowerSaveMode(enabled);
        //ESP_LOGI(TAG, "WakeUp WifiBoard: enabled=%d", enabled);
    }

    // 重写 StartNetwork：在 WiFi 模式下注入 PMF 补丁
    // ESP-IDF 事件分发顺序：ANY_ID 处理器先于 specific_id 处理器
    // WifiStation 的 ESP_EVENT_ANY_ID 处理器先执行：StartConnect() → set_config(无PMF) → connect()
    // 本处理器（WIFI_EVENT_SCAN_DONE）后执行：将 pmf_cfg.capable=true 写回驱动
    // 确保重试连接（第2~5次）及后续扫描周期均使用正确的 PMF 配置，解决 auth→init(0x200) 失败
    virtual void StartNetwork() override {
        if (GetNetworkType() == NetworkType::WIFI) {
            static esp_event_handler_instance_t pmf_patch_handle = nullptr;
            if (!pmf_patch_handle) {
                esp_event_handler_instance_register(WIFI_EVENT, WIFI_EVENT_SCAN_DONE,
                    [](void*, esp_event_base_t, int32_t, void*) {
                        wifi_config_t cfg = {};
                        if (esp_wifi_get_config(WIFI_IF_STA, &cfg) == ESP_OK &&
                            strlen((char*)cfg.sta.password) > 0) {
                            cfg.sta.pmf_cfg.capable  = true;
                            cfg.sta.pmf_cfg.required = false;
                            if (cfg.sta.threshold.authmode < WIFI_AUTH_WPA2_PSK)
                                cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
                            esp_wifi_set_config(WIFI_IF_STA, &cfg);
                            ESP_LOGI(TAG, "WiFi PMF/authmode 已修正 (capable=1, authmode=%d)",
                                     cfg.sta.threshold.authmode);
                        }
                    }, nullptr, &pmf_patch_handle);
            }
        }
        DualNetworkBoard::StartNetwork();
    }

    // Provide a board-level soft power off hook used by centralized MCP tools
    virtual void SoftPowerOff() override {
        PowerOff();
    }
};

// 任务处理函数实现（放到完整类定义后）
static void axp173_irq_task(void* arg) {
    VibratalkieBoard* board = (VibratalkieBoard*)arg;
    uint32_t io_num;
    while (true) {
        if (xQueueReceive(axp173_evt_queue, &io_num, portMAX_DELAY)) {
            //ESP_LOGI(TAG, "AXP173 IRQ GPIO中断触发，读取寄存器...");
            if (board && board->GetPmic()) {
                // 读取IRQ寄存器（通过新增的public方法）
                uint8_t reg46 = board->GetPmic()->ReadIrqReg46();
                // IRQ23: PEK长按。先锁存RGB全灭帧，再执行PMIC关机。
                if (reg46 & (1 << 0)) {
                    ESP_LOGI(TAG, "PEK长按关机：先关闭RGB灯");
                    board->SoftPowerOff();
                    continue;
                }
                // IRQ22: PEK短按
                if (reg46 & (1 << 1)) {
                    //board->GetPowerSaveTimer()->WakeUp();
                    vTaskDelay(pdMS_TO_TICKS(200));
                    auto* timer = board->GetPowerSaveTimer();
                    if (timer) {
                        timer->WakeUp();
                    }
                    auto codec = board->GetAudioCodec();
                    if (codec) {
                        auto volume = codec->output_volume() - 10;
                        if (volume < 0) {
                            volume = 0;
                        }
                        codec->SetOutputVolume(volume);
                        board->GetDisplay()->ShowNotification(Lang::Strings::VOLUME + std::to_string(volume));
                    }
                }
                // 最后再清除中断标志
                board->GetPmic()->PrintIrqStatusRegs();
            }
        }
    }
}

DECLARE_BOARD(VibratalkieBoard);
