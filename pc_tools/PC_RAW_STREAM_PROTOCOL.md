# Vibratalkie PC Raw Stream UDP 协议说明

本文档面向自行开发 Vibratalkie 上位机的用户，描述固件 `PC Raw Stream` 模式使用的
UDP 协议。当前协议版本为 **VTK1 / Version 1**。

参考实现：

- 设备端：`main/pc_raw_stream_service.cc`
- PC 端：`pc_tools/pc_raw_stream_server.py`
- GUI：`pc_tools/pc_audio_gui.py`

## 1. 功能与默认参数

协议支持：

- 设备向 PC 上传原始多通道 PCM 音频；
- 设备向 PC 上传带独立时间戳的喉振 ADC 原始值；
- PC 向设备扬声器下发单声道 PCM；
- 通过 UDP 广播自动发现设备，随后切换为单播；
- 使用设备单调时钟对音频和 ADC 做时间对齐。

当前 Vibratalkie PC Stream 固件默认参数：

| 项目 | 当前值 |
|---|---|
| 传输层 | IPv4 UDP |
| 设备与 PC 端口 | `9999`，可由固件配置修改 |
| 协议魔数 | ASCII `VTK1` |
| 协议版本 | `1` |
| 上行音频 | 24000 Hz、3 通道、signed int16、10 ms/包 |
| 上行通道顺序 | `MIC1, REF, MIC2` |
| ADC | 默认目标 800 Hz、signed int16，约 50 包/秒 |
| 下行播放 | 24000 Hz、单通道、signed int16，推荐 10 ms/包 |

上位机应以包头中的 `sample_rate`、`channels` 和 `sample_width` 为准，不要把当前默认值
硬编码到上行解析器中。下行播放格式必须与设备输出格式一致；当前固件仅接受
24000 Hz、单通道、16 位 PCM。

## 2. 连接与发现流程

设备和 PC 应处于同一 IPv4 局域网，防火墙需允许双向 UDP 9999。

```text
设备                                         PC 上位机
  |                                             |
  |-- Type 5 Device Discovery，广播 ---------->|
  |-- Type 1/2 数据，初始目标可为广播 -------->|
  |                                             |
  |<--------- Type 4 Server Hello，单播 --------|
  |                                             |
  |-- Type 1/2 音频与 ADC，切换为单播 -------->|
  |<--------- Type 4，每约 2 秒保活 ------------|
  |<--------- Type 3 播放 PCM（可选） ----------|
```

具体规则：

1. 设备创建 UDP socket，绑定 `0.0.0.0:9999`。
2. 默认情况下，设备把上行数据发往 `255.255.255.255:9999`。
3. ADC 正常读取时，设备约每 2 秒额外广播一个 `Device Discovery` 包。
4. PC 收到任意合法设备包后，必须从自己的监听 socket 向该包的源地址回复
   `Server Hello`。不要另建随机源端口发送，否则设备会把流切换到该随机端口。
5. 设备收到 `Server Hello` 后，将后续音频和 ADC 单播到该 Hello 的源 IP、源端口。
6. PC 建议前 5 个设备包都回复 Hello，之后至少每 2 秒回复一次，以便任一端重启后恢复。

设备仍会周期性发送广播发现包。一个设备同一时刻只记录一个 PC 目标；多个上位机持续
发送 Hello 会互相抢占流目标，因此同一广播域内建议一个设备只运行一个上位机实例。

## 3. UDP 数据报格式

每个 UDP 数据报由固定 32 字节头部和可变长度 payload 组成：

```text
+--------------------------+----------------------+
| 32-byte VTK1 header      | payload              |
+--------------------------+----------------------+
0                          32                     32 + payload_size
```

### 3.1 固定头部

多字节头部字段全部使用 **网络字节序（big-endian）**。

| 偏移 | 长度 | 类型 | 字段 | 说明 |
|---:|---:|---|---|---|
| 0 | 4 | bytes | `magic` | 固定为 ASCII `VTK1`，十六进制 `56 54 4B 31` |
| 4 | 1 | uint8 | `version` | 当前为 `1` |
| 5 | 1 | uint8 | `type` | 包类型，见下表 |
| 6 | 2 | uint16 BE | `flags` | 类型相关标志，未定义位必须为 0 |
| 8 | 8 | uint64 BE | `timestamp_us` | 微秒时间戳 |
| 16 | 4 | uint32 BE | `sequence` | 该发送方向的包序号，溢出后回到 0 |
| 20 | 4 | uint32 BE | `sample_rate` | 每秒采样帧/样本数；非媒体包为 0 |
| 24 | 2 | uint16 BE | `channels` | 音频通道数；ADC 当前为 1；非媒体包为 0 |
| 26 | 2 | uint16 BE | `sample_width` | 单个样本的字节数，当前 PCM/ADC 均为 2 |
| 28 | 4 | uint32 BE | `payload_size` | payload 字节数 |

