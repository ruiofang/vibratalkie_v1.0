# 电脑原始音频与 ADC 服务（UDP 9999）

## 功能

`vibratalkie-pc-stream` 固件关闭云端对话协议，连接 Wi-Fi 后执行以下功能：

- 向电脑 UDP 9999 发送 ES7210 原生 PCM：24 kHz、signed 16-bit little-endian、全部输入通道、10 ms/包。当前硬件三通道顺序为 `MIC1, REF, MIC2`，不经过 AFE、混音或 Opus 编码。
- ADS1115 以硬件最高档 `860 SPS` 连续转换，固件默认以 `800 Hz` 读取 AIN0，并批量发送 signed 16-bit 喉振原始码；每个样本都保留独立的设备微秒时间戳。
- 每包携带设备启动后的微秒时间戳和连续序号。
- 配置为 `255.255.255.255` 时仅用广播发现电脑；电脑服务端收到数据后会回应，设备自动切换到单播，降低 Wi-Fi 广播丢包。
- 从 UDP 9999 接收 24 kHz、单声道、signed 16-bit PCM，并通过设备扬声器播放。

## 配置与编译

默认不需要填写电脑 IP。`CONFIG_PC_RAW_STREAM_SERVER` 保持
`255.255.255.255`，端口保持 `9999`，然后编译：

```bash
python scripts/release.py vibratalkie --name vibratalkie-pc-stream
```

也可以通过 `idf.py menuconfig` 的
`Xiaozhi Assistant -> VIBRATALKIE_CONFIG -> Enable PC raw audio/ADC server mode`
配置发现地址、端口和 ADC 采样率。新配置首次启动默认选择 Wi-Fi；如果 NVS
里已有 4G 选择，启动阶段按 BOOT 键切换到 Wi-Fi，或长按 BOOT 恢复网络设置。

## 9999 端口自动发现流程

1. PC 服务绑定 `0.0.0.0:9999`。
2. 设备连接 Wi-Fi 后，先向 `255.255.255.255:9999` 发送带 `VTK1` 包头的数据。
3. PC 从收到的数据包中取得设备 IP，并从本机 9999 端口向设备返回 `SERVER_HELLO`。
4. 设备从应答包的源地址取得 PC IP，立即把音频和 ADC 的目标地址切换为该 PC 的 `IP:9999` 单播。
5. 串口出现 `PC discovered, switching stream to unicast <PC-IP>:9999`，且屏幕目标地址由
   `TX 255.255.255.255:9999` 更新为 `PC <PC-IP>:9999`，表示发现完成。
6. 进入单播后，设备仍每 2 秒向 UDP 9999 广播一个无音频 payload 的发现心跳；PC
   服务重启或 IP 改变后会重新应答，设备自动切换到新的 PC 地址。

PC 和设备必须处于同一广播域；建议先启动 PC 服务，再启动或重启设备。系统防火墙需允许入站和出站 UDP 9999。

## 运行电脑服务端

图形化调试工具（支持接收时选择并下发音频）：

```bash
sudo apt install python3-tk ffmpeg
python pc_tools/pc_audio_gui.py
```

仅采集保存，不播放：

```bash
python pc_tools/pc_raw_stream_server.py --no-play
```

实时播放设备采集的第一路麦克风：

```bash
pip install sounddevice
python pc_tools/pc_raw_stream_server.py
```

同时把电脑麦克风回传至设备扬声器：

```bash
python pc_tools/pc_raw_stream_server.py --send-mic
```

可以通过 `--input-device` 指定输入设备。未安装 `sounddevice` 时会自动尝试 FFmpeg 的
PulseAudio 和 ALSA 后端。GUI 中“下发电脑麦克风 → 设备扬声器”可在接收服务运行期间
随时启停；“麦克风下发TX/错误/峰值”用于确认声音是否实际发送到设备。
文件下发期间麦克风回传会暂时静音，避免两路 PCM 数据交错。

GUI 的“播放设备 MIC1 → 电脑扬声器”可动态启停电脑本地监听。关闭监听不会停止
设备音频和 ADC 的接收保存，也不会影响电脑麦克风或文件向设备扬声器下发。

下发 OGG、MP3、WAV 等本地音频到设备扬声器：

```bash
sudo apt install ffmpeg
python pc_tools/pc_raw_stream_server.py --play-file test.mp3
```

可以重复使用 `--play-file` 顺序播放多个文件。PC 端通过 FFmpeg 统一解码为
24 kHz、单声道、signed 16-bit PCM 后从 UDP 9999 下发。发现应答会持续低频发送，
设备或 PC 服务重启后可自动重连。

## `pc_tools/pc_stream_data` 目录结构

命令行默认创建 `cli_YYYYMMDD_HHMMSS`，GUI 默认创建
`gui_YYYYMMDD_HHMMSS`。正常结束采集后目录如下：

```text
pc_tools/pc_stream_data/
└── cli_20260827_011519/
    ├── audio_24000hz_3ch.wav
    ├── audio_mic1_24000hz_mono.wav
    ├── adc_raw.csv
    ├── audio_timing.csv
    └── capture_metadata.json
```

