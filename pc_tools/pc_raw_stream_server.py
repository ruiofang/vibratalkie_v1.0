#!/usr/bin/env python3
"""Vibratalkie standalone PC raw-stream UDP server (default port 9999)."""

from __future__ import annotations

import argparse
import array
import csv
import heapq
import json
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

MAGIC = b"VTK1"
VERSION = 1
TYPE_AUDIO_CAPTURE = 1
TYPE_ADC_CAPTURE = 2
TYPE_AUDIO_PLAYBACK = 3
TYPE_SERVER_HELLO = 4
TYPE_DEVICE_DISCOVERY = 5
FLAG_ADC_TIMESTAMPED_RECORDS = 1
ADC_RECORD = struct.Struct("!Q")
ADC_RECORD_SIZE = 10
HEADER = struct.Struct("!4sBBHQIIHHI")
TOOL_DIR = Path(__file__).resolve().parent


def make_default_output_dir(prefix: str = "cli") -> Path:
    session = datetime.now().strftime(f"{prefix}_%Y%m%d_%H%M%S")
    candidate = TOOL_DIR / "pc_stream_data" / session
    suffix = 1
    while candidate.exists():
        candidate = TOOL_DIR / "pc_stream_data" / f"{session}_{suffix:02d}"
        suffix += 1
    return candidate


def make_packet(packet_type: int, sequence: int, timestamp_us: int, sample_rate: int,
                channels: int, sample_width: int, payload: bytes) -> bytes:
    return HEADER.pack(MAGIC, VERSION, packet_type, 0, timestamp_us, sequence,
                       sample_rate, channels, sample_width, len(payload)) + payload


def first_channel(payload: bytes, channels: int) -> bytes:
    if channels == 1:
        return payload
    samples = array.array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()
    mono = array.array("h", samples[0::channels])
    if sys.byteorder != "little":
        mono.byteswap()
    return mono.tobytes()


