# `pc_stream_data` 数据格式说明

本文档随 `pc_tools` 一起复制，用于说明设备音频、人工喉振 ADC 数据的保存格式、
时间对齐方式和训练模型导入方法。

## 目录结构

采集数据固定放在 `pc_tools` 的相对子目录 `pc_stream_data/`。程序默认每次创建独立的
`cli_YYYYMMDD_HHMMSS/` 或 `gui_YYYYMMDD_HHMMSS/` 会话目录，例如：

```text
pc_stream_data/
└── gui_20260827_011519/
    ├── audio_24000hz_3ch.wav
    ├── audio_mic1_24000hz_mono.wav
    ├── adc_raw.csv
    ├── audio_timing.csv
    └── capture_metadata.json
```

启动采集：

```bash
python3 pc_raw_stream_server.py --output-dir pc_stream_data/session_001
```

正常使用 `Ctrl+C` 结束程序后，WAV 文件头和元数据会被完整写入。

## 各文件作用

### `audio_24000hz_3ch.wav`

- 格式：24 kHz、signed 16-bit little-endian PCM、三通道。
- 通道顺序：`MIC1, REF, MIC2`。
- `MIC1` 和 `MIC2` 是设备麦克风；`REF` 是扬声器回采参考通道。
- 文件按照设备时间戳重建。UDP 丢包区间会补静音，避免 WAV 时间轴相对 ADC 缩短。
- 适合多通道降噪、回声消除或保留全部原始信号的训练任务。

### `audio_mic1_24000hz_mono.wav`

- 从三通道数据同步提取的 `MIC1`。
- 格式：24 kHz、signed 16-bit、单声道。
- 适合直接试听以及只使用一个空气麦克风的训练模型。
- 它与三通道 WAV 使用完全相同的音频帧时间轴。

### `adc_raw.csv`

保存人工喉振传感器的 ADS1115 原始数据。默认采集目标频率为 800 Hz。

| 字段 | 含义 |
|---|---|
| `device_timestamp_us` | ADC 样本在设备启动时间轴上的微秒时间戳 |
| `sequence` | 该样本所属 UDP 数据包的连续序号 |
| `raw` | ADS1115 signed 16-bit 原始码 |
| `audio_frame_index` | 此 ADC 样本对应的 WAV 音频帧位置 |
| `audio_time_s` | 相对 WAV 起点的时间，单位秒 |
| `mic1_raw` | `audio_frame_index` 位置的 MIC1 signed 16-bit PCM 原始值 |
| `ref_raw` | `audio_frame_index` 位置的扬声器参考通道 PCM 原始值 |
| `mic2_raw` | `audio_frame_index` 位置的 MIC2 signed 16-bit PCM 原始值 |

对齐关系：

```text
audio_time_s = audio_frame_index / 24000
```

例如 `audio_frame_index=24000` 表示该 ADC 样本对应音频文件第 1 秒的位置。
PC 服务先等待对应音频通过约 80 ms 的 UDP 重排序缓冲，然后实时把三路音频值和 ADC
写到同一行；因此 CSV 尾部通常会比最新 ADC 晚约 80–100 ms。正常结束时还会从已经按
设备时间轴重建的三通道 WAV 统一校验和回填。做 800 Hz 点对点特征训练时可以直接读取
同一行，不再查询 WAV。UDP 丢包补静音位置对应的音频值为 0。程序被强制断电或
`kill -9` 时，最后少量数据可能尚未写入或仍为空。

三路字段是 ADC 时刻对应的单个音频采样点，不代表该 ADC 周期内完整的 24 kHz 波形。
训练语音波形模型时仍应使用 `audio_frame_index` 和 `pc_stream_dataset.py` 读取 WAV，
否则把音频降到 800 Hz 会丢失 400 Hz 以上的信息。不要使用 UDP 包到达电脑的时间对齐。

### `audio_timing.csv`

记录服务端如何依据设备时间戳重建音频时间轴。

| 字段 | 含义 |
|---|---|
| `device_timestamp_us` | 音频包的设备时间戳 |
| `sequence` | UDP 包序号 |
| `target_frame` | 根据时间戳计算出的目标 WAV 帧 |
| `written_frame` | 写入该数据前的 WAV 帧位置 |
| `source_frames` | 原始包中的音频帧数 |
| `gap_frames` | 网络丢包或延迟造成的补静音帧数 |
| `trimmed_frames` | 包重叠时裁掉的帧数 |

该文件主要用于排查 Wi-Fi 抖动和丢包，一般不直接作为训练输入。

### `capture_metadata.json`

记录本次采集的总体信息和质量指标，主要字段如下：

