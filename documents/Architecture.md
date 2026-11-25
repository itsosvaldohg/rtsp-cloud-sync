# System Architecture Document

## Project: `rtsp-cloud-sync` (Headless IP Camera RTSP/ONVIF Recorder & Cloud Sync)

---

### 1. High-Level System Architecture

**`rtsp-cloud-sync`** operates as an autonomous, low-overhead edge recording and cloud sync gateway between IP cameras on a local network and `rclone`-backed cloud storage. A single Linux host (such as a Raspberry Pi 4) coordinates continuous 24/7 RTSP capture for exterior cameras and ONVIF event-triggered capture for interior cameras, driven by a declarative `config/cameras.json` file (modeled on [`config/cameras.example.json`](../config/cameras.example.json)) and modular Python workers in [`src/`](../src/).

```mermaid
graph TD
    subgraph "Local Network (LAN / Subnet 192.168.1.0/24)"
        subgraph "Exterior Zone (Continuous 24/7)"
            EXT1["front_yard (Tapo TC41)<br/>IP: 192.168.1.101<br/>RTSP: Port 554"]
            EXT2["back_yard (Tapo TC41)<br/>IP: 192.168.1.102<br/>RTSP: Port 554"]
        end

        subgraph "Interior Zone (ONVIF Motion-Triggered)"
            INT1["entryway (Tapo C110)<br/>IP: 192.168.1.103<br/>RTSP: 554 | ONVIF: 2020"]
            INT2["hallway (Tapo C110)<br/>IP: 192.168.1.104<br/>RTSP: 554 | ONVIF: 2020"]
            INT3["living_room (Tapo C110)<br/>IP: 192.168.1.105<br/>RTSP: 554 | ONVIF: 2020"]
            INT4["garage (Tapo C110)<br/>IP: 192.168.1.106<br/>RTSP: 554 | ONVIF: 2020"]
        end

        ROUTER["Router / DHCP Gateway<br/>(Static MAC-to-IP Reservations)"]

        subgraph "Edge Host (Raspberry Pi 4 @ 192.168.1.100)"
            CONFIG_FILE[("config/cameras.json + .env<br/>(Camera Specs & Policies)")]

            subgraph "Application Core (src/)"
                ORCHESTRATOR["src/main.py<br/>SyncOrchestrator"]
                CFG_MOD["src/config.py<br/>Config Parser & Env Expander"]

                subgraph "Worker Pipelines"
                    CONT_WORKERS["src/continuous_worker.py<br/>- Continuous RTSP Workers<br/>- 15-min MP4 Segmenter<br/>- Stream Copy (-c:v copy)"]
                    ONVIF_LISTENERS["src/onvif_listener.py<br/>- ONVIF PullPoint SOAP Clients<br/>- Port 2020 Event Subscriber"]
                    MOTION_RECS["src/motion_recorder.py<br/>- Dynamic RTSP Recorder<br/>- +15s Post-Motion Buffer"]
                    SYNC_WORKER["src/sync_worker.py<br/>- Multi-Camera rclone Sync<br/>- Retention & FIFO Watchdogs"]
                end
            end

            LOCAL_BUFFER[("Local Rolling Buffer<br/>~/rtsp-cloud-sync/videos/<camera>/<br/>(Configurable Retention & Cap)")]
            LOGS_BUFFER[("Per-Camera Logs<br/>~/rtsp-cloud-sync/logs/<camera>.log<br/>(logrotate managed)")]
        end
    end

    subgraph "Cloud Storage (rclone)"
        CLOUD["Cloud Remote<br/>(e.g. gdrive:SecurityVideos/<camera>)"]
    end

    EXT1 -->|RTSP Stream| ROUTER
    EXT2 -->|RTSP Stream| ROUTER
    INT1 -->|RTSP + ONVIF| ROUTER
    INT2 -->|RTSP + ONVIF| ROUTER
    INT3 -->|RTSP + ONVIF| ROUTER
    INT4 -->|RTSP + ONVIF| ROUTER

    ROUTER --> ORCHESTRATOR
    CONFIG_FILE --> CFG_MOD
    CFG_MOD --> ORCHESTRATOR
    ORCHESTRATOR --> CONT_WORKERS
    ORCHESTRATOR --> ONVIF_LISTENERS
    ONVIF_LISTENERS -->|Motion Detected| MOTION_RECS
    ORCHESTRATOR --> SYNC_WORKER

    CONT_WORKERS -->|Writes 15-min MP4s| LOCAL_BUFFER
    MOTION_RECS -->|Writes Event MP4s| LOCAL_BUFFER
    CONT_WORKERS --> LOGS_BUFFER
    ONVIF_LISTENERS --> LOGS_BUFFER
    MOTION_RECS --> LOGS_BUFFER

    SYNC_WORKER -->|Purges Expired / Over-Quota Clips| LOCAL_BUFFER
    SYNC_WORKER -->|Mirrors Completed Segments| CLOUD
```

