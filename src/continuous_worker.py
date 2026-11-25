"""
Continuous RTSP Ingestion Worker for rtsp-cloud-sync.
Manages 24/7 continuous segmented stream copy for IP cameras (e.g. Tapo TC41).
"""

import datetime
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from src.config import CameraConfig, SystemConfig


class ContinuousWorker:
    """
    Supervises continuous FFmpeg recording for a single camera.
    Automatically reconnects on stream drop and ensures clean shutdown.
    """

    def __init__(self, camera: CameraConfig, system: SystemConfig):
        self.camera = camera
        self.system = system
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._video_dir = camera.get_video_dir(system.local_storage_base)
        self._log_file = camera.get_log_file(system.log_dir)

    def _log(self, message: str) -> None:
        """Appends timestamped message to camera log file and console."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{self.camera.name}] {message}\n"
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass

    def build_ffmpeg_cmd(self) -> list:
        """
        Builds the FFmpeg command line for segmented direct stream copy.
        """
        rtsp_url = self.camera.get_rtsp_url()
        segment_time = str(self.camera.segment_time_seconds)
        output_pattern = str(self._video_dir / "%Y-%m-%d_%H-%M-%S.mp4")

        return [
            "ffmpeg",
            "-loglevel", "warning",
            "-timeout", "5000000",                # 5-second socket timeout (microseconds)
            "-rtsp_transport", "tcp",             # Reliable TCP transport for RTSP
            "-fflags", "+genpts+igndts+discardcorrupt", # Generate missing PTS, ignore corrupt DTS
            "-use_wallclock_as_timestamps", "1",
            "-avoid_negative_ts", "make_zero",
            "-i", rtsp_url,
            "-c:v", "copy",                       # Zero CPU direct stream copy for H.264
            "-bsf:v", "setts=pts=if(eq(PTS\\,NOPTS)\\,DTS\\,PTS)", # Ensure PTS is never unset
            "-c:a", "aac",                        # Transcode pcm_alaw audio to AAC
            "-b:a", "48k",                        # 48k matches exact 6144 bits/frame limit for 8kHz mono audio
            "-af", "aresample=async=1",           # Smooth audio sync to eliminate backward-in-time jitter
            "-f", "segment",
            "-segment_time", segment_time,
            "-reset_timestamps", "1",             # Monotonic 0-based timestamps per segment
            "-segment_format", "mp4",
            "-strftime", "1",
            output_pattern,
        ]

    def _run_loop(self) -> None:
        """
        Continuous supervision loop. Restarts FFmpeg upon disconnect unless stopped.
        """
        self._log(f"Starting continuous capture worker (Target: {self.camera.ip}:{self.camera.rtsp_port})...")
        os.makedirs(self._video_dir, exist_ok=True)

        while not self._stop_event.is_set():
            cmd = self.build_ffmpeg_cmd()
            self._log(f"Spawning FFmpeg stream capture process...")

            try:
                with open(self._log_file, "a", encoding="utf-8") as log_out:
                    self._process = subprocess.Popen(
                        cmd,
                        stdout=log_out,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.PIPE,
                    )

                # Wait for process exit or stop signal
                while self._process.poll() is None:
                    if self._stop_event.is_set():
                        self._terminate_ffmpeg()
                        break
                    time.sleep(1)

                exit_code = self._process.poll()
                if not self._stop_event.is_set():
                    self._log(
                        f"FFmpeg process exited (code {exit_code}). "
                        f"Stream disconnected or network drop. Reconnecting in 10s..."
                    )
                    # Reconnection backoff wait
                    self._stop_event.wait(timeout=10)

            except FileNotFoundError:
                self._log("ERROR: ffmpeg binary not found on system PATH. Retrying in 30s...")
                self._stop_event.wait(timeout=30)
            except Exception as e:
                self._log(f"Unexpected error in capture loop: {e}. Retrying in 10s...")
                self._stop_event.wait(timeout=10)

        self._log("Continuous worker loop terminated cleanly.")

    def _terminate_ffmpeg(self) -> None:
        """Gracefully terminates FFmpeg process to finalize active MP4 segment."""
        if self._process and self._process.poll() is None:
            self._log("Stopping FFmpeg process and finalizing active segment...")
            try:
                # 1. Give FFmpeg up to 2 seconds to finalize if already interrupted by terminal Ctrl+C
                try:
                    self._process.wait(timeout=2)
                    return
                except subprocess.TimeoutExpired:
                    pass

                # 2. If still active, send single SIGINT to trigger clean MP4 trailer write
                if hasattr(signal, "SIGINT") and os.name != "nt":
                    self._process.send_signal(signal.SIGINT)
                else:
                    self._process.terminate()

                self._process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self._log("FFmpeg did not stop in 6s. Force killing...")
                self._process.kill()
                self._process.wait()
            except Exception as e:
                self._log(f"Error terminating FFmpeg: {e}")

    def start(self) -> None:
        """Starts worker in background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"Worker-{self.camera.name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signals worker to stop and waits for exit."""
        self._stop_event.set()
        self._terminate_ffmpeg()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=8)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
