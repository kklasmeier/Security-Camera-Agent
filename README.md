# Security-Camera-Agent
Camera agent for Raspberry Pi Zero 2W security cameras
ARCHITECTURE OVERVIEW
┌─────────────────────────────────────────────────────────────┐
│              MULTI-CAMERA SYSTEM ARCHITECTURE               │
│                    (5 Threads Active)                       │
└─────────────────────────────────────────────────────────────┘

Thread 1: Circular Buffer (Camera)
    └─► Captures H.264 video continuously
        └─► Feeds to Thread 2 and Thread 3

Thread 2: Motion Detector
    └─► Monitors for motion
        └─► Creates events via API
            └─► Signals Thread 3

Thread 3: Event Processor
    └─► Saves pictures and video
        └─► Creates sentinel files (.READY)
            └─► Triggers Thread 5

Thread 4: MJPEG Server (Future)
    └─► Will provide live streaming

Thread 5: Transfer Manager ⭐ NEW!
    └─► Monitors for sentinels
        └─► Transfers files to NFS
            └─► Notifies central server API
                └─► Cleans up local files

Result: Fully autonomous camera system! 🎉

🔧 CONFIGURATION SUMMARY
Transfer Settings:
pythonTRANSFER_CHECK_INTERVAL = 0.25  # Poll every 0.25 seconds
TRANSFER_TIMEOUT = 30           # Network timeout: 30 seconds
# No max retries - retry forever until success
Directory Structure:
/home/pi/Security-Camera-Agent/
├── tmp/pending/              # Local staging (transient)
└── security_footage/         # NFS mount
    └── camera_1/             # Per-camera (pre-created on NFS)
        ├── pictures/
        ├── thumbs/
        └── videos/