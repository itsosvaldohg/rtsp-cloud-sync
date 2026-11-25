#!/usr/bin/env python3
"""
rtsp-cloud-sync - Main Orchestrator Service
Lightweight, headless IP camera cloud sync & recorder — supervises 24/7 RTSP capture,
ONVIF motion-triggered recording, and automated rclone cloud sync.
"""

import argparse
import logging
import signal
import sys
import time
from typing import Dict, List

from src import __version__
from src.config import AppConfig, CameraConfig, load_config
from src.continuous_worker import ContinuousWorker
from src.motion_recorder import MotionRecorder
from src.onvif_listener import OnvifEventListener
from src.sync_worker import SyncWorker

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rtsp_cloud_sync")


class SyncOrchestrator:
    """
    Main controller coordinating camera capture workers and service lifecycle.
    """

    def __init__(self, config: AppConfig, camera_filter: List[str] = None, no_sync: bool = False):
        self.config = config
        self.camera_filter = camera_filter or []
        self.no_sync = no_sync

        self.continuous_workers: Dict[str, ContinuousWorker] = {}
        self.motion_recorders: Dict[str, MotionRecorder] = {}
        self.onvif_listeners: Dict[str, OnvifEventListener] = {}
        self.sync_worker: SyncWorker = None
        self._running = False

    def get_target_cameras(self) -> List[CameraConfig]:
        """Filters enabled cameras according to CLI arguments."""
        cameras = self.config.get_enabled_cameras()
        if self.camera_filter:
            cameras = [c for c in cameras if c.name in self.camera_filter]
        return cameras

    def start(self) -> None:
        """Launches all camera capture workers and sync manager."""
        target_cameras = self.get_target_cameras()
        if not target_cameras:
            logger.warning("No enabled cameras found to run.")
            return

        self._running = True
        logger.info("==========================================================")
        logger.info(f" rtsp-cloud-sync v{__version__} - Headless IP Camera Cloud Sync")
        logger.info("==========================================================")
        logger.info(f"Local Storage:  {self.config.system.local_storage_base}")
        logger.info(f"Cloud Target:   {self.config.system.rclone_remote_base}")
        logger.info(f"Logs Path:      {self.config.system.log_dir}")
        logger.info(f"Active Cameras: {len(target_cameras)}")

        # 1. Start Continuous Workers (24/7 RTSP capture)
        continuous_cams = [c for c in target_cameras if c.recording_mode == "continuous"]
        for cam in continuous_cams:
            logger.info(
                f"-> Launching Continuous Worker: '{cam.name}' ({cam.model}) "
                f"@ {cam.ip}:{cam.rtsp_port} [Segment: {cam.segment_time_seconds}s]"
            )
            worker = ContinuousWorker(camera=cam, system=self.config.system)
            worker.start()
            self.continuous_workers[cam.name] = worker

        # 2. Start ONVIF Listeners & Motion Recorders (Event-triggered capture)
        motion_cams = [c for c in target_cameras if c.recording_mode == "motion"]
        for cam in motion_cams:
            logger.info(
                f"-> Launching ONVIF Motion Listener: '{cam.name}' ({cam.model}) "
                f"@ {cam.ip} [ONVIF: {cam.onvif_port}, Post-roll: {cam.post_motion_record_seconds}s]"
            )
            recorder = MotionRecorder(camera=cam, system=self.config.system)
            listener = OnvifEventListener(camera=cam, system=self.config.system, recorder=recorder)

            listener.start()
            self.motion_recorders[cam.name] = recorder
            self.onvif_listeners[cam.name] = listener

        # 3. Start Cloud Sync & Purge Worker
        if not self.no_sync:
            logger.info(
                f"-> Launching Cloud Sync Worker (Interval: {self.config.system.sync_interval_seconds}s, "
                f"Retention: {self.config.system.retention_days}d)..."
            )
            self.sync_worker = SyncWorker(config=self.config)
            self.sync_worker.start()

        logger.info("All camera workers and event listeners successfully initialized.")

    def stop(self) -> None:
        """Gracefully stops all active workers and listeners."""
        if not self._running:
            return
        logger.info("Initiating graceful shutdown of all rtsp-cloud-sync workers...")
        self._running = False

        # Stop sync worker
        if self.sync_worker:
            logger.info("Stopping cloud sync worker...")
            self.sync_worker.stop()

        # Stop ONVIF listeners & motion recorders
        for name, listener in self.onvif_listeners.items():
            logger.info(f"Stopping ONVIF listener for '{name}'...")
            listener.stop()
        for name, recorder in self.motion_recorders.items():
            recorder.stop()

        # Stop continuous workers
        for name, worker in self.continuous_workers.items():
            logger.info(f"Stopping continuous worker for '{name}'...")
            worker.stop()

        self.continuous_workers.clear()
        self.onvif_listeners.clear()
        self.motion_recorders.clear()
        logger.info("rtsp-cloud-sync service stopped cleanly.")

    def run(self) -> None:
        """Main service loop with health supervision."""
        self.start()

        try:
            while self._running:
                # Continuous workers health check
                for name, worker in list(self.continuous_workers.items()):
                    if not worker.is_alive():
                        logger.warning(f"Continuous worker '{name}' thread is not running. Restarting...")
                        worker.start()

                time.sleep(10)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self.stop()


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            f"rtsp-cloud-sync v{__version__} - Lightweight, headless IP camera cloud sync & recorder"
        )
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Path to custom cameras.json configuration file",
    )
    parser.add_argument(
        "--cameras",
        nargs="+",
        help="Filter specific camera names to run (e.g. --cameras front_yard hallway)",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Disable background cloud rclone sync and purge",
    )
    parser.add_argument(
        "--sync-now",
        action="store_true",
        help="Run a single cloud sync cycle immediately and exit",
    )
    parser.add_argument(
        "--list-cameras", "-l",
        action="store_true",
        help="Display all configured cameras and exit",
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"rtsp-cloud-sync v{__version__}",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        config = load_config(args.config)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    if args.list_cameras:
        print(f"\nrtsp-cloud-sync v{__version__} - Configured Cameras ({len(config.cameras)}):")
        print("-" * 80)
        print(f"{'NAME':<14} {'ZONE':<10} {'MODEL':<14} {'IP ADDRESS':<16} {'MODE':<12} {'STATUS'}")
        print("-" * 80)
        for cam in config.cameras:
            status = "ENABLED" if cam.enabled else "DISABLED"
            print(
                f"{cam.name:<14} {cam.location:<10} {cam.model:<14} "
                f"{cam.ip:<16} {cam.recording_mode:<12} {status}"
            )
        print("-" * 80 + "\n")
        sys.exit(0)

    if args.sync_now:
        logger.info("Executing on-demand cloud sync cycle...")
        syncer = SyncWorker(config=config)
        syncer.sync_all_cameras()
        logger.info("On-demand sync complete.")
        sys.exit(0)

    orchestrator = SyncOrchestrator(
        config=config,
        camera_filter=args.cameras,
        no_sync=args.no_sync,
    )

    def handle_signal(sig, frame):
        sig_name = "SIGINT" if sig == signal.SIGINT else "SIGTERM"
        logger.info(f"Received signal {sig_name}. Shutting down...")
        orchestrator.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    orchestrator.run()


if __name__ == "__main__":
    main()
