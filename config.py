"""
Security Camera System - Configuration File
============================================
All system configuration parameters in one place.
Modify these values to tune system behavior.

ARCHITECTURE NOTE:
This Config class is designed for multi-camera deployment with central server.
Phase 1B: Config reads from local variables (like current system)
Phase 7: Config will fetch from central server API

IMPORTANT: Camera Identity Override Required
============================================
Each camera MUST have a config_local.py file defining:
- CAMERA_ID (unique identifier)
- CAMERA_NAME (descriptive name)
- CAMERA_LOCATION (physical location)

This prevents accidental camera ID collisions in multi-camera deployments.
See SETUP.md for instructions on creating config_local.py
"""

import os
import sys
from pathlib import Path


class Config:
    """
    Configuration management for security camera system.
    
    This class encapsulates all configuration parameters and provides
    methods for validation and directory management.
    
    Design allows for future API-based configuration (Phase 7) by
    replacing __init__ to fetch from central server instead of
    using local variables.
    """
    
    def __init__(self):
        """
        Initialize configuration with default values, then apply local overrides.
        
        CRITICAL: config_local.py MUST exist with camera identity settings.
        System will refuse to start without it to prevent camera ID conflicts.
        
        Future implementation (Phase 7):
        - Fetch from central server API: GET /api/v1/cameras/{camera_id}/config
        - Update instance variables with server values
        - Maintain backward compatibility
        """
        
        # ====================================================================
        # CAMERA IDENTITY
        # ====================================================================
        # These settings identify this camera in the multi-camera system
        # MUST BE UNIQUE for each camera deployment
        
        self.CAMERA_ID = "camera_1"              # Unique ID: camera_1, camera_2, etc.
        self.CAMERA_NAME = "Front Walkway"       # Human-readable name
        self.CAMERA_LOCATION = "Study"           # Physical location description
        
        # ====================================================================
        # CENTRAL SERVER
        # ====================================================================
        # API endpoint for multi-camera central server
        
        self.CENTRAL_SERVER_HOST = "192.168.1.26"  # Central server IP
        self.CENTRAL_SERVER_PORT = 8000             # API port
        self.CENTRAL_SERVER_API_BASE = f"http://{self.CENTRAL_SERVER_HOST}:{self.CENTRAL_SERVER_PORT}/api/v1"
        
        # ====================================================================
        # FILE PATHS
        # ====================================================================
        
        self.BASE_PATH = "/home/pi/Security-Camera-Agent"
        
        # NFS mount point (where pictures, videos, thumbs are stored)
        # This is managed by the central server
        self.NFS_MOUNT_PATH = os.path.join(self.BASE_PATH, "security_footage")
        
        # Local temporary directories
        self.TMP_PATH = os.path.join(self.BASE_PATH, "tmp")
        self.PENDING_DIR = os.path.join(self.TMP_PATH, "pending")  # Staging for transfer
        
        # NFS subdirectories (on mounted filesystem, managed by central server)
        # These are NOT created by ensure_directories() - they exist on NFS
        self.VIDEO_PATH = os.path.join(self.NFS_MOUNT_PATH, "videos")
        self.PICTURES_PATH = os.path.join(self.NFS_MOUNT_PATH, "pictures")
        self.THUMBS_PATH = os.path.join(self.NFS_MOUNT_PATH, "thumbs")
        
        # DEPRECATED (Phase 1B):
        # - DATABASE_PATH: Replaced by central server API
        # Database now managed centrally, cameras use API for event logging
        
        # ====================================================================
        # CIRCULAR BUFFER SETTINGS
        # ====================================================================
        
        # Circular buffer maintains ~40-60 seconds of continuous footage (capacity-driven)
        # When motion detected, wait ~30s then dump entire buffer for complete event coverage
        # Buffer captures: [T-30s (pre-event) → T0 (motion) → T+30s (post-event)]
        
        self.CIRCULAR_BUFFER_SECONDS = 60   # Target duration (approximate)
        
        # Post-motion recording: wait this many seconds after motion detection
        # This allows the continuous buffer to capture post-event footage
        # With 2000-chunk buffer, we wait ~30s then dump the entire buffer
        self.POST_MOTION_WAIT_SECONDS = 50
        
        # Abort timeout: maximum time to wait for event processing to abort (seconds)
        # When live streaming is requested during event processing, wait up to this long
        # Even flushing a large video buffer should complete within 2-3 seconds
        self.ABORT_TIMEOUT_SECONDS = 5.0
        
        # Total event processing time: ~30s wait + ~3s dump = ~33 seconds
        # Video contains: pre-event (~30s) + during event + post-event (~30s) ≈ 60s total
        
        # ====================================================================
        # VIDEO BUFFER SETTINGS (Capacity-Driven)
        # ====================================================================
        
        # Circular buffer capacity (chunks, not time-based)
        # This determines how much pre-motion footage is captured.
        # Actual duration will vary based on scene complexity and motion.
        # 
        # Tuning guide:
        # - Start with 2000 chunks (typically 40-60 seconds)
        # - Monitor logs to see actual durations
        # - Increase if videos too short, decrease if too long
        # 
        # At ~12KB per chunk average:
        #   1000 chunks ≈ 12 MB ≈ 20-30 seconds
        #   1500 chunks ≈ 18 MB ≈ 30-40 seconds
        #   2000 chunks ≈ 24 MB ≈ 40-60 seconds  (RECOMMENDED - single continuous dump)
        self.CIRCULAR_BUFFER_MAX_CHUNKS = 2500
        
        # Maximum memory for circular buffer (bytes)
        # Safety limit to prevent runaway memory usage
        self.CIRCULAR_BUFFER_MAX_BYTES = 60 * 1024 * 1024  # 60 MB
        
        # NOTE: BUFFER_DURATION_SECONDS removed - now capacity-driven
        # The actual duration will be logged during operation
        
        # ====================================================================
        # VIDEO SETTINGS
        # ====================================================================
        
        # Video resolution (width, height)
        self.VIDEO_RESOLUTION = (1280, 720)
        
        # Video framerate (fps)
        self.VIDEO_FRAMERATE = 15
        
        # H.264 bitrate (bits per second)
        # 3Mbps provides good quality at 720p
        self.VIDEO_BITRATE = 3000000
        
        # ====================================================================
        # PICTURE CAPTURE SETTINGS
        # ====================================================================
        
        # How often to capture full-resolution frames for motion detection (seconds)
        # These frames are used for both motion comparison AND saving as Picture A/B
        self.PICTURE_CAPTURE_INTERVAL = 0.5
        
        # JPEG quality for saved images (1-100)
        self.JPEG_QUALITY = 80
        
        # Thumbnail size (width, height)
        self.THUMBNAIL_SIZE = (240, 180)
        
        # ====================================================================
        # VIDEO FORMAT SETTINGS
        # ====================================================================
        
        # Video file format
        # H.264 only - MP4 conversion happens on central server
        self.VIDEO_OUTPUT_FORMAT = 'h264'  # Changed from 'mp4' in legacy system
        
        # DEPRECATED (Phase 1B):
        # - FFMPEG_TIMEOUT: MP4 conversion moved to central server
        # Cameras now record raw H.264, central server handles conversion
        
        # ====================================================================
        # REBOOT WATCHDOG CONFIGURATION
        # ====================================================================
        # Automatic camera reboot on hang detection
        
        # Core timing
        self.REBOOT_WATCHDOG_CHECK_INTERVAL = 300          # 5 minutes between checks
        self.REBOOT_WATCHDOG_HANG_THRESHOLD = 60           # Trigger reboot after 60m of NoFrames
        
        # Safety limits
        self.REBOOT_WATCHDOG_COOLDOWN = 300                # 5 minutes minimum between reboots
        self.REBOOT_WATCHDOG_MAX_REBOOTS_PER_HOUR = 5      # Trigger pause after 5 reboots/hour
        self.REBOOT_WATCHDOG_PAUSE_DURATION = 24           # Pause reboots for 24 hours
        
        # Reboot execution
        self.REBOOT_WATCHDOG_PRE_REBOOT_DELAY = 60         # Grace period before reboot (seconds)
        
        # Post-reboot monitoring
        self.REBOOT_WATCHDOG_POST_REBOOT_CHECK_INTERVAL = 30    # Check every 30 seconds
        self.REBOOT_WATCHDOG_POST_REBOOT_TIMEOUT = 300          # Give up after 5 minutes
        
        # Feature flags
        self.REBOOT_WATCHDOG_ENABLED = True                     # Master on/off switch
        self.REBOOT_WATCHDOG_CHECK_STREAMING = True             # Skip reboot if streaming
        
        # Tracking files
        self.REBOOT_WATCHDOG_HISTORY_FILE = '/var/tmp/camera-reboot-history.json'
        self.REBOOT_WATCHDOG_DISABLE_FLAG = '/var/tmp/disable-auto-reboot'
        
        # Camera Control API endpoint (local)
        self.CAMERA_CONTROL_API_PORT = 5000
        self.CAMERA_CONTROL_API_BASE = f"http://localhost:{self.CAMERA_CONTROL_API_PORT}"
        
        # ====================================================================
        # MOTION DETECTION SETTINGS
        # ====================================================================
        
        # Motion detection logging
        self.MOTION_LOG_INTERVAL = 100  # Log motion check stats every N checks (0 = disable)
        self.MOTION_LOG_DETAILS = True  # Log detailed comparison info when motion detected
        
        # Resolution for motion detection comparison (downscaled for efficiency)
        # Original frames are 1280x720, downscaled to 100x75 for comparison
        self.DETECTION_RESOLUTION = (100, 75)
        
        # Threshold: how much a single pixel must change to be considered "changed"
        # Range: 0-255 (higher = less sensitive to small changes)
        self.MOTION_THRESHOLD = 60
        
        # Sensitivity: how many pixels must change to trigger motion detection
        # This is the count of changed pixels in the detection resolution frame
        self.MOTION_SENSITIVITY = 50
        
        # Cooldown period between motion events (seconds)
        # Must be longer than Thread 3 processing time (~17s) to prevent overlaps
        self.MOTION_COOLDOWN_SECONDS = 70
        
        # ====================================================================
        # WEB/STREAMING SETTINGS
        # ====================================================================
        
        # Port for MJPEG livestream server
        self.LIVESTREAM_PORT = 8080
        
        # Picture capture interval during livestream (faster for smooth stream)
        # Normal operation: 0.5s (2fps), Streaming: 0.1s (10fps)
        self.LIVESTREAM_CAPTURE_INTERVAL = 0.1
        
        # Livestream framerate (fps)
        # Lower than video recording to reduce CPU load
        self.LIVESTREAM_FRAMERATE = 10
        
        # MJPEG stream quality (lower than saved images to reduce bandwidth)
        self.LIVESTREAM_JPEG_QUALITY = 65
        
        # ====================================================================
        # TRANSFER SETTINGS
        # ====================================================================
        # Configuration for TransferManager (Session 1B-7)
        # Controls how files move from pending/ to NFS storage
        
        self.TRANSFER_CHECK_INTERVAL = 0.25    # Check for sentinel files every 0.25s (4x per second)
        self.TRANSFER_TIMEOUT = 30             # Network timeout for file operations (seconds)
        
        # NOTE: TransferManager retries indefinitely (no max retries)
        # Files remain in pending/ until successfully transferred or manually removed
        
        # ====================================================================
        # LOGGING SETTINGS
        # ====================================================================
        
        # Logging destination
        # "api" = send to central server, "local" = SQLite only (fallback/testing)
        self.LOG_DESTINATION = "api"
        
        # How often to send log batches to central server (seconds)
        # Batching reduces network overhead
        self.LOG_BATCH_INTERVAL = 10
        
        # Local log buffer size (number of log entries to buffer before sending)
        # Send when buffer reaches this size OR interval expires (whichever comes first)
        self.LOG_BUFFER_SIZE = 100
        
        # ====================================================================
        # SYSTEM SETTINGS
        # ====================================================================
        
        # Camera warmup time (seconds)
        # Time to allow camera to adjust exposure/white balance on startup
        self.CAMERA_WARMUP_SECONDS = 2
        
        # Graceful shutdown timeout (seconds)
        # Maximum time to wait for threads to stop cleanly
        self.SHUTDOWN_TIMEOUT_SECONDS = 10
    
        # ====================================================================
        # CAMERA CONTROL API (Session 8.5)
        # ====================================================================
        # Flask-based HTTP API for remote control of camera streaming
        # Central server calls these endpoints to start/stop livestreaming

        self.API_CONTROL_HOST = '0.0.0.0'  # Listen on all interfaces
        self.API_CONTROL_PORT = 5000        # Control API port

        # Capture interval modes
        # PICTURE_CAPTURE_INTERVAL is the "active" interval used by circular buffer
        # It switches between NORMAL and STREAMING modes based on streaming state
        self.NORMAL_CAPTURE_INTERVAL = 0.5      # Normal motion detection mode
        self.STREAMING_CAPTURE_INTERVAL = 0.1   # Fast mode during livestream (10fps)
        
        # Streaming timeout and safety limits
        self.STREAM_HEARTBEAT_TIMEOUT = 30      # Auto-stop if no heartbeat for 30 seconds
        self.STREAM_MAX_DURATION_SECONDS = 1800 # Maximum stream duration (30 minutes)

        # System version (semantic versioning)
        self.SYSTEM_VERSION = "1.1.25"

        # Note: PICTURE_CAPTURE_INTERVAL remains at 0.5s as default
        # When streaming starts, it's changed to STREAMING_CAPTURE_INTERVAL
        # When streaming stops, it's restored to NORMAL_CAPTURE_INTERVAL
        
        # ====================================================================
        # APPLY LOCAL OVERRIDES
        # ====================================================================
        # Camera identity and optional settings from config_local.py
        
        self._load_local_overrides()

    def _load_local_overrides(self):
        """
        Load and apply settings from config_local.py.
        
        CRITICAL: This method REQUIRES config_local.py to exist with camera identity.
        System will exit if the file is missing or incomplete.
        
        Required in config_local.py:
        - CAMERA_ID
        - CAMERA_NAME
        - CAMERA_LOCATION
        
        Optional in config_local.py:
        - CENTRAL_SERVER_HOST
        - CENTRAL_SERVER_PORT
        - Any other config parameter
        """
        # Attempt to load config_local module (try normal import first, then fallback to file path)
        config_local = None
        try:
            import importlib
            # Try normal import (may be resolvable when module is installed or in PYTHONPATH)
            config_local = importlib.import_module("config_local")
        except Exception:
            # Fallback: try loading config_local.py from the same directory as this file
            config_path = Path(__file__).resolve().parent / "config_local.py"
            if config_path.exists():
                import importlib.util
                spec = importlib.util.spec_from_file_location("config_local", str(config_path))
                if spec is None or spec.loader is None:
                    print("\n" + "="*70)
                    print("❌ CRITICAL ERROR: Unable to load config_local.py (spec or loader missing)")
                    print("="*70)
                    print()
                    print("Ensure config_local.py is a valid Python file and is readable.")
                    print("If the file exists but cannot be loaded, check file permissions and syntax.")
                    print()
                    sys.exit(1)
                config_local = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(config_local)
            else:
                print("\n" + "="*70)
                print("❌ CRITICAL ERROR: config_local.py NOT FOUND")
                print("="*70)
                print()
                print("Each camera MUST have a config_local.py file to prevent ID conflicts.")
                print()
                print("Create config_local.py in the same directory as config.py with:")
                print()
                print('  CAMERA_ID = "camera_X"           # Unique: camera_1, camera_2, etc.')
                print('  CAMERA_NAME = "Descriptive Name"  # e.g., "Front Walkway"')
                print('  CAMERA_LOCATION = "Location"      # e.g., "Front Entrance"')
                print()
                print("Example:")
                print("  echo 'CAMERA_ID = \"camera_2\"' > config_local.py")
                print("  echo 'CAMERA_NAME = \"Back Yard\"' >> config_local.py")
                print("  echo 'CAMERA_LOCATION = \"Rear Entrance\"' >> config_local.py")
                print()
                print("See SETUP.md for detailed instructions.")
                print("="*70)
                print()
                sys.exit(1)
        
        # Validate required camera identity fields
        missing_fields = []
        if not hasattr(config_local, 'CAMERA_ID') or not config_local.CAMERA_ID:
            missing_fields.append('CAMERA_ID')
        if not hasattr(config_local, 'CAMERA_NAME') or not config_local.CAMERA_NAME:
            missing_fields.append('CAMERA_NAME')
        if not hasattr(config_local, 'CAMERA_LOCATION') or not config_local.CAMERA_LOCATION:
            missing_fields.append('CAMERA_LOCATION')
        
        if missing_fields:
            print("\n" + "="*70)
            print("❌ CRITICAL ERROR: config_local.py INCOMPLETE")
            print("="*70)
            print()
            print(f"Missing required fields: {', '.join(missing_fields)}")
            print()
            print("config_local.py MUST define:")
            print('  CAMERA_ID = "camera_X"           # Unique identifier')
            print('  CAMERA_NAME = "Descriptive Name"  # Human-readable name')
            print('  CAMERA_LOCATION = "Location"      # Physical location')
            print()
            print("See SETUP.md for detailed instructions.")
            print("="*70)
            print()
            sys.exit(1)
        
        # Apply camera identity (required)
        self.CAMERA_ID = config_local.CAMERA_ID
        self.CAMERA_NAME = config_local.CAMERA_NAME
        self.CAMERA_LOCATION = config_local.CAMERA_LOCATION
        
        # Apply optional overrides
        if hasattr(config_local, 'CENTRAL_SERVER_HOST'):
            self.CENTRAL_SERVER_HOST = config_local.CENTRAL_SERVER_HOST
            # Rebuild API base URL
            self.CENTRAL_SERVER_API_BASE = f"http://{self.CENTRAL_SERVER_HOST}:{self.CENTRAL_SERVER_PORT}/api/v1"
        
        if hasattr(config_local, 'CENTRAL_SERVER_PORT'):
            self.CENTRAL_SERVER_PORT = config_local.CENTRAL_SERVER_PORT
            # Rebuild API base URL
            self.CENTRAL_SERVER_API_BASE = f"http://{self.CENTRAL_SERVER_HOST}:{self.CENTRAL_SERVER_PORT}/api/v1"
        
        # ====================================================================
        # GENERIC CONFIG OVERRIDES
        # ====================================================================
        # Apply any other config overrides from config_local.py
        # This allows each camera to have custom tuning without modifying config.py
        # 
        # Examples in config_local.py:
        #   MOTION_SENSITIVITY = 1         # Lower for more sensitive detection
        #   MOTION_THRESHOLD = 40          # Lower for low-light conditions
        #   MOTION_COOLDOWN_SECONDS = 30   # Longer for high-traffic areas
        #   VIDEO_BITRATE = 2000000        # Higher for better quality
        # ====================================================================
        
        overrides_applied = []
        for attr in dir(config_local):
            # Only process uppercase attributes (config constants)
            if not attr.startswith('_') and attr.isupper():
                # Skip the ones we already handled explicitly
                if attr not in ['CAMERA_ID', 'CAMERA_NAME', 'CAMERA_LOCATION', 
                               'CENTRAL_SERVER_HOST', 'CENTRAL_SERVER_PORT']:
                    # Apply the override
                    override_value = getattr(config_local, attr)
                    setattr(self, attr, override_value)
                    overrides_applied.append(f"{attr}={override_value}")
        
        print(f"✓ Loaded camera identity from config_local.py")
        print(f"  Camera ID:   {self.CAMERA_ID}")
        print(f"  Camera Name: {self.CAMERA_NAME}")
        print(f"  Location:    {self.CAMERA_LOCATION}")
        
        # Report any additional overrides
        if overrides_applied:
            print(f"✓ Applied {len(overrides_applied)} config override(s):")
            for override in overrides_applied:
                print(f"  {override}")


