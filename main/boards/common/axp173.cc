#include "axp173.h"
#include "board.h"
#include "display.h"

#include <esp_log.h>
#include <algorithm>
#include "system_info.h"
#define TAG "Axp173"

Axp173::Axp173(i2c_master_bus_handle_t i2c_bus, uint8_t addr)
    : I2cDevice(i2c_bus, addr, 100 * 1000) {
}
//控制EXTEN_AU_EN
void Axp173::SetExten(int on) {
    // 读取当前寄存器值
    uint8_t reg = ReadReg(AXP173_REG_10_EXTEN);
    if (on)
        reg |= (1 << 2); // EXTEN置1
    else
        reg &= ~(1 << 2); // EXTEN清0
    // 写回寄存器
    WriteReg(AXP173_REG_10_EXTEN, reg);
}

// 检测是否正在充电
bool Axp173::IsCharging() {
    uint8_t value = ReadReg(AXP173_REG_01_CHARGE_STATUS);
    return (value & CHARGING_BIT);
}

// 检测充电是否完成
bool Axp173::IsChargingDone() {
    uint8_t value = ReadReg(AXP173_REG_01_CHARGE_STATUS);
    // 充电完成条件：不在充电状态且有电池连接
    return !(value & CHARGING_BIT) && (value & BAT_PRESENT_BIT);
}

// 判断电池放电状态
bool Axp173::IsDischarging() {
    uint8_t reg00 = ReadReg(AXP173_REG_00_INPUT_STATUS);
    uint8_t reg01 = ReadReg(AXP173_REG_01_CHARGE_STATUS);
    
    // 放电条件：
    // 1. 电流方向为放电 (REG00 bit2=0)
    // 2. 没有有效外部电源
    // 3. 电池存在且未在充电
    bool bat_connected = (reg01 & BAT_PRESENT_BIT);
    bool no_external_power = !(reg00 & (VBUS_USABLE_BIT | ACIN_USABLE_BIT));
    bool current_direction = !(reg00 & BAT_CURRENT_DIR_BIT);
    
    return bat_connected && no_external_power && current_direction;
}

//返回高八位 + 低四位电池电压   地址：高0x78 低0x79 精度：1.1mV
float Axp173::getBatVoltage() {
    float ADCLSB = 1.1 / 1000.0;
    uint8_t high = ReadReg(0x78);
    uint8_t low = ReadReg(0x79);
    float voltage = (high << 4 | (low & 0x0F)) * ADCLSB; // 合并高低位
    //ESP_LOGI(TAG, "Battery Voltage: %.3fV (Raw: 0x%02X%02X)", voltage, high, low);
    return voltage;
}

//返回电池电量等级（%）
int Axp173::GetBatteryLevel() {
    const float batVoltage = getBatVoltage();
    const float batPercentage = (batVoltage < 3.248088) ? 0 : (batVoltage - 3.120712) * 100;
    const int percentage = static_cast<int>(batPercentage);
    int result = (percentage > 100) ? 100 : percentage;
    //ESP_LOGI(TAG, "Battery level: %d%%", result);
    return result;
}

//切断电源
void Axp173::PowerOff() {
    // 1. 检查I2C有效性
    if (!i2c_device_) {
        ESP_LOGE(TAG, "I2C未初始化！");
        return;
    }
    ESP_LOGI(TAG, "关闭所有电源输出并关机...");

    // 2. 关闭EXTEN（功放）
    WriteReg(AXP173_REG_10_EXTEN, 0x00);

    // 3. 关闭所有电源输出：DCDC1/DCDC2/LDO2/LDO3/LDO4/EXTEN 全部关闭
    //    REG 12H: bit6=EXTEN, bit4=DCDC2, bit3=LDO3, bit2=LDO2, bit1=LDO4, bit0=DCDC1
    WriteReg(0x12, 0x00);

    vTaskDelay(pdMS_TO_TICKS(100));

    // 4. 触发关机（REG 32H bit7=1），充电电路独立工作不受影响
    uint8_t value = ReadReg(0x32);
    value = value | 0B10000000;
    WriteReg(0x32, value);
}

// 新增：检测VBUS是否插入
bool Axp173::IsVbusPresent() {
    uint8_t reg00 = ReadReg(AXP173_REG_00_INPUT_STATUS);
    bool present = (reg00 & VBUS_PRESENT_BIT) != 0;
    //ESP_LOGD(TAG, "VBUS状态检测: REG00=0x%02X, VBUS_PRESENT=%s", reg00, present ? "是" : "否");
    return present;
}

// 打印AXP173中断状态寄存器（0x44~0x47）

