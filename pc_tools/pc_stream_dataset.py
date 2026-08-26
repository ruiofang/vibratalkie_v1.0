#!/usr/bin/env python3
"""Load synchronized Vibratalkie microphone and throat-ADC training windows on PC."""

from __future__ import annotations

import argparse
import csv
import json
import wave
from pathlib import Path

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover - dependency message for CLI users
    raise SystemExit("缺少 numpy，请运行: pip install numpy") from exc


class PcStreamDataset:
    """A NumPy/PyTorch-compatible lazy dataset of aligned audio and ADC windows.

    Each item is a dict containing:
      audio: float32 [channels, audio_samples]
      adc: float32 [1, adc_samples]
      audio_start_frame: int64 position in the source WAV
      audio_sample_rate / adc_sample_rate: int64 scalars
    """

    def __init__(self, capture_dir: str | Path, window_seconds: float = 1.0,
                 hop_seconds: float = 0.5, use_all_channels: bool = False,
                 adc_rate: float | None = None, remove_adc_dc: bool = True) -> None:
        self.capture_dir = Path(capture_dir)
        metadata_path = self.capture_dir / "capture_metadata.json"
        self.metadata = (json.loads(metadata_path.read_text())
                         if metadata_path.exists() else {})

        mono_files = sorted(self.capture_dir.glob("audio_mic1_*hz_mono.wav"))
        multi_files = sorted(self.capture_dir.glob("audio_*hz_*ch.wav"))
        if use_all_channels:
            if not multi_files:
                raise FileNotFoundError("未找到 audio_<rate>hz_<channels>ch.wav")
            audio_path = multi_files[0]
        elif mono_files:
            audio_path = mono_files[0]
        elif multi_files:
            audio_path = multi_files[0]
        else:
            raise FileNotFoundError("采集目录中没有 WAV 文件")

        with wave.open(str(audio_path), "rb") as wav_file:
            if wav_file.getsampwidth() != 2:
                raise ValueError("仅支持 signed 16-bit PCM WAV")
            self.audio_sample_rate = wav_file.getframerate()
            source_channels = wav_file.getnchannels()
            pcm = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype="<i2")

        pcm = pcm.reshape(-1, source_channels)
        if not use_all_channels and source_channels > 1:
            pcm = pcm[:, :1]
        self.audio = pcm.astype(np.float32) / 32768.0
        self.audio_channels = self.audio.shape[1]

        adc_frames: list[int] = []
        adc_values: list[int] = []
        adc_path = self.capture_dir / "adc_raw.csv"
        with adc_path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                frame = row.get("audio_frame_index", "")
                if frame == "":
                    continue
                adc_frames.append(int(frame))
                adc_values.append(int(row["raw"]))
        if len(adc_frames) < 2:
            raise ValueError("有效 ADC 对齐样本不足；请使用新版固件重新采集")

        order = np.argsort(np.asarray(adc_frames))
        self.adc_frames = np.asarray(adc_frames, dtype=np.float64)[order]
        adc_raw = np.asarray(adc_values, dtype=np.float32)[order]
        if remove_adc_dc:
            adc_raw -= np.median(adc_raw)
        self.adc_values = adc_raw / 32768.0

        metadata_rate = self.metadata.get("adc_effective_rate_hz")
        self.adc_sample_rate = float(adc_rate or metadata_rate or 800.0)
        self.window_audio_frames = round(window_seconds * self.audio_sample_rate)
        self.hop_audio_frames = max(1, round(hop_seconds * self.audio_sample_rate))
        self.window_adc_samples = max(1, round(window_seconds * self.adc_sample_rate))
        if self.window_audio_frames <= 0:
            raise ValueError("window_seconds 必须大于 0")

        first_common = max(0, int(np.ceil(self.adc_frames[0])))
        last_common = min(len(self.audio), int(np.floor(self.adc_frames[-1])) + 1)
        first_start = ((first_common + self.hop_audio_frames - 1) //
                       self.hop_audio_frames * self.hop_audio_frames)
        stop = last_common - self.window_audio_frames
        self.window_starts = (np.arange(first_start, stop + 1, self.hop_audio_frames,
                                        dtype=np.int64)
                              if stop >= first_start else np.empty(0, dtype=np.int64))

    def __len__(self) -> int:
        return len(self.window_starts)

    def __getitem__(self, index: int) -> dict[str, np.ndarray]:
        start = int(self.window_starts[index])
        stop = start + self.window_audio_frames
        audio = self.audio[start:stop].T.copy()

        adc_positions = (start + np.arange(self.window_adc_samples, dtype=np.float64) *
                         self.audio_sample_rate / self.adc_sample_rate)
        adc = np.interp(adc_positions, self.adc_frames, self.adc_values).astype(np.float32)
        return {
            "audio": audio,
            "adc": adc[np.newaxis, :],
            "audio_start_frame": np.asarray(start, dtype=np.int64),
            "audio_sample_rate": np.asarray(self.audio_sample_rate, dtype=np.int64),
            "adc_sample_rate": np.asarray(round(self.adc_sample_rate), dtype=np.int64),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="加载或导出音频/喉振同步训练窗口")
    parser.add_argument("capture_dir", type=Path, help="pc_stream_data 采集目录")
    parser.add_argument("--window-seconds", type=float, default=1.0)
    parser.add_argument("--hop-seconds", type=float, default=0.5)
    parser.add_argument("--all-channels", action="store_true",
                        help="使用 MIC1/REF/MIC2 三通道；默认只用 MIC1")
    parser.add_argument("--keep-adc-dc", action="store_true", help="不移除 ADC 直流偏置")
    parser.add_argument("--export-npz", type=Path, help="可选：导出所有窗口为压缩 NPZ")
    args = parser.parse_args()

    dataset = PcStreamDataset(
        args.capture_dir, args.window_seconds, args.hop_seconds,
        use_all_channels=args.all_channels, remove_adc_dc=not args.keep_adc_dc)
    print(f"训练窗口: {len(dataset)}")
    print(f"音频: {dataset.audio_sample_rate} Hz, {dataset.audio_channels} ch, "
          f"每窗口 {dataset.window_audio_frames} 点")
    print(f"ADC: {dataset.adc_sample_rate:.2f} Hz, "
          f"每窗口 {dataset.window_adc_samples} 点")

    if args.export_npz:
        if not len(dataset):
            raise SystemExit("采集时长不足以生成一个窗口")
        items = [dataset[i] for i in range(len(dataset))]
        np.savez_compressed(
            args.export_npz,
            audio=np.stack([item["audio"] for item in items]),
            adc=np.stack([item["adc"] for item in items]),
            audio_start_frame=np.stack([item["audio_start_frame"] for item in items]),
            audio_sample_rate=dataset.audio_sample_rate,
            adc_sample_rate=dataset.adc_sample_rate,
        )
        print(f"已导出: {args.export_npz}")


if __name__ == "__main__":
    main()
