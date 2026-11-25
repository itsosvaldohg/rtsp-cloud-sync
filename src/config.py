"""
Configuration loader and validator for rtsp-cloud-sync.
Parses config/cameras.json with environment variable substitution and path resolution.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class SystemConfig:
    local_storage_base: str = "~/rtsp-cloud-sync/videos"
    log_dir: str = "~/rtsp-cloud-sync/logs"
    rclone_remote_base: str = "gdrive:SecurityVideos"
    retention_days: int = 1
    sync_interval_seconds: int = 300
    min_free_disk_space_gb: float = 5.0
    max_storage_size_gb: float = 15.0


@dataclass
class CameraConfig:
    name: str
    model: str
    location: str  # "exterior" or "interior"
    mac: str
    ip: str
    username: str
    password: str
    rtsp_port: int = 554
    rtsp_stream: str = "stream2"
    onvif_port: int = 2020
    recording_mode: str = "continuous"  # "continuous" or "motion"
    segment_time_seconds: int = 900
    post_motion_record_seconds: int = 15
    max_event_clip_seconds: int = 300
    enabled: bool = True
    display_name: Optional[str] = None

    @property
    def label(self) -> str:
        return self.display_name or self.name

    def get_rtsp_url(self) -> str:
        """Constructs authenticated RTSP URL over TCP."""
        return (
            f"rtsp://{self.username}:{self.password}@"
            f"{self.ip}:{self.rtsp_port}/{self.rtsp_stream}"
        )

    def get_video_dir(self, base_storage: str) -> Path:
        """Returns local directory path for this camera's video segments."""
        return Path(base_storage).expanduser().resolve() / self.name

    def get_log_file(self, log_dir: str) -> Path:
        """Returns log file path for this camera."""
        return Path(log_dir).expanduser().resolve() / f"{self.name}.log"

    def get_rclone_destination(self, remote_base: str) -> str:
        """Returns remote cloud directory target for rclone."""
        return f"{remote_base}/{self.name}"


@dataclass
class AppConfig:
    system: SystemConfig = field(default_factory=SystemConfig)
    cameras: List[CameraConfig] = field(default_factory=list)

    def get_camera(self, name: str) -> Optional[CameraConfig]:
        for cam in self.cameras:
            if cam.name == name:
                return cam
        return None

    def get_enabled_cameras(self) -> List[CameraConfig]:
        return [cam for cam in self.cameras if cam.enabled]

    def get_continuous_cameras(self) -> List[CameraConfig]:
        return [
            cam for cam in self.cameras
            if cam.enabled and cam.recording_mode == "continuous"
        ]

    def get_motion_cameras(self) -> List[CameraConfig]:
        return [
            cam for cam in self.cameras
            if cam.enabled and cam.recording_mode == "motion"
        ]


def _expand_env_vars(text: str) -> str:
    """
    Expands ${VAR} or ${VAR:default} patterns from environment variables.
    """
    pattern = re.compile(r"\$\{([A-Za-z0-9_]+)(?::([^}]*))?\}")

    def replace_match(match):
        var_name = match.group(1)
        default_val = match.group(2) if match.group(2) is not None else ""
        return os.environ.get(var_name, default_val)

    return pattern.sub(replace_match, text)


def _resolve_config_path(config_path: Optional[str] = None) -> Path:
    """Finds the configuration file path."""
    if config_path:
        path = Path(config_path).expanduser().resolve()
        if path.is_file():
            return path
        raise FileNotFoundError(f"Config file not found: {config_path}")

    # Standard lookup locations
    locations = [
        Path.cwd() / "config" / "cameras.json",
        Path(__file__).resolve().parent.parent / "config" / "cameras.json",
        Path.home() / "rtsp-cloud-sync" / "config" / "cameras.json",
    ]

    for loc in locations:
        if loc.is_file():
            return loc

    raise FileNotFoundError(
        "Could not locate cameras.json. Searched locations:\n"
        + "\n".join(f"  - {loc}" for loc in locations)
        + "\nTip: Copy config/cameras.example.json to config/cameras.json and configure your cameras."
    )


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """
    Loads and validates configuration from JSON file.
    """
    path = _resolve_config_path(config_path)

    with open(path, "r", encoding="utf-8") as f:
        raw_content = f.read()

    # Expand environment variables across JSON content
    expanded_content = _expand_env_vars(raw_content)
    data = json.loads(expanded_content)

    # Parse system section
    sys_data = data.get("system", {})
    system_config = SystemConfig(
        local_storage_base=sys_data.get("local_storage_base", "~/rtsp-cloud-sync/videos"),
        log_dir=sys_data.get("log_dir", "~/rtsp-cloud-sync/logs"),
        rclone_remote_base=sys_data.get("rclone_remote_base", "gdrive:SecurityVideos"),
        retention_days=int(sys_data.get("retention_days", 1)),
        sync_interval_seconds=int(sys_data.get("sync_interval_seconds", 300)),
        min_free_disk_space_gb=float(sys_data.get("min_free_disk_space_gb", 5.0)),
        max_storage_size_gb=float(sys_data.get("max_storage_size_gb", 15.0)),
    )

    # Ensure system directories exist
    os.makedirs(Path(system_config.local_storage_base).expanduser(), exist_ok=True)
    os.makedirs(Path(system_config.log_dir).expanduser(), exist_ok=True)

    # Parse cameras section
    cameras: List[CameraConfig] = []
    for cam_data in data.get("cameras", []):
        camera = CameraConfig(
            name=cam_data["name"],
            model=cam_data.get("model", "Generic IP Camera"),
            location=cam_data.get("location", "interior"),
            mac=cam_data.get("mac", ""),
            ip=cam_data["ip"],
            username=cam_data["username"],
            password=cam_data["password"],
            rtsp_port=int(cam_data.get("rtsp_port", 554)),
            rtsp_stream=cam_data.get("rtsp_stream", "stream2"),
            onvif_port=int(cam_data.get("onvif_port", 2020)),
            recording_mode=cam_data.get("recording_mode", "continuous"),
            segment_time_seconds=int(cam_data.get("segment_time_seconds", 900)),
            post_motion_record_seconds=int(cam_data.get("post_motion_record_seconds", 15)),
            max_event_clip_seconds=int(cam_data.get("max_event_clip_seconds", 300)),
            enabled=bool(cam_data.get("enabled", True)),
            display_name=cam_data.get("display_name"),
        )

        # Ensure camera-specific video directory exists
        if camera.enabled:
            os.makedirs(camera.get_video_dir(system_config.local_storage_base), exist_ok=True)

        cameras.append(camera)

    return AppConfig(system=system_config, cameras=cameras)