void Axp173::PrintIrqStatusRegs() {
    uint8_t reg44 = ReadReg(0x44);
    uint8_t reg45 = ReadReg(0x45);
    uint8_t reg46 = ReadReg(0x46);
    uint8_t reg47 = ReadReg(0x47);
    /*
    ESP_LOGI(TAG, "AXP173 IRQ Status Registers:");
    ESP_LOGI(TAG, "REG44H: 0x%02X", reg44);
    ESP_LOGI(TAG, "REG45H: 0x%02X", reg45);
    ESP_LOGI(TAG, "REG46H: 0x%02X", reg46);
    ESP_LOGI(TAG, "REG47H: 0x%02X", reg47);

    // REG44H
    if (reg44 & (1 << 7)) LOG_WARN("IRQ1: 电源ACIN超压");
    if (reg44 & (1 << 6)) LOG_INFO("IRQ2: 电源ACIN插入");
    if (reg44 & (1 << 5)) LOG_INFO("IRQ3: 电源ACIN移除");
    if (reg44 & (1 << 4)) LOG_WARN("IRQ4: 电源VBUS超压");
    if (reg44 & (1 << 3)) LOG_INFO("IRQ5: 电源VBUS插入");
    if (reg44 & (1 << 2)) LOG_INFO("IRQ6: 电源VBUS移除");
    if (reg44 & (1 << 1)) LOG_WARN("IRQ7: VBUS电压小于Vhold");
    // REG45H
    if (reg45 & (1 << 7)) LOG_INFO("IRQ8: 电池接入");
    if (reg45 & (1 << 6)) LOG_INFO("IRQ9: 电池移除");
    if (reg45 & (1 << 5)) LOG_INFO("IRQ10: 进入电池激活模式");
    if (reg45 & (1 << 4)) LOG_INFO("IRQ11: 退出电池激活模式");
    if (reg45 & (1 << 3)) LOG_INFO("IRQ12: 正在充电");
    if (reg45 & (1 << 2)) LOG_INFO("IRQ13: 充电完成");
    if (reg45 & (1 << 1)) LOG_WARN("IRQ14: 电池温度过高");
    if (reg45 & (1 << 0)) LOG_WARN("IRQ15: 电池温度过低");
    // REG46H
    if (reg46 & (1 << 7)) LOG_WARN("IRQ16: IC内部过温");
    if (reg46 & (1 << 6)) LOG_WARN("IRQ17: 充电电流不足");
    if (reg46 & (1 << 5)) LOG_WARN("IRQ18: DCDC1电压低");
    if (reg46 & (1 << 4)) LOG_WARN("IRQ19: DCDC2电压低");
    if (reg46 & (1 << 3)) LOG_WARN("IRQ20: LDO4电压低");
    // 46H[2] 保留
    if (reg46 & (1 << 1)) ESP_LOGW(TAG, "IRQ22: PEK短按");
    if (reg46 & (1 << 0)) ESP_LOGW(TAG, "IRQ23: PEK长按");
    // REG47H
    if (reg47 & (1 << 7)) LOG_INFO("IRQ24: 保留");
    if (reg47 & (1 << 6)) LOG_INFO("IRQ25: 保留");
    if (reg47 & (1 << 5)) LOG_INFO("IRQ26: VBUS有效");
    if (reg47 & (1 << 4)) LOG_INFO("IRQ27: VBUS无效");
    if (reg47 & (1 << 3)) LOG_INFO("IRQ28: VBUS SESSION有效");
    if (reg47 & (1 << 2)) LOG_INFO("IRQ29: VBUS SESSION无效");
    // 47H[1] 保留
    if (reg47 & (1 << 0)) LOG_WARN("IRQ30: 低电警告");
*/
    if (reg46 & (1 << 1)) ESP_LOGW(TAG, "IRQ22: PEK短按");
    if (reg46 & (1 << 0)) ESP_LOGW(TAG, "IRQ23: PEK长按");

    // 读取寄存器后，写回以清除中断标志
    // 修改：确保中断标志位正确清除
    // 写入1来清除中断标志位，而不是写入读取到的值
    if (reg44) WriteReg(0x44, reg44);
    if (reg45) WriteReg(0x45, reg45);
    if (reg46) WriteReg(0x46, reg46);
    if (reg47) WriteReg(0x47, reg47);
    
    // 增加验证：再次读取确认清除
    uint8_t verify44 = ReadReg(0x44);
    uint8_t verify45 = ReadReg(0x45);
    uint8_t verify46 = ReadReg(0x46);
    uint8_t verify47 = ReadReg(0x47);
    
    if (verify44 || verify45 || verify46 || verify47) {
        ESP_LOGW(TAG, "IRQ清除验证失败: 44=%02X 45=%02X 46=%02X 47=%02X", 
                 verify44, verify45, verify46, verify47);
    }
}
