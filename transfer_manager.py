"""
Security Camera System - Transfer Manager Module
=================================================
Thread 5: File Transfer Management

Monitors pending directory for sentinel files and transfers to NFS.

Architecture:
- Watches for .READY sentinel files in pending directory
- Transfers files from local pending to NFS mount progressively
- Uses atomic operations (copy to .tmp, then rename)
- Notifies central server API after successful transfer
- Retries failed transfers indefinitely (never gives up)
- Deletes local files after successful transfer

Key Design Decisions:
- Poll every 0.25 seconds (4 checks per second)
- Process sentinels in chronological order (oldest first)
- Atomic rename prevents partial files on NFS
- API notification is best-effort (non-critical)
- No max retries - keep trying until success or manual intervention
- No failed directory - files stay in pending until transferred

Usage:
    from transfer_manager import TransferManager
    
    transfer_manager = TransferManager(api_client)
    transfer_manager.start()
    
    # Later, during shutdown:
    transfer_manager.stop()
"""

from pathlib import Path
import threading
import time
import shutil
import os
from typing import Optional, Dict
from config import config
from logger import log


class TransferManager:
    """
    Transfer Manager - Thread 5
    
    Monitors pending directory for sentinel files and transfers to NFS.
    
    Responsibilities:
    - Watch for .READY sentinel files
    - Parse filenames to extract metadata
    - Transfer files to NFS with atomic rename
    - Notify central server API
    - Retry failed transfers indefinitely
    - Cleanup local files after success
    
    Design: Never give up on transfers. Files remain in pending/
    until successfully transferred or manually removed.
    """
    
    def __init__(self, api_client):
        """
        Initialize transfer manager.
        
        Args:
            api_client: APIClient instance for notifications
        """
        self.api_client = api_client
        self.camera_id = config.CAMERA_ID
        
        # Directories
        self.pending_dir = Path(config.PENDING_DIR)
        self.nfs_base = Path(config.NFS_MOUNT_PATH)
        # NFS mount is directly at the subdirectory level (no camera_id subdir)
        self.camera_nfs_dir = self.nfs_base
        
        # Ensure pending directory exists
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        
        # Thread control
        self.running = False
        self.thread = None
        
        # Transfer settings from config
        self.check_interval = config.TRANSFER_CHECK_INTERVAL  # 0.25 seconds
        self.transfer_timeout = config.TRANSFER_TIMEOUT  # 30 seconds
        
        # Statistics (for monitoring)
        self.files_transferred = 0
        self.total_bytes_transferred = 0
        self.last_stats_time = time.time()
        self.stats_interval = 60  # Log stats every 60 seconds
        
        log(f"TransferManager initialized", level="INFO")
        log(f"  Pending dir: {self.pending_dir}", level="INFO")
        log(f"  NFS mount: {self.nfs_base}", level="INFO")
        log(f"  NFS structure: flat (no camera subdirectory)", level="INFO")
        log(f"  Check interval: {self.check_interval}s", level="INFO")
        log(f"  Transfer timeout: {self.transfer_timeout}s", level="INFO")
    
    def start(self):
        """Start transfer manager background thread."""
        if self.running:
            log("TransferManager already running", level="WARNING")
            return
        
        # Verify NFS mount on startup
        if not self._check_nfs_mounted():
            log("WARNING: NFS not mounted, transfers will wait until mount available", level="WARNING")
        
        self.running = True
        self.thread = threading.Thread(
            target=self._transfer_loop,
            name="TransferManager",
            daemon=True
        )
        self.thread.start()
        log("TransferManager started", level="INFO")
    
    def stop(self):
        """Stop transfer manager thread."""
        log("Stopping TransferManager...", level="INFO")
        self.running = False
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5.0)
        
        log(f"TransferManager stopped. Stats: {self.files_transferred} transferred, "
            f"{self.total_bytes_transferred/(1024*1024):.1f}MB total", level="INFO")
    
    def _transfer_loop(self):
        """
        Main transfer loop.
        
        Continuously scans pending directory for .READY sentinel files
        and processes them in chronological order.
        
        Never gives up on transfers - retries indefinitely until success.
        """
        log("Transfer loop started", level="INFO")
        
        while self.running:
            try:
                # Scan for sentinel files
                sentinels = sorted(
                    self.pending_dir.glob("*.READY"),
                    key=lambda p: p.stat().st_mtime  # Oldest first
                )
                
                if sentinels:
                    log(f"Found {len(sentinels)} pending transfers", level="DEBUG")
                
                # Process each sentinel
                for sentinel_path in sentinels:
                    if not self.running:
                        break
                    
                    try:
                        success = self._process_sentinel(sentinel_path)
                        if success:
                            self.files_transferred += 1
                        # If failed, will retry next loop (no max retries)
                    
                    except Exception as e:
                        log(f"Error processing sentinel {sentinel_path.name}: {e}", level="ERROR")
                        import traceback
                        log(traceback.format_exc(), level="ERROR")
                
                # Periodic statistics logging
                if time.time() - self.last_stats_time >= self.stats_interval:
                    log(
                        f"Transfer stats: {self.files_transferred} transferred, "
                        f"{self.total_bytes_transferred/(1024*1024):.1f}MB total",
                        level="INFO"
                    )
                    self.last_stats_time = time.time()
                
                # Sleep before next check
                time.sleep(self.check_interval)
            
            except Exception as e:
                log(f"Error in transfer loop: {e}", level="ERROR")
                time.sleep(1.0)  # Back off on error
        
        log("Transfer loop stopped", level="INFO")
    
    def _process_sentinel(self, sentinel_path: Path) -> bool:
        """
        Process a single sentinel file.
        
        Args:
            sentinel_path: Path to .READY sentinel file
        
        Returns:
            True if transfer succeeded, False if failed (will retry next loop)
        """
        # Get the actual file path (remove .READY extension)
        file_path = sentinel_path.with_name(sentinel_path.stem)
        
        # Check if file still exists (might have been transferred already)
        if not file_path.exists():
            log(f"File missing (already transferred?): {file_path.name}", level="DEBUG")
            sentinel_path.unlink()  # Remove orphaned sentinel
            return True
        
        # Parse filename to extract metadata
        file_info = self._parse_filename(file_path.name)
        if not file_info:
            log(f"Invalid filename format: {file_path.name}", level="ERROR")
            # Can't process invalid filename, but don't delete
            # Manual intervention needed to fix or remove
            return False
        
        event_id = file_info['event_id']
        file_type = file_info['file_type']
        
        log(f"Processing transfer: event_id={event_id}, type={file_type}, file={file_path.name}", level="INFO")
        
        # Attempt transfer
        success = self._transfer_file(file_path, file_info)
        
        if success:
            # Transfer succeeded - cleanup
            log(f"Transfer successful: {file_path.name}", level="INFO")
            
            # Delete local file and sentinel
            file_path.unlink()
            sentinel_path.unlink()
            
            return True
        else:
            # Transfer failed - will retry next loop
            log(f"Transfer failed: {file_path.name} (will retry)", level="WARNING")
            return False
    
    def _parse_filename(self, filename: str) -> Optional[Dict]:
        """
        Parse filename to extract metadata.
        
        Filename format: {event_id}_{timestamp}_{type}.{ext}
        Example: 42_20251030_143022_a.jpg
        
        Args:
            filename: Filename to parse
        
        Returns:
            Dict with: event_id, timestamp, file_type, dest_subdir, extension
            None if filename format is invalid
        """
        try:
            # Remove extension
            name_without_ext = filename.rsplit('.', 1)[0]
            extension = filename.rsplit('.', 1)[1]
            
            # Split by underscore
            parts = name_without_ext.split('_')
            
            if len(parts) < 4:
                return None
            
            # Extract components
            event_id = int(parts[0])
            timestamp = f"{parts[1]}_{parts[2]}"  # YYYYMMDD_HHMMSS
            type_indicator = parts[3]  # a, b, thumb, video
            
            # Map type indicator to file_type and destination subdirectory
            type_map = {
                'a': ('image_a', 'pictures'),
                'b': ('image_b', 'pictures'),
                'thumb': ('thumbnail', 'thumbs'),
                'video': ('video', 'videos')
            }
            
            if type_indicator not in type_map:
                log(f"Unknown type indicator: {type_indicator}", level="ERROR")
                return None
            
            file_type, dest_subdir = type_map[type_indicator]
            
            return {
                'event_id': event_id,
                'timestamp': timestamp,
                'file_type': file_type,
                'dest_subdir': dest_subdir,
                'extension': extension,
                'original_filename': filename
            }
        
        except Exception as e:
            log(f"Error parsing filename {filename}: {e}", level="ERROR")
            return None
    
    def _check_nfs_mounted(self) -> bool:
        """
        Check if NFS is mounted and writable.
        
        Returns:
            True if NFS mount is available and writable, False otherwise
        """
        try:
            # Check if mount point exists
            if not self.nfs_base.exists():
                return False
            
            # Check if subdirectories exist (pictures, thumbs, videos)
            required_subdirs = ['pictures', 'thumbs', 'videos']
            for subdir in required_subdirs:
                subdir_path = self.camera_nfs_dir / subdir
                if not subdir_path.exists():
                    log(f"Required subdirectory missing on NFS: {subdir_path}", level="ERROR")
                    log(f"Expected structure: {self.nfs_base}/{{pictures,thumbs,videos}}/", level="ERROR")
                    return False
            
            # Test write access
            test_file = self.camera_nfs_dir / 'pictures' / '.transfer_health_check'
            test_file.touch()
            test_file.unlink()
            
            return True
        
        except Exception as e:
            log(f"NFS mount check failed: {e}", level="DEBUG")
            return False
    
    def _transfer_file(self, local_path: Path, file_info: Dict) -> bool:
        """
        Transfer file to NFS with atomic rename.
        
        Args:
            local_path: Path to local file
            file_info: Dict from _parse_filename()
        
        Returns:
            True if transfer succeeded, False if failed
        """
        event_id = file_info['event_id']
        file_type = file_info['file_type']
        dest_subdir = file_info['dest_subdir']
        original_filename = file_info['original_filename']
        
        try:
            # Check NFS mounted
            if not self._check_nfs_mounted():
                log(f"NFS not mounted, cannot transfer {original_filename}", level="WARNING")
                return False
            
            # Build destination path
            # Format: {NFS_BASE}/{camera_id}/{dest_subdir}/{original_filename}
            # Example: /mnt/footage/camera_1/pictures/42_20251030_143022_a.jpg
            dest_dir = self.camera_nfs_dir / dest_subdir
            dest_path = dest_dir / original_filename
            
            # Ensure destination directory exists
            dest_dir.mkdir(parents=True, exist_ok=True)
            
            # Step 1: Copy to temporary file (atomic operation prevention)
            temp_path = dest_path.with_suffix(dest_path.suffix + '.tmp')
            
            log(f"Copying {local_path.name} to NFS...", level="DEBUG")
            
            # Copy with timeout protection
            start_time = time.time()
            shutil.copy2(local_path, temp_path)
            elapsed = time.time() - start_time
            
            if elapsed > self.transfer_timeout:
                log(f"Transfer timeout ({elapsed:.1f}s > {self.transfer_timeout}s)", level="WARNING")
                if temp_path.exists():
                    temp_path.unlink()
                return False
            
            # Step 2: Atomic rename (file appears complete or not at all)
            temp_path.rename(dest_path)
            
            # Calculate file size for statistics
            file_size = local_path.stat().st_size
            self.total_bytes_transferred += file_size
            
            log(f"Copied to NFS: {original_filename} ({file_size/(1024*1024):.2f}MB in {elapsed:.2f}s)", level="INFO")
            
            # Step 3: Notify central server API (best effort)
            # Build relative path for API (include camera_id for central server tracking)
            nfs_relative_path = f"{self.camera_id}/{dest_subdir}/{original_filename}"
            
            api_success = self._notify_api(event_id, file_type, nfs_relative_path)
            if not api_success:
                log(f"API notification failed (non-critical)", level="WARNING")
                # Don't fail the transfer - file is on NFS, that's what matters
            
            return True
        
        except Exception as e:
            log(f"Transfer failed: {original_filename}: {e}", level="ERROR")
            
            # Cleanup temporary file if exists
            if 'temp_path' in locals() and temp_path.exists():
                try:
                    temp_path.unlink()
                except:
                    pass
            
            return False
    
    def _notify_api(self, event_id: int, file_type: str, nfs_path: str) -> bool:
        """
        Notify central server that file was transferred.
        
        Args:
            event_id: Event ID
            file_type: One of: image_a, image_b, thumbnail, video
            nfs_path: Relative path on NFS (e.g., "camera_1/pictures/42_a.jpg")
        
        Returns:
            True if notification succeeded, False if failed
        """
        try:
            # For video files, we don't have duration yet (will be calculated by central server)
            video_duration = None
            
            success = self.api_client.update_file(
                event_id=event_id,
                file_type=file_type,
                file_path=nfs_path,
                transferred=True,
                video_duration=video_duration
            )
            
            if success:
                log(f"API notified: event_id={event_id}, file_type={file_type}", level="DEBUG")
                return True
            else:
                log(f"API notification failed: event_id={event_id}, file_type={file_type}", level="WARNING")
                return False
        
        except Exception as e:
            log(f"Exception notifying API: {e}", level="WARNING")
            return False


