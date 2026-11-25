# Supported Features & Recommended Hardware

## Project: `rtsp-cloud-sync`

---

### 1. Supported Features

#### 1.1 Recording & Capture Modes
- **24/7 Continuous Segmented Recording (`continuous`):**
  - Captures uninterrupted RTSP video over TCP (`-rtsp_transport tcp`) and writes timestamped MP4 segments (`YYYY-MM-DD_HH-MM-SS.mp4`) at a configurable interval (`segment_time_seconds`, default `900`s / 15 minutes).
- **ONVIF Motion-Triggered Recording (`motion`):**
  - Subscribes directly to camera hardware motion notifications via ONVIF PullPoint SOAP services (`CreatePullPointSubscription` / `PullMessages`, default TCP port `2020`) authenticated with WS-Security `PasswordDigest`.
  - Dynamically spawns FFmpeg on motion start (`YYYY-MM-DD_HH-MM-SS_motion.mp4`), extends recording while motion continues, appends a configurable post-motion tail (`post_motion_record_seconds`, default `15`s), and cleanly splits extended activity at `max_event_clip_seconds` (default `300`s).
- **Per-Camera Enable/Disable & CLI Filtering:**
  - Toggle individual cameras via `"enabled": true/false` in `config/cameras.json` or run specific subsets on demand with `--cameras <name1> <name2>`.

#### 1.2 Media Pipeline & Playback Compatibility
- **Zero-CPU H.264 Stream Passthrough (`-c:v copy`):**
  - Writes raw H.264 video packets directly to disk without video re-encoding, preserving original camera quality while keeping SBC CPU usage minimal.
- **RTSP Timestamp Sanitization:**
  - Normalizes missing or drifting Wi-Fi camera timestamps (`-fflags +genpts+igndts+discardcorrupt`, `-use_wallclock_as_timestamps 1`, `-bsf:v setts=...`, `-reset_timestamps 1`) for smooth seeking and glitch-free segment playback.
- **Universal AAC Audio Transcoding:**
  - Converts camera G.711 `pcm_alaw` audio into AAC (`-c:a aac -b:a 48k -af aresample=async=1`) so MP4 clips play natively with sound across iOS, Android, macOS, Windows, and web browsers.
- **Clean MP4 Trailer Finalization:**
  - Gracefully signals FFmpeg (`SIGINT` / `terminate`) during segment rotation, motion clip completion, and service shutdown so MP4 `moov` atoms are always finalized cleanly.

#### 1.3 Cloud Sync & Automated Storage Watchdogs
- **Provider-Agnostic `rclone` Mirroring:**
  - Periodically syncs each camera's local folder to `<rclone_remote_base>/<camera_name>` (`sync_interval_seconds`, default `300`s). Works with Google Drive, Amazon S3 / Cloudflare R2, Backblaze B2, OneDrive, Dropbox, Nextcloud / WebDAV, SFTP, and 40+ other `rclone` remotes.
- **In-Flight Write Protection:**
  - Skips actively written segments and temporary files (`--min-age 1m --exclude "*.tmp"` during sync; 5-minute age guard during emergency pruning) to prevent partial uploads or mid-write deletions.
- **Time-Based Rolling Retention (`retention_days`):**
  - Automatically deletes local `.mp4` segments older than `retention_days` (default `1` day) and mirrors deletions upstream.
- **Cloud Quota Cap Watchdog (`max_storage_size_gb`):**
  - Caps total local/mirrored video folder size (default `15.0` GB, sized for free-tier 15 GB Google Drive accounts) using oldest-first FIFO pruning *before* running `rclone sync --delete-before --drive-use-trash=false`.
  - Automatically detects `storageQuotaExceeded` responses from Google Drive, triggers an immediate 1 GB emergency FIFO prune, and retries the sync cycle.
- **Low-Disk-Space Emergency Watchdog (`min_free_disk_space_gb`):**
  - Monitors host filesystem free space and automatically prunes the oldest recorded clips across all camera folders if free space drops below `min_free_disk_space_gb` (default `5.0` GB), restoring a +1 GB safety margin.

#### 1.4 Reliability, Security & Operations
- **Zero External Python Dependencies:**
  - Built entirely with the Python 3.8+ standard library (`urllib.request`, `xml.etree.ElementTree`, `hashlib`, `subprocess`, `threading`) plus system `ffmpeg` and `rclone`.
- **Self-Healing Supervision:**
  - Automatic 10-second RTSP reconnect loop on stream drops (`-timeout 5000000`), proactive 8-minute ONVIF subscription refresh (prior to 10-minute camera expiry) with log throttling, and worker thread health checks in [`src/main.py`](../src/main.py).
- **Environment Variable Secret Expansion:**
  - Supports `${VAR}` and `${VAR:default}` placeholders in `config/cameras.json` loaded from shell environment variables or an untracked `.env` file.
- **Production Daemon Templates:**
  - Ships with [`deploy/rtsp-cloud-sync.service`](../deploy/rtsp-cloud-sync.service) (`systemd` auto-restart on boot/failure) and [`deploy/rtsp-cloud-sync.logrotate`](../deploy/rtsp-cloud-sync.logrotate) for per-camera log rotation.

---

### 2. Camera Compatibility

#### 2.1 Tested Camera Models

