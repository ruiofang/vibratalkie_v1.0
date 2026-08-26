# Vibratalkie PC 本地音频工具

本目录可以单独复制到电脑，用于发现设备、实时监听、保存本地音频与喉振 ADC，
以及生成音频/ADC 同步训练窗口。

## 文件

- `pc_raw_stream_server.py`：UDP 9999 发现设备、接收音频和 ADC、实时监听、保存采集数据，以及把 PC 麦克风声音回传给设备。
- `pc_audio_gui.py`：图形化调试工具，可在接收、监听和保存数据的同时随时选择音频下发到设备。
- `pc_stream_dataset.py`：读取 `pc_stream_data`，按共同时间轴生成 NumPy/PyTorch 训练窗口或导出 NPZ。
- `pc_stream_data说明.md`：采集目录、各字段、时间对齐、质量检查和训练模型导入说明。
- `requirements.txt`：PC Python 依赖。

## 安装与运行

```bash
cd pc_tools
python3 -m pip install -r requirements.txt
python3 pc_raw_stream_server.py
```

默认数据统一保存到本目录下按启动时间创建的独立会话，例如：

```text
pc_tools/pc_stream_data/cli_20260827_011519/
```

即使从工程根目录运行 `python3 pc_tools/pc_raw_stream_server.py`，保存位置也不会改变。
GUI 对应使用 `gui_YYYYMMDD_HHMMSS/`，因此命令行和 GUI 都不会覆盖上一轮采集。

仅采集、不播放：

```bash
python3 pc_raw_stream_server.py --no-play
```

向设备扬声器下发 OGG、MP3 或 WAV：

```bash
sudo apt install ffmpeg
python3 pc_raw_stream_server.py --play-file test.ogg
```

多个文件可以重复指定并按顺序播放：

```bash
python3 pc_raw_stream_server.py \
  --play-file first.mp3 \
  --play-file second.wav
```

PC 工具会统一转换为设备支持的 24 kHz、单声道、signed 16-bit PCM。PC 服务会持续
每 2 秒发送一次低频连接确认，因此设备或 PC 服务任一端重启后都会自动重新连接。

## GUI 调试工具

Ubuntu 首次使用安装 Tkinter 和 FFmpeg：

```bash
sudo apt install python3-tk ffmpeg
python3 -m pip install -r requirements.txt
```

启动界面：

```bash
python3 pc_audio_gui.py
```

点击“启动接收”后，界面会显示设备地址、接收包数、推测丢包、ADC 数量和设备重启次数。
采集过程中可点击“选择并播放 OGG / MP3 / WAV”加入播放队列，接收和数据保存不会暂停。
点击“停止文件播放”会取消当前任务并清空旧队列，随后可以立即重新选择文件下发。
关闭窗口或点击“停止并保存”时，请等待 WAV 文件头、ADC 音频对应列及元数据写入完成。

“下发电脑麦克风 → 设备扬声器”是独立的动态开关：服务运行期间勾选会立即启动下发，
取消会立即停止，不需要重启接收服务。GUI 会显示“麦克风下发TX、错误、峰值”：

- `麦克风下发TX` 持续增加且峰值随说话变化，表示 PC 正在向设备扬声器发送声音。
- TX 为 0 时检查运行日志；没有 `sounddevice` 时程序会自动尝试 FFmpeg PulseAudio/ALSA。
- 峰值长期为 0 时，在“输入设备”中填写正确的 PulseAudio/ALSA 设备名，或检查系统录音权限。
- 选择文件下发期间会暂时停止麦克风包，避免两路 PCM 在设备端交错；文件结束后自动恢复。

“播放设备 MIC1 → 电脑扬声器”也是动态开关。取消后只停止电脑本地扬声器播放，设备
上传的三通道 WAV、MIC1 WAV 和 ADC 仍会继续接收保存；再次勾选即可恢复电脑监听。
它与“下发电脑麦克风 → 设备扬声器”是两个相反方向、互相独立的功能。

命令行指定输入设备：

```bash
python3 pc_raw_stream_server.py --send-mic --input-device default
```

每次采集建议使用独立目录：

```bash
python3 pc_raw_stream_server.py --output-dir pc_stream_data/session_001
```

导出同步训练数据：

```bash
python3 pc_stream_dataset.py pc_stream_data/session_001 \
  --window-seconds 1.0 \
  --hop-seconds 0.5 \
  --export-npz session_001.npz
```

PC 和设备应处于同一局域网，并允许防火墙入站和出站 UDP 9999。

设备连接成功并进入 PC 原始流待机状态后，短按设备 BOOT 键可将扬声器音量降低
10%（最低 0%）；右侧按键增加 10%（最高 100%）。BOOT 长按仍用于清除网络配置并重新配网。