---

### 2. Reference Hardware Topology

```mermaid
classDiagram
    class EdgeHost_RaspberryPi4 {
        +CPU: Quad-core ARM Cortex-A72 @ 1.5/1.8 GHz
        +RAM: 2GB / 4GB LPDDR4
        +OS: Raspberry Pi OS Lite (64-bit Debian)
        +Hostname: raspberrypi (192.168.1.100)
        +Storage: High-Endurance MicroSD / USB SSD
    }
    class ExteriorCameras {
        +Example: TP-Link Tapo TC41 (Outdoor Pan/Tilt)
        +Cameras: front_yard (.101), back_yard (.102)
        +Video: H.264 (Mainstream stream1 / Substream stream2)
        +Audio: G.711 A-law (pcm_alaw)
        +Mode: 24/7 Continuous Streaming (RTSP Port 554)
    }
    class InteriorCameras {
        +Example: TP-Link Tapo C110 (Indoor Fixed)
        +Cameras: entryway (.103), hallway (.104), living_room (.105), garage (.106)
        +Video: H.264
        +Audio: G.711 A-law (pcm_alaw)
        +Mode: ONVIF Motion-Triggered (Port 2020 + Port 554)
    }
    class NetworkRouter {
        +Subnet: 192.168.1.0/24
        +Features: DHCP MAC-to-IP Reservation
    }

    EdgeHost_RaspberryPi4 o-- NetworkRouter
    NetworkRouter o-- ExteriorCameras
    NetworkRouter o-- InteriorCameras
```

#### Reference Node Table

| Node / Device | Placeholder MAC | Example IP | Primary Role | Protocol Interfaces |
| :--- | :--- | :--- | :--- | :--- |
| **Raspberry Pi 4 (`raspberrypi`)** | Host NIC | `192.168.1.100` | Headless Recorder & Sync Host | SSH (`22`), `rclone` HTTPS |
| **`front_yard`** | `AA:BB:CC:DD:EE:01` | `192.168.1.101` | Exterior Front Perimeter | RTSP TCP (`port 554`) |
| **`back_yard`** | `AA:BB:CC:DD:EE:02` | `192.168.1.102` | Exterior Rear Perimeter | RTSP TCP (`port 554`) |
| **`entryway`** | `AA:BB:CC:DD:EE:03` | `192.168.1.103` | Interior Main Entryway | RTSP (`554`) + ONVIF (`2020`) |
| **`hallway`** | `AA:BB:CC:DD:EE:04` | `192.168.1.104` | Interior Hallway | RTSP (`554`) + ONVIF (`2020`) |
| **`living_room`** | `AA:BB:CC:DD:EE:05` | `192.168.1.105` | Interior Living Area | RTSP (`554`) + ONVIF (`2020`) |
| **`garage`** | `AA:BB:CC:DD:EE:06` | `192.168.1.106` | Interior Garage | RTSP (`554`) + ONVIF (`2020`) |

---

### 3. Modular Software Architecture (`src/`)

The codebase is organized into self-contained Python standard-library modules under [`src/`](../src/) supervised by `systemd`:

