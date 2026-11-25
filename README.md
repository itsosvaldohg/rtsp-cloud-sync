# rtsp-cloud-sync

Lightweight, headless IP camera cloud sync & recorder — 24/7 RTSP capture and ONVIF motion-triggered recording with automated `rclone` sync & rolling storage retention (Raspberry Pi friendly, zero Python dependencies).

---

## Features

- **Dual Capture Modes:**
  - **24/7 Continuous Recording (`continuous`):** Uninterrupted RTSP stream ingestion segmented into timestamped MP4 files (e.g., 15-minute chunks) for exterior perimeters.
  - **ONVIF Motion-Triggered Recording (`motion`):** Subscribes directly to camera ONVIF PullPoint motion notifications (TCP port `2020` with WS-Security PasswordDigest auth) and records event clips with configurable post-motion buffers and max-duration splitting.
- **Zero-CPU Video Passthrough:** Copies raw H.264 video frames directly to disk (`-c:v copy`) while transcoding camera `pcm_alaw` audio into AAC (`-c:a aac -b:a 48k`) — keeping Raspberry Pi 4 CPU usage under 12% across 6+ cameras while producing MP4 files natively playable in browsers, iOS, and Android.
- **Automated Cloud Sync & Storage Watchdogs:** Mirrors completed segments to any `rclone` remote (Google Drive, S3, OneDrive, Dropbox, Nextcloud, etc.) while protecting in-flight segments (`--min-age 1m`), purging expired local recordings, and enforcing FIFO pruning when folder size (`max_storage_size_gb`) or free disk space (`min_free_disk_space_gb`) reaches configured limits.
- **Zero Python Dependencies:** Runs entirely on the Python 3 standard library alongside `ffmpeg` and `rclone` — no third-party pip packages or C extensions required.
- **Declarative JSON Config with Secret Expansion:** Manage camera fleets, stream paths, and retention policies in `config/cameras.json` using `${ENV_VAR}` placeholders backed by environment variables or `.env`.

---

## Quick Start

### 1. Prerequisites

Ensure **Python 3.8+**, **FFmpeg**, and **rclone** are installed on your host (e.g., Raspberry Pi OS / Debian / Ubuntu):

```bash
sudo apt update && sudo apt install -y python3 ffmpeg rclone
```

Configure an `rclone` remote — name it **`gdrive`** when prompted so the example config and documentation commands work as-is (or update `system.rclone_remote_base` in `config/cameras.json` if you use another remote name such as `s3:` or `onedrive:`):

```bash
rclone config
```

### 2. Configure Cameras

Clone the repository, copy the example configuration, and supply your camera credentials via environment variables:

```bash
git clone https://github.com/itsosvaldohg/rtsp-cloud-sync.git
cd rtsp-cloud-sync

cp config/cameras.example.json config/cameras.json
```

Edit `config/cameras.json` with your camera IPs, usernames, RTSP streams, and recording modes (`continuous` or `motion`), then store your camera password(s) in `.env` (or export them in your shell):

```bash
cat << 'EOF' > .env
# Option A: Single shared password across all cameras
CAM_PASS=your_camera_password

# Option B: Per-camera passwords (then set "${FRONT_YARD_PASS}", etc. in config/cameras.json)
# FRONT_YARD_PASS=password_1
# HALLWAY_PASS=password_2
EOF
chmod 600 .env
```

> **Tip:** Avoid `@` or `:` characters in camera RTSP passwords (or percent-encode them as `%40` / `%3A`) so FFmpeg parses `rtsp://user:pass@ip` URLs cleanly.

### 3. Run

```bash
# Verify configured cameras
CAM_PASS=your_camera_password python3 -m src.main --list-cameras

# Start the recorder & cloud sync orchestrator
CAM_PASS=your_camera_password python3 -m src.main
```

### 4. Run as a `systemd` Service (Optional)

```bash
# Adjust User/Group and paths in deploy/ files if your username is not 'pi'
sudo cp deploy/rtsp-cloud-sync.service /etc/systemd/system/
sudo cp deploy/rtsp-cloud-sync.logrotate /etc/logrotate.d/rtsp-cloud-sync

sudo systemctl daemon-reload
sudo systemctl enable --now rtsp-cloud-sync.service
```

---

## CLI Options

```text
usage: python3 -m src.main [-h] [--config CONFIG] [--cameras CAMERAS [CAMERAS ...]]
                           [--no-sync] [--sync-now] [--list-cameras] [--version]

options:
  --config, -c PATH     Path to custom cameras.json configuration file
  --cameras NAME ...    Filter specific camera names to run (e.g. --cameras front_yard hallway)
  --no-sync             Disable background cloud rclone sync and purge
  --sync-now            Run a single cloud sync & retention cycle immediately and exit
  --list-cameras, -l    Display all configured cameras and exit
  --version, -v         Show program version and exit
```

---

## Documentation

Detailed design and operational guides are located in [`documents/`](documents/):

- **[Supported Features & Recommended Hardware (Features_and_Hardware.md)](documents/Features_and_Hardware.md):** Current feature set, tested & compatible IP cameras, Raspberry Pi / storage hardware recommendations, and bandwidth sizing.
- **[System Architecture (Architecture.md)](documents/Architecture.md):** Hardware & software topology, Mermaid process/sequence diagrams, ONVIF PullPoint event flow, config specification, and key design decisions.
- **[Operations & Usage Manual (Usage.md)](documents/Usage.md):** Command cheatsheet, `systemd` & `logrotate` setup, RTSP/ONVIF network diagnostics, `rclone` verification, and remote headless administration.

---

## Repository Structure

```text
rtsp-cloud-sync/
├── config/
│   └── cameras.example.json         # Tracked template configuration (copy to cameras.json)
├── deploy/
│   ├── rtsp-cloud-sync.service      # systemd unit file
│   └── rtsp-cloud-sync.logrotate    # logrotate configuration
├── documents/
│   ├── Features_and_Hardware.md     # Supported features, camera compatibility & hardware sizing
│   ├── Architecture.md              # System Architecture & Design
│   └── Usage.md                     # Operations, Commands & Diagnostics
├── src/
│   ├── __init__.py
│   ├── main.py                      # CLI entrypoint & worker orchestrator
│   ├── config.py                    # JSON configuration loader & env var expansion
│   ├── continuous_worker.py         # 24/7 segmented RTSP stream copy worker
│   ├── onvif_listener.py            # Zero-dependency ONVIF PullPoint SOAP subscriber
│   ├── motion_recorder.py           # Event-triggered FFmpeg recorder with post-roll buffer
│   └── sync_worker.py               # Multi-camera rclone sync, retention & disk watchdogs
├── tests/                           # Unit test suite (Python unittest)
├── .gitignore
├── LICENSE                          # MIT License
└── README.md
```

---

## Running Tests

```bash
python3 -m unittest discover -s tests -v
```

---

## License

Released under the [MIT License](LICENSE).  
Copyright (c) 2026 Osvaldo Huerta ([@itsosvaldohg](https://github.com/itsosvaldohg)).
