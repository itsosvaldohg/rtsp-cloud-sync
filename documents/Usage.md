# Operations & Usage Manual

## Project: `rtsp-cloud-sync` (Headless IP Camera RTSP/ONVIF Recorder & Cloud Sync)

---

### 1. Quick Command Cheatsheet

| Action | Command |
| :--- | :--- |
| **Check Service Status** | `sudo systemctl status rtsp-cloud-sync.service` |
| **Start Service** | `sudo systemctl start rtsp-cloud-sync.service` |
| **Stop Service** | `sudo systemctl stop rtsp-cloud-sync.service` |
| **Restart Service** | `sudo systemctl restart rtsp-cloud-sync.service` |
| **View Live `systemd` Journal** | `sudo journalctl -u rtsp-cloud-sync.service -f` |
| **List Configured Cameras** | `python3 -m src.main --list-cameras` |
| **Run Immediate Cloud Sync Cycle** | `python3 -m src.main --sync-now` |
| **View Live Sync Log** | `tail -f ~/rtsp-cloud-sync/logs/sync.log` |
| **View Continuous Camera Log** | `tail -f ~/rtsp-cloud-sync/logs/front_yard.log` |
| **View ONVIF Motion Camera Log** | `tail -f ~/rtsp-cloud-sync/logs/hallway.log` |
| **List Local Recorded Segments** | `ls -lh ~/rtsp-cloud-sync/videos/front_yard/` |
| **List Cloud Stored Segments** | `rclone ls gdrive:SecurityVideos/front_yard` |
| **Validate Config JSON Syntax** | `python3 -m json.tool ~/rtsp-cloud-sync/config/cameras.json` |
| **Test ONVIF Port 2020 Reachability** | `nc -zv 192.168.1.104 2020` |

*Tip: Commands in this guide use the default `gdrive` remote name and example camera names (`front_yard`, `hallway`) from [`config/cameras.example.json`](../config/cameras.example.json) — substitute yours if customized.*

---

### 2. Service Installation & Management (`systemd`)

`rtsp-cloud-sync` runs as a unified background service supervised by `systemd` ([`deploy/rtsp-cloud-sync.service`](../deploy/rtsp-cloud-sync.service)). By default, the unit template uses `/home/pi/rtsp-cloud-sync` and user `pi` — update `User`, `Group`, `WorkingDirectory`, and `EnvironmentFile` if your Linux username differs.

#### 2.1 Initial Service Setup
```bash
# 1. Create .env containing your camera password(s)
cat << 'EOF' > ~/rtsp-cloud-sync/.env
# Shared password across all cameras:
CAM_PASS=your_camera_password

# Or per-camera passwords (reference as "${FRONT_YARD_PASS}", etc. in config/cameras.json):
# FRONT_YARD_PASS=password_1
# HALLWAY_PASS=password_2
EOF
chmod 600 ~/rtsp-cloud-sync/.env

# 2. Install systemd unit and reload daemon
sudo cp ~/rtsp-cloud-sync/deploy/rtsp-cloud-sync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rtsp-cloud-sync.service
```

#### 2.2 Starting, Stopping, and Restarting
```bash
# Start the service
sudo systemctl start rtsp-cloud-sync.service

# Stop the service (gracefully finalizes open MP4 trailers and stops workers)
sudo systemctl stop rtsp-cloud-sync.service

# Restart the service (run after editing config/cameras.json or .env)
sudo systemctl restart rtsp-cloud-sync.service

# Inspect service health and recent logs
sudo systemctl status rtsp-cloud-sync.service
```

#### 2.3 Enabling or Disabling Boot Startup
```bash
# Enable auto-start on boot
sudo systemctl enable rtsp-cloud-sync.service

# Disable auto-start on boot
sudo systemctl disable rtsp-cloud-sync.service
```

---

### 3. Managing Camera Configuration (`config/cameras.json`)

Copy [`config/cameras.example.json`](../config/cameras.example.json) to `config/cameras.json` (`config/cameras.json` is ignored by Git so local IPs and credentials are never committed):

```json
{
  "system": {
    "local_storage_base": "~/rtsp-cloud-sync/videos",
    "log_dir": "~/rtsp-cloud-sync/logs",
    "rclone_remote_base": "gdrive:SecurityVideos",
    "retention_days": 1,
    "sync_interval_seconds": 300,
    "min_free_disk_space_gb": 5.0,
    "max_storage_size_gb": 15.0
  },
  "cameras": [
    {
      "name": "front_yard",
      "display_name": "Front Yard",
      "model": "Tapo TC41",
      "location": "exterior",
      "mac": "AA:BB:CC:DD:EE:01",
      "ip": "192.168.1.101",
      "username": "cam_user",
      "password": "${CAM_PASS}",
      "rtsp_port": 554,
      "rtsp_stream": "stream2",
      "onvif_port": 2020,
      "recording_mode": "continuous",
      "segment_time_seconds": 900,
      "enabled": true
    },
    {
      "name": "hallway",
      "display_name": "Main Hallway",
      "model": "Tapo C110",
      "location": "interior",
      "mac": "AA:BB:CC:DD:EE:04",
      "ip": "192.168.1.104",
      "username": "cam_user",
      "password": "${CAM_PASS}",
      "rtsp_port": 554,
      "rtsp_stream": "stream2",
      "onvif_port": 2020,
      "recording_mode": "motion",
      "post_motion_record_seconds": 15,
      "max_event_clip_seconds": 300,
      "enabled": true
    }
  ]
}
```