| Brand & Model | Form Factor | RTSP Port & Paths | ONVIF Port | Tested Modes |
| :--- | :--- | :--- | :--- | :--- |
| **TP-Link Tapo TC41** | Outdoor Pan/Tilt (Wi-Fi) | `554` (`/stream1` HD, `/stream2` 360p/VGA) | `2020` | `continuous`, `motion` |
| **TP-Link Tapo C110** | Indoor Fixed (Wi-Fi) | `554` (`/stream1` HD, `/stream2` 360p/VGA) | `2020` | `continuous`, `motion` |

> [!TIP]
> **TP-Link Tapo Setup Note:** Enable local RTSP/ONVIF access in the Tapo mobile app under **Camera Settings → Advanced Settings → Camera Account** to create a dedicated username/password. Using `rtsp-cloud-sync` does not disable live viewing or microSD recording in the Tapo app.

#### 2.2 Generic IP Camera Requirements
Any brand of IP camera (Reolink, Amcrest, Hikvision, Dahua, Axis, TP-Link VigI/Tapo, etc.) works out of the box if it meets the protocol requirements for your chosen recording mode:

- **For `continuous` mode:**
  - Local **RTSP over TCP** endpoint (`rtsp://<user>:<pass>@<ip>:<port>/<stream>`).
  - **H.264** video stream (recommended for universal browser/mobile playback with `-c:v copy`).
- **For `motion` mode:**
  - All `continuous` mode requirements above, plus:
  - **ONVIF Profile S** Event service (`http://<ip>:<onvif_port>/onvif/Events`) supporting `CreatePullPointSubscription` and `PullMessages` with WS-Security `PasswordDigest` authentication.

#### 2.3 Known Limitations
- **Cloud-Only Cameras:** Cameras without local RTSP servers (e.g., Google Nest, Ring, Blink, or stock Wyze without bridge software) are not directly supported.
- **H.265 / HEVC Streams:** Because video is copied without re-encoding (`-c:v copy`), H.265-only camera streams will record cleanly to disk/cloud, but some web browsers may require downloading clips to a local player (such as VLC) if hardware HEVC decoding is unavailable.
- **Pre-Motion Buffering:** To maintain near-zero idle CPU and RAM usage, `motion` mode connects and begins recording when the ONVIF event fires (plus `post_motion_record_seconds` after motion ends) rather than keeping an in-memory pre-roll ring buffer.

---

### 3. Recommended Hardware & Sizing

#### 3.1 Host Hardware Recommendations

| Deployment Scale | Recommended Host | RAM | Expected CPU Load | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **1–2 Cameras** | Raspberry Pi Zero 2 W / Pi 3B+ | 512 MB – 1 GB | ~5–10% | Prefer 2.4/5 GHz strong Wi-Fi or USB Ethernet adapter |
| **3–8 Cameras (Recommended)** | **Raspberry Pi 4 Model B** (2 GB / 4 GB) | 2 GB+ | **< 12%** | Ideal 24/7 headless edge gateway over Gigabit Ethernet |
| **8+ Cameras** | Raspberry Pi 5 / x86_64 Mini PC (N100) / Linux VM | 4 GB+ | < 10% | Recommended when syncing many high-bitrate `stream1` feeds |

**Measured 6-Camera Footprint on Raspberry Pi 4 (64-bit Raspberry Pi OS Lite):**
- **2 Continuous RTSP Workers (`-c:v copy`):** ~2–4% CPU total
- **4 ONVIF PullPoint Listeners (Idle polling):** ~1–2% CPU total
- **Active Motion Recording Spikes (2 concurrent clips):** ~2–3% CPU
- **`rclone` Cloud Sync Cycle:** ~2–4% CPU
- **Total Memory Usage (OS + Python + FFmpeg):** < 350 MB RAM

#### 3.2 Local Storage Media
- **Best (24/7 Continuous Fleets):** **External USB 3.0 SSD** (128 GB – 500 GB). Handles continuous multi-stream writes indefinitely with zero SD card wear concerns.
- **Compact Alternative:** **High-Endurance MicroSD Card** (128 GB – 256 GB, e.g., SanDisk MAX Endurance or Samsung PRO Endurance).
- **Avoid:** Standard consumer-grade MicroSD cards for 24/7 `continuous` recording, as constant segment writes can wear out non-endurance flash cells prematurely.

#### 3.3 Storage & Upload Bandwidth Sizing

Estimates below reflect a typical **6-camera hybrid fleet** (2 exterior `continuous` cameras + 4 interior `motion` cameras averaging 1–2 hours of motion per day):

| Stream Profile | Bitrate per Camera | Daily Volume (2 Continuous + 4 Motion) | Recommended Internet Uplink | Fits 15 GB Free Cloud Tier? |
| :--- | :--- | :--- | :--- | :--- |
| **Substream (`stream2`, 360p/480p)** | ~0.3 – 0.6 Mbps | **~7 – 14 GB / day** | ≥ 5 Mbps upload | Yes (~1+ day rolling history) |
| **Mainstream (`stream1`, 1080p/2K)** | ~1.5 – 2.2 Mbps | **~35 – 50 GB / day** | ≥ 10–15 Mbps upload | Requires ≥ 100 GB cloud plan (or shorter FIFO window) |

#### 3.4 Network Best Practices
1. **Wire the Host via Ethernet:** Connect the Raspberry Pi / Linux host to your router via Ethernet so RTSP ingestion and `rclone` cloud uploads do not compete for Wi-Fi airtime with the cameras.
2. **Set Static DHCP Reservations:** Bind each camera's MAC address to a fixed local IP (`192.168.1.x`) on your router so camera IPs in `config/cameras.json` never change after power outages or router reboots.
