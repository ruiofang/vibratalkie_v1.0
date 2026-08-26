#ifndef __AXP173_H__
#define __AXP173_H__
#include "i2c_device.h"
// axp173.h

// 寄存器常量定义
#define AXP173_REG_00_INPUT_STATUS  0x00
#define AXP173_REG_01_CHARGE_STATUS  0x01
#define AXP173_REG_10_EXTEN  0x10

// REG00H 位定义
#define VBUS_PRESENT_BIT  (1 << 5)  // REG00H bit5: VBUS存在标志位
#define VBUS_USABLE_BIT     (1 << 4)
#define BAT_CURRENT_DIR_BIT (1 << 2)  // 0:放电, 1:充电
#define ACIN_PRESENT_BIT    (1 << 7)
#define ACIN_USABLE_BIT     (1 << 6)

// REG01H 位定义 
#define OVER_TEMP_BIT       (1 << 7)
#define CHARGING_BIT        (1 << 6)  // 1:充电中
#define BAT_PRESENT_BIT     (1 << 5)
#define BAT_ACTIVE_BIT      (1 << 3)
#define CHARGE_CURRENT_LOW  (1 << 2)  // 1:充电电流不足

// 声明全局pmic指针，供伪GPIO逻辑使用
class Axp173;
extern Axp173* g_pmic_ptr;

class Axp173 : public I2cDevice {
public:
    Axp173(i2c_master_bus_handle_t i2c_bus, uint8_t addr);
    bool IsCharging();
    bool IsDischarging();
    bool IsChargingDone();
    float getBatVoltage();
    int GetBatteryLevel();
    void PowerOff(); 
    void PrintIrqStatusRegs();
    void SetExten(int on);
    uint8_t ReadReg10() { return ReadReg(AXP173_REG_10_EXTEN); }
    
    // 检测VBUS是否插入
    bool IsVbusPresent();
};

#endif