# ============================================================================
# STANDALONE TESTING
# ============================================================================

if __name__ == "__main__":
    """
    Test transfer manager functionality.
    
    Prerequisites:
    - config.py properly configured
    - NFS mount available at config.NFS_MOUNT_PATH
    - Camera directory exists on NFS
    - API client configured
    
    Usage:
        python transfer_manager.py
    """
    from api_client import APIClient
    
    print("="*60)
    print("Testing Transfer Manager")
    print("="*60)
    
    # Initialize API client
    print("\nInitializing API Client...")
    api_client = APIClient()
    
    # Initialize transfer manager
    print("\nInitializing Transfer Manager...")
    transfer_manager = TransferManager(api_client)
    
    # Check NFS mount
    print("\nChecking NFS mount...")
    if transfer_manager._check_nfs_mounted():
        print("✓ NFS mount available and writable")
        print(f"  Mount point: {config.NFS_MOUNT_PATH}")
        print(f"  Structure: flat (pictures/, thumbs/, videos/)")
    else:
        print("✗ NFS mount not available")
        print("  Make sure NFS is mounted at:", config.NFS_MOUNT_PATH)
        print("  And subdirectories exist: pictures/, thumbs/, videos/")
    
    # Test filename parsing
    print("\nTesting filename parsing...")
    test_filenames = [
        "42_20251030_143022_a.jpg",
        "42_20251030_143022_b.jpg",
        "42_20251030_143022_thumb.jpg",
        "42_20251030_143022_video.h264",
        "invalid_filename.jpg"
    ]
    
    for filename in test_filenames:
        result = transfer_manager._parse_filename(filename)
        if result:
            print(f"  ✓ {filename}")
            print(f"    event_id={result['event_id']}, type={result['file_type']}, dest={result['dest_subdir']}")
        else:
            print(f"  ✗ {filename} - invalid format")
    
    # Start transfer manager
    print("\nStarting transfer manager...")
    transfer_manager.start()
    
    print("\nTransfer manager is now running.")
    print("Create test files in pending directory:")
    print(f"  {config.PENDING_DIR}/")
    print("\nPress Ctrl+C to stop...")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nStopping transfer manager...")
        transfer_manager.stop()
        print("✓ Transfer manager stopped")
    
    print("\n" + "="*60)
    print("Transfer Manager Test Complete")
    print("="*60)