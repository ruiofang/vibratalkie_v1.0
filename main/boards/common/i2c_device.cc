#include "i2c_device.h"

#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#define TAG "I2cDevice"


I2cDevice::I2cDevice(i2c_master_bus_handle_t i2c_bus, uint8_t addr, uint32_t scl_speed_hz)
    : i2c_bus_(i2c_bus), i2c_device_(nullptr) {
    i2c_device_config_t i2c_device_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = addr,
        .scl_speed_hz = scl_speed_hz,
        .scl_wait_us = 0,
        .flags = {
            .disable_ack_check = 0,
        },
    };
    ESP_ERROR_CHECK(i2c_master_bus_add_device(i2c_bus, &i2c_device_cfg, &i2c_device_));
    assert(i2c_device_ != NULL);
}

void I2cDevice::WriteReg(uint8_t reg, uint8_t value) {
    uint8_t buffer[2] = {reg, value};
    esp_err_t ret = ESP_FAIL;
    for (int attempt = 1; attempt <= 3; ++attempt) {
        ret = i2c_master_transmit(i2c_device_, buffer, 2, 200);
        if (ret == ESP_OK) {
            return;
        }
        ESP_LOGW(TAG, "WriteReg 0x%02X failed (%s), attempt %d/3", reg,
                 esp_err_to_name(ret), attempt);
        esp_err_t reset_ret = i2c_master_bus_reset(i2c_bus_);
        if (reset_ret != ESP_OK) {
            ESP_LOGW(TAG, "I2C bus reset failed: %s", esp_err_to_name(reset_ret));
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    ESP_ERROR_CHECK(ret);
}

uint8_t I2cDevice::ReadReg(uint8_t reg) {
    uint8_t buffer[1] = {};
    esp_err_t ret = ESP_FAIL;
    for (int attempt = 1; attempt <= 3; ++attempt) {
        ret = i2c_master_transmit_receive(i2c_device_, &reg, 1, buffer, 1, 200);
        if (ret == ESP_OK) {
            return buffer[0];
        }
        ESP_LOGW(TAG, "ReadReg 0x%02X failed (%s), attempt %d/3", reg,
                 esp_err_to_name(ret), attempt);
        i2c_master_bus_reset(i2c_bus_);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    ESP_ERROR_CHECK(ret);
    return buffer[0];
}

void I2cDevice::ReadRegs(uint8_t reg, uint8_t* buffer, size_t length) {
    esp_err_t ret = ESP_FAIL;
    for (int attempt = 1; attempt <= 3; ++attempt) {
        ret = i2c_master_transmit_receive(i2c_device_, &reg, 1, buffer, length, 200);
        if (ret == ESP_OK) {
            return;
        }
        ESP_LOGW(TAG, "ReadRegs 0x%02X failed (%s), attempt %d/3", reg,
                 esp_err_to_name(ret), attempt);
        i2c_master_bus_reset(i2c_bus_);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    ESP_ERROR_CHECK(ret);
}