接收端至少应检查：

```text
len(datagram) >= 32
magic == "VTK1"
version == 1
len(datagram) == 32 + payload_size
```

不能直接把网络数据强制转换为本机 C struct；需要显式按大端读取头部字段，同时避免
结构体对齐问题。

### 3.2 包类型

| type | 名称 | 方向 | payload |
|---:|---|---|---|
| 1 | `Audio Capture` | 设备 → PC | 交错排列的原始 PCM |
| 2 | `ADC Capture` | 设备 → PC | 一个或多个 ADC 记录 |
| 3 | `Audio Playback` | PC → 设备 | 交错排列的原始 PCM |
| 4 | `Server Hello` | PC → 设备 | 空 |
| 5 | `Device Discovery` | 设备 → PC | 空 |

未知类型应忽略，以便未来扩展。

## 4. Type 1：设备上行音频

头部字段：

| 字段 | 当前值/含义 |
|---|---|
| `flags` | 0 |
| `timestamp_us` | 包内第一个音频帧在设备启动时间轴上的近似时间 |
| `sample_rate` | 当前 24000 |
| `channels` | 当前 3 |
| `sample_width` | 当前 2 |
| `payload_size` | 当前 `240 帧 × 3 通道 × 2 = 1440` 字节 |

payload 为 **signed int16 little-endian PCM**，按帧、按通道交错：

```text
frame 0: MIC1_0, REF_0, MIC2_0
frame 1: MIC1_1, REF_1, MIC2_1
...
```

通用帧数计算：

```text
frame_bytes = channels * sample_width
frames = payload_size / frame_bytes
duration_us = frames * 1_000_000 / sample_rate
```

解析前必须验证 `channels > 0`、`sample_width == 2`，并确认
`payload_size % (channels * sample_width) == 0`。

当前完整 UDP payload 为 `32 + 1440 = 1472` 字节，加上 UDP/IP 头后正好为常见
1500 字节以太网 MTU，因此不会发生 IPv4 分片。不要无必要地增大包长。

## 5. Type 2：设备上行 ADC

当前固件设置 `flags & 0x0001 != 0`，表示 payload 由多个带时间戳的 ADC 记录组成。

### 5.1 flags bit 0：带时间戳记录

每条记录固定 10 字节：

| 记录偏移 | 长度 | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 8 | uint64 BE | ADC 样本的设备微秒时间戳 |
| 8 | 2 | int16 LE | ADC signed 16-bit 原始码 |

注意：**记录时间戳是大端，但 ADC 原始值是小端**。头部 `timestamp_us` 等于该包第一条
ADC 记录的时间戳。`payload_size` 必须是 10 的整数倍。

默认 ADC 目标频率为 800 Hz，设备按 `ADC_RATE / 50` 条记录组包，即通常每包 16 条、
约 50 包/秒。每条记录有独立时间戳，不应根据包到达时间或固定 1/800 秒间隔反推。

`raw` 是 ADS1115 原始码，不是电压值。若需要换算电压，必须结合设备 PGA/量程配置。

### 5.2 旧格式兼容

早期固件可能发送：

- `flags == 0`
- `payload_size == 2`
- payload 为单个 int16 little-endian ADC 值
- 样本时间使用头部 `timestamp_us`

新上位机可保留此兼容分支，但发送端应使用带时间戳的新格式。

## 6. Type 3：PC 下行扬声器 PCM

当前设备接受：

| 字段 | 必须值 |
|---|---|
| `flags` | 0 |
| `sample_rate` | 24000 |
| `channels` | 1 |
| `sample_width` | 2 |
| payload | signed int16 little-endian PCM |

推荐每包发送 240 个样本，即 10 ms、480 字节 payload。发送端应按真实音频时长节流：

```text
发送间隔 = 样本数 / 24000 秒
```