```mermaid
graph TD
    subgraph "System Supervision (systemd)"
        SVC["rtsp-cloud-sync.service<br/>(ExecStart: /usr/bin/python3 -m src.main)"]
    end

    subgraph "Application Core (src/)"
        MAIN["src/main.py<br/>SyncOrchestrator & Signal Handler"]
        CONFIG["src/config.py<br/>Schema Loader & Env Var Substitution"]

        subgraph "Worker Threads & Subprocesses"
            SUB_CONT["src/continuous_worker.py<br/>Manages Continuous FFmpeg Subprocesses"]
            SUB_ONVIF["src/onvif_listener.py<br/>Manages ONVIF PullPoint SOAP Subscriptions"]
            SUB_REC["src/motion_recorder.py<br/>Spawns Event-Driven FFmpeg Recorders"]
            SUB_SYNC["src/sync_worker.py<br/>Periodic rclone Sync & Storage Watchdogs"]
        end
    end

    subgraph "Configuration Layer (config/)"
        JSON_CFG["config/cameras.json<br/>- System paths, retention & storage caps<br/>- Camera list with IPs & ${ENV_VAR} secrets"]
    end

    SVC -->|Starts / Supervises| MAIN
    MAIN -->|Loads| CONFIG
    CONFIG -->|Reads| JSON_CFG
    MAIN -->|Spawns Continuous Workers| SUB_CONT
    MAIN -->|Spawns Event Listeners| SUB_ONVIF
    SUB_ONVIF -->|Dispatches Motion Trigger| SUB_REC
    MAIN -->|Spawns Background Sync Loop| SUB_SYNC
```

#### Module Responsibilities
- [`src/config.py`](../src/config.py): Parses `config/cameras.json`, expands `${VAR}` / `${VAR:default}` environment variables, resolves `~` paths, and constructs RTSP and `rclone` targets.
- [`src/continuous_worker.py`](../src/continuous_worker.py): Supervises 24/7 FFmpeg segmented stream-copy processes with timestamped filenames, clean `SIGINT` MP4 trailer finalization, and 10-second reconnect backoff.
- [`src/onvif_listener.py`](../src/onvif_listener.py): Implements a zero-dependency ONVIF PullPoint SOAP client (`urllib.request` + `xml.etree.ElementTree`) with WS-Security `PasswordDigest` authentication, 5-second `PullMessages` polling, throttled error logging, and proactive 8-minute subscription renewal.
- [`src/motion_recorder.py`](../src/motion_recorder.py): Dynamically launches FFmpeg when motion begins, extends the session while motion persists, appends `post_motion_record_seconds` after motion ends, and cleanly splits long events at `max_event_clip_seconds`.
- [`src/sync_worker.py`](../src/sync_worker.py): Runs periodic retention cleanup, max-folder-size FIFO pruning (`max_storage_size_gb`), low-disk-space emergency FIFO pruning (`min_free_disk_space_gb`), and `rclone sync` (`--min-age 1m --delete-before --drive-use-trash=false`) with automatic retry on cloud quota errors.
- [`src/main.py`](../src/main.py): CLI entrypoint and `SyncOrchestrator` that coordinates worker lifecycles, monitors continuous worker thread health, and handles `SIGINT`/`SIGTERM` shutdowns.

---

### 4. Configuration File Specification (`config/cameras.json`)

Copy [`config/cameras.example.json`](../config/cameras.example.json) to `config/cameras.json` (which is excluded via `.gitignore`):

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

---

### 5. Sequence & Data Flow Diagrams

#### 5.1 Continuous RTSP Capture Pipeline (`continuous`)

```mermaid
sequenceDiagram
    autonumber
    participant CAM as Exterior Camera (192.168.1.101)
    participant WORKER as src/continuous_worker.py
    participant FFMPEG as FFmpeg Process
    participant DISK as Local Storage (videos/front_yard/)

    WORKER->>FFMPEG: Spawn FFmpeg (-rtsp_transport tcp -c:v copy -c:a aac -f segment)
    FFMPEG->>CAM: Connect RTSP Port 554
    CAM-->>FFMPEG: Continuous H.264 Video + G.711 Audio Stream
    FFMPEG->>DISK: Incrementally write YYYY-MM-DD_HH-MM-SS.mp4
    Note over FFMPEG,DISK: Every 900s (15 min): Finalize MP4 trailer & rotate segment
    FFMPEG->>DISK: Close completed segment and open next
```

