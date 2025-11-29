# Security Camera Agent - File Map

## Directory Structure
```
Security-Camera-Agent/
├── Core Application Files
│   ├── sec_cam_main.py              [ENTRY POINT] Main orchestrator
│   ├── config.py                    [CONFIG] System configuration
│   └── config_local.py              [CONFIG] Camera-specific overrides (not in git)
│
├── Camera & Video Capture
│   └── circular_buffer.py           [THREAD 1] Dual-buffer camera system
│                                    ├─ Picture buffer (motion detection)
│                                    └─ H.264 circular buffer (video clips)
│
├── Motion Detection & Events
│   ├── motion_detector.py           [THREAD 2] Frame comparison & motion detection
│   ├── motion_event.py              [COORD] Thread coordination object
│   └── event_processor.py           [THREAD 3] Event processing pipeline
│
├── File Transfer & Communication
│   ├── transfer_manager.py          [THREAD 5] NFS file transfer manager
│   └── api_client.py                [API] Central server REST API client
│
├── Web Services
│   ├── camera_control_api.py        [API] FastAPI control endpoint (port 8001)
│   └── mjpeg_server.py              [THREAD 4] MJPEG streaming (port 8002)
│
├── Monitoring & Logging
│   ├── system_watchdog.py           [MONITOR] Health monitoring & reporting
│   └── logger.py                    [LOG] Centralized logging system
│
├── Service Management Scripts
│   ├── camera_agent.sh              systemd service launcher
│   ├── camera_controller.sh         Controller service launcher
│   ├── run.sh                       Quick start script
│   ├── gitsync.sh                   Git sync utility
│   └── killpython.sh                Process cleanup
│
├── Documentation
│   ├── README.md                    Project overview
│   ├── SETUP.md                     Setup instructions
│   ├── LICENSE                      Apache 2.0 license
│   ├── PROJECT.md                   [NEW] Comprehensive project docs
│   └── INVENTORY_SUMMARY.md         [NEW] Inventory findings
│
├── Configuration & Dependencies
│   ├── requirements.txt             Python dependencies
│   └── .gitignore                   Git ignore rules
│
├── Testing
│   └── testing/
│       └── 1b01_test_config.py      Configuration tests
│
├── Development Tools
│   ├── project_inventory.py         [NEW] Inventory generation script
│   └── .vscode/
│       └── settings.json            VSCode configuration
│
└── Runtime Directories (created at startup)
    ├── tmp/
    │   └── pending/                 Staging for NFS transfer
    ├── security_footage/            NFS mount point
    │   ├── videos/                  Event videos (H.264)
    │   ├── pictures/                Event images (JPEG)
    │   └── thumbs/                  Thumbnails
    └── run.log                      Application log file
```

## Component Interaction Map

```
┌─────────────────────────────────────────────────────────────────┐
│                        sec_cam_main.py                          │
│                     (Main Orchestrator)                         │
│  Initializes → Starts → Monitors → Gracefully Shuts Down       │
└────────────┬────────────────────────────────────────────────────┘
             │
             ├──> config.py + config_local.py
             │    (Load configuration)
             │
             ├──> api_client.py
             │    ├─ Register camera with central server
             │    ├─ Create events
             │    ├─ Update file status
             │    └─ Send logs
             │
             ├──> THREAD 1: circular_buffer.py
             │    ├─ Initialize camera (picam2)
             │    ├─ Capture frames for motion detection
             │    └─ Record H.264 circular buffer
             │
             ├──> THREAD 2: motion_detector.py
             │    ├─ Get frames from circular_buffer
             │    ├─ Compare frames (numpy diff)
             │    ├─ Detect motion
             │    ├─ Create event via api_client
             │    └─ Signal motion_event → THREAD 3
             │
             ├──> THREAD 3: event_processor.py
             │    ├─ Wait for motion_event signal
             │    ├─ Save Picture A + thumbnail (T+0s)
             │    ├─ Save Picture B (T+4s)
             │    ├─ Dump H.264 video (T+35s)
             │    ├─ Create .READY sentinels
             │    └─ Files staged in tmp/pending/
             │
             ├──> THREAD 5: transfer_manager.py
             │    ├─ Monitor tmp/pending/ for .READY files
             │    ├─ Transfer to NFS (security_footage/)
             │    ├─ Update file status via api_client
             │    └─ Clean up sentinels
             │
             ├──> THREAD 4: mjpeg_server.py
             │    ├─ HTTP server on port 8002
             │    ├─ Serve /stream.mjpg
             │    └─ Get frames from circular_buffer
             │
             ├──> camera_control_api.py
             │    ├─ FastAPI server on port 8001
             │    ├─ Control streaming (start/stop)
             │    ├─ Abort event processing
             │    ├─ Pause/resume processing
             │    └─ Health checks
             │
             ├──> system_watchdog.py
             │    ├─ Monitor all threads
             │    ├─ Track buffer health
             │    ├─ Report to central server (60s)
             │    └─ System summaries (5min)
             │
             └──> logger.py
                  ├─ Local file logging (run.log)
                  ├─ Batch log transmission to API
                  └─ Thread-safe operations
```

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     MOTION EVENT PIPELINE                        │
└─────────────────────────────────────────────────────────────────┘

