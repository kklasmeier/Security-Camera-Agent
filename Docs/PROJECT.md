# Security Camera Agent - Project Documentation

## System Overview
Python-based security camera agent running on Raspberry Pi (Zero 2W/Pi 4/Pi 5) as part of a multi-camera distributed system. Each camera operates independently, communicating with a central server (MariaDB + API) for event coordination while storing media files on shared NFS storage.

**Architecture:** Distributed camera agents → Central API server → Shared NFS storage → AI processing (Pi 5)

## Core Components

### Entry Point
- **sec_cam_main.py** - Main orchestrator that initializes and coordinates all system components in proper sequence: validates config → creates directories → registers camera → starts threads (circular buffer, motion detector, event processor, transfer manager, MJPEG server, watchdog). Handles graceful shutdown and signal handling.

### Configuration
- **config.py** - Centralized configuration class managing all system parameters: camera identity, central server API endpoint, file paths, circular buffer settings (capacity-driven: 2500 chunks ≈ 40-60s), video resolution (1280x720), motion detection thresholds, timing parameters. Designed to support future API-based config fetching.
- **config_local.py** - Camera-specific overrides (CAMERA_ID, CAMERA_NAME, CAMERA_LOCATION). Never committed to git. Required for each deployment to prevent ID collisions.

### Camera & Video Capture
- **circular_buffer.py** (1285 lines) - Manages dual-buffer camera system:
  - Picture buffer: Two 1920x1080 frames updated every 0.5s for motion detection and still images (~12.5MB)
  - H.264 circular buffer: Capacity-driven (2500 chunks), hardware-encoded, provides pre/post-motion footage (~24MB)
  - Handles camera initialization, frame capture via `picam2.capture_array()`, video recording, and progressive buffer dumps

### Motion Detection & Event Processing
- **motion_detector.py** - Thread 2: Compares consecutive frames using numpy diff, detects motion via pixel change threshold, creates events on central server via API, coordinates with circular buffer for video capture timing. Implements cooldown period to prevent event spam.
- **motion_event.py** - Thread-safe Event/threading coordination using threading.Event() for passing motion signals between detector (Thread 2) and processor (Thread 3) threads. Non-blocking set(), blocking wait_and_get().
- **event_processor.py** (504 lines) - Thread 3: Processes motion events in timed sequence: saves Picture A immediately (T+0s), creates thumbnail, waits 4s, saves Picture B (T+4s), dumps H.264 video to pending directory (T+35s). Creates .READY sentinel files after each file for progressive transfer.

### File Transfer & Communication
- **transfer_manager.py** - Thread 5: Monitors pending directory using inotify-like polling, transfers files to NFS when .READY sentinels appear, updates file status via API, handles sentinel cleanup. Implements fail-fast if NFS unavailable to prevent orphaned files. Supports progressive transfer (thumbnails first, then images, then video).
- **api_client.py** (724 lines) - REST API client for central server communication. Handles camera registration (infinite retry), event creation (infinite retry), file status updates (3x retry), log transmission (best-effort). Maintains HTTP session pooling, implements progressive backoff, graceful degradation. Base URL: http://192.168.1.26:8000/api/v1

### Web Services
- **camera_control_api.py** - FastAPI REST endpoint for camera control: live stream requests (abort event processing to switch to streaming mode), event abort, system health checks, pause/resume event processing. Runs on port 8001. Coordinates with circular buffer and event processor for state management.
- **mjpeg_server.py** - MJPEG streaming server (API-controlled, not database-polled). Serves live video feed at /stream.mjpg on port 8002. Integrates with circular buffer for frame access. Auto-detects NoIR vs standard cameras for proper color conversion (BGR→RGB for standard, no conversion for NoIR). Client connection tracking, configurable framerate and JPEG quality.

### Monitoring & Utilities
- **system_watchdog.py** (27.6KB) - Periodic health monitoring: tracks buffer status (chunk counts, memory usage), thread health (alive checks), camera health (frame capture), sends status reports to central API every 60s with 5-minute summaries. Implements graceful shutdown coordination.
- **logger.py** - Centralized logging with both local file output (run.log) and batched API transmission to central server. Manages log rotation, buffering (sends in batches), and graceful fallback when API unavailable. Thread-safe, async log transmission to avoid blocking main threads.

### Service Management Scripts
- **camera_agent.sh** - systemd service launcher script
- **camera_controller.sh** - Controller service launcher
- **run.sh** - Quick start script for development/testing
- **gitsync.sh** - Interactive commit/push to GitHub (fleet releases)
- **Docs/DEPLOYMENT.md** - Fleet deployment workflow (Study NFS dev node + Ansible production)
- **killpython.sh** - Emergency process cleanup

## Data Flow

1. **Motion Detection:** CircularBuffer captures frames → MotionDetector compares → Creates event via API → Signals EventProcessor
2. **Event Processing:** EventProcessor saves Picture A + thumbnail → Waits 4s → Saves Picture B → Dumps H.264 video → Creates .READY sentinels
3. **File Transfer:** TransferManager detects .READY files → Transfers to NFS → Updates file status via API → Cleans up sentinels
4. **Central Coordination:** All cameras register with central API → Events logged to MariaDB → Files tracked by status → AI processing retrieves from NFS

## Key Design Patterns

- **Fail-Fast NFS:** Transfer manager checks NFS availability before processing; refuses to create orphaned local files
- **Progressive Transfer:** Thumbnail appears immediately, images transfer quickly, video processes in background
- **Infinite Retry Critical Ops:** Camera registration and event creation retry forever with backoff (can't lose events)
- **Best-Effort Secondary Ops:** File updates and logs use limited retries with graceful degradation
- **Atomic Operations:** Database updates via API transactions; sentinel files coordinate async file availability
- **Capacity-Driven Buffers:** Circular buffer uses chunk count limits (not time) for predictable memory behavior

## Current State & Known Issues

**Active Issue:** Camera deadlocks during `picam2.capture_array()` on piCameraBack2 (IMX708 standard lens). Root cause: 15x `gc.collect()` calls throughout codebase create timing windows that trigger driver-level issues. Solution in progress: Remove all `gc.collect()` calls (legacy from when FFmpeg conversion ran locally).

**GC.COLLECT() REMOVAL CHECKLIST:**
- circular_buffer.py: 10 calls (lines 473, 483, 618, 725, 783, 856, 872, 908, 956, 965)
- event_processor.py: 4 calls (lines 321, 358, 399, 465)
- motion_detector.py: 1 call (line 304)

**Recent Updates (Nov 26, 2025):**
- circular_buffer.py - Active debugging of capture deadlock
- config.py - Configuration tuning
- api_client.py - Connection handling improvements
- logger.py - Enhanced diagnostics

**Architecture Evolution:**
- Phase 1A: Single-camera with local SQLite database (deprecated)
- Phase 1B: Multi-camera with central MariaDB API (current)
- Phase 7: API-based configuration distribution (planned)

## Dependencies
See requirements.txt for full list. Key dependencies:
- picamera2 - Raspberry Pi camera interface
- numpy - Frame differencing for motion detection
- Pillow - Image processing and thumbnail generation
- requests - HTTP API communication
- FastAPI/uvicorn - REST API server
- SQLAlchemy - Central server database (not used on agents)

## Deployment
Each camera requires:
1. Copy codebase to ~/Security-Camera-Agent
2. Create config_local.py with unique CAMERA_ID
3. Mount NFS storage to security_footage/
4. Install dependencies from requirements.txt
5. Configure systemd service via camera_agent.sh
6. Central server must be running and accessible

**Critical:** Never commit config_local.py - contains camera-specific identity
