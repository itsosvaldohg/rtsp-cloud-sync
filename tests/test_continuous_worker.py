"""
Unit tests for ContinuousWorker command generation and lifecycle.
"""

import tempfile
import unittest

from src.config import CameraConfig, SystemConfig
from src.continuous_worker import ContinuousWorker


class TestContinuousWorker(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.system = SystemConfig(
            local_storage_base=self.test_dir.name,
            log_dir=self.test_dir.name,
        )
        self.camera = CameraConfig(
            name="front_yard",
            model="Tapo TC41",
            location="exterior",
            mac="AA:BB:CC:DD:EE:01",
            ip="192.168.1.101",
            username="cam_user",
            password="testpassword",
            rtsp_port=554,
            rtsp_stream="stream1",
            recording_mode="continuous",
            segment_time_seconds=900,
        )

    def tearDown(self):
        self.test_dir.cleanup()

    def test_ffmpeg_cmd_generation(self):
        worker = ContinuousWorker(camera=self.camera, system=self.system)
        cmd = worker.build_ffmpeg_cmd()

        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("-rtsp_transport", cmd)
        self.assertIn("tcp", cmd)
        self.assertIn("-c:v", cmd)
        self.assertIn("copy", cmd)
        self.assertIn("-c:a", cmd)
        self.assertIn("aac", cmd)
        self.assertIn("-segment_time", cmd)
        self.assertIn("900", cmd)
        self.assertIn("rtsp://cam_user:testpassword@192.168.1.101:554/stream1", cmd)


if __name__ == "__main__":
    unittest.main()