1. CONTINUOUS CAPTURE
   circular_buffer.py
   ├─ Picture buffer: captures 1920x1080 @ 0.5s intervals
   └─ Video buffer: records H.264 circular loop (2500 chunks)

2. MOTION DETECTION
   motion_detector.py
   ├─ Gets frames from circular_buffer
   ├─ Compares prev_frame vs curr_frame (numpy diff)
   ├─ Threshold check → MOTION DETECTED
   ├─ api_client.create_event() → Central Server
   └─ motion_event.set(event_id, timestamp) → Signal Thread 3

3. EVENT PROCESSING
   event_processor.py
   ├─ motion_event.wait_and_get() [BLOCKS until signal]
   ├─ T+0s:  Save Picture A (current_frame)
   │         Create thumbnail (480x270)
   │         Write .READY sentinels
   ├─ T+4s:  Save Picture B (4 seconds later)
   │         Write .READY sentinel
   └─ T+35s: Dump H.264 video buffer
             Write .READY sentinel
             All files → tmp/pending/

4. FILE TRANSFER
   transfer_manager.py
   ├─ Detects .READY sentinels in tmp/pending/
   ├─ Transfers files to NFS (security_footage/)
   ├─ api_client.update_file() → Mark as transferred
   └─ Cleanup sentinels

5. CENTRAL PROCESSING (External)
   Central Server
   ├─ Converts H.264 → MP4
   ├─ Optimizes file sizes
   ├─ AI analysis (moondream + deepseek-r1)
   └─ Updates event descriptions
```

## File Dependencies

```
sec_cam_main.py
  └─> config.py
       └─> config_local.py (import override)
  └─> logger.py
       └─> api_client.py
  └─> api_client.py
       └─> config.py
  └─> circular_buffer.py
       └─> config.py, logger.py
  └─> motion_event.py
       └─> logger.py
  └─> motion_detector.py
       └─> circular_buffer, motion_event, api_client, config, logger
  └─> event_processor.py
       └─> circular_buffer, motion_event, api_client, config, logger
  └─> transfer_manager.py
       └─> api_client, config, logger
  └─> mjpeg_server.py
       └─> circular_buffer, config, logger
  └─> camera_control_api.py
       └─> circular_buffer, mjpeg_server, event_processor, config, logger
  └─> system_watchdog.py
       └─> circular_buffer, motion_detector, event_processor, 
           transfer_manager, api_client, config, logger
```

## Thread Architecture

```
MAIN THREAD (sec_cam_main.py)
  │
  ├─> THREAD 1: CircularBuffer
  │   ├─ Name: "CameraCapture"
  │   ├─ Type: Daemon thread
  │   ├─ Function: Continuous camera capture
  │   └─ Resources: picam2, dual buffers (~36MB)
  │
  ├─> THREAD 2: MotionDetector
  │   ├─ Name: "MotionDetector"
  │   ├─ Type: Daemon thread
  │   ├─ Function: Frame comparison & motion detection
  │   └─> Signals: motion_event.set() → THREAD 3
  │
  ├─> THREAD 3: EventProcessor
  │   ├─ Name: "EventProcessor"
  │   ├─ Type: Daemon thread
  │   ├─ Function: Save images & video to pending/
  │   └─< Waits: motion_event.wait_and_get()
  │
  ├─> THREAD 4: MJPEGServer (HTTP)
  │   ├─ Name: "MJPEGServerHTTP"
  │   ├─ Type: Daemon thread
  │   ├─ Function: Serve MJPEG stream on port 8002
  │   └─ Endpoint: /stream.mjpg
  │
  ├─> THREAD 5: TransferManager
  │   ├─ Name: "TransferManager"
  │   ├─ Type: Daemon thread
  │   ├─ Function: Monitor pending/ & transfer to NFS
  │   └─ Polling: inotify-like file watching
  │
  ├─> THREAD 6: SystemWatchdog
  │   ├─ Name: "SystemWatchdog"
  │   ├─ Type: Daemon thread
  │   ├─ Function: Health monitoring & reporting
  │   └─ Schedule: 60s status, 5min summaries
  │
  ├─> THREAD 7: CameraControlAPI (FastAPI)
  │   ├─ Name: "CameraControlAPI"
  │   ├─ Type: Daemon thread
  │   ├─ Function: REST API on port 8001
  │   └─ Endpoints: /stream/start, /stream/stop, /health, /abort
  │
  └─> THREAD 8+: Logger (async workers)
      ├─ Name: "LogTransmitter"
      ├─ Type: Daemon threads
      └─ Function: Async log batching & API transmission