> [!TIP]
> **Shared vs. Per-Camera Passwords:** Any `${VAR_NAME}` placeholder in `config/cameras.json` is expanded from the environment (`.env` under `systemd`). Use `${CAM_PASS}` if all cameras share one password, or define separate variables in `.env` (e.g., `FRONT_YARD_PASS=...`, `HALLWAY_PASS=...`) and reference `"${FRONT_YARD_PASS}"` / `"${HALLWAY_PASS}"` on each camera entry. Avoid `@` or `:` characters in RTSP passwords (or URL-encode them as `%40` / `%3A`) so `rtsp://user:pass@ip` URLs parse cleanly.

#### 3.1 Adding a New Camera
1. Append a camera object to `"cameras"` in `config/cameras.json` with a unique `"name"` and static `"ip"`.
2. Set `"recording_mode"` to `"continuous"` (24/7 segmented recording) or `"motion"` (ONVIF PullPoint event-triggered recording).
3. Restart the service: `sudo systemctl restart rtsp-cloud-sync.service`.

#### 3.2 Disabling / Pausing a Camera
Set `"enabled": false` on the camera entry and restart `rtsp-cloud-sync.service`.

---

### 4. Camera & Network Verification Commands

Use these diagnostic commands to verify connectivity, RTSP streams, and ONVIF ports before enabling a camera.

#### 4.1 Verify IP Connectivity
```bash
ping -c 3 192.168.1.101  # front_yard
ping -c 3 192.168.1.104  # hallway
```

#### 4.2 Test RTSP (`554`) and ONVIF (`2020`) TCP Ports
```bash
# Test RTSP port
nc -zv 192.168.1.101 554

# Test ONVIF event service port
nc -zv 192.168.1.104 2020
```

#### 4.3 Inspect RTSP Stream Metadata (`ffprobe`)
```bash
ffprobe -rtsp_transport tcp -v error -show_streams \
  "rtsp://cam_user:${CAM_PASS}@192.168.1.101:554/stream2"
```

#### 4.4 Perform a 10-Second Sample Capture
```bash
ffmpeg -rtsp_transport tcp \
  -i "rtsp://cam_user:${CAM_PASS}@192.168.1.101:554/stream2" \
  -c:v copy \
  -c:a aac -b:a 48k \
  -t 10 ~/test_sample.mp4
```

---

### 5. Log Monitoring & Rotation

Logs are written per camera under `~/rtsp-cloud-sync/logs/`.

#### 5.1 Following Live Logs
```bash
# Continuous camera logs
tail -f ~/rtsp-cloud-sync/logs/front_yard.log
tail -f ~/rtsp-cloud-sync/logs/back_yard.log

# ONVIF motion camera logs
tail -f ~/rtsp-cloud-sync/logs/entryway.log
tail -f ~/rtsp-cloud-sync/logs/hallway.log

# Cloud sync & retention watchdog log
tail -f ~/rtsp-cloud-sync/logs/sync.log
```

#### 5.2 Log Rotation (`logrotate`)
Install [`deploy/rtsp-cloud-sync.logrotate`](../deploy/rtsp-cloud-sync.logrotate) to `/etc/logrotate.d/rtsp-cloud-sync` (updating `pi` to your Linux username if needed):
```bash
sudo cp ~/rtsp-cloud-sync/deploy/rtsp-cloud-sync.logrotate /etc/logrotate.d/rtsp-cloud-sync

# Dry-run test logrotate rules
sudo logrotate -d /etc/logrotate.d/rtsp-cloud-sync

# Force immediate log rotation
sudo logrotate -f /etc/logrotate.d/rtsp-cloud-sync
```

---

### 6. Cloud Storage (`rclone`) Operations

> [!NOTE]
> Examples below use the default `gdrive` remote name from [`config/cameras.example.json`](../config/cameras.example.json). Replace `gdrive:` if you configured a different `rclone` remote name (such as `s3:` or `onedrive:`).

#### 6.1 Inspecting Remote Cloud Directories
```bash
# List all synced camera directories on the remote
rclone lsd gdrive:SecurityVideos/

# List uploaded MP4 segments for specific cameras
rclone ls gdrive:SecurityVideos/front_yard
rclone ls gdrive:SecurityVideos/hallway
```

#### 6.2 Running a Manual or Dry-Run Sync
```bash
# Run a full retention + sync cycle immediately from the CLI
python3 -m src.main --sync-now

# Run a dry-run sync without modifying remote files
python3 -c "from src.config import load_config; from src.sync_worker import SyncWorker; SyncWorker(load_config()).sync_all_cameras(dry_run=True)"
```

---

### 7. Headless Remote Administration

For secure remote SSH access without opening router firewall ports, you can optionally install a mesh VPN such as [Tailscale](https://tailscale.com/) or WireGuard on your Raspberry Pi host:

```bash
# Connect to the edge host over LAN or mesh VPN
ssh pi@<raspberry-pi-ip>
```