| 字段 | 含义 |
|---|---|
| `audio_origin_device_timestamp_us` | WAV 第 0 帧对应的设备时间戳 |
| `sample_rate` | 音频采样率，当前为 24000 Hz |
| `channels` | 原始音频通道数，当前为 3 |
| `channel_order` | 通道顺序，当前为 `MIC1, REF, MIC2` |
| `audio_frames` | WAV 总帧数 |
| `inserted_gap_frames` | 为保持时间轴而插入的静音帧数 |
| `trimmed_overlap_frames` | 因数据包时间重叠而裁掉的帧数 |
| `estimated_udp_packets_lost` | 根据序号估算的 UDP 丢包数 |
| `adc_samples` | 收到的 ADC 样本总数 |
| `adc_effective_rate_hz` | 根据时间戳计算的实际 ADC 接收频率 |
| `device_restart_count` | 本次 PC 采集期间检测到并自动续接的设备重启次数 |

建议训练前确认：

- `adc_effective_rate_hz` 接近 800 Hz。
- `inserted_gap_frames / audio_frames` 尽量低于 1%。
- `estimated_udp_packets_lost` 尽量接近 0。
- ADC CSV 中存在有效的 `audio_frame_index`。

## 导出 NumPy 训练数据

安装依赖：

```bash
python3 -m pip install numpy
```

按 1 秒窗口、0.5 秒步长导出：

```bash
python3 pc_stream_dataset.py pc_stream_data/session_001 \
  --window-seconds 1.0 \
  --hop-seconds 0.5 \
  --export-npz session_001.npz
```

NPZ 内容：

```text
audio:             [窗口数, 1, 24000]，float32
adc:               [窗口数, 1, 800]，float32
audio_start_frame: [窗口数]，int64
audio_sample_rate: 标量，通常为 24000
adc_sample_rate:   标量，通常接近 800
```

默认使用 `MIC1` 单声道，并移除每次采集 ADC 的中值直流偏置。需要保留全部音频通道时增加：

```bash
--all-channels
```

此时 `audio` 形状为 `[窗口数, 3, 24000]`。如果需要保留 ADC 绝对压力值，增加：

```bash
--keep-adc-dc
```

## 直接导入 PyTorch

从项目根目录运行训练代码时：

```python
from torch.utils.data import DataLoader
from pc_tools.pc_stream_dataset import PcStreamDataset

dataset = PcStreamDataset(
    "pc_tools/pc_stream_data/session_001",
    window_seconds=1.0,
    hop_seconds=0.5,
    use_all_channels=False,
)

loader = DataLoader(
    dataset,
    batch_size=16,
    shuffle=True,
    num_workers=0,
)

for batch in loader:
    audio = batch["audio"]  # [B, 1, 24000]
    adc = batch["adc"]      # [B, 1, 800]
    output = model(audio, adc)
```

如果训练程序也位于 `pc_tools` 目录，可以改为：

```python
from pc_stream_dataset import PcStreamDataset
```

加载器依据 `audio_frame_index` 在共同音频时间轴上生成窗口，并将不等间隔 ADC 样本
插值到规则的 ADC 采样点。因此同一窗口内的空气麦克风和人工喉振数据保持同步。

## ADC 频率说明

ADS1115 硬件最高数据率为 860 SPS，当前目标读取频率为 800 Hz，奈奎斯特频率约为
400 Hz。它适合声带基频及低频喉部振动训练，但不能完整保存数 kHz 的语音波形和辅音细节。
如模型需要高频喉振信号，应更换至少 8–16 kSPS 的 ADC，或把传感器接入音频 Codec
的额外模拟输入通道。

## UDP 数据格式

设备和 PC 使用 UDP 9999。包头字段使用网络字节序，PCM 和 ADC 原始值使用
little-endian：

| 偏移 | 类型 | 内容 |
|---:|---|---|
| 0 | 4 bytes | 固定魔数 `VTK1` |
| 4 | uint8 | 协议版本，当前为 1 |
| 5 | uint8 | 1=音频采集，2=ADC，3=扬声器 PCM，4=PC 发现应答 |
| 6 | uint16 | flags；ADC 批量时间戳格式使用 bit0=1 |
| 8 | uint64 | 设备微秒时间戳 |
| 16 | uint32 | 连续序号 |
| 20 | uint32 | 采样率 |
| 24 | uint16 | 通道数 |
| 26 | uint16 | 每样本字节数 |
| 28 | uint32 | payload 字节数 |
| 32 | bytes | 原始 payload |

ADC 批量 payload 由若干个 10 字节记录组成：

| 记录偏移 | 类型 | 内容 |
|---:|---|---|
| 0 | uint64, network endian | 单个 ADC 样本的设备微秒时间戳 |
| 8 | int16, little endian | ADS1115 原始码 |