各文件作用：

- `audio_24000hz_3ch.wav`：按设备时间轴重建的三通道音频，UDP 丢失区间会补静音，因此时长和 ADC 时间轴不会漂移。
- `audio_mic1_24000hz_mono.wav`：从三通道数据同步提取的 MIC1 单声道文件，适合直接试听及单麦训练，避免播放器错误解释三通道布局。
- `adc_raw.csv`：每条喉振 ADC 样本包含 `audio_frame_index` 和对应位置的
  `mic1_raw/ref_raw/mic2_raw` 三路 PCM 原始值；做 800 Hz 点对点训练时可直接按行读取。
  `audio_time_s` 是相对 WAV 起点的秒数。完整 24 kHz 波形训练仍使用 WAV。
- `audio_timing.csv`：每个音频包的位置、补帧数和重叠裁剪数，用于检查网络质量。
- `capture_metadata.json`：音频起始设备时间戳、通道顺序、补帧统计和估算丢包数。三通道顺序为 `MIC1, REF, MIC2`。
  其中 `adc_effective_rate_hz` 可用于检查实际收到的 ADC 采样率是否接近配置的 800 Hz。

实时监听默认使用 80 ms UDP 重排序缓冲和 120 ms 声卡预缓冲。如网络抖动较大：

```bash
python pc_tools/pc_raw_stream_server.py --reorder-buffer-ms 150 --playback-buffer-ms 250
```

## 导入训练模型

安装数据加载依赖：

```bash
pip install numpy
```

检查采集并按 1 秒窗口、0.5 秒步长导出 NPZ：

```bash
python pc_tools/pc_stream_dataset.py pc_tools/pc_stream_data \
  --window-seconds 1.0 \
  --hop-seconds 0.5 \
  --export-npz pc_stream_training.npz
```

导出的数组形状：

```text
audio: [窗口数, 1, 24000]   # 默认 MIC1，float32，范围约 -1~1
adc:   [窗口数, 1, 800]     # 喉振，已去直流中值，float32
audio_start_frame: [窗口数] # 每个窗口在原 WAV 中的起始点
```

需要三通道音频时增加 `--all-channels`，此时 `audio` 为
`[窗口数, 3, 24000]`，通道顺序是 `MIC1, REF, MIC2`。

直接用于 PyTorch，不必先导出 NPZ：

```python
from torch.utils.data import DataLoader
from pc_tools.pc_stream_dataset import PcStreamDataset

dataset = PcStreamDataset(
    "pc_tools/pc_stream_data",
    window_seconds=1.0,
    hop_seconds=0.5,
    use_all_channels=False,
)
loader = DataLoader(dataset, batch_size=16, shuffle=True, num_workers=0)

for batch in loader:
    audio = batch["audio"]  # [B, 1, 24000]
    adc = batch["adc"]      # [B, 1, 800]
    prediction = model(audio, adc)
```

加载器依据 `adc_raw.csv` 的 `audio_frame_index` 对齐，并把不等间隔 ADC
时间戳插值到规则的 800 Hz 窗口。默认移除 ADC 的静态直流偏置；若模型需要绝对压力值，传入
`remove_adc_dc=False` 或命令行使用 `--keep-adc-dc`。

训练前建议检查 `capture_metadata.json`：

- `adc_effective_rate_hz` 应接近 800。
- `inserted_gap_frames / audio_frames` 应尽量低于 1%。
- `estimated_udp_packets_lost` 应尽量接近 0。
- 旧版没有 `audio_frame_index` 的采集不能保证同步，应重新采集。

## UDP 包格式

所有头字段使用网络字节序，PCM/ADC payload 使用 little-endian：

| 偏移 | 类型 | 内容 |
|---:|---|---|
| 0 | 4 bytes | `VTK1` |
| 4 | uint8 | 协议版本，当前为 1 |
| 5 | uint8 | 1=采集音频，2=ADC，3=扬声器播放 PCM，4=PC 发现应答，5=设备发现心跳 |
| 6 | uint16 | flags；ADC 批量时间戳格式使用 bit0=1 |
| 8 | uint64 | 时间戳，微秒 |
| 16 | uint32 | 连续序号 |
| 20 | uint32 | 采样率 |
| 24 | uint16 | 通道数 |
| 26 | uint16 | 每样本字节数 |
| 28 | uint32 | payload 字节数 |
| 32 | bytes | 原始 payload |

ADC 批量包的 `flags bit0=1`。payload 由若干个 10 字节记录组成：

| 记录偏移 | 类型 | 内容 |
|---:|---|---|
| 0 | uint64, network endian | 该 ADC 样本的设备微秒时间戳 |
| 8 | int16, little endian | ADS1115 原始码 |

默认 `800 Hz` 的奈奎斯特频率为 `400 Hz`，适合声带基频和低频喉部振动训练。如果需要保留数 kHz 的语音波形或辅音细节，ADS1115 本身不够快，需要更换为至少 8–16 kSPS 的 ADC 或使用音频 Codec 的额外输入通道。