设备接收缓冲区总长为 4096 字节，因此下行 `payload_size` 不得超过 4064 字节；实际
开发建议始终使用 480 字节的 10 ms 包。设备播放队列有限，突发发送或不按实时速率
节流会造成丢包。当前协议没有播放 ACK、重传、暂停、结束或音量控制消息；停止发送后，
设备播放完队列中的 PCM 即停止。

下行 `timestamp_us` 可填 PC 单调时钟微秒值，设备目前不使用它做网络时钟同步。
不要同时交错发送麦克风 PCM 和文件 PCM，否则两路样本会在设备播放队列中混合排列。

## 7. Type 4：Server Hello

PC 使用自己的监听 socket 向设备包的源地址发送：

| 字段 | 值 |
|---|---|
| `type` | 4 |
| `flags` | 0 |
| `timestamp_us` | PC 单调时钟微秒值，设备当前不依赖此值 |
| `sequence` | PC → 设备方向的 uint32 序号 |
| `sample_rate/channels/sample_width` | 0 |
| `payload_size` | 0 |

设备以 UDP 包的实际源 IP 和源端口作为新的上位机地址，而不是读取 payload 中的地址。

## 8. Type 5：Device Discovery

设备约每 2 秒向 `255.255.255.255:端口` 广播空 payload 的发现包。媒体相关字段为 0，
`timestamp_us` 和 `sequence` 使用设备上行时间轴及序号。PC 收到后按第 2 节回复 Hello。

上位机也可以对收到的 Type 1 或 Type 2 包回复 Hello，不必等待 Type 5。

## 9. 序号、丢包、乱序与设备重启

### 9.1 序号

- 设备 → PC 和 PC → 设备各自维护独立的 uint32 序号；
- 设备上行 Type 1、Type 2、Type 5 共用同一个序号空间；
- PC 下行 Type 3、Type 4 共用另一个序号空间；
- 序号按模 `2^32` 回绕；
- 协议不提供 ACK 或重传。

统计上行丢包时，应先接收所有合法设备包再统计序号。如果只观察音频包，ADC 和发现包
占用的序号会被错误计算为音频丢包。UDP 可能乱序，建议结合序号和时间戳维护短重排缓冲。

### 9.2 时间戳与音频重建

设备时间戳来自 `esp_timer_get_time()`，单位微秒，从本次设备启动开始单调递增。它不是
Unix 时间，也没有和 PC 墙上时钟同步。

建议将第一包音频的设备时间戳记为 `origin_us`，后续包的目标帧位置为：

```text
target_frame = round((packet_timestamp_us - origin_us) * sample_rate / 1_000_000)
```

- `target_frame` 晚于当前输出位置：中间补静音；
- `target_frame` 早于当前输出位置：裁掉重叠帧或丢弃完全过期包；
- 建议先按时间戳做约 80 ms 的短重排，再写入音频时间轴；
- ADC 对齐使用每条 ADC 记录自己的设备时间戳，不使用 PC 收包时间。

ADC 对应音频帧：

```text
audio_frame = round((adc_timestamp_us - origin_us) * audio_sample_rate / 1_000_000)
```

### 9.3 设备重启

设备重启后时间戳和上行序号都会从较小值重新开始。参考 PC 实现把时间戳向后跳变超过
5 秒视为设备重启，并清空序号/乱序状态、重新发送 Hello。应用可以新建采集文件，也可以
像参考实现一样续接时间轴。

## 10. 最小 Python 接收与发现示例

下面示例演示包头解析、发现响应、音频解包和 ADC 解包。生产软件还应加入乱序缓冲、
文件写入线程、设备重启检测和更完整的异常统计。

