"""
Unit tests for MotionRecorder, OnvifEventListener, and SyncWorker.
"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config import AppConfig, CameraConfig, SystemConfig
from src.motion_recorder import MotionRecorder
from src.onvif_listener import OnvifEventListener
from src.sync_worker import SyncWorker


class TestMotionAndSync(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.system = SystemConfig(
            local_storage_base=self.test_dir.name,
            log_dir=self.test_dir.name,
            rclone_remote_base="gdrive:SecurityVideos",
            retention_days=1,
        )
        self.motion_cam = CameraConfig(
            name="hallway",
            model="Tapo C110",
            location="interior",
            mac="AA:BB:CC:DD:EE:04",
            ip="192.168.1.104",
            username="cam_user",
            password="testpassword",
            recording_mode="motion",
            post_motion_record_seconds=15,
        )

    def tearDown(self):
        self.test_dir.cleanup()

    def test_motion_recorder_command_generation(self):
        recorder = MotionRecorder(camera=self.motion_cam, system=self.system)
        target_path = Path(self.test_dir.name) / "test_motion.mp4"
        cmd = recorder.build_ffmpeg_cmd(target_path)

        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("-rtsp_transport", cmd)
        self.assertIn("tcp", cmd)
        self.assertIn("-c:v", cmd)
        self.assertIn("copy", cmd)
        self.assertIn(str(target_path), cmd)

    def test_onvif_listener_wsse_header(self):
        recorder = MotionRecorder(camera=self.motion_cam, system=self.system)
        listener = OnvifEventListener(camera=self.motion_cam, system=self.system, recorder=recorder)
        header = listener._build_wsse_header()

        self.assertIn("<wsse:Username>cam_user</wsse:Username>", header)
        self.assertIn("PasswordDigest", header)
        self.assertIn("Nonce", header)

    def test_sync_worker_purge_expired_files(self):
        config = AppConfig(system=self.system, cameras=[self.motion_cam])
        sync_worker = SyncWorker(config=config)

        cam_dir = self.motion_cam.get_video_dir(self.system.local_storage_base)
        cam_dir.mkdir(parents=True, exist_ok=True)

        # Create 1 fresh file and 1 old file
        fresh_file = cam_dir / "fresh.mp4"
        fresh_file.write_text("video_data")

        old_file = cam_dir / "old.mp4"
        old_file.write_text("video_data")
        # Set mtime to 3 days ago (3 * 86400 seconds ago)
        old_mtime = time.time() - (3 * 86400)
        os.utime(old_file, (old_mtime, old_mtime))

        deleted = sync_worker.purge_expired_files(self.motion_cam)
        self.assertEqual(deleted, 1)
        self.assertTrue(fresh_file.exists())
        self.assertFalse(old_file.exists())

    def test_enforce_disk_space_threshold(self):
        # Set a huge threshold to trigger emergency purge
        self.system.min_free_disk_space_gb = 9999999.0
        config = AppConfig(system=self.system, cameras=[self.motion_cam])
        sync_worker = SyncWorker(config=config)

        cam_dir = self.motion_cam.get_video_dir(self.system.local_storage_base)
        cam_dir.mkdir(parents=True, exist_ok=True)

        old_clip = cam_dir / "2026-08-20_00-00-00_motion.mp4"
        old_clip.write_text("old_video_bytes")
        # Set mtime to 1 hour ago (past 5-minute safety buffer)
        past_time = time.time() - 3600
        os.utime(old_clip, (past_time, past_time))

        purged = sync_worker.enforce_disk_space_threshold()
        self.assertGreaterEqual(purged, 1)
        self.assertFalse(old_clip.exists())

    def test_enforce_storage_size_threshold(self):
        # Set tiny folder size threshold (e.g. 1 KB = ~0.000001 GB)
        self.system.max_storage_size_gb = 1024 / (1024 ** 3)
        config = AppConfig(system=self.system, cameras=[self.motion_cam])
        sync_worker = SyncWorker(config=config)

        cam_dir = self.motion_cam.get_video_dir(self.system.local_storage_base)
        cam_dir.mkdir(parents=True, exist_ok=True)

        # Oldest clip (2 hours ago, 1 KB) -> should be purged first
        oldest_clip = cam_dir / "2026-08-20_00-00-00.mp4"
        oldest_clip.write_bytes(b"a" * 1024)
        t_oldest = time.time() - 7200
        os.utime(oldest_clip, (t_oldest, t_oldest))

        # Newer eligible clip (1 hour ago, 512 bytes) -> kept once under target
        newer_clip = cam_dir / "2026-08-20_01-00-00.mp4"
        newer_clip.write_bytes(b"b" * 512)
        t_newer = time.time() - 3600
        os.utime(newer_clip, (t_newer, t_newer))

        # Active in-flight clip (now, 512 bytes) -> skipped because < 5 mins old
        active_clip = cam_dir / "2026-08-20_02-00-00.mp4"
        active_clip.write_bytes(b"c" * 512)

        purged = sync_worker.enforce_storage_size_threshold()
        self.assertGreaterEqual(purged, 1)
        self.assertFalse(oldest_clip.exists())
        self.assertTrue(active_clip.exists())

    def test_sync_worker_rclone_command_args(self):
        config = AppConfig(system=self.system, cameras=[self.motion_cam])
        sync_worker = SyncWorker(config=config)

        cam_dir = self.motion_cam.get_video_dir(self.system.local_storage_base)
        cam_dir.mkdir(parents=True, exist_ok=True)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            sync_worker.perform_sync_for_camera(self.motion_cam)

            mock_run.assert_called_once()
            cmd_args = mock_run.call_args[0][0]
            self.assertIn("--drive-use-trash=false", cmd_args)
            self.assertIn("--delete-before", cmd_args)
            self.assertIn("gdrive:SecurityVideos/hallway", cmd_args)

    def test_sync_worker_quota_exceeded_triggers_emergency_prune_and_retry(self):
        config = AppConfig(system=self.system, cameras=[self.motion_cam])
        sync_worker = SyncWorker(config=config)

        cam_dir = self.motion_cam.get_video_dir(self.system.local_storage_base)
        cam_dir.mkdir(parents=True, exist_ok=True)

        old_clip = cam_dir / "2026-08-20_00-00-00.mp4"
        old_clip.write_bytes(b"x" * 2048)
        past_time = time.time() - 3600
        os.utime(old_clip, (past_time, past_time))

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1, stderr="googleapi: Error 403: storageQuotaExceeded"),
                MagicMock(returncode=0, stderr=""),
            ]
            sync_worker.perform_sync_for_camera(self.motion_cam)

            self.assertEqual(mock_run.call_count, 2)
            self.assertFalse(old_clip.exists())

    def test_motion_recorder_split_clip_keeps_monitoring(self):
        cam = CameraConfig(
            name="hallway",
            model="Tapo C110",
            location="interior",
            mac="AA:BB:CC:DD:EE:04",
            ip="192.168.1.104",
            username="cam_user",
            password="testpassword",
            recording_mode="motion",
            post_motion_record_seconds=5,
            max_event_clip_seconds=1,
        )
        recorder = MotionRecorder(camera=cam, system=self.system)

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_proc.wait.return_value = 0
            mock_popen.return_value = mock_proc

            recorder.trigger_motion()
            self.assertIsNotNone(recorder._timer_thread)
            self.assertTrue(recorder._timer_thread.is_alive())

            # Simulate continuous motion over the split threshold
            time.sleep(1.5)
            recorder.trigger_motion()
            time.sleep(0.5)

            # Check that Popen was called at least twice (split occurred)
            self.assertGreaterEqual(mock_popen.call_count, 2)
            # Crucially, the monitor thread MUST still be alive!
            self.assertIsNotNone(recorder._timer_thread)
            self.assertTrue(recorder._timer_thread.is_alive())

            recorder.stop()
            if recorder._timer_thread:
                recorder._timer_thread.join(timeout=3)
            self.assertFalse(recorder._timer_thread.is_alive())

    def test_trigger_motion_restarts_dead_monitor_thread(self):
        cam = CameraConfig(
            name="hallway",
            model="Tapo C110",
            location="interior",
            mac="AA:BB:CC:DD:EE:04",
            ip="192.168.1.104",
            username="cam_user",
            password="testpassword",
            recording_mode="motion",
            post_motion_record_seconds=5,
            max_event_clip_seconds=10,
        )
        recorder = MotionRecorder(camera=cam, system=self.system)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0
        recorder._process = mock_proc
        recorder._timer_thread = None

        recorder.trigger_motion()
        self.assertIsNotNone(recorder._timer_thread)
        self.assertTrue(recorder._timer_thread.is_alive())

        recorder.stop()
        if recorder._timer_thread:
            recorder._timer_thread.join(timeout=3)
        self.assertFalse(recorder._timer_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
