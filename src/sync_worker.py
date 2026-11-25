"""
Multi-Camera Cloud Synchronization, Rolling Purge, and Disk Space Watchdog Worker for rtsp-cloud-sync.
Synchronizes completed video segments across all camera folders to cloud storage via rclone,
enforces local rolling storage retention windows, and triggers automated FIFO purge if storage runs low.
"""

import datetime
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

from src.config import AppConfig, CameraConfig, SystemConfig


class SyncWorker:
    """
    Supervises periodic rclone mirroring to cloud storage, local rolling file purging,
    and automated low-disk-space emergency FIFO pruning.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.system = config.system
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._log_file = Path(self.system.log_dir).expanduser() / "sync.log"

    def _log(self, message: str) -> None:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [SYNC] {message}\n"
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass

    def perform_sync_for_camera(self, camera: CameraConfig, dry_run: bool = False) -> None:
        """
        Runs rclone sync for a single camera's directory.
        """
        local_dir = camera.get_video_dir(self.system.local_storage_base)
        remote_dest = camera.get_rclone_destination(self.system.rclone_remote_base)

        if not local_dir.exists():
            return

        cmd = [
            "rclone", "sync",
            str(local_dir),
            remote_dest,
            "--min-age", "1m",          # Skip active recording segments
            "--exclude", "*.tmp",
            "--drive-use-trash=false",  # Permanently purge deleted segments from Google Drive (bypass 30-day trash quota)
            "--delete-before",          # Delete purged files on Drive before uploading new segments (prevents quota deadlock)
            "--log-file", str(self._log_file),
            "--log-level", "NOTICE",
        ]

        if dry_run:
            cmd.append("--dry-run")

        try:
            self._log(f"Starting cloud sync for '{camera.name}' -> {remote_dest}...")
            log_offset = self._log_file.stat().st_size if self._log_file.exists() else 0
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if res.returncode == 0:
                self._log(f"Sync complete for '{camera.name}'.")
            else:
                error_detail = res.stderr or ""
                if self._log_file.exists():
                    try:
                        with open(self._log_file, "r", encoding="utf-8", errors="ignore") as f:
                            f.seek(log_offset)
                            error_detail += "\n" + f.read()
                    except Exception:
                        pass

                self._log(f"Warning: rclone sync returned exit code {res.returncode}: {res.stderr}")

                if not dry_run and "storageQuotaExceeded" in error_detail:
                    self._log(
                        f"WARNING: Google Drive storage quota exceeded during '{camera.name}' sync. "
                        f"Triggering emergency FIFO purge and retrying..."
                    )
                    if self.enforce_storage_size_threshold(force_prune_gb=1.0) > 0:
                        retry_res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                        if retry_res.returncode == 0:
                            self._log(f"Retry sync complete for '{camera.name}'.")
                        else:
                            self._log(f"Warning: retry rclone sync returned exit code {retry_res.returncode}: {retry_res.stderr}")
        except FileNotFoundError:
            self._log("ERROR: rclone binary not found on PATH. Skipping cloud upload.")
        except subprocess.TimeoutExpired:
            self._log(f"Warning: rclone sync timed out for '{camera.name}'.")
        except Exception as e:
            self._log(f"Unexpected error during rclone sync for '{camera.name}': {e}")

    def purge_expired_files(self, camera: CameraConfig) -> int:
        """
        Purges local MP4 video segments older than retention_days.
        Returns the number of deleted files.
        """
        local_dir = camera.get_video_dir(self.system.local_storage_base)
        if not local_dir.exists():
            return 0

        retention_seconds = self.system.retention_days * 86400
        now = time.time()
        deleted_count = 0

        for file_path in local_dir.glob("*.mp4"):
            try:
                mtime = file_path.stat().st_mtime
                age_seconds = now - mtime
                if age_seconds >= retention_seconds:
                    file_path.unlink()
                    deleted_count += 1
                    self._log(f"Purged expired segment: {camera.name}/{file_path.name} (Age: {age_seconds / 3600:.1f}h)")
            except Exception as e:
                self._log(f"Error purging file {file_path.name}: {e}")

        return deleted_count

    def enforce_storage_size_threshold(self, force_prune_gb: float = 0.0) -> int:
        """
        Monitors total size of the videos directory (local_storage_base).
        If total folder size reaches/exceeds max_storage_size_gb (e.g. 15 GB Google Drive limit)
        or if force_prune_gb > 0 is requested on Drive quota error, performs FIFO deletion
        of the oldest recorded segments across all camera folders.
        """
        base_storage = Path(self.system.local_storage_base).expanduser().resolve()
        if not base_storage.exists():
            return 0

        try:
            total_bytes = 0
            all_files = []
            now = time.time()

            for file_path in base_storage.rglob("*"):
                try:
                    if file_path.is_file():
                        stat = file_path.stat()
                        total_bytes += stat.st_size
                        # Candidate for pruning: MP4 files older than 5 minutes (skip active segments)
                        if file_path.suffix.lower() == ".mp4" and (now - stat.st_mtime) >= 300:
                            all_files.append((stat.st_mtime, stat.st_size, file_path))
                except OSError:
                    pass

            used_gb = total_bytes / (1024 ** 3)
            max_gb = self.system.max_storage_size_gb

            if force_prune_gb <= 0 and used_gb < max_gb:
                return 0  # Folder size within limit

            if force_prune_gb > 0:
                target_size_gb = max(0.0, min(used_gb, max_gb) - force_prune_gb)
            else:
                margin_gb = 1.0 if max_gb >= 2.0 else (max_gb * 0.2)
                target_size_gb = max(0.0, max_gb - margin_gb)

            self._log(
                f"WARNING: Videos folder size is {used_gb:.2f} GB "
                f"(Max threshold: {max_gb:.1f} GB). Triggering FIFO purge to <= {target_size_gb:.2f} GB..."
            )

            # Sort ascending by modification time (oldest files first)
            all_files.sort(key=lambda x: x[0])

            purged_count = 0
            freed_bytes = 0
            current_bytes = total_bytes
            target_bytes = target_size_gb * (1024 ** 3)

            for _, file_size, file_path in all_files:
                if current_bytes <= target_bytes and purged_count > 0:
                    break
                try:
                    file_path.unlink()
                    purged_count += 1
                    freed_bytes += file_size
                    current_bytes = max(0, current_bytes - file_size)

                    if current_bytes <= target_bytes:
                        break
                except Exception as e:
                    self._log(f"Failed to delete {file_path}: {e}")

            if purged_count > 0:
                current_gb = current_bytes / (1024 ** 3)
                self._log(
                    f"Folder size purge complete: Pruned {purged_count} oldest clips "
                    f"({freed_bytes / (1024 ** 2):.1f} MB freed). Videos folder size reduced to {current_gb:.2f} GB."
                )

            return purged_count

        except Exception as e:
            self._log(f"Error checking videos folder size: {e}")
            return 0

    def enforce_disk_space_threshold(self) -> int:
        """
        Monitors available local storage. If free space drops below min_free_disk_space_gb,
        automatically performs emergency FIFO deletion of oldest recorded segments
        across all camera folders to prevent disk exhaustion.
        """
        base_storage = Path(self.system.local_storage_base).expanduser().resolve()
        if not base_storage.exists():
            return 0

        try:
            usage = shutil.disk_usage(base_storage)
            free_gb = usage.free / (1024 ** 3)
            min_gb = self.system.min_free_disk_space_gb

            if free_gb >= min_gb:
                return 0  # Storage healthy

            self._log(
                f"WARNING: Low local storage! Only {free_gb:.2f} GB free "
                f"(Safety threshold: {min_gb:.1f} GB). Triggering emergency FIFO purge..."
            )

            # Find all MP4 files across all camera directories
            all_files = []
            now = time.time()

            for mp4_file in base_storage.rglob("*.mp4"):
                try:
                    stat = mp4_file.stat()
                    # Skip files modified within the last 5 minutes (in-flight or active post-buffer)
                    if (now - stat.st_mtime) >= 300:
                        all_files.append((stat.st_mtime, stat.st_size, mp4_file))
                except OSError:
                    pass

            # Sort ascending by modification time (oldest files first)
            all_files.sort(key=lambda x: x[0])

            purged_count = 0
            freed_bytes = 0
            target_free_gb = min_gb + 1.0  # Prune until 1 GB above safety threshold

            for _, file_size, file_path in all_files:
                try:
                    file_path.unlink()
                    purged_count += 1
                    freed_bytes += file_size

                    current_free = shutil.disk_usage(base_storage).free / (1024 ** 3)
                    if current_free >= target_free_gb:
                        self._log(
                            f"Emergency purge complete: Pruned {purged_count} oldest clips "
                            f"({freed_bytes / (1024 ** 2):.1f} MB freed). Free storage restored to {current_free:.2f} GB."
                        )
                        break
                except Exception as e:
                    self._log(f"Failed to delete {file_path}: {e}")

            return purged_count

        except Exception as e:
            self._log(f"Error checking disk space usage: {e}")
            return 0

    def sync_all_cameras(self, dry_run: bool = False) -> None:
        """
        Executes standard retention purge, folder size & disk space watchdogs, and cloud sync.
        Purging runs before rclone sync (--delete-before) so expired/excess files are removed
        from Google Drive before uploading new segments, preventing 15 GB Drive quota deadlocks.
        """
        enabled_cameras = self.config.get_enabled_cameras()
        self._log(f"--- Starting Sync & Purge Cycle ({len(enabled_cameras)} cameras) ---")

        # 1. Standard Rolling Retention Purge
        for camera in enabled_cameras:
            self.purge_expired_files(camera)

        # 2. Max Videos Folder Size Safeguard (15 GB Google Drive quota protection)
        self.enforce_storage_size_threshold()

        # 3. Low-Disk-Space Emergency Safeguard (FIFO Pruning)
        self.enforce_disk_space_threshold()

        # 4. Cloud Mirror (deletes purged remote files first via --delete-before, then uploads)
        for camera in enabled_cameras:
            self.perform_sync_for_camera(camera, dry_run=dry_run)

        self._log("--- Completed Sync & Purge Cycle ---")

    def _run_loop(self) -> None:
        interval = self.system.sync_interval_seconds
        self._log(f"Sync worker loop started (Interval: {interval}s).")

        while not self._stop_event.is_set():
            self.sync_all_cameras()
            # Wait for next interval or stop signal
            self._stop_event.wait(timeout=interval)

        self._log("Sync worker loop terminated.")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="SyncWorker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