```

## API Endpoints

```
CAMERA CONTROL API (port 8001)
  POST   /stream/start          Start live streaming
  POST   /stream/stop           Stop live streaming
  POST   /abort                 Abort current event processing
  GET    /health                System health check
  POST   /processing/pause      Pause event processing
  POST   /processing/resume     Resume event processing

MJPEG STREAM (port 8002)
  GET    /stream.mjpg           MJPEG video stream

CENTRAL SERVER API (192.168.1.26:8000)
  POST   /api/v1/cameras/register           Register camera
  POST   /api/v1/events                     Create motion event
  PATCH  /api/v1/events/{id}/status         Update event status
  PATCH  /api/v1/files/{id}/status          Update file status
  POST   /api/v1/logs                       Send log batch
  GET    /api/v1/health                     Health check
```

## File Naming Conventions

```
Event Files (in tmp/pending/ then security_footage/)
  Pictures:    {event_id}_{timestamp}_pic{A|B}.jpg
  Thumbnail:   {event_id}_{timestamp}_thumb.jpg
  Video:       {event_id}_{timestamp}_video.h264
  Sentinels:   {filename}.READY

Examples:
  123_20251126_143022_picA.jpg
  123_20251126_143022_thumb.jpg
  123_20251126_143022_picB.jpg
  123_20251126_143022_video.h264
  123_20251126_143022_picA.jpg.READY

Log Files:
  run.log                         Local application log
```

## Configuration Hierarchy

```
config.py (defaults)
  ├─ CAMERA_ID = "camera_1"
  ├─ CAMERA_NAME = "Front Walkway"
  ├─ CAMERA_LOCATION = "Study"
  ├─ CENTRAL_SERVER_HOST = "192.168.1.26"
  ├─ CENTRAL_SERVER_PORT = 8000
  ├─ CIRCULAR_BUFFER_MAX_CHUNKS = 2500
  ├─ VIDEO_RESOLUTION = (1280, 720)
  └─ ... (all system parameters)
     ↓
config_local.py (overrides - camera-specific)
  ├─ CAMERA_ID = "camera_2"          [REQUIRED]
  ├─ CAMERA_NAME = "Back Patio"      [REQUIRED]
  ├─ CAMERA_LOCATION = "Backyard"    [REQUIRED]
  └─ CENTRAL_SERVER_HOST = "..." (optional override)
```

## Known Issues Reference

```
GC.COLLECT() REMOVAL CHECKLIST (15 total)
├─ circular_buffer.py (10 calls)
│  └─ Lines: 473, 483, 618, 725, 783, 856, 872, 908, 956, 965
├─ event_processor.py (4 calls)
│  └─ Lines: 321, 358, 399, 465
└─ motion_detector.py (1 call)
   └─ Line: 304

ISSUE: Legacy gc.collect() calls create timing windows that trigger
       IMX708 sensor deadlocks during picam2.capture_array()
       
SOLUTION: Remove all gc.collect() calls (no replacement needed)
```

## Quick Reference

**Start System:**
```bash
cd ~/Security-Camera-Agent
./run.sh                          # Development
sudo systemctl start sec-cam      # Production
```

**View Logs:**
```bash
tail -f run.log                   # Local logs
```

**Access Services:**
```bash
# Live stream
http://<camera-ip>:8002/stream.mjpg

# Control API
curl http://<camera-ip>:8001/health

# Central server
http://192.168.1.26:8000
```

**Project Size:** 363.2KB (tracked files)
**Python Files:** 14
**Total Files:** 29
**Threads:** 8+
**Ports:** 8001 (API), 8002 (MJPEG)