# ============================================================================
# GLOBAL CONFIG INSTANCE
# ============================================================================

# Create global config instance (singleton pattern)
# This will be imported by other modules: from config import config
config = Config()


# ============================================================================
# DIRECTORY MANAGEMENT
# ============================================================================

def ensure_directories():
    """
    Create required local directories if they don't exist.
    
    NOTE: Does NOT create NFS directories - those are managed by central server.
    Only creates local temporary/staging directories.
    """
    # Local directories to create
    local_dirs = [
        config.TMP_PATH,
        config.PENDING_DIR,
    ]
    
    for directory in local_dirs:
        Path(directory).mkdir(parents=True, exist_ok=True)
    
    print(f"✓ Local directories verified")


# ============================================================================
# VALIDATION
# ============================================================================

def validate_config():
    """
    Validate configuration settings.
    
    Raises:
        ValueError: If configuration is invalid
    """
    
    # ========================================================================
    # CAMERA IDENTITY VALIDATION
    # ========================================================================
    # These checks should never fail now because _load_local_overrides()
    # ensures they exist, but we keep them for defense in depth
    
    if not config.CAMERA_ID:
        raise ValueError("CAMERA_ID must be set in config_local.py")
    
    if not config.CAMERA_NAME:
        raise ValueError("CAMERA_NAME must be set in config_local.py")
    
    if not config.CAMERA_LOCATION:
        print(f"Warning: CAMERA_LOCATION is empty")
    
    # ========================================================================
    # CENTRAL SERVER VALIDATION
    # ========================================================================
    
    if not config.CENTRAL_SERVER_HOST:
        raise ValueError("CENTRAL_SERVER_HOST must be set")
    
    if not config.CENTRAL_SERVER_PORT:
        raise ValueError("CENTRAL_SERVER_PORT must be set")
    
    # ========================================================================
    # NFS MOUNT VALIDATION
    # ========================================================================
    
    nfs_path = Path(config.NFS_MOUNT_PATH)
    if not nfs_path.exists():
        print(f"Warning: NFS mount point {config.NFS_MOUNT_PATH} does not exist")
        print("         This should be your mounted NFS share")
        print("         Check with: mount | grep security_footage")
    
    # Validate NFS subdirectories exist
    for subdir_name, subdir_path in [
        ("pictures", config.PICTURES_PATH),
        ("videos", config.VIDEO_PATH),
        ("thumbs", config.THUMBS_PATH)
    ]:
        if not Path(subdir_path).exists():
            print(f"Warning: NFS subdirectory {subdir_path} does not exist")
            print(f"         Expected structure: {config.NFS_MOUNT_PATH}/{{pictures,videos,thumbs}}/")
    
    # ========================================================================
    # MOTION DETECTION VALIDATION
    # ========================================================================
    
    # Check cooldown vs processing time
    if config.MOTION_COOLDOWN_SECONDS < 17:
        raise ValueError(
            f"MOTION_COOLDOWN_SECONDS ({config.MOTION_COOLDOWN_SECONDS}) should be >= 17 "
            "to prevent overlap with Thread 3 processing time"
        )
    
    # ========================================================================
    # VIDEO SETTINGS VALIDATION
    # ========================================================================
    
    # Check resolution
    if config.VIDEO_RESOLUTION not in [(1920, 1080), (1280, 720), (640, 480)]:
        print(f"Warning: Non-standard resolution {config.VIDEO_RESOLUTION}")
    
    # Check framerate
    if config.VIDEO_FRAMERATE > 30:
        print(f"Warning: High framerate {config.VIDEO_FRAMERATE} may strain Pi Zero 2 W")
    
    # Check video format
    if config.VIDEO_OUTPUT_FORMAT != 'h264':
        print(f"Warning: VIDEO_OUTPUT_FORMAT is '{config.VIDEO_OUTPUT_FORMAT}', should be 'h264'")
        print("         MP4 conversion now happens on central server")
    
    # ========================================================================
    # BUFFER VALIDATION
    # ========================================================================
    
    # Check buffer capacity (capacity-driven)
    if config.CIRCULAR_BUFFER_MAX_CHUNKS < 300:
        print(f"Warning: Low buffer capacity {config.CIRCULAR_BUFFER_MAX_CHUNKS} chunks "
              f"(may result in very short pre-motion footage)")
    
    if config.CIRCULAR_BUFFER_MAX_CHUNKS > 3000:
        print(f"Warning: High buffer capacity {config.CIRCULAR_BUFFER_MAX_CHUNKS} chunks "
              f"(may use excessive memory)")
    
    if config.CIRCULAR_BUFFER_MAX_BYTES > 100 * 1024 * 1024:
        print(f"Warning: Buffer memory limit very high "
              f"({config.CIRCULAR_BUFFER_MAX_BYTES/(1024*1024):.0f} MB)")
    
    # ========================================================================
    # TRANSFER SETTINGS VALIDATION
    # ========================================================================
    
    if config.TRANSFER_CHECK_INTERVAL <= 0:
        raise ValueError("TRANSFER_CHECK_INTERVAL must be positive")
    
    if config.TRANSFER_TIMEOUT <= 0:
        raise ValueError("TRANSFER_TIMEOUT must be positive")
    
    print("Configuration validation complete")


