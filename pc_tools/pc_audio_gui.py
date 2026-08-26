#!/usr/bin/env python3
"""Tk GUI for simultaneous Vibratalkie capture and device audio playback."""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

try:
    from .pc_raw_stream_server import PcRawStreamServer, TOOL_DIR
except ImportError:  # Direct execution: python3 pc_audio_gui.py
    from pc_raw_stream_server import PcRawStreamServer, TOOL_DIR


class QueueWriter:
    def __init__(self, output_queue: queue.Queue[str]) -> None:
        self.output_queue = output_queue

    def write(self, text: str) -> int:
        if text:
            self.output_queue.put(text)
        return len(text)

    def flush(self) -> None:
        pass


class PcAudioGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Vibratalkie PC 音频/ADC 调试工具")
        self.root.geometry("920x680")
        self.root.minsize(780, 580)

        self.server: PcRawStreamServer | None = None
        self.service_thread: threading.Thread | None = None
        self.closing = False
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        sys.stdout = QueueWriter(self.log_queue)
        sys.stderr = QueueWriter(self.log_queue)

        default_session = datetime.now().strftime("gui_%Y%m%d_%H%M%S")
        self.port_var = tk.StringVar(value="9999")
        self.output_var = tk.StringVar(
            value=str(TOOL_DIR / "pc_stream_data" / default_session))
        self.monitor_var = tk.BooleanVar(value=True)
        self.send_mic_var = tk.BooleanVar(value=False)
        self.input_device_var = tk.StringVar(value="")
        self.service_status_var = tk.StringVar(value="服务未启动")
        self.device_var = tk.StringVar(value="设备：未连接")
        self.stats_var = tk.StringVar(value="包：0    推测丢包：0    ADC：0")
        self.output_status_var = tk.StringVar(value=f"保存：{self.output_var.get()}")

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.poll_gui)

    def build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        config = ttk.LabelFrame(outer, text="接收服务", padding=10)
        config.pack(fill=tk.X)
        config.columnconfigure(1, weight=1)

        ttk.Label(config, text="UDP 端口").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        ttk.Entry(config, textvariable=self.port_var, width=10).grid(
            row=0, column=1, sticky=tk.W)
        ttk.Label(config, text="保存目录").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(8, 0))
        ttk.Entry(config, textvariable=self.output_var).grid(
            row=1, column=1, sticky=tk.EW, pady=(8, 0))
        ttk.Button(config, text="选择…", command=self.choose_output_dir).grid(
            row=1, column=2, padx=(8, 0), pady=(8, 0))

        options = ttk.Frame(config)
        options.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))
        ttk.Checkbutton(options, text="播放设备 MIC1 → 电脑扬声器",
                        variable=self.monitor_var,
                        command=self.toggle_monitor_playback).pack(side=tk.LEFT)
        ttk.Checkbutton(options, text="下发电脑麦克风 → 设备扬声器",
                        variable=self.send_mic_var,
                        command=self.toggle_mic_downlink).pack(side=tk.LEFT, padx=(20, 0))
        ttk.Label(options, text="输入设备（空=默认）").pack(side=tk.LEFT, padx=(20, 4))
        ttk.Entry(options, textvariable=self.input_device_var, width=18).pack(side=tk.LEFT)

        controls = ttk.Frame(config)
        controls.grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))
        self.start_button = ttk.Button(controls, text="启动接收", command=self.start_service)
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(
            controls, text="停止并保存", command=self.stop_service, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))

        status = ttk.LabelFrame(outer, text="连接状态", padding=10)
        status.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(status, textvariable=self.service_status_var).pack(anchor=tk.W)
        ttk.Label(status, textvariable=self.device_var).pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(status, textvariable=self.stats_var).pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(status, textvariable=self.output_status_var).pack(anchor=tk.W, pady=(4, 0))

        playback = ttk.LabelFrame(outer, text="采集过程中向设备下发音频", padding=10)
        playback.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(playback, text="选择并播放 OGG / MP3 / WAV…",
                   command=self.choose_audio_files).pack(side=tk.LEFT)
        ttk.Button(playback, text="停止文件播放", command=self.stop_playback).pack(
            side=tk.LEFT, padx=(8, 0))
        ttk.Label(playback, text="文件会加入队列；未连接设备时自动等待连接").pack(
            side=tk.LEFT, padx=(14, 0))

        log_frame = ttk.LabelFrame(outer, text="运行日志", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.log_text = ScrolledText(log_frame, height=16, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def choose_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_var.get())
        if selected:
            self.output_var.set(selected)
            self.output_status_var.set(f"保存：{selected}")

    def make_server_args(self) -> argparse.Namespace:
        try:
            port = int(self.port_var.get())
        except ValueError as exc:
            raise ValueError("UDP 端口必须是整数") from exc
        if not 1 <= port <= 65535:
            raise ValueError("UDP 端口必须在 1–65535 之间")
        output_dir = Path(self.output_var.get()).expanduser().resolve()
        return argparse.Namespace(
            bind="0.0.0.0",
            port=port,
            output_dir=output_dir,
            no_play=not self.monitor_var.get(),
            playback_buffer_ms=120,
            playback_block_ms=20,
            reorder_buffer_ms=80,
            send_mic=self.send_mic_var.get(),
            input_device=self.input_device_var.get().strip(),
            play_file=[],
        )

    def start_service(self) -> None:
        if self.service_thread is not None and self.service_thread.is_alive():
            return
        try:
            args = self.make_server_args()
        except ValueError as exc:
            messagebox.showerror("配置错误", str(exc))
            return
        capture_files = ("adc_raw.csv", "audio_timing.csv",
                         "audio_24000hz_3ch.wav", "capture_metadata.json")
        if any((args.output_dir / name).exists() for name in capture_files):
            suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
            args.output_dir = args.output_dir.parent / f"{args.output_dir.name}_{suffix}"
            self.output_var.set(str(args.output_dir))

        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.service_status_var.set("正在启动 UDP 服务…")
        self.output_status_var.set(f"保存：{args.output_dir}")

        def worker() -> None:
            try:
                server = PcRawStreamServer(args)
                self.server = server
                server.run()
            except Exception:
                traceback.print_exc()
            finally:
                self.server = None

        self.service_thread = threading.Thread(
            target=worker, name="pc-audio-server", daemon=True)
        self.service_thread.start()

    def stop_service(self) -> None:
        server = self.server
        if server is not None:
            self.service_status_var.set("正在停止并写入 WAV/ADC 元数据…")
            server.running = False
            self.stop_button.configure(state=tk.DISABLED)

    def choose_audio_files(self) -> None:
        server = self.server
        if server is None or not server.running:
            messagebox.showwarning("服务未启动", "请先启动接收服务，再下发音频。")
            return
        paths = filedialog.askopenfilenames(
            title="选择要在设备播放的音频",
            filetypes=[
                ("音频文件", "*.ogg *.oga *.mp3 *.wav *.flac *.m4a *.aac"),
                ("所有文件", "*.*"),
            ],
        )
        for path in paths:
            server.enqueue_audio_file(path)

    def toggle_mic_downlink(self) -> None:
        server = self.server
        if server is None or not server.running:
            return
        server.set_microphone_downlink(
            self.send_mic_var.get(), self.input_device_var.get().strip())

    def toggle_monitor_playback(self) -> None:
        server = self.server
        if server is None or not server.running:
            return
        server.set_monitor_playback(self.monitor_var.get())
        self.monitor_var.set(server.monitor_enabled)

    def stop_playback(self) -> None:
        server = self.server
        if server is not None:
            server.stop_file_playback(clear_queue=True)

    def append_log(self, text: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        if int(self.log_text.index("end-1c").split(".")[0]) > 5000:
            self.log_text.delete("1.0", "1000.0")
        self.log_text.configure(state=tk.DISABLED)

    def poll_gui(self) -> None:
        chunks: list[str] = []
        while True:
            try:
                chunks.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if chunks:
            self.append_log("".join(chunks))

        server = self.server
        thread_alive = self.service_thread is not None and self.service_thread.is_alive()
        if server is not None and server.running:
            self.service_status_var.set(f"UDP 0.0.0.0:{server.args.port} 接收中")
            connected = (server.device_address is not None and
                         time.monotonic() - server.last_device_seen < 2.0)
            self.device_var.set(
                f"设备：{server.device_address[0]}:{server.device_address[1]}"
                if connected and server.device_address else "设备：等待连接")
            self.stats_var.set(
                f"包：{server.packet_count}    推测丢包：{server.lost_count}    "
                f"ADC：{server.adc_samples}    设备重启：{server.device_restart_count}    "
                f"麦克风下发TX：{server.mic_packets_sent} / 错误：{server.mic_send_errors} / "
                f"峰值：{server.mic_peak}    "
                f"电脑监听：{'开' if server.monitor_enabled else '关'}    "
                f"文件播放：{'进行中' if server.file_playback_process else '空闲'}    "
                f"队列：{server.file_playback_queue.qsize()}")
        elif not thread_alive:
            self.service_status_var.set("服务已停止，数据已保存")
            self.device_var.set("设备：未连接")
            self.start_button.configure(state=tk.NORMAL)
            self.stop_button.configure(state=tk.DISABLED)

        if self.closing and not thread_alive:
            sys.stdout = self.original_stdout
            sys.stderr = self.original_stderr
            self.root.destroy()
            return
        self.root.after(100, self.poll_gui)

    def on_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.stop_service()
        if self.service_thread is None or not self.service_thread.is_alive():
            sys.stdout = self.original_stdout
            sys.stderr = self.original_stderr
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = PcAudioGui(root)
    root.mainloop()
    del app


if __name__ == "__main__":
    main()