class PcRawStreamServer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((args.bind, args.port))
        self.sock.settimeout(0.5)
        self.device_address: tuple[str, int] | None = None
        self.hello_address: tuple[str, int] | None = None
        self.hello_remaining = 0
        self.last_hello_sent = 0.0
        self.last_device_seen = 0.0
        self.last_device_timestamp_us: int | None = None
        self.device_restart_count = 0
        self.running = True
        self.output_stream = None
        self.monitor_enabled = not args.no_play
        self.playback_buffer = bytearray()
        self.playback_lock = threading.Lock()
        self.playback_started = False
        self.playback_sample_rate = 0
        self.last_playback_status_time = 0.0
        self.input_stream = None
        self.mic_enabled = False
        self.mic_lock = threading.Lock()
        self.mic_capture_thread: threading.Thread | None = None
        self.mic_capture_process: subprocess.Popen[bytes] | None = None
        self.mic_packets_sent = 0
        self.mic_send_errors = 0
        self.mic_peak = 0
        self.last_mic_sent = 0.0
        self.file_playback_thread: threading.Thread | None = None
        self.file_playback_process: subprocess.Popen[bytes] | None = None
        self.file_playback_queue: queue.Queue[Path] = queue.Queue()
        self.file_playback_cancel = threading.Event()
        self.file_playback_lock = threading.Lock()
        self.file_playback_generation = 0
        for audio_path in getattr(args, "play_file", []):
            self.file_playback_queue.put(audio_path)
        self.wav_file: wave.Wave_write | None = None
        self.mono_wav_file: wave.Wave_write | None = None
        self.audio_path: Path | None = None
        self.wav_format: tuple[int, int] | None = None
        self.audio_heap: list[tuple[int, int, bytes, int, int]] = []
        self.max_audio_timestamp_us = 0
        self.last_audio_arrival = 0.0
        self.audio_origin_us: int | None = None
        self.audio_written_frames = 0
        self.audio_gap_frames = 0
        self.audio_trimmed_frames = 0
        self.pending_adc: list[tuple[int, int, int]] = []
        self.aligned_adc_heap: list[tuple[int, int, int, int]] = []
        self.audio_cache = bytearray()
        self.audio_cache_start_frame = 0
        self.audio_cache_channels = 0
        self.adc_samples = 0
        self.adc_first_timestamp_us: int | None = None
        self.adc_last_timestamp_us: int | None = None
        self.sequence = 0
        self.sequence_lock = threading.Lock()
        self.last_sequence: int | None = None
        self.packet_count = 0
        self.lost_count = 0

        args.output_dir.mkdir(parents=True, exist_ok=True)
        self.adc_handle = (args.output_dir / "adc_raw.csv").open("w", newline="", buffering=1)
        self.adc_writer = csv.writer(self.adc_handle)
        self.adc_writer.writerow(("device_timestamp_us", "sequence", "raw",
                                  "audio_frame_index", "audio_time_s",
                                  "mic1_raw", "ref_raw", "mic2_raw"))
        self.timing_handle = (args.output_dir / "audio_timing.csv").open(
            "w", newline="", buffering=1)
        self.timing_writer = csv.writer(self.timing_handle)
        self.timing_writer.writerow(("device_timestamp_us", "sequence", "target_frame",
                                     "written_frame", "source_frames", "gap_frames",
                                     "trimmed_frames"))

        try:
            import sounddevice as sd  # type: ignore
            self.sd = sd
        except ImportError:
            self.sd = None
            if not args.no_play:
                self.monitor_enabled = False
                self.args.no_play = True
                print("提示: 未安装 sounddevice，仅保存数据；实时音频请运行 pip install sounddevice")

    def open_audio_outputs(self, sample_rate: int, capture_channels: int) -> None:
        fmt = (sample_rate, capture_channels)
        if self.wav_format == fmt:
            return
        if self.wav_file:
            self.wav_file.close()
        path = self.args.output_dir / f"audio_{sample_rate}hz_{capture_channels}ch.wav"
        self.audio_path = path
        self.wav_file = wave.open(str(path), "wb")
        self.wav_file.setnchannels(capture_channels)
        self.wav_file.setsampwidth(2)
        self.wav_file.setframerate(sample_rate)
        mono_path = self.args.output_dir / f"audio_mic1_{sample_rate}hz_mono.wav"
        if self.mono_wav_file:
            self.mono_wav_file.close()
        self.mono_wav_file = wave.open(str(mono_path), "wb")
        self.mono_wav_file.setnchannels(1)
        self.mono_wav_file.setsampwidth(2)
        self.mono_wav_file.setframerate(sample_rate)
        self.wav_format = fmt
        self.audio_cache.clear()
        self.audio_cache_start_frame = self.audio_written_frames
        self.audio_cache_channels = capture_channels
        print(f"保存音频: {path}")
        print(f"保存监听声道: {mono_path}")

        self.reset_monitor_output(sample_rate)

    def reset_monitor_output(self, sample_rate: int) -> None:
        if self.output_stream:
            self.output_stream.stop()
            self.output_stream.close()
            self.output_stream = None
        with self.playback_lock:
            self.playback_buffer.clear()
        self.playback_started = False
        self.playback_sample_rate = sample_rate
        if self.monitor_enabled and self.sd:
            blocksize = max(1, sample_rate * self.args.playback_block_ms // 1000)
            self.output_stream = self.sd.RawOutputStream(
                samplerate=sample_rate, channels=1, dtype="int16",
                blocksize=blocksize, latency="high", callback=self.playback_callback)

    def set_monitor_playback(self, enabled: bool) -> None:
        """Dynamically control device MIC1 -> PC speaker monitoring."""
        self.monitor_enabled = enabled
        self.args.no_play = not enabled
        if enabled and self.sd is None:
            self.monitor_enabled = False
            self.args.no_play = True
            print("电脑监听无法开启：未安装 sounddevice", file=sys.stderr)
            return
        sample_rate = self.wav_format[0] if self.wav_format else 24000
        self.reset_monitor_output(sample_rate)
        if enabled:
            print("设备 MIC1 → 电脑扬声器实时监听已开启")
        else:
            print("设备 MIC1 → 电脑扬声器实时监听已关闭，采集保存继续")

    def playback_callback(self, outdata, frames, time_info, status) -> None:
        del frames, time_info
        now = time.monotonic()
        if status and now - self.last_playback_status_time >= 2.0:
            print(f"电脑播放状态: {status}", file=sys.stderr)
            self.last_playback_status_time = now

        output_size = len(outdata)
        with self.playback_lock:
            available = min(output_size, len(self.playback_buffer))
            if available:
                outdata[:available] = self.playback_buffer[:available]
                del self.playback_buffer[:available]
            if available < output_size:
                outdata[available:] = bytes(output_size - available)

    def queue_playback(self, payload: bytes) -> None:
        if not self.output_stream:
            return

        start_stream = False
        with self.playback_lock:
            self.playback_buffer.extend(payload)
            # Bound latency if the computer is temporarily unable to play audio.
            max_bytes = self.playback_sample_rate * 2 * 2
            if len(self.playback_buffer) > max_bytes:
                del self.playback_buffer[:-max_bytes]
            prebuffer_bytes = (self.playback_sample_rate * 2 *
                               self.args.playback_buffer_ms // 1000)
            if not self.playback_started and len(self.playback_buffer) >= prebuffer_bytes:
                self.playback_started = True
                start_stream = True

        if start_stream:
            try:
                self.output_stream.start()
                print(f"实时播放已启动，缓冲 {self.args.playback_buffer_ms} ms")
            except Exception:
                self.playback_started = False
                raise

    def send_server_hello(self, address: tuple[str, int]) -> None:
        """Continuously confirm the PC address so either side may restart."""
        now = time.monotonic()
        if address != self.hello_address:
            self.hello_address = address
            self.hello_remaining = 5
            self.last_hello_sent = 0.0
            local_ip = "未知"
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route_probe:
                    route_probe.connect(address)
                    local_ip = route_probe.getsockname()[0]
            except OSError:
                pass
            print(f"发现设备 {address[0]}:{address[1]}，从电脑 "
                  f"{local_ip}:{self.args.port} 回应，设备将切换为单播")
        if self.hello_remaining <= 0 and now - self.last_hello_sent < 2.0:
            return
        packet = make_packet(TYPE_SERVER_HELLO, self.next_sequence(),
                             time.monotonic_ns() // 1000, 0, 0, 0, b"")
        self.sock.sendto(packet, address)
        self.last_hello_sent = now
        if self.hello_remaining > 0:
            self.hello_remaining -= 1

    def next_sequence(self) -> int:
        with self.sequence_lock:
            sequence = self.sequence
            self.sequence = (self.sequence + 1) & 0xFFFFFFFF
            return sequence

    def write_adc(self, timestamp_us: int, sequence: int, raw: int) -> None:
        if self.audio_origin_us is None or self.wav_format is None:
            self.pending_adc.append((timestamp_us, sequence, raw))
            return
        sample_rate = self.wav_format[0]
        audio_frame = round((timestamp_us - self.audio_origin_us) * sample_rate / 1_000_000)
        audio_time_s = audio_frame / sample_rate
        # Wait until the matching audio frame has passed through the reorder
        # heap. This makes the CSV point-wise aligned while capture is running.
        heapq.heappush(self.aligned_adc_heap,
                       (audio_frame, timestamp_us, sequence, raw))
        self.adc_samples += 1
        if self.adc_first_timestamp_us is None:
            self.adc_first_timestamp_us = timestamp_us
        self.adc_last_timestamp_us = timestamp_us

    def flush_pending_adc(self) -> None:
        pending, self.pending_adc = self.pending_adc, []
        if self.audio_origin_us is None or self.wav_format is None:
            for timestamp_us, sequence, raw in pending:
                self.adc_writer.writerow((timestamp_us, sequence, raw, "", "",
                                          "", "", ""))
                self.adc_samples += 1
                if self.adc_first_timestamp_us is None:
                    self.adc_first_timestamp_us = timestamp_us
                self.adc_last_timestamp_us = timestamp_us
            return
        for timestamp_us, sequence, raw in pending:
            self.write_adc(timestamp_us, sequence, raw)

    def append_audio_cache(self, payload: bytes, channels: int,
                           start_frame: int) -> None:
        """Keep a short contiguous PCM history for live ADC row alignment."""
        frame_bytes = channels * 2
        if frame_bytes <= 0 or len(payload) % frame_bytes:
            return
        cached_frames = (len(self.audio_cache) // frame_bytes
                         if self.audio_cache_channels == channels else 0)
        expected_start = self.audio_cache_start_frame + cached_frames
        if (not self.audio_cache or self.audio_cache_channels != channels or
                expected_start != start_frame):
            self.audio_cache = bytearray(payload)
            self.audio_cache_start_frame = start_frame
            self.audio_cache_channels = channels
        else:
            self.audio_cache.extend(payload)

        # Trim in one-second chunks to avoid moving the buffer every 10 ms.
        sample_rate = self.wav_format[0] if self.wav_format else 24000
        cached_frames = len(self.audio_cache) // frame_bytes
        if cached_frames > sample_rate * 3:
            drop_frames = cached_frames - sample_rate * 2
            del self.audio_cache[:drop_frames * frame_bytes]
            self.audio_cache_start_frame += drop_frames

    def cached_audio_values(self, frame: int) -> tuple[str, str, str]:
        channels = self.audio_cache_channels
        if channels <= 0:
            return "", "", ""
        cached_frames = len(self.audio_cache) // (channels * 2)
        if not self.audio_cache_start_frame <= frame < \
                self.audio_cache_start_frame + cached_frames:
            return "", "", ""
        byte_offset = (frame - self.audio_cache_start_frame) * channels * 2
        values = [""] * 3
        for channel in range(min(channels, 3)):
            values[channel] = str(struct.unpack_from(
                "<h", self.audio_cache, byte_offset + channel * 2)[0])
        return values[0], values[1], values[2]

    def flush_ready_adc(self, force: bool = False) -> None:
        while self.aligned_adc_heap and \
                (force or self.aligned_adc_heap[0][0] < self.audio_written_frames):
            audio_frame, timestamp_us, sequence, raw = heapq.heappop(
                self.aligned_adc_heap)
            sample_rate = self.wav_format[0] if self.wav_format else 24000
            audio_time_s = audio_frame / sample_rate
            mic1_raw, ref_raw, mic2_raw = self.cached_audio_values(audio_frame)
            self.adc_writer.writerow((timestamp_us, sequence, raw, audio_frame,
                                      f"{audio_time_s:.9f}", mic1_raw,
                                      ref_raw, mic2_raw))

    def enrich_adc_with_audio(self) -> None:
        """Fill each ADC row with PCM samples at its aligned WAV frame."""
        if self.audio_path is None or not self.audio_path.exists():
            return
        adc_path = self.args.output_dir / "adc_raw.csv"
        temp_path = self.args.output_dir / "adc_raw.csv.tmp"
        audio_columns = ("mic1_raw", "ref_raw", "mic2_raw")
        chunk_frames = 4096

        try:
            with wave.open(str(self.audio_path), "rb") as wav_file, \
                    adc_path.open(newline="") as source, \
                    temp_path.open("w", newline="") as target:
                reader = csv.DictReader(source)
                fieldnames = list(reader.fieldnames or [])
                for column in audio_columns:
                    if column not in fieldnames:
                        fieldnames.append(column)
                writer = csv.DictWriter(target, fieldnames=fieldnames)
                writer.writeheader()

                channels = wav_file.getnchannels()
                total_frames = wav_file.getnframes()
                cached_start = -1
                cached_count = 0
                cached_pcm = array.array("h")
                for row in reader:
                    values = [""] * len(audio_columns)
                    frame_text = row.get("audio_frame_index", "")
                    if frame_text:
                        frame = int(frame_text)
                        if 0 <= frame < total_frames:
                            wanted_start = frame // chunk_frames * chunk_frames
                            if wanted_start != cached_start:
                                wav_file.setpos(wanted_start)
                                cached_count = min(chunk_frames, total_frames - wanted_start)
                                cached_pcm = array.array("h")
                                cached_pcm.frombytes(wav_file.readframes(cached_count))
                                if sys.byteorder != "little":
                                    cached_pcm.byteswap()
                                cached_start = wanted_start
                            offset = (frame - cached_start) * channels
                            for channel in range(min(channels, len(audio_columns))):
                                values[channel] = str(cached_pcm[offset + channel])
                    for column, value in zip(audio_columns, values):
                        row[column] = value
                    writer.writerow(row)
            temp_path.replace(adc_path)
            print("ADC 对齐音频已写入: mic1_raw, ref_raw, mic2_raw")
        except (OSError, ValueError, wave.Error) as exc:
            print(f"警告: ADC 音频回填失败: {exc}", file=sys.stderr)

    def write_silence(self, frames: int, channels: int) -> None:
        assert self.wav_file is not None
        assert self.mono_wav_file is not None
        cache_frame = self.audio_written_frames
        while frames > 0:
            chunk_frames = min(frames, 4096)
            silence = bytes(chunk_frames * channels * 2)
            self.wav_file.writeframesraw(silence)
            self.append_audio_cache(silence, channels, cache_frame)
            mono_silence = bytes(chunk_frames * 2)
            self.mono_wav_file.writeframesraw(mono_silence)
            if self.output_stream:
                self.queue_playback(mono_silence)
            frames -= chunk_frames
            cache_frame += chunk_frames

    def process_audio_packet(self, timestamp_us: int, sequence: int, payload: bytes,
                             sample_rate: int, channels: int) -> None:
        assert self.wav_file is not None
        frame_bytes = channels * 2
        if frame_bytes <= 0 or len(payload) % frame_bytes:
            return
        source_frames = len(payload) // frame_bytes
        original_source_frames = source_frames
        if self.audio_origin_us is None:
            self.audio_origin_us = timestamp_us
            self.audio_written_frames = 0
            self.flush_pending_adc()

        target_frame = round((timestamp_us - self.audio_origin_us) * sample_rate / 1_000_000)
        delta = target_frame - self.audio_written_frames
        # Ignore sub-frame timestamp jitter from task scheduling/I2C contention.
        if abs(delta) <= 2:
            target_frame = self.audio_written_frames
            delta = 0

        gap_frames = max(0, delta)
        trimmed_frames = max(0, -delta)
        written_frame = self.audio_written_frames

        if gap_frames:
            self.write_silence(gap_frames, channels)
            self.audio_written_frames += gap_frames
            written_frame = self.audio_written_frames
            self.audio_gap_frames += gap_frames

        if trimmed_frames >= source_frames:
            self.audio_trimmed_frames += source_frames
            self.timing_writer.writerow((timestamp_us, sequence, target_frame, written_frame,
                                         original_source_frames, gap_frames, source_frames))
            return
        if trimmed_frames:
            payload = payload[trimmed_frames * frame_bytes:]
            source_frames -= trimmed_frames
            self.audio_trimmed_frames += trimmed_frames

        self.wav_file.writeframesraw(payload)
        self.append_audio_cache(payload, channels, self.audio_written_frames)
        mono_payload = first_channel(payload, channels)
        assert self.mono_wav_file is not None
        self.mono_wav_file.writeframesraw(mono_payload)
        self.audio_written_frames += source_frames
        self.flush_ready_adc()
        if self.output_stream:
            self.queue_playback(mono_payload)
        self.timing_writer.writerow((timestamp_us, sequence, target_frame, written_frame,
                                     original_source_frames, gap_frames, trimmed_frames))

    def flush_audio_packets(self, force: bool = False) -> None:
        if not self.audio_heap:
            return
        threshold = self.max_audio_timestamp_us - self.args.reorder_buffer_ms * 1000
        while self.audio_heap and (force or self.audio_heap[0][0] <= threshold):
            timestamp_us, sequence, payload, sample_rate, channels = heapq.heappop(
                self.audio_heap)
            self.process_audio_packet(timestamp_us, sequence, payload, sample_rate, channels)

    def queue_audio_packet(self, timestamp_us: int, sequence: int, payload: bytes,
                           sample_rate: int, channels: int) -> None:
        fmt = (sample_rate, channels)
        if self.wav_format is not None and self.wav_format != fmt:
            self.flush_audio_packets(force=True)
        self.open_audio_outputs(sample_rate, channels)
        heapq.heappush(self.audio_heap,
                       (timestamp_us, sequence, payload, sample_rate, channels))
        self.max_audio_timestamp_us = max(self.max_audio_timestamp_us, timestamp_us)
        self.last_audio_arrival = time.monotonic()
        self.flush_audio_packets()

    def start_microphone(self) -> None:
        if not self.args.send_mic:
            return
        with self.mic_lock:
            if self.mic_enabled:
                return
            self.mic_enabled = True

        def callback(indata, frames, time_info, status) -> None:
            del frames, time_info
            if not self.mic_enabled:
                return
            if status:
                print(f"电脑麦克风状态: {status}", file=sys.stderr)
            self.send_microphone_payload(bytes(indata))

        if self.sd:
            try:
                self.input_stream = self.sd.RawInputStream(
                    device=self.args.input_device or None,
                    samplerate=24000, channels=1, dtype="int16", blocksize=240,
                    callback=callback)
                self.input_stream.start()
                device = self.args.input_device or "系统默认"
                print(f"电脑麦克风 → 设备扬声器下发已开启(sounddevice): {device}, "
                      "24000 Hz / mono / int16")
                return
            except Exception as exc:
                self.input_stream = None
                print(f"sounddevice 麦克风启动失败，改用 FFmpeg: {exc}", file=sys.stderr)

        self.mic_capture_thread = threading.Thread(
            target=self.ffmpeg_microphone_loop, name="ffmpeg-microphone", daemon=True)
        self.mic_capture_thread.start()

    def set_microphone_downlink(self, enabled: bool, input_device: str = "") -> None:
        """Dynamically control PC microphone -> device speaker downlink."""
        self.args.send_mic = enabled
        self.args.input_device = input_device
        if enabled:
            self.start_microphone()
        else:
            self.stop_microphone()

    def stop_microphone(self) -> None:
        with self.mic_lock:
            was_enabled = self.mic_enabled
            self.mic_enabled = False
        stream = self.input_stream
        self.input_stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            stream.close()
        process = self.mic_capture_process
        if process is not None and process.poll() is None:
            process.terminate()
        thread = self.mic_capture_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self.mic_capture_thread = None
        self.mic_capture_process = None
        if was_enabled:
            print("电脑麦克风 → 设备扬声器下发已停止")

    def send_microphone_payload(self, payload: bytes) -> None:
        if not payload or len(payload) % 2:
            return
        # Do not interleave live microphone PCM with a selected audio file.
        if self.file_playback_process is not None:
            return
        address = self.device_address
        if address is None or time.monotonic() - self.last_device_seen >= 2.0:
            return
        samples = array.array("h")
        samples.frombytes(payload)
        if sys.byteorder != "little":
            samples.byteswap()
        self.mic_peak = max((abs(value) for value in samples), default=0)
        packet = make_packet(TYPE_AUDIO_PLAYBACK, self.next_sequence(),
                             time.monotonic_ns() // 1000, 24000, 1, 2, payload)
        try:
            self.sock.sendto(packet, address)
            self.mic_packets_sent += 1
            self.last_mic_sent = time.monotonic()
            if self.mic_packets_sent % 200 == 0:
                print(f"电脑麦克风 TX: 包={self.mic_packets_sent}, "
                      f"峰值={self.mic_peak}, 目标={address[0]}:{address[1]}")
        except OSError as exc:
            self.mic_send_errors += 1
            if self.mic_send_errors <= 5 or self.mic_send_errors % 100 == 0:
                print(f"电脑麦克风 UDP 发送失败: {exc}", file=sys.stderr)

    def ffmpeg_microphone_loop(self) -> None:
        device = self.args.input_device or "default"
        candidates = (("pulse", device), ("alsa", device))
        for backend, source in candidates:
            if not self.running or not self.mic_enabled:
                return
            command = [
                "ffmpeg", "-nostdin", "-v", "error", "-f", backend,
                "-i", source, "-f", "s16le", "-acodec", "pcm_s16le",
                "-ac", "1", "-ar", "24000", "pipe:1",
            ]
            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except FileNotFoundError:
                print("电脑麦克风不可用：缺少 sounddevice 且未安装 ffmpeg",
                      file=sys.stderr)
                return
            self.mic_capture_process = process
            assert process.stdout is not None
            first_packet = True
            while self.running and self.mic_enabled:
                payload = process.stdout.read(240 * 2)
                if not payload:
                    break
                if first_packet:
                    print(f"电脑麦克风 → 设备扬声器下发已开启(FFmpeg/{backend}): {source}, "
                          "24000 Hz / mono / int16")
                    first_packet = False
                self.send_microphone_payload(payload)
            if process.poll() is None:
                process.terminate()
            stderr = process.stderr.read().decode(errors="replace").strip() \
                if process.stderr else ""
            return_code = process.wait()
            self.mic_capture_process = None
            if not first_packet:
                return
            print(f"FFmpeg {backend} 麦克风启动失败 ({return_code}): {stderr}",
                  file=sys.stderr)
        if self.mic_enabled:
            print("电脑麦克风不可用：请检查系统输入设备或安装 sounddevice",
                  file=sys.stderr)
            self.mic_enabled = False

    def playback_was_cancelled(self, generation: int) -> bool:
        with self.file_playback_lock:
            return generation != self.file_playback_generation

    def wait_for_device(self, generation: int) -> tuple[str, int] | None:
        while self.running:
            if (self.file_playback_cancel.is_set() or
                    self.playback_was_cancelled(generation)):
                return None
            address = self.device_address
            if address is not None and time.monotonic() - self.last_device_seen < 1.0:
                return address
            time.sleep(0.05)
        return None

    def play_audio_files(self) -> None:
        """Decode supported files with FFmpeg and stream PCM to the device."""
        while self.running:
            try:
                audio_path = self.file_playback_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            with self.file_playback_lock:
                generation = self.file_playback_generation
            audio_path = audio_path.expanduser().resolve()
            if not audio_path.is_file():
                print(f"下发音频不存在: {audio_path}", file=sys.stderr)
                continue
            if self.wait_for_device(generation) is None:
                if self.running:
                    continue
                break

            command = [
                "ffmpeg", "-nostdin", "-v", "error", "-i", str(audio_path),
                "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1",
                "-ar", "24000", "pipe:1",
            ]
            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except FileNotFoundError:
                print("未找到 ffmpeg，请先安装: sudo apt install ffmpeg",
                      file=sys.stderr)
                return
            self.file_playback_process = process
            assert process.stdout is not None
            print(f"开始向设备播放: {audio_path.name}")
            packet_bytes = 240 * 2  # 10 ms, 24 kHz, mono, signed 16-bit
            next_deadline = time.monotonic()
            decoded_eof = False
            while self.running:
                if self.playback_was_cancelled(generation):
                    break
                payload = process.stdout.read(packet_bytes)
                if not payload:
                    decoded_eof = True
                    break
                if len(payload) % 2:
                    payload = payload[:-1]
                address = self.wait_for_device(generation)
                if address is None:
                    break
                packet = make_packet(
                    TYPE_AUDIO_PLAYBACK, self.next_sequence(),
                    time.monotonic_ns() // 1000, 24000, 1, 2, payload)
                try:
                    self.sock.sendto(packet, address)
                except OSError as exc:
                    if self.running:
                        print(f"下发音频发送失败: {exc}", file=sys.stderr)
                    break
                next_deadline += len(payload) / 2 / 24000
                delay = next_deadline - time.monotonic()
                if delay > 0:
                    time.sleep(delay)

            cancelled = self.playback_was_cancelled(generation)
            if not decoded_eof and process.poll() is None:
                process.stdout.close()
                process.terminate()
            try:
                return_code = process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait()
            stderr = process.stderr.read().decode(errors="replace").strip() \
                if process.stderr else ""
            self.file_playback_process = None
            if return_code == 0 and self.running and not cancelled:
                print(f"设备播放完成: {audio_path.name}")
            elif self.running and not cancelled:
                print(f"音频解码失败 ({return_code}): {stderr}", file=sys.stderr)

    def start_file_playback(self) -> None:
        if self.file_playback_thread is not None and self.file_playback_thread.is_alive():
            return
        self.file_playback_thread = threading.Thread(
            target=self.play_audio_files, name="device-file-playback", daemon=True)
        self.file_playback_thread.start()
        if not self.file_playback_queue.empty():
            print("音频文件已加入下发队列，等待设备连接")

    def enqueue_audio_file(self, audio_path: str | Path) -> None:
        """Add a file while capture is running; used by the GUI."""
        path = Path(audio_path)
        self.file_playback_cancel.clear()
        self.file_playback_queue.put(path)
        self.start_file_playback()
        print(f"已加入设备播放队列: {path.name}")

    def stop_file_playback(self, clear_queue: bool = True) -> None:
        self.file_playback_cancel.set()
        with self.file_playback_lock:
            self.file_playback_generation += 1
        process = self.file_playback_process
        if process is not None and process.poll() is None:
            process.terminate()
        if clear_queue:
            while True:
                try:
                    self.file_playback_queue.get_nowait()
                except queue.Empty:
                    break
        print("已停止设备文件播放")

    def handle_device_restart(self, timestamp_us: int) -> None:
        """Continue the WAV timeline when the device clock restarts from zero."""
        self.flush_audio_packets(force=True)
        self.flush_pending_adc()
        self.flush_ready_adc(force=True)
        self.audio_heap.clear()
        self.max_audio_timestamp_us = 0
        self.last_audio_arrival = 0.0
        self.last_sequence = None
        self.hello_remaining = 5
        self.last_hello_sent = 0.0
        self.device_restart_count += 1
        if self.wav_format is not None:
            sample_rate = self.wav_format[0]
            self.audio_origin_us = round(
                timestamp_us - self.audio_written_frames * 1_000_000 / sample_rate)
        else:
            self.audio_origin_us = None
        print(f"检测到设备重启，已自动重连并续接采集时间轴（第 {self.device_restart_count} 次）")

    def handle_packet(self, data: bytes, address: tuple[str, int]) -> None:
        if len(data) < HEADER.size:
            return
        magic, version, packet_type, flags, timestamp_us, sequence, sample_rate, \
            channels, sample_width, payload_size = HEADER.unpack_from(data)
        if magic != MAGIC or version != VERSION or len(data) != HEADER.size + payload_size:
            return
        payload = data[HEADER.size:]
        if (self.last_device_timestamp_us is not None and
                timestamp_us + 5_000_000 < self.last_device_timestamp_us):
            self.handle_device_restart(timestamp_us)
        if (self.last_device_timestamp_us is None or
                timestamp_us > self.last_device_timestamp_us or
                timestamp_us + 5_000_000 < self.last_device_timestamp_us):
            self.last_device_timestamp_us = timestamp_us
        self.device_address = address
        self.last_device_seen = time.monotonic()
        self.send_server_hello(address)
        self.packet_count += 1
        if self.last_sequence is not None:
            forward = (sequence - self.last_sequence) & 0xFFFFFFFF
            if 0 < forward < 0x80000000:
                self.lost_count += forward - 1
                self.last_sequence = sequence
        else:
            self.last_sequence = sequence

        if packet_type == TYPE_AUDIO_CAPTURE and sample_width == 2 and channels > 0:
            self.queue_audio_packet(timestamp_us, sequence, payload, sample_rate, channels)
        elif packet_type == TYPE_ADC_CAPTURE and sample_width == 2:
            if flags & FLAG_ADC_TIMESTAMPED_RECORDS:
                if payload_size % ADC_RECORD_SIZE:
                    return
                for offset in range(0, payload_size, ADC_RECORD_SIZE):
                    sample_timestamp_us = ADC_RECORD.unpack_from(payload, offset)[0]
                    raw = struct.unpack_from("<h", payload, offset + 8)[0]
                    self.write_adc(sample_timestamp_us, sequence, raw)
            elif payload_size == 2:
                # Backward compatibility with firmware that sent one ADC value per packet.
                raw = struct.unpack("<h", payload)[0]
                self.write_adc(timestamp_us, sequence, raw)

        if self.packet_count % 1000 == 0:
            print(f"设备={address[0]} 包={self.packet_count} 推测丢包={self.lost_count} "
                  f"最新时间戳={timestamp_us} us")

    def run(self) -> None:
        print(f"监听 UDP {self.args.bind}:{self.args.port}，等待 Vibratalkie 数据...")
        print(f"本次采集保存目录: {self.args.output_dir}")
        self.start_microphone()
        self.start_file_playback()
        try:
            while self.running:
                try:
                    data, address = self.sock.recvfrom(65535)
                    self.handle_packet(data, address)
                except socket.timeout:
                    if (self.audio_heap and self.last_audio_arrival and
                            time.monotonic() - self.last_audio_arrival >=
                            self.args.reorder_buffer_ms / 1000):
                        self.flush_audio_packets(force=True)
                    continue
        except KeyboardInterrupt:
            print("\n正在停止...")
        finally:
            self.running = False
            self.stop_microphone()
            if (self.file_playback_process is not None and
                    self.file_playback_process.poll() is None):
                self.file_playback_process.terminate()
            if self.file_playback_thread is not None:
                self.file_playback_thread.join(timeout=2.0)
            self.flush_audio_packets(force=True)
            self.flush_pending_adc()
            self.flush_ready_adc(force=True)
            if self.output_stream:
                self.output_stream.stop()
                self.output_stream.close()
            if self.wav_file:
                self.wav_file.close()
            if self.mono_wav_file:
                self.mono_wav_file.close()
            channels = self.wav_format[1] if self.wav_format else None
            channel_order = (["MIC1", "REF", "MIC2"] if channels == 3 else
                             [f"CH{i + 1}" for i in range(channels or 0)])
            adc_span_us = ((self.adc_last_timestamp_us - self.adc_first_timestamp_us)
                           if self.adc_first_timestamp_us is not None and
                           self.adc_last_timestamp_us is not None else 0)
            adc_effective_rate = ((self.adc_samples - 1) * 1_000_000 / adc_span_us
                                  if self.adc_samples > 1 and adc_span_us > 0 else None)
            metadata = {
                "audio_origin_device_timestamp_us": self.audio_origin_us,
                "sample_rate": self.wav_format[0] if self.wav_format else None,
                "channels": channels,
                "channel_order": channel_order,
                "audio_frames": self.audio_written_frames,
                "inserted_gap_frames": self.audio_gap_frames,
                "trimmed_overlap_frames": self.audio_trimmed_frames,
                "estimated_udp_packets_lost": self.lost_count,
                "adc_samples": self.adc_samples,
                "adc_effective_rate_hz": adc_effective_rate,
                "device_restart_count": self.device_restart_count,
            }
            (self.args.output_dir / "capture_metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
            self.adc_handle.close()
            self.timing_handle.close()
            self.enrich_adc_with_audio()
            self.sock.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vibratalkie 原始音频/ADC 电脑服务端")
    parser.add_argument("--bind", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=9999, help="UDP 端口，默认 9999")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="WAV/CSV 保存目录；默认按时间创建 pc_tools/pc_stream_data/cli_*目录")
    parser.add_argument("--no-play", action="store_true", help="不实时播放设备麦克风")
    parser.add_argument("--playback-buffer-ms", type=int, default=120,
                        help="实时播放预缓冲毫秒数，默认 120")
    parser.add_argument("--playback-block-ms", type=int, default=20,
                        help="声卡回调块大小毫秒数，默认 20")
    parser.add_argument("--reorder-buffer-ms", type=int, default=80,
                        help="UDP 音频重排序缓冲毫秒数，默认 80")
    parser.add_argument("--send-mic", action="store_true",
                        help="把电脑麦克风下发到设备扬声器")
    parser.add_argument("--input-device", default="",
                        help="电脑输入设备名称；默认使用系统默认设备")
    parser.add_argument("--play-file", type=Path, action="append", default=[],
                        help="向设备播放 OGG/MP3/WAV；可重复指定多个文件")
    args = parser.parse_args()
    if args.output_dir is None:
        args.output_dir = make_default_output_dir()
    return args


if __name__ == "__main__":
    PcRawStreamServer(parse_args()).run()