# ============================================================================
# DISPLAY CONFIGURATION
# ============================================================================

def print_config():
    """
    Print current configuration for verification.
    Useful during startup and debugging.
    """
    print("\n" + "="*60)
    print("Security Camera System - Configuration")
    print("="*60)
    
    print(f"\nCamera Identity:")
    print(f"  ID:         {config.CAMERA_ID}")
    print(f"  Name:       {config.CAMERA_NAME}")
    print(f"  Location:   {config.CAMERA_LOCATION}")
    
    print(f"\nCentral Server:")
    print(f"  Host:       {config.CENTRAL_SERVER_HOST}")
    print(f"  Port:       {config.CENTRAL_SERVER_PORT}")
    print(f"  API Base:   {config.CENTRAL_SERVER_API_BASE}")
    
    print(f"\nPaths:")
    print(f"  Base:       {config.BASE_PATH}")
    print(f"  NFS Mount:  {config.NFS_MOUNT_PATH}")
    print(f"  Temp:       {config.TMP_PATH}")
    print(f"  Pending:    {config.PENDING_DIR}")
    
    print(f"\nNFS Storage (managed by central server):")
    print(f"  Videos:     {config.VIDEO_PATH}")
    print(f"  Pictures:   {config.PICTURES_PATH}")
    print(f"  Thumbnails: {config.THUMBS_PATH}")
    
    print(f"\nVideo Settings:")
    print(f"  Resolution: {config.VIDEO_RESOLUTION[0]}x{config.VIDEO_RESOLUTION[1]}")
    print(f"  Framerate:  {config.VIDEO_FRAMERATE} fps")
    print(f"  Bitrate:    {config.VIDEO_BITRATE/1000000:.1f} Mbps")
    print(f"  Format:     {config.VIDEO_OUTPUT_FORMAT}")
    
    print(f"\nCircular Buffer (Continuous Recording):")
    print(f"  Max chunks: {config.CIRCULAR_BUFFER_MAX_CHUNKS}")
    print(f"  Max memory: {config.CIRCULAR_BUFFER_MAX_BYTES/(1024*1024):.1f} MB")
    print(f"  Target:     ~{config.CIRCULAR_BUFFER_SECONDS}s (actual varies)")
    print(f"  Post-motion wait: {config.POST_MOTION_WAIT_SECONDS}s (continuous recording)")
    print(f"  Estimated video length: ~{config.CIRCULAR_BUFFER_SECONDS}s (no gap!)")
    
    print(f"\nMotion Detection:")
    print(f"  Threshold:   {config.MOTION_THRESHOLD}")
    print(f"  Sensitivity: {config.MOTION_SENSITIVITY} pixels")
    print(f"  Cooldown:    {config.MOTION_COOLDOWN_SECONDS} seconds")
    print(f"  Check every: {config.PICTURE_CAPTURE_INTERVAL} seconds")
    
    print(f"\nStreaming:")
    print(f"  Port:        {config.LIVESTREAM_PORT}")
    print(f"  Framerate:   {config.LIVESTREAM_FRAMERATE} fps")
    
    print(f"\nTransfer Settings:")
    print(f"  Check Interval: {config.TRANSFER_CHECK_INTERVAL}s")
    print(f"  Timeout:        {config.TRANSFER_TIMEOUT}s")
    print(f"  Retry Policy:   Indefinite (never give up)")
    
    print(f"\nLogging:")
    print(f"  Destination: {config.LOG_DESTINATION}")
    print(f"  Batch every: {config.LOG_BATCH_INTERVAL} seconds")
    print(f"  Buffer size: {config.LOG_BUFFER_SIZE} entries")
    
    print("="*60 + "\n")


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

if __name__ == "__main__":
    """
    Run this file directly to validate and display configuration.
    
    Usage:
        python config.py
    """
    print("Security Camera System - Configuration Module")
    print_config()
    
    try:
        validate_config()
        print("\n✓ Configuration is valid")
    except ValueError as e:
        print(f"\n✗ Configuration error: {e}")
        exit(1)
    
    print("\nCreating directories...")
    ensure_directories()
    print("\n✓ All directories verified")