"""
Unit tests for configuration loader and camera models.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import AppConfig, CameraConfig, SystemConfig, load_config


class TestConfigLoader(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.test_dir.name) / "cameras.json"
        self.example_config_path = (
            Path(__file__).resolve().parent.parent / "config" / "cameras.example.json"
        )
        self._env_patcher = patch.dict(
            os.environ,
            {
                "HOME": self.test_dir.name,
                "USERPROFILE": self.test_dir.name,
                "CAM_PASS": "example_secret_pass",
            },
        )
        self._env_patcher.start()

    def tearDown(self):
        self._env_patcher.stop()
        self.test_dir.cleanup()

    def test_system_config_defaults(self):
        defaults = SystemConfig()
        self.assertEqual(defaults.local_storage_base, "~/rtsp-cloud-sync/videos")
        self.assertEqual(defaults.log_dir, "~/rtsp-cloud-sync/logs")
        self.assertEqual(defaults.rclone_remote_base, "gdrive:SecurityVideos")
        self.assertEqual(defaults.max_storage_size_gb, 15.0)
        self.assertEqual(defaults.min_free_disk_space_gb, 5.0)

    def test_load_example_cameras_json(self):
        config = load_config(str(self.example_config_path))
        self.assertIsInstance(config, AppConfig)
        self.assertEqual(len(config.cameras), 4)
        self.assertEqual(config.system.local_storage_base, "~/rtsp-cloud-sync/videos")
        self.assertEqual(config.system.log_dir, "~/rtsp-cloud-sync/logs")
        self.assertEqual(config.system.rclone_remote_base, "gdrive:SecurityVideos")
        self.assertEqual(config.system.max_storage_size_gb, 15.0)
        self.assertEqual(config.system.min_free_disk_space_gb, 5.0)

        continuous = config.get_continuous_cameras()
        self.assertEqual([c.name for c in continuous], ["front_yard", "back_yard"])

        motion = config.get_motion_cameras()
        self.assertEqual([c.name for c in motion], ["entryway", "hallway"])

    def test_rtsp_url_construction(self):
        config = load_config(str(self.example_config_path))
        front_yard = config.get_camera("front_yard")
        self.assertIsNotNone(front_yard)
        self.assertEqual(
            front_yard.get_rtsp_url(),
            "rtsp://cam_user:example_secret_pass@192.168.1.101:554/stream2",
        )
        hallway = config.get_camera("hallway")
        self.assertIsNotNone(hallway)
        self.assertEqual(
            hallway.get_rtsp_url(),
            "rtsp://cam_user:example_secret_pass@192.168.1.104:554/stream2",
        )
        self.assertEqual(
            hallway.get_rclone_destination(config.system.rclone_remote_base),
            "gdrive:SecurityVideos/hallway",
        )

    def test_env_var_substitution(self):
        os.environ["CUSTOM_TEST_PASSWORD"] = "secret_pass_123"
        sample_json = {
            "system": {
                "local_storage_base": self.test_dir.name,
                "log_dir": self.test_dir.name,
            },
            "cameras": [
                {
                    "name": "test_cam",
                    "model": "Tapo TC41",
                    "location": "exterior",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "ip": "192.168.1.199",
                    "username": "testuser",
                    "password": "${CUSTOM_TEST_PASSWORD:fallback}",
                    "recording_mode": "continuous",
                }
            ],
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(sample_json, f)

        loaded = load_config(str(self.config_path))
        cam = loaded.get_camera("test_cam")
        self.assertIsNotNone(cam)
        self.assertEqual(cam.password, "secret_pass_123")


if __name__ == "__main__":
    unittest.main()