```python
import socket
import struct
import time

MAGIC = b"VTK1"
VERSION = 1
TYPE_AUDIO_CAPTURE = 1
TYPE_ADC_CAPTURE = 2
TYPE_SERVER_HELLO = 4
FLAG_ADC_TIMESTAMPED_RECORDS = 1

# ! = network byte order (big-endian), total 32 bytes
HEADER = struct.Struct("!4sBBHQIIHHI")


def make_hello(sequence: int) -> bytes:
    timestamp_us = time.monotonic_ns() // 1000
    return HEADER.pack(
        MAGIC, VERSION, TYPE_SERVER_HELLO, 0,
        timestamp_us, sequence, 0, 0, 0, 0,
    )


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("0.0.0.0", 9999))

pc_sequence = 0
while True:
    datagram, device_address = sock.recvfrom(65535)
    if len(datagram) < HEADER.size:
        continue

    fields = HEADER.unpack_from(datagram)
    magic, version, packet_type, flags, timestamp_us, sequence, \
        sample_rate, channels, sample_width, payload_size = fields

    if magic != MAGIC or version != VERSION:
        continue
    if len(datagram) != HEADER.size + payload_size:
        continue

    # 必须从同一个监听 socket 回复，设备会记住该源 IP/端口。
    sock.sendto(make_hello(pc_sequence), device_address)
    pc_sequence = (pc_sequence + 1) & 0xFFFFFFFF
    payload = datagram[HEADER.size:]

    if packet_type == TYPE_AUDIO_CAPTURE:
        frame_bytes = channels * sample_width
        if sample_width != 2 or channels == 0 or payload_size % frame_bytes:
            continue
        # PCM 样本是 little-endian；samples 为交错的一维 tuple。
        samples = struct.unpack(f"<{payload_size // 2}h", payload)
        frame_count = payload_size // frame_bytes
        print("audio", sequence, timestamp_us, sample_rate,
              channels, frame_count, samples[:channels])

    elif packet_type == TYPE_ADC_CAPTURE:
        if sample_width != 2:
            continue
        if flags & FLAG_ADC_TIMESTAMPED_RECORDS:
            if payload_size % 10:
                continue
            for offset in range(0, payload_size, 10):
                adc_timestamp_us = struct.unpack_from("!Q", payload, offset)[0]
                adc_raw = struct.unpack_from("<h", payload, offset + 8)[0]
                print("adc", sequence, adc_timestamp_us, adc_raw)
        elif payload_size == 2:
            adc_raw = struct.unpack("<h", payload)[0]
            print("legacy adc", sequence, timestamp_us, adc_raw)
```

实际程序不应对每个数据包都打印日志，否则可能导致接收线程阻塞和 UDP 丢包。建议接收
线程只负责校验、复制并入队，由后台线程完成解析、播放和文件写入。

## 11. PC 下行 PCM 打包示例

```python
TYPE_AUDIO_PLAYBACK = 3


def make_playback_packet(sequence: int, pcm_s16le: bytes) -> bytes:
    if not pcm_s16le or len(pcm_s16le) % 2:
        raise ValueError("PCM must contain complete signed int16 samples")
    timestamp_us = time.monotonic_ns() // 1000
    header = HEADER.pack(
        MAGIC, VERSION, TYPE_AUDIO_PLAYBACK, 0,
        timestamp_us, sequence, 24000, 1, 2, len(pcm_s16le),
    )
    return header + pcm_s16le


# pcm_s16le 推荐为 240 个样本、480 字节；每 10 ms 发送一包。
```

## 12. 健壮性与安全建议

- 丢弃 magic、version、长度或媒体格式不合法的包；
- 在分配内存前限制 `payload_size`，设备下行上限为 4064 字节；
- 不要信任广播域内的源地址或包内容；
- 接收循环不要执行阻塞式播放、解码、磁盘 flush 或大量日志输出；
- 使用单调时钟节流下行 PCM，不要使用会跳变的系统日期时间；
- 对乱序、重复包、序号回绕、设备重启和 PC 重启分别处理；
- WAV 等文件结束时应正常关闭，以便写回正确文件头；
- 协议没有应用层鉴权、加密或完整性校验，仅适合受信任局域网；
- 任意能访问设备 UDP 端口的主机都可能发送 Hello 抢占上行目标，或发送 PCM 播放声音。

如需跨公网使用，应在 VPN/受控隧道内传输，或扩展带鉴权和防重放的新协议版本，不要
直接将 UDP 端口暴露到互联网。

## 13. 版本兼容策略

上位机建议按以下规则实现：

1. magic 不匹配：忽略；
2. version 不支持：记录一次兼容性错误并忽略；
3. 已知 version 中遇到未知 type：忽略；
4. 忽略未知 flags 位，但不要误解其 payload；
5. 读取 `payload_size` 后必须与实际 UDP 数据报长度严格核对；
6. 不依赖数据包固定到达顺序、固定包长或固定 ADC 组包条数；
7. 若未来需要修改头部布局或字段语义，应增加 version，而不是静默改变 Version 1。