#### 5.2 ONVIF Event-Triggered Capture Pipeline (`motion`)

```mermaid
sequenceDiagram
    autonumber
    participant CAM as Interior Camera (192.168.1.104)
    participant ONVIF as src/onvif_listener.py
    participant REC as src/motion_recorder.py
    participant FFMPEG as Event FFmpeg Process
    participant DISK as Local Storage (videos/hallway/)

    ONVIF->>CAM: SOAP CreatePullPointSubscription (Port 2020, WSSE Digest)
    CAM-->>ONVIF: Subscription Endpoint URL (InitialTerminationTime=PT10M)

    loop Polling PullPoint Messages (Renewed Every 8m)
        ONVIF->>CAM: SOAP PullMessages(Timeout=PT5S)
        CAM-->>ONVIF: NotificationMessage (Motion = true)
    end

    ONVIF->>REC: trigger_motion()
    REC->>FFMPEG: Spawn FFmpeg -> YYYY-MM-DD_HH-MM-SS_motion.mp4
    FFMPEG->>CAM: Connect RTSP Port 554
    CAM-->>FFMPEG: Stream H.264 Video + Audio
    FFMPEG->>DISK: Write motion clip

    loop While Motion Persists
        ONVIF->>CAM: SOAP PullMessages()
        CAM-->>ONVIF: Motion = true (Extends post-motion timer)
    end

    Note over REC: Motion ceases - wait +15s post_motion_record_seconds
    REC->>FFMPEG: Send SIGINT / Terminate cleanly
    FFMPEG->>DISK: Write MP4 trailer and close clip
```

#### 5.3 Cloud Mirror, Retention & Quota Watchdog Pipeline

```mermaid
sequenceDiagram
    autonumber
    participant SYNC as src/sync_worker.py
    participant DISK as Local Storage (videos/*/)
    participant CLOUD as Cloud Storage (gdrive:SecurityVideos/*)

    Note over SYNC: Sync Cycle Runs Every 300s (5 minutes)
    SYNC->>DISK: 1. Purge MP4 files older than retention_days
    SYNC->>DISK: 2. Enforce max_storage_size_gb (FIFO prune oldest clips > 5m old)
    SYNC->>DISK: 3. Enforce min_free_disk_space_gb (Emergency FIFO prune)

    loop For Each Enabled Camera
        SYNC->>CLOUD: 4. rclone sync --min-age 1m --delete-before --drive-use-trash=false
        CLOUD-->>SYNC: Sync Complete (or triggers emergency FIFO prune + retry on quota error)
    end
```

---

### 6. Key Technical Design Decisions

1. **Zero External Python Dependencies:**
   - *Rationale:* Implementing ONVIF WS-Security `PasswordDigest` headers, SOAP PullPoint requests, XML parsing, and process supervision exclusively with Python's standard library eliminates `pip` dependency breakage on headless Raspberry Pi installations.

2. **Hybrid Capture Strategy (`continuous` + `motion`):**
   - *Rationale:* Recording exterior zones continuously while recording interior zones only on hardware ONVIF motion triggers cuts daily disk writes and upstream bandwidth by >60% without sacrificing perimeter coverage.

3. **Zero-CPU Direct Stream Copy (`-c:v copy`) + Timestamp Sanitization:**
   - *Rationale:* Passing raw H.264 packets directly into MP4 segments while normalizing timestamps (`-fflags +genpts+igndts+discardcorrupt`, `-bsf:v setts=...`, `-af aresample=async=1`) keeps CPU load minimal while preventing playback stutter from Wi-Fi RTSP clock drift.

4. **Quota-Deadlock-Safe Cloud Sync (`--delete-before` + `--drive-use-trash=false`):**
   - *Rationale:* Running local retention and `max_storage_size_gb` pruning *before* `rclone sync --delete-before --drive-use-trash=false` frees cloud storage space prior to uploading new segments, preventing 15 GB free-tier Google Drive accounts from deadlocking on full trash or quota limits.
