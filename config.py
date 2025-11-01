"""
Security Camera System - Configuration File
============================================
All system configuration parameters in one place.
Modify these values to tune system behavior.

ARCHITECTURE NOTE:
This Config class is designed for multi-camera deployment with central server.
Phase 1B: Config reads from local variables (like current system)
Phase 7: Config will fetch from central server API
"""

import os
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
        Initialize configuration with default values.
        
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
        
        # Circular buffer maintains ~17-30 seconds of pre-motion footage (capacity-driven)
        # When motion detected, clear buffer and wait for it to refill for post-motion footage
        # Both pre-motion and post-motion use capacity-driven approach (not time-based)
        
        self.CIRCULAR_BUFFER_SECONDS = 20   # Target duration (approximate)
        
        # Post-motion recording: wait for buffer to fill to this percentage
        # 95% = ~950 chunks = ~28-30 seconds of footage
        self.POST_MOTION_BUFFER_FILL_PERCENT = 0.95
        
        # Post-motion timeout: maximum time to wait for buffer to fill
        # Safety mechanism - if buffer doesn't fill to target, dump whatever we have
        self.POST_MOTION_TIMEOUT_SECONDS = 60
        
        # Total event duration is variable (capacity-driven for both pre and post)
        # Typical: 20-30s (pre-buffer) + 28-30s (post-buffer) = 48-60s total
        
        # ====================================================================
        # VIDEO BUFFER SETTINGS (Capacity-Driven)
        # ====================================================================
        
        # Circular buffer capacity (chunks, not time-based)
        # This determines how much pre-motion footage is captured.
        # Actual duration will vary based on scene complexity and motion.
        # 
        # Tuning guide:
        # - Start with 1000 chunks (typically 15-25 seconds)
        # - Monitor logs to see actual durations
        # - Increase if videos too short, decrease if too long
        # 
        # At ~12KB per chunk average:
        #   600 chunks  ≈ 7 MB  ≈ 15-20 seconds
        #   1000 chunks ≈ 12 MB ≈ 20-30 seconds  (RECOMMENDED)
        #   1500 chunks ≈ 18 MB ≈ 30-40 seconds
        self.CIRCULAR_BUFFER_MAX_CHUNKS = 1000
        
        # Maximum memory for circular buffer (bytes)
        # Safety limit to prevent runaway memory usage
        self.CIRCULAR_BUFFER_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
        
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
        self.MOTION_COOLDOWN_SECONDS = 65
        
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
    
    def reload(self):
        """
        Reload configuration (stub for future API implementation).
        
        Future implementation (Phase 7):
        1. Call central server API: GET /api/v1/cameras/{camera_id}/config
        2. Update instance variables with new values
        3. Re-run validation
        4. Return success/failure status
        
        Current implementation:
        No-op in Phase 1B - configuration is static from local variables
        """
        pass


# ============================================================================
# MODULE-LEVEL INSTANCE
# ============================================================================
# Create singleton-like instance for easy import and backward compatibility

config = Config()


# ============================================================================
# DIRECTORY MANAGEMENT
# ============================================================================

def ensure_directories():
    """
    Create all required local directories if they don't exist.
    
    Note: NFS directories (pictures, videos, thumbs) are NOT created here.
    Those exist on the NFS mount and are managed by the central server.
    This only creates local working directories on the camera.
    
    Should be called during system initialization.
    """
    directories = [
        config.TMP_PATH,      # Temporary files and processing
        config.PENDING_DIR,   # Local staging for files before transfer
    ]
    
    for directory in directories:
        os.makedirs(directory, exist_ok=True)
        print(f"Directory verified: {directory}")


# ============================================================================
# CONFIGURATION VALIDATION
# ============================================================================

def validate_config():
    """
    Validate configuration parameters for common issues.
    Raises ValueError if configuration is invalid.
    Prints warnings for non-critical issues.
    """
    
    # ========================================================================
    # CAMERA IDENTITY VALIDATION
    # ========================================================================
    
    if not config.CAMERA_ID:
        raise ValueError("CAMERA_ID must be set (e.g., 'camera_1')")
    
    if not config.CAMERA_ID.startswith("camera_"):
        print(f"Warning: CAMERA_ID '{config.CAMERA_ID}' doesn't follow 'camera_N' convention")
    
    if not config.CAMERA_NAME:
        print(f"Warning: CAMERA_NAME is empty")
    
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
    
    print(f"\nCircular Buffer (Capacity-Driven):")
    print(f"  Max chunks: {config.CIRCULAR_BUFFER_MAX_CHUNKS}")
    print(f"  Max memory: {config.CIRCULAR_BUFFER_MAX_BYTES/(1024*1024):.1f} MB")
    print(f"  Target:     ~{config.CIRCULAR_BUFFER_SECONDS}s (actual varies)")
    print(f"  Post-motion fill target: {config.POST_MOTION_BUFFER_FILL_PERCENT*100:.0f}% (~{int(config.CIRCULAR_BUFFER_MAX_CHUNKS*config.POST_MOTION_BUFFER_FILL_PERCENT)} chunks)")
    print(f"  Post-motion timeout: {config.POST_MOTION_TIMEOUT_SECONDS}s")
    
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