# Vibratalkie

> **ESP-IDF 版本要求：≥ v5.4.0**（推荐 v5.5.x，本项目使用 v5.5.2 开发测试）

## 简介

Vibratalkie AI聊天机器人控制板套件，支持 WiFi + 4G 双网络模式。

## 硬件配置

| 项目 | 规格 |
|------|------|
| 主控 | ESP32-S3R8 |
| 存储 | 16MB NOR Flash |
| 音频编解码 | ES8311（DAC）+ ES7210（ADC），支持硬件 AEC |
| 麦克风 | 双全指向 MEMS 麦克风 ZTS6216 |
| 电源管理 | AXP173（充电 4.2V/1000mA，长按 4s 关机，LDO3/LDO4 均为 3.3V） |
| 显示屏 | 2.0 寸 TFT IPS（ST7789，240×320） |
| ADC | ADS1115（地址 0x48，周期打印 AIN0 电压） |
| 扬声器 | 4Ω 3W，AXP173 EXTEN 控制功放 |
| 电池 | 3.7V 锂电池 |
| 网络 | WiFi + ML307 4G（双网络可切换） |
| 按键 | 3 个：电源键（AXP173 PEK）/ 唤醒键（BOOT）/ 音量+键 |
| LED | RGB 指示灯 + 充电指示灯 |

## 功能特性

- **语音唤醒**：「你好小智」
- **设备端 AEC**：ES8311 + ES7210 硬件回声消除电路 + AFE 软件算法优化
  - AEC 滤波器长度 8，VOIP 高性能模式
  - AGC 自动增益控制，抑制残留回声
  - 播放期间软件残留回声抑制（-8.5dB 衰减）
- **双网络模式**：默认使用 WiFi，启动时按 BOOT 键切换 WiFi / 4G
- **节能管理**：电池供电时自动休眠/关机，充电时阻止休眠
- **电源管理**：LDO3/LDO4 上电后配置为 3.3V，长按关机后仍可充电
- **MCP 工具**：支持 MCP 协议扩展工具
- **OTA 更新**：固件同步官方 OTA 更新（自编译固件需手动更新）
- **PC 原始流模式**：通过 UDP 上传三通道原始音频和喉振 ADC，并支持 PC 向设备扬声器下发 PCM
- **标准表情界面**：使用普通 LCD 表情显示，不包含动态眼睛素材和相关配置

## 按键说明

| 按键 | 短按 | 长按 | 双击 |
|------|------|------|------|
| 电源键（PEK） | 开机 / 音量- | 4s 关机 | — |
| BOOT 键 | 唤醒/停止对话（启动时切换网络） | 恢复出厂设置并进入 WiFi 配网 | 切换 AEC 开关 |
| 音量+键 | 音量+10 | 静音 | — |

## 使用说明

本产品适用于 Vibratalkie 小智AI聊天机器人控制板套件，当前版本不启用摄像头。

首次启动或清除网络配置后，设备会创建名称为 `vibratalkie-XXXX` 的 WiFi 配网热点，
其中 `XXXX` 是设备 MAC 地址的后四位。手机连接该热点后，访问
`http://192.168.4.1` 完成网络配置。

## 移植说明（自编译固件）

### 1. 添加板级代码

下载附件中的代码，覆盖到完整项目代码中。

本仓库已将构建所需的托管组件复制到 `components/`，并通过
`main/idf_component.yml` 的 `override_path` 使用本地组件，重新配置或编译时不会因
`managed_components/` 被重新生成而丢失定制组件。

### 2. 编辑 `main/CMakeLists.txt`

在 `# 根据 BOARD_TYPE 配置添加对应的板级文件` 下添加：

```cmake
set(BOARD_TYPE "vibratalkie")
```

### 3. 编辑 `main/Kconfig.projbuild`

在 `# Board type. 开发板类型` 下添加：

```kconfig
config BOARD_TYPE_VIBRATALKIE
    bool "Vibratalkie"
    depends on IDF_TARGET_ESP32S3
```

在 `config USE_DEVICE_AEC` 的 `depends on` 中添加 `BOARD_TYPE_VIBRATALKIE`。

### 4. 编译烧录

```bash
# 配置编译目标
idf.py set-target esp32s3

# 打开 menuconfig
idf.py menuconfig
# 选择: Xiaozhi Assistant → Board Type → Vibratalkie
# 按 S 保存，按 Q 退出

# 编译
idf.py build

# 烧录
idf.py flash

# 监视串口输出
idf.py monitor
```

如果提示串口被占用，请先关闭正在运行的 `idf.py monitor`、串口助手或其他烧录任务，
重新插拔设备后使用 `ls /dev/ttyACM* /dev/ttyUSB*` 确认实际端口，再执行：

```bash
idf.py -p /dev/ttyACM0 flash
```

## PC 原始音频与 ADC 模式

在 `idf.py menuconfig` 中进入：

```text
Xiaozhi Assistant → VIBRATALKIE_CONFIG → Enable PC raw audio/ADC server mode
```

启用后，设备通过 UDP 9999 自动发现同一局域网中的上位机，上传 24 kHz 三通道原始
PCM 和带时间戳的 ADS1115 数据，也可接收 PC 下发的扬声器 PCM。项目提供命令行工具、
GUI、训练数据导出工具以及完整协议说明：

- [PC 工具使用说明](pc_tools/README.md)
- [上位机 UDP 协议说明](pc_tools/PC_RAW_STREAM_PROTOCOL.md)
- [采集数据说明](pc_tools/pc_stream_data说明.md)
- [ADS1115 更换为 CM1103 说明](docs/RUIO/CM1103更换说明.md)

## GPIO 引脚分配

| 功能 | 引脚 |
|------|------|
| I2C SDA | GPIO2 |
| I2C SCL | GPIO3 |
| I2S MCLK | GPIO41 |
| I2S BCLK | GPIO42 |
| I2S WS | GPIO40 |
| I2S DOUT | GPIO39 |
| I2S DIN | GPIO45 |
| 显示屏 MOSI | GPIO8 |
| 显示屏 CLK | GPIO9 |
| 显示屏 DC | GPIO6 |
| 显示屏 RST | GPIO7 |
| 显示屏 CS | GPIO5 |
| 显示屏背光 | GPIO4 |
| BOOT 按键 | GPIO0 |
| 音量+按键 | GPIO1 |
| LED | GPIO38 |
| AXP173 IRQ | GPIO46 |
| 4G TX | GPIO43 |
| 4G RX | GPIO44 |

> 更多引脚定义请参考 [Vibratalkie config.h](main/boards/vibratalkie/config.h)。

## 整片固件备份与恢复

执行前先加载 ESP-IDF 环境，并按实际情况修改串口：

```bash
source ~/esp/v5.5.2/esp-idf/export.sh

# 备份 Flash 前 8 MiB
python -m esptool --chip esp32s3 --port /dev/ttyACM0 \
  read_flash 0x0 0x800000 firmware_backup.bin

# 恢复备份；该操作会先擦除整片 Flash
idf.py -p /dev/ttyACM0 erase-flash
python -m esptool --chip esp32s3 --port /dev/ttyACM0 \
  write_flash 0x0 firmware_backup.bin
```
