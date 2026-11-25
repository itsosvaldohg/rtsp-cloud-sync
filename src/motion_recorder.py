"""
Dynamic Event-Triggered Motion Recorder for rtsp-cloud-sync.
Manages on-demand FFmpeg capture processes with post-motion buffering for ONVIF cameras.
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


class MotionRecorder:
    """
    Manages active video recording sessions triggered by ONVIF motion events.
    Handles dynamic process spawning, post-roll buffering, and clean MP4 container finalization.
    """

    def __init__(self, camera: CameraConfig, system: SystemConfig):
        self.camera = camera
        self.system = system
        self._video_dir = camera.get_video_dir(system.local_storage_base)
        self._log_file = camera.get_log_file(system.log_dir)

        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._recording_start_time: Optional[float] = None
        self._last_motion_time: Optional[float] = None
        self._current_file: Optional[Path] = None
        self._timer_thread: Optional[threading.Thread] = None
        self._stop_requested = False

    def _log(self, message: str) -> None:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{self.camera.name}] [MOTION] {message}\n"
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass

    def build_ffmpeg_cmd(self, output_path: Path) -> list:
        """
        Builds FFmpeg command for discrete motion event recording.
        """
        return [
            "ffmpeg",
            "-loglevel", "warning",
            "-timeout", "5000000",                # 5s socket timeout
            "-rtsp_transport", "tcp",
            "-fflags", "+genpts+igndts+discardcorrupt",
            "-use_wallclock_as_timestamps", "1",
            "-avoid_negative_ts", "make_zero",
            "-i", self.camera.get_rtsp_url(),
            "-c:v", "copy",                       # Zero CPU copy
            "-bsf:v", "setts=pts=if(eq(PTS\\,NOPTS)\\,DTS\\,PTS)",
            "-c:a", "aac",
            "-b:a", "48k",                        # Fits exact 6144 bits/frame limit for 8kHz mono audio
            "-af", "aresample=async=1",           # Smooth audio sync
            "-y",
            str(output_path),
        ]

    def trigger_motion(self) -> None:
        """
        Called when an ONVIF motion event is detected.
        Starts recording if idle, or extends the active session.
        """
        with self._lock:
            now = time.time()
            self._last_motion_time = now

            if self._process is None or self._process.poll() is not None:
                # Start new recording session
                self._start_recording(now)
            elif self._timer_thread is None or not self._timer_thread.is_alive():
                # Defensive check: ensure monitor thread is active if process is running
                self._timer_thread = threading.Thread(
                    target=self._monitor_session,
                    name=f"Monitor-{self.camera.name}",
                    daemon=True,
                )
                self._timer_thread.start()

    def _start_recording(self, start_timestamp: float) -> None:
        os.makedirs(self._video_dir, exist_ok=True)
        time_str = datetime.datetime.fromtimestamp(start_timestamp).strftime("%Y-%m-%d_%H-%M-%S")
        output_file = self._video_dir / f"{time_str}_motion.mp4"
        self._current_file = output_file
        self._recording_start_time = start_timestamp

        cmd = self.build_ffmpeg_cmd(output_file)
        self._log(f"Motion detected! Recording started -> {output_file.name}")

        try:
            with open(self._log_file, "a", encoding="utf-8") as log_out:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=log_out,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
                )

            # Spawn monitor thread to watch post-motion buffer and max duration
            if self._timer_thread is None or not self._timer_thread.is_alive():
                self._timer_thread = threading.Thread(
                    target=self._monitor_session,
                    name=f"Monitor-{self.camera.name}",
                    daemon=True,
                )
                self._timer_thread.start()

        except Exception as e:
            self._log(f"Failed to start FFmpeg motion capture: {e}")
            self._process = None

    def _monitor_session(self) -> None:
        """
        Monitors active recording session. Closes file when post-motion timeout
        or max clip duration is reached.
        """
        post_buffer = self.camera.post_motion_record_seconds
        max_duration = self.camera.max_event_clip_seconds

        while True:
            time.sleep(1)
            with self._lock:
                if self._process is None or self._process.poll() is not None:
                    break

                now = time.time()
                time_since_motion = now - (self._last_motion_time or now)
                duration = int(now - (self._recording_start_time or now))
                file_name = self._current_file.name if self._current_file else "motion.mp4"

                # Check if max clip duration reached (split clip if motion still active)
                if duration >= max_duration:
                    self._log(f"Max clip length ({max_duration}s) reached. Finalizing {file_name} and splitting...")
                    self._stop_active_process()
                    if time_since_motion <= post_buffer:
                        self._start_recording(now)
                        continue
                    break

                # Check if motion ceased and post-motion buffer expired
                if time_since_motion >= post_buffer or self._stop_requested:
                    self._log(f"Motion ended. Recording finalized -> {file_name} (Duration: {duration}s)")
                    self._stop_active_process()
                    break

    def _stop_active_process(self) -> None:
        """Gracefully closes FFmpeg process."""
        if self._process and self._process.poll() is None:
            try:
                try:
                    self._process.wait(timeout=2)
                    return
                except subprocess.TimeoutExpired:
                    pass

                if hasattr(signal, "SIGINT") and os.name != "nt":
                    self._process.send_signal(signal.SIGINT)
                else:
                    self._process.terminate()

                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            except Exception as e:
                self._log(f"Error terminating motion FFmpeg: {e}")
            finally:
                self._process = None
        else:
            self._process = None

    def stop(self) -> None:
        """Stops recorder and cleanly terminates active sessions."""
        self._stop_requested = True
        with self._lock:
            self._stop_active_process()
