"""
Security Camera System - Circular Buffer Module
================================================
Manages camera with dual-purpose buffer system:
- Two-frame picture buffer for motion detection and still images
- Capacity-driven H.264 circular buffer for video clips

The circular buffer uses a capacity-driven approach (max chunks, not time).
Actual duration varies based on scene complexity and motion.
Typical: 1000 chunks ≈ 15-25 seconds pre-motion footage.

Thread-safe for concurrent read/write operations.
"""

import threading
import time
import io
from PIL import Image
import numpy as np
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import CircularOutput
from config import config
from logger import log

class BoundedCircularOutput(CircularOutput):
    """
    Wrapper around CircularOutput that enforces maximum chunk count.
    Prevents unbounded deque growth that causes memory exhaustion.
    """
    
    def __init__(self, buffersize, max_chunks=400):
        """
        Initialize bounded circular output.
        
        Args:
            buffersize (int): Total bytes limit (passed to parent CircularOutput)
            max_chunks (int): Maximum number of video chunks to retain
        """
        super().__init__(buffersize=buffersize)
        self.max_chunks = max_chunks
        self._chunk_count = 0
        log(f"BoundedCircularOutput created: {buffersize/(1024*1024):.1f} MB, "
            f"max {max_chunks} chunks")
    
    def outputframe(self, frame, keyframe=True, timestamp=None, packet=None, audio=None):
        """
        Override to enforce chunk limit before adding new frame.
        
        When buffer reaches max_chunks, oldest chunks are removed
        before adding new ones, ensuring true circular behavior.
        This is NORMAL operation once buffer is full.
        """
        # Enforce hard limit by removing oldest chunks if at capacity
        while len(self._circular) >= self.max_chunks:
            try:
                self._circular.popleft()  # Remove oldest chunk
                self._chunk_count += 1
            except IndexError:
                break
        
        # Logging removed - watchdog reports buffer health periodically
        # Constant eviction at max capacity is normal circular buffer behavior
        
        # Now add the new frame using parent's logic
        # Ensure 'audio' is a bool to match parent signature (None -> False)
        audio_flag = bool(audio) if audio is not None else False
        return super().outputframe(frame, keyframe, timestamp, packet, audio_flag)

class CircularBuffer:
    """
    Manages camera with dual buffer system.
    
    Two-frame picture buffer:
    - Captures at 1920x1080 every 0.5 seconds
    - Stores only previous_frame and current_frame
    - Used for motion detection and still image capture
    - Memory: ~12.5MB (2 frames × 6.2MB)
    
    H.264 circular buffer:
    - Continuously records 30-second loop
    - Hardware-encoded H.264
    - Memory: ~25MB (compressed)
    - When saved, contains perfect [T-15s to T+15s] clip
    
    Usage:
        buffer = CircularBuffer()
        buffer.start()
        
        # For motion detection
        prev, curr = buffer.get_frames_for_detection()
        
        # Save current frame as image
        buffer.save_current_frame_as_image("image.jpg")
        
        # Save 30-second video clip
        buffer.save_h264_buffer("video.h264")
        
        buffer.stop()
    """
    
    def __init__(self, resolution=None, framerate=None):
        """
        Initialize circular buffer system (capacity-driven).
        
        Args:
            resolution (tuple): Video resolution (default: from config)
            framerate (int): Video framerate (default: from config)
        
        Note: Buffer size is now capacity-driven (max chunks), not time-driven.
            Actual video duration will vary based on scene complexity.
        """
        # SET THIS FIRST - before anything else
        self._capture_interval = config.PICTURE_CAPTURE_INTERVAL  # Default: 0.5s
        
        self.resolution = resolution or config.VIDEO_RESOLUTION
        self.framerate = framerate or config.VIDEO_FRAMERATE
        
        # Two-frame picture buffer
        self.previous_frame = None
        self.current_frame = None
        self.frame_lock = threading.Lock()
        
        # Camera and encoder
        self.picam2 = None
        self.encoder = None
        self.circular_output = None
        
        # Control flags
        self.running = False
        self.capture_thread = None
        self.frame_count = 0
        self.last_frame_time = 0
        
        # Frame hash tracking for freeze detection
        from collections import deque
        self.frame_hash_history = deque(maxlen=20)  # Last 20 frame hashes
        self.frame_hash_timestamps = deque(maxlen=20)  # Timestamps for each hash
        
        log(f"CircularBuffer initialized: {self.resolution[0]}x{self.resolution[1]} "
            f"@ {self.framerate}fps, capacity-driven buffer")
        
        # Motion detector reference (for pause/resume during streaming)
        self.motion_detector = None
        
    @property
    def capture_interval(self):
        """Get current capture interval."""
        return self._capture_interval

    @capture_interval.setter
    def capture_interval(self, value):
        """Set capture interval with logging to track changes."""
        import traceback
        old_value = self._capture_interval
        self._capture_interval = value
        if old_value != value:
            # Log the change with object ID to track instances
            caller = ''.join(traceback.format_stack()[-3:-1])
            log(f"[INTERVAL CHANGE] {old_value}s -> {value}s (object id={id(self)})\nCalled from:\n{caller}")

    def start(self):
        """
        Start camera capture and recording.
        """
        try:
            log("Starting camera and circular buffer...")
            
            # Initialize Picamera2
            self.picam2 = Picamera2()
            
            # ===================================================================
            # DETECT CAMERA TYPE (NoIR vs Standard)
            # ===================================================================
            # NoIR cameras have no IR filter and handle colors differently
            # Auto-detect camera model to apply appropriate color processing
            camera_properties = self.picam2.camera_properties
            camera_model = camera_properties.get('Model', 'unknown')
            self.is_noir = 'noir' in camera_model.lower()
            
            log("="*60)
            log(f"Camera Model: {camera_model}")
            log(f"NoIR Camera: {self.is_noir}")
            if self.is_noir:
                log("NoIR camera detected - will apply appropriate color handling")
            log("="*60)
            # ===================================================================
            
            # Configure for video
            video_config = self.picam2.create_video_configuration(
                main={
                    "size": self.resolution,
                    "format": "RGB888"
                },
                controls={
                    "FrameRate": self.framerate
                }
            )
            
            self.picam2.configure(video_config)
            
            # Create H.264 encoder with keyframe interval
            # Use target duration for keyframe spacing (smoother seeking in videos)
            target_duration = config.CIRCULAR_BUFFER_SECONDS  # From config (approximate target)
            intra_period = target_duration * self.framerate  # e.g., 20s × 15fps = 300 frames

            self.encoder = H264Encoder(
                bitrate=config.VIDEO_BITRATE,
                iperiod=intra_period
            )

            # ===================================================================
            # CAPACITY-DRIVEN BUFFER: Use chunk count, not time calculation
            # ===================================================================

            log(f"Creating capacity-driven circular buffer:")
            log(f"  Max chunks: {config.CIRCULAR_BUFFER_MAX_CHUNKS}")
            log(f"  Max memory: {config.CIRCULAR_BUFFER_MAX_BYTES / (1024*1024):.1f} MB")
            log(f"  Target duration: ~{target_duration}s (actual will vary by scene)")

            # Use capacity-driven approach - no time-based calculations
            self.circular_output = BoundedCircularOutput(
                buffersize=config.CIRCULAR_BUFFER_MAX_BYTES,
                max_chunks=config.CIRCULAR_BUFFER_MAX_CHUNKS
            )
            
            # ===================================================================
            
            # Start camera
            self.picam2.start()
            # -------------------------------------------------------------------
            # Enable continuous autofocus for IMX708_WIDE (Camera Module 3)
            # -------------------------------------------------------------------
            try:
                import libcamera
                controls = getattr(libcamera, 'controls', None)
                if controls is None:
                    raise ImportError("libcamera.controls not found")
                # Set autofocus mode to continuous and trigger initial focus
                self.picam2.set_controls({
                    "AfMode": controls.AfModeEnum.Continuous,
                    "AfTrigger": controls.AfTriggerEnum.Start
                })
                log("Autofocus enabled: Continuous mode", level="INFO")
            except Exception as e:
                log(f"Autofocus not supported or failed to initialize: {e}", level="WARNING")
            # -------------------------------------------------------------------
            
            # Camera warmup
            log(f"Camera warming up ({config.CAMERA_WARMUP_SECONDS}s)...")
            time.sleep(config.CAMERA_WARMUP_SECONDS)
            
            # Start H.264 encoding to circular buffer
            self.picam2.start_encoder(self.encoder, self.circular_output)
            log(f"H.264 circular buffer recording started (keyframe every {intra_period} frames)")
            
            # Start picture capture thread
            self.running = True
            self.capture_thread = threading.Thread(
                target=self._capture_pictures,
                name="PictureCapture",
                daemon=True
            )
            self.capture_thread.start()
            log("Picture capture thread started")
            
            log("CircularBuffer started successfully")
            
        except Exception as e:
            log(f"Error starting CircularBuffer: {e}", level="ERROR")
            self.stop()
            raise RuntimeError(f"Failed to start camera: {e}")

    def save_event_with_continuation(self, filepath_h264, wait_seconds=None, timeout_seconds=None, abort_flag=None):
        """
        Save complete event video with continuous recording (single-phase approach).
        
        NEW APPROACH (eliminates gap):
        1. Motion detected at T0
        2. Wait ~30 seconds while buffer continues recording (T0 → T+30)
        3. Dump entire buffer once (contains T-30 → T+30, ~60 seconds total)
        
        Buffer holds ~60 seconds continuously, so one dump captures:
        - Pre-event footage (T-30 to T0)
        - During event (T0)
        - Post-event footage (T0 to T+30)
        
        No gap, no buffer clear, single write operation.
        
        Args:
            filepath_h264 (str): Output path for H.264 file
            wait_seconds (int, optional): Seconds to wait before dump (default: from config)
            timeout_seconds (int, optional): DEPRECATED - kept for compatibility
            abort_flag (threading.Event, optional): Check for abort during wait
            
        Returns:
            float: Estimated video duration in seconds (calculated from file size and bitrate)
        """
        import os, time, gc
        from pathlib import Path
        
        # Use config value if not specified
        if wait_seconds is None:
            wait_seconds = config.POST_MOTION_WAIT_SECONDS
        
        max_chunks = config.CIRCULAR_BUFFER_MAX_CHUNKS
        
        try:
            # ================================================================
            # PHASE 1: Buffer health check
            # ================================================================
            log("="*60)
            log("Starting continuous buffer event save")
            log("="*60)
            
            # Ensure encoder and circular buffer are initialized
            if not getattr(self, "circular_output", None) or getattr(self.circular_output, "_circular", None) is None:
                raise RuntimeError("Circular buffer not initialized or encoder not started")
            
            circ = getattr(self, "circular_output", None)
            circ_store = getattr(circ, "_circular", None)
            if circ_store is None:
                raise RuntimeError("Circular buffer not initialized or encoder not started")
            
            initial_chunks = len(circ_store)
            initial_utilization = (initial_chunks / max_chunks) * 100
            
            log(f"Phase 1: Buffer health check")
            log(f"  Buffer size: {initial_chunks}/{max_chunks} chunks ({initial_utilization:.1f}% full)")
            log(f"  Max capacity: {max_chunks} chunks (~60 seconds)")
            
            # Warn if buffer is suspiciously empty
            if initial_chunks < (max_chunks * 0.5):
                log(f"  WARNING: Buffer only {initial_utilization:.1f}% full - may have insufficient footage", 
                    level="WARNING")
            
            # ================================================================
            # PHASE 2: Wait for post-event recording (WITH ABORT CHECK)
            # ================================================================
            log(f"Phase 2: Waiting {wait_seconds}s for post-event recording...")
            log(f"  Buffer continues recording during wait (no gap!)")
            
            wait_start = time.time()
            last_log_time = wait_start
            
            while time.time() - wait_start < wait_seconds:
                # Check for abort
                if abort_flag and abort_flag.is_set():
                    elapsed = time.time() - wait_start
                    current_size = len(circ_store) if circ_store is not None else 0
                    log(f"ABORT: Stopping wait after {elapsed:.1f}s, will dump partial buffer "
                        f"({current_size} chunks)", level="WARNING")
                    break
                
                # Log progress every 5 seconds
                if time.time() - last_log_time >= 5.0:
                    elapsed = time.time() - wait_start
                    current_size = len(circ_store) if circ_store is not None else 0
                    log(f"  Recording post-event: {elapsed:.1f}s / {wait_seconds}s elapsed, "
                        f"buffer at {current_size} chunks")
                    last_log_time = time.time()
                
                time.sleep(0.5)  # Check every 500ms
            
            wait_elapsed = time.time() - wait_start
            final_chunks = len(circ_store) if circ_store is not None else 0
            log(f"  Wait complete: {wait_elapsed:.1f}s elapsed, buffer now has {final_chunks} chunks")
            
            # ================================================================
            # PHASE 3: Dump entire buffer (single write operation)
            # ================================================================
            log(f"Phase 3: Dumping entire buffer to disk...")
            log(f"  Output: {filepath_h264}")
            
            dump_start = time.time()
            
            with open(filepath_h264, "wb", buffering=65536) as f:  # 64KB buffer
                
                # Take shallow snapshot of buffer
                chunks_snapshot = tuple(circ_store) if circ_store is not None else tuple()
                total_chunks = len(chunks_snapshot)
                
                log(f"  Snapshot captured: {total_chunks} chunks to write")
                
                # Write all chunks to disk
                chunk_count = 0
                found_keyframe = False
                bytes_written = 0
                
                for chunk in chunks_snapshot:
                    # Check for abort during write
                    if abort_flag and abort_flag.is_set():
                        log(f"  ABORT during write at chunk {chunk_count}/{total_chunks}", level="WARNING")
                        break
                    
                    if isinstance(chunk, tuple) and len(chunk) >= 2:
                        chunk_data = chunk[0]
                        is_keyframe = chunk[1] if len(chunk) > 1 else False
                        
                        # Skip chunks until we find a keyframe (ensures valid H.264 start)
                        if not found_keyframe:
                            if is_keyframe:
                                found_keyframe = True
                                log(f"  Starting from keyframe at chunk {chunk_count}")
                            else:
                                continue  # Skip non-keyframe chunks at start
                        
                        # Write chunk data
                        if isinstance(chunk_data, bytes):
                            f.write(chunk_data)
                            chunk_count += 1
                            bytes_written += len(chunk_data)
                            
                            # Periodic flush and progress logging
                            if chunk_count % 200 == 0:
                                f.flush()
                                mb_written = bytes_written / (1024 * 1024)
                                log(f"  Progress: {chunk_count}/{total_chunks} chunks ({mb_written:.1f} MB)")

                if not found_keyframe:
                    log("  WARNING: No keyframe found in buffer - video may be unplayable", level="WARNING")
                
                # Final flush
                f.flush()
                os.fsync(f.fileno())
                
                # Release snapshot immediately
                del chunks_snapshot
            
            dump_elapsed = time.time() - dump_start
            
            # ================================================================
            # PHASE 4: Verify and report with detailed metrics
            # ================================================================
            log(f"Phase 4: Verification and metrics")
            
            if os.path.exists(filepath_h264):
                file_size = os.path.getsize(filepath_h264)
                size_mb = file_size / (1024 * 1024)
                
                # Calculate write speed
                write_speed_mbps = size_mb / dump_elapsed if dump_elapsed > 0 else 0
                
                # Calculate actual duration from file size and bitrate
                size_bits = size_mb * 8 * 1024 * 1024
                estimated_duration = size_bits / config.VIDEO_BITRATE
                
                # Calculate average chunk size for diagnostics
                avg_chunk_kb = (size_mb * 1024) / chunk_count if chunk_count > 0 else 0
                
                log("="*60)
                log("Event save COMPLETE - Single continuous buffer dump")
                log("="*60)
                log(f"File metrics:")
                log(f"  Output file: {os.path.basename(filepath_h264)}")
                log(f"  File size: {size_mb:.2f} MB ({file_size:,} bytes)")
                log(f"  Chunks written: {chunk_count} / {total_chunks} total")
                log(f"  Avg chunk size: {avg_chunk_kb:.1f} KB")
                log(f"")
                log(f"Timing metrics:")
                log(f"  Wait time: {wait_elapsed:.1f}s (post-event recording)")
                log(f"  Write time: {dump_elapsed:.2f}s (disk I/O)")
                log(f"  Write speed: {write_speed_mbps:.1f} MB/s")
                log(f"  Total processing: {wait_elapsed + dump_elapsed:.1f}s")
                log(f"")
                log(f"Video metrics:")
                log(f"  Estimated duration: {estimated_duration:.1f}s")
                log(f"  Coverage: T-{estimated_duration/2:.0f}s → T0 (motion) → T+{estimated_duration/2:.0f}s")
                log(f"  Bitrate: {config.VIDEO_BITRATE/1000000:.1f} Mbps")
                log("="*60)
                
                # Force cleanup
                gc.collect()
                
                # Return estimated duration
                return estimated_duration
            else:
                raise IOError("File not created")
            
        except Exception as e:
            log(f"Error in save_event_with_continuation: {e}", level="ERROR")
            # Clean up on error
            gc.collect()
            raise

    def _capture_pictures(self):
        import gc
        
        # Thread state tracking for diagnostics
        self.capture_thread_state = "STARTING"
        self.last_state_change_time = time.time()
        self.last_successful_capture_time = 0
        
        capture_start_time = time.time()  # Local variable, not self attribute
        log(f"Picture capture loop started (initial interval: {self.capture_interval}s)")
        frame_count = 0
        last_logged_interval = self.capture_interval
        
        while self.running:
            try:
                # Log if interval changed
                if self.capture_interval != last_logged_interval:
                    log(f"[CAPTURE DEBUG] Interval changed: {last_logged_interval}s -> {self.capture_interval}s")
                    last_logged_interval = self.capture_interval
                
                # Capture frame (ensure camera initialized)
                if self.picam2 is None:
                    # Picamera2 not yet initialized; wait briefly and retry to avoid attribute errors
                    self.capture_thread_state = "WAITING_FOR_CAMERA_INIT"
                    self.last_state_change_time = time.time()
                    log("capture_array skipped: picam2 not initialized yet, waiting...", level="WARNING")
                    time.sleep(0.1)
                    continue

                # ===================================================================
                # CRITICAL SECTION: capture_array() can hang
                # ===================================================================
                try:
                    self.capture_thread_state = "CALLING_CAPTURE_ARRAY"
                    self.last_state_change_time = time.time()
                    capture_call_start = time.time()
                    
                    frame = self.picam2.capture_array()
                    
                    capture_call_duration = time.time() - capture_call_start
                    self.capture_thread_state = "CAPTURE_SUCCESSFUL"
                    self.last_state_change_time = time.time()
                    self.last_successful_capture_time = time.time()
                    
                    # Log slow captures
                    if capture_call_duration > 2.0:
                        log(f"⚠️  SLOW CAPTURE: capture_array() took {capture_call_duration:.2f}s", level="WARNING")
                    
                except Exception as e:
                    self.capture_thread_state = f"CAPTURE_EXCEPTION: {type(e).__name__}"
                    self.last_state_change_time = time.time()
                    log(f"🚨 EXCEPTION in capture_array(): {e}", level="ERROR")
                    log(f"Exception type: {type(e).__name__}", level="ERROR")
                    import traceback
                    log(f"Traceback: {traceback.format_exc()}", level="ERROR")
                    
                    # Try to recover
                    time.sleep(1.0)
                    continue
                # ===================================================================
                
                self.last_frame_time = time.time()
                frame_count += 1
                self.frame_count += 1
                
                # ===================================================================
                # FRAME HASH TRACKING FOR FREEZE DETECTION
                # ===================================================================
                self.capture_thread_state = "COMPUTING_HASH"
                self.last_state_change_time = time.time()
                
                # Compute hash of raw frame to detect if camera is frozen
                # Uses SHA256 for reliable detection - ~10-20ms overhead per frame
                import hashlib
                frame_hash = hashlib.sha256(frame.tobytes()).hexdigest()[:16]  # Short hash (64-bit)
                
                # Store hash and timestamp in ring buffer
                self.frame_hash_history.append(frame_hash)
                self.frame_hash_timestamps.append(time.time())
                
                self.capture_thread_state = "HASH_COMPLETE"
                self.last_state_change_time = time.time()
                
                # Periodic logging of hash status for debugging
                if frame_count % 100 == 0:
                    unique_recent = len(set(list(self.frame_hash_history)[-10:])) if len(self.frame_hash_history) >= 10 else 0
                    
                    # Show timestamp range to prove hashes are recent
                    if len(self.frame_hash_timestamps) >= 10:
                        oldest_hash_time = self.frame_hash_timestamps[-10]
                        newest_hash_time = self.frame_hash_timestamps[-1]
                        current_time = time.time()
                        
                        # Time since oldest and newest hash
                        oldest_age = current_time - oldest_hash_time
                        newest_age = current_time - newest_hash_time
                        
                        log(f"[HASH DEBUG] Frame #{frame_count}, hash={frame_hash}, "
                            f"unique_in_last_10={unique_recent}, "
                            f"oldest_hash={oldest_age:.1f}s_ago, newest_hash={newest_age:.1f}s_ago")
                    else:
                        log(f"[HASH DEBUG] Frame #{frame_count}, hash={frame_hash}, "
                            f"unique_in_last_10={unique_recent} (building history...)")
                # ===================================================================

                # Debug log every 50 frames with timing info
                if frame_count % 50 == 0:
                    elapsed = time.time() - capture_start_time
                    avg_interval = elapsed / frame_count if frame_count > 0 else 0
                    log(f"[CAPTURE DEBUG] Frame #{frame_count}, "
                        f"config interval={self.capture_interval}s, "
                        f"actual avg={avg_interval:.3f}s "
                        f"(object id={id(self)})")
                
                # Update two-frame buffer
                self.capture_thread_state = "UPDATING_FRAME_BUFFER"
                self.last_state_change_time = time.time()
                
                with self.frame_lock:
                    old_previous = self.previous_frame
                    self.previous_frame = self.current_frame
                    self.current_frame = frame
                
                self.capture_thread_state = "FRAME_BUFFER_UPDATED"
                self.last_state_change_time = time.time()
                
                # Explicitly delete old frame reference
                if old_previous is not None:
                    del old_previous
                
                # Force GC every 500 frames
                if frame_count % 500 == 0:  # Every ~4 minutes
                    gc.collect()
                    time.sleep(0.1)  # Breathing room after GC
                
                # Responsive sleep that checks for interval changes
                # Read target at start of sleep period
                self.capture_thread_state = "SLEEPING"
                self.last_state_change_time = time.time()
                
                sleep_start = time.time()
                initial_interval = self.capture_interval
                
                while self.running:
                    elapsed = time.time() - sleep_start
                    current_interval = self.capture_interval
                    
                    # If interval changed mid-sleep, log it and break early
                    if current_interval != initial_interval:
                        log(f"[CAPTURE DEBUG] Interval changed mid-sleep: {initial_interval}s -> {current_interval}s (after {elapsed:.2f}s)")
                        break
                    
                    # If we've slept long enough for the current interval, break
                    if elapsed >= current_interval:
                        break
                    
                    # Sleep in small chunks (50ms) to stay responsive
                    remaining = current_interval - elapsed
                    sleep_time = min(0.05, remaining)
                    time.sleep(sleep_time)
                
            except Exception as e:
                if self.running:
                    log(f"Error capturing picture frame: {e}", level="ERROR")
                    time.sleep(1)
        
        log("Picture capture loop stopped")
    
    def get_frames_for_detection(self):
        """
        Get downscaled frames for motion detection (memory optimized).
        
        Downscales frames BEFORE copying to minimize memory allocation.
        Returns 100x75 frames instead of full resolution.
        
        Thread-safe, non-blocking.
        
        Returns:
            tuple: (previous_frame, current_frame) as small numpy arrays (100x75x3).
                Returns (None, None) if frames not yet available.
        """
        import cv2
        
        with self.frame_lock:
            if self.previous_frame is None or self.current_frame is None:
                return (None, None)
            
            # Downscale BEFORE copying - huge memory savings
            # From ~2.7MB per frame to ~22KB per frame
            prev_small = cv2.resize(
                self.previous_frame, 
                config.DETECTION_RESOLUTION, 
                interpolation=cv2.INTER_AREA
            )
            curr_small = cv2.resize(
                self.current_frame, 
                config.DETECTION_RESOLUTION,
                interpolation=cv2.INTER_AREA
            )
            
            # Return small copies (total ~45KB vs 5.4MB before)
            return (prev_small.copy(), curr_small.copy())
    
    def save_current_frame_as_image(self, filepath, force_color=True):
        """
        Save current frame as high-resolution JPEG (color if requested).

        If the current frame is grayscale (Y-only), and force_color=True,
        a new RGB888 capture is taken from the camera to preserve color.
        """
        from PIL import Image

        try:
            if force_color and self.picam2:
                # Capture a fresh color image directly from sensor
                color_frame = self.picam2.capture_array("main")  # RGB888 by default
                img = Image.fromarray(color_frame)
                img.save(filepath, "JPEG", quality=config.JPEG_QUALITY)
                log(f"Saved COLOR image: {filepath}")
                return

            # Otherwise, fall back to whatever frame we have (Y or RGB)
            frame_copy = None
            with self.frame_lock:
                if self.current_frame is None:
                    raise RuntimeError("No frame available to save")
                frame_copy = self.current_frame.copy()

            img = Image.fromarray(frame_copy)
            img.save(filepath, "JPEG", quality=config.JPEG_QUALITY)
            log(f"Saved image: {filepath}")

        except Exception as e:
            log(f"Error saving image {filepath}: {e}", level="ERROR")
            raise
        finally:
            if 'img' in locals():
                img.close()
            import gc
            gc.collect()

    def capture_color_still(self, filepath):
        """
        Capture a full-color still image for A/B snapshots.

        - Uses the ISP's JPEG pipeline for accurate color and tone (same as video).
        - Falls back to capture_array() + Pillow if capture_file() fails.
        - Has no effect on the continuous motion-detection capture loop.
        """
        import gc
        from PIL import Image
        import numpy as np
        import cv2

        try:
            if not self.picam2:
                raise RuntimeError("Camera not initialized")

            log(f"[DEBUG] capture_color_still start: {filepath}")

            try:
                # ✅ Preferred path: full ISP JPEG (hardware-processed)
                self.picam2.capture_file(filepath, format="jpeg")
                log(f"Saved COLOR still (ISP processed): {filepath}")
                return

            except Exception as e:
                # 🚨 Fallback path: legacy raw array -> Pillow JPEG
                log(f"[WARNING] capture_file() failed ({e}); using fallback capture_array() method.")

                color_frame = self.picam2.capture_array("main")
                log(f"[DEBUG] dtype={color_frame.dtype}, shape={color_frame.shape}")

                # Handle grayscale fallback
                if len(color_frame.shape) == 2:
                    log("[DEBUG] Detected grayscale frame — converting to RGB for color snapshot")
                    color_frame = cv2.cvtColor(color_frame, cv2.COLOR_GRAY2RGB)

                # Normalize to 8-bit if needed
                if color_frame.dtype != np.uint8:
                    color_frame = color_frame.astype(np.uint8)

                img = Image.fromarray(color_frame, mode="RGB")
                img.save(filepath, "JPEG", quality=int(config.JPEG_QUALITY))
                log(f"Saved COLOR still (fallback raw method): {filepath}")

        except Exception as e:
            log(f"Error capturing color still: {e}", level="ERROR")
            raise

        finally:
            # Clean up memory aggressively since stills can be large
            if "img" in locals():
                try:
                    img.close()
                except Exception:
                    pass
            gc.collect()


    def get_latest_frame_for_livestream(self):
        """
        Get most recent frame for MJPEG streaming.
        
        Thread-safe, non-blocking. Returns a copy for encoding.
        
        Returns:
            numpy.ndarray: Current frame as image array, or None if unavailable
            
        Example:
            frame = buffer.get_latest_frame_for_livestream()
            if frame is not None:
                # Encode as JPEG and send to client
                ...
        """
        with self.frame_lock:
            if self.current_frame is None:
                return None
            return self.current_frame.copy()

    def save_h264_as_mp4(self, filepath_mp4, use_continuation=True, wait_seconds=None):
        """
        Save event as .h264 file for later MP4 conversion.
        Adds .pending marker *after* final merge and flush.
        
        Uses continuous buffer approach: waits for post-event recording,
        then dumps entire buffer once (~60 seconds total).
        
        Args:
            filepath_mp4 (str): Desired MP4 output path (will save as .h264 initially)
            use_continuation (bool): Whether to use continuation recording (default True)
            wait_seconds (int, optional): Seconds to wait for post-motion recording
            
        Returns:
            float or None: Estimated video duration in seconds, or None if use_continuation=False
        """
        import os
        import gc
        from pathlib import Path

        filepath_h264 = filepath_mp4.replace('.mp4', '.h264')
        pending_marker = filepath_h264 + ".pending"

        # Use config value if not specified
        if wait_seconds is None:
            wait_seconds = config.POST_MOTION_WAIT_SECONDS
            
        log(f"Using post-motion wait: {wait_seconds}s")

        try:
            # Step 1: Write the H.264 file and get estimated duration
            estimated_duration = None
            
            if use_continuation:
                log(f"Saving event with continuous buffer (wait: {wait_seconds}s)...")
                estimated_duration = self.save_event_with_continuation(filepath_h264, wait_seconds=wait_seconds)
            else:
                log(f"Saving buffer only (~30s)...")
                self.save_h264_buffer(filepath_h264)
                # For buffer-only saves, we don't calculate duration (uncommon path)

            # Step 2: Ensure all writes and merges are done
            if os.path.exists(filepath_h264):
                size = os.path.getsize(filepath_h264)
                log(f"Finalized H.264 file size: {size:,} bytes")
            else:
                raise RuntimeError("Missing H.264 file after save")

            # Flush disk buffers (important on Raspberry Pi)
            os.sync()
            gc.collect()

            # Step 3: Create .pending marker *after* final merge and flush
            Path(pending_marker).touch(exist_ok=True)
            log(f"Queued {os.path.basename(filepath_h264)} for later conversion")

            # Step 4: Skip live ffmpeg conversion
            log("Skipping inline ffmpeg conversion (handled by convert_pending.sh)")
            
            # Return estimated duration for database storage
            return estimated_duration

        except Exception as e:
            log(f"Error saving H.264 video: {e}", level="ERROR")
            if os.path.exists(filepath_h264):
                log(f"Keeping incomplete H.264 file: {filepath_h264}", level="WARNING")
            gc.collect()
            raise RuntimeError(f"Failed to save H.264 file: {e}")

    def save_h264_buffer(self, filepath):
        """
        Save buffer WITHOUT stopping encoder (zero-copy, no fragmentation).
        """
        import gc
        import os
        
        try:
            chunk_count = 0
            
            # Safely obtain the internal circular store; fail early if not available
            circ = getattr(self, "circular_output", None)
            circ_store = getattr(circ, "_circular", None)
            if circ_store is None:
                raise RuntimeError("Circular buffer not initialized or encoder not started")
            
            with open(filepath, 'wb', buffering=0) as f:
                # Direct iteration - no list copy, no encoder stop
                for chunk in circ_store:
                    if isinstance(chunk, tuple) and len(chunk) > 0:
                        if isinstance(chunk[0], bytes):
                            f.write(chunk[0])
                            chunk_count += 1
                            
                            if chunk_count % 50 == 0:
                                f.flush()
                                try:
                                    os.fsync(f.fileno())
                                except Exception:
                                    # fsync may fail on some filesystems; continue anyway
                                    pass
        
            log(f"Saved H.264 buffer: {filepath} ({chunk_count} chunks, no encoder restart)")
            gc.collect()
            
        except Exception as e:
            log(f"Error saving H.264 buffer: {e}", level="ERROR")
            raise

    def save_h264(self, output_path, abort_flag=None):
        """
        Save event video as raw H.264 file using continuous recording.
        
        This is the method for Session 1B-5 multi-camera architecture.
        Saves raw H.264 (no MP4 conversion on camera).
        Central server will convert H.264 → MP4 in background.
        
        Uses continuous buffer approach:
        1. Wait ~30 seconds for post-event recording
        2. Dump entire buffer once (~60 seconds total)
        
        Args:
            output_path: Path to save .h264 file
            abort_flag: Optional threading.Event to check for abort during video save
        
        Returns:
            float: Estimated video duration in seconds
        """
        import os
        import gc
        
        try:
            log(f"Saving H.264 video with continuous buffer: {output_path}")
            
            # Use save_event_with_continuation with new approach
            # Pass abort_flag through
            # This returns estimated duration
            estimated_duration = self.save_event_with_continuation(
                output_path,
                wait_seconds=config.POST_MOTION_WAIT_SECONDS,
                abort_flag=abort_flag
            )
            
            # Verify file was created
            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                size_mb = file_size / (1024 * 1024)
                
                log(f"H.264 saved: {size_mb:.2f} MB, ~{estimated_duration:.1f}s duration")
                
                # Clean up memory
                gc.collect()
                
                return estimated_duration
            else:
                log(f"Error: H.264 file not created: {output_path}", level="ERROR")
                return 0.0
                
        except Exception as e:
            log(f"Error saving H.264: {e}", level="ERROR")
            gc.collect()
            raise

    def get_buffer_health(self):
        """
        Get current buffer health metrics for monitoring (capacity-driven).
        
        In capacity-driven mode, high utilization (even 100%) is NORMAL and expected.
        The buffer fills to capacity and stays there during normal operation.
        
        Health warnings only for:
        - Buffer suspiciously empty (< 30%) - might indicate encoder problems
        - Excessive evictions - might indicate max_chunks set too low
        
        Returns:
            dict: {
                'current_chunks': int,
                'max_chunks': int,
                'utilization_pct': float,
                'is_healthy': bool,
                'status': str,
                'eviction_count': int
            }
        """
        try:
            # Safely obtain circular_output and its internal store; return None if not initialized
            circ = getattr(self, "circular_output", None)
            if circ is None:
                return None

            circ_store = getattr(circ, "_circular", None)
            maximum = getattr(circ, "max_chunks", getattr(config, "CIRCULAR_BUFFER_MAX_CHUNKS", 0))

            # Guard against None or zero maximum to avoid ZeroDivisionError
            current = len(circ_store) if circ_store is not None else 0
            utilization = (current / maximum) * 100 if maximum > 0 else 0.0
            evictions = getattr(circ, '_chunk_count', 0)
            
            # Determine health status
            # In capacity-driven mode, 80-100% utilization is IDEAL
            if utilization >= 80:
                is_healthy = True
                status = "optimal"
            elif utilization >= 50:
                is_healthy = True
                status = "filling"
            elif utilization >= 30:
                is_healthy = True
                status = "low"
            else:
                is_healthy = False
                status = "critically_low"
            
            return {
                'current_chunks': current,
                'max_chunks': maximum,
                'utilization_pct': utilization,
                'is_healthy': is_healthy,
                'status': status,
                'eviction_count': evictions
            }
        except Exception:
            return None

    def set_motion_detector(self, detector):
        """
        Link motion detector for pause/resume control during streaming.
        
        Args:
            detector: MotionDetector instance
        """
        self.motion_detector = detector
        log("Motion detector linked to CircularBuffer")

    def start_streaming(self):
        
        log("Starting streaming mode...")
        
        # Increase capture rate for smooth stream
        old_interval = self.capture_interval
        self.capture_interval = config.LIVESTREAM_CAPTURE_INTERVAL
        log(f"[DEBUG] Changed capture_interval: {old_interval} -> {self.capture_interval} (target: {config.LIVESTREAM_CAPTURE_INTERVAL})")
        
        # Pause motion detection
        if self.motion_detector:
            self.motion_detector.pause()
        
        log(f"Streaming mode active: {self.capture_interval}s capture interval, "
            f"motion detection paused")

    def stop_streaming(self):
        
        log("Stopping streaming mode...")
        
        # Reset capture rate
        old_interval = self.capture_interval
        self.capture_interval = config.PICTURE_CAPTURE_INTERVAL
        log(f"[DEBUG] Reset capture_interval: {old_interval} -> {self.capture_interval} (target: {config.PICTURE_CAPTURE_INTERVAL})")
        
        # Resume motion detection
        if self.motion_detector:
            self.motion_detector.resume()
        
        log(f"Normal mode restored: {self.capture_interval}s capture interval, "
            f"motion detection resumed")

    def get_health(self):
        """Get health status for watchdog monitoring."""
        # Calculate unique frame hashes for freeze detection
        unique_hashes_recent = 0
        if len(self.frame_hash_history) >= 10:
            unique_hashes_recent = len(set(list(self.frame_hash_history)[-10:]))
        
        # Get thread state info
        current_time = time.time()
        thread_state = getattr(self, 'capture_thread_state', 'UNKNOWN')
        time_in_current_state = current_time - getattr(self, 'last_state_change_time', current_time)
        time_since_successful_capture = current_time - getattr(self, 'last_successful_capture_time', 0)
        
        return {
            'thread_alive': self.capture_thread.is_alive() if self.capture_thread else False,
            'last_frame_time': self.last_frame_time,
            'frame_count': self.frame_count,
            'running': self.running,
            'camera_initialized': self.picam2 is not None,
            'frame_hash_history': list(self.frame_hash_history),  # For detailed analysis
            'frame_hash_timestamps': list(self.frame_hash_timestamps),
            'unique_hashes_recent': unique_hashes_recent,  # Quick check
            'thread_state': thread_state,  # NEW: What is thread doing?
            'time_in_current_state': time_in_current_state,  # NEW: How long in this state?
            'time_since_successful_capture': time_since_successful_capture  # NEW: Time since last success
        }

    def stop(self):
        """
        Gracefully stop camera and all capture threads.
        
        Waits for picture capture thread to finish current operation.
        Stops H.264 encoder and closes camera.
        """
        log("Stopping CircularBuffer...")
        
        # Signal capture thread to stop
        self.running = False
        
        # Wait for capture thread to finish (with timeout)
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        
        # Stop encoder if running
        if self.picam2 and self.encoder:
            try:
                self.picam2.stop_encoder()
                log("H.264 encoder stopped")
            except Exception as e:
                log(f"Error stopping encoder: {e}", level="WARNING")
        
        # Stop camera
        if self.picam2:
            try:
                self.picam2.stop()
                self.picam2.close()
                log("Camera stopped and closed")
            except Exception as e:
                log(f"Error stopping camera: {e}", level="WARNING")
        
        log("CircularBuffer stopped")


# ============================================================================
# STANDALONE TESTING
# ============================================================================

# ============================================================================
# STANDALONE TESTING
# ============================================================================

if __name__ == "__main__":
    """
    Test circular buffer functionality with real camera.
    
    This test will:
    1. Initialize camera and buffers
    2. Run for 30+ seconds capturing frames
    3. Save test images and video
    4. Verify buffer contents
    """
    import os
    
    print("Testing CircularBuffer with real camera...\n")
    print("This test requires camera hardware to be connected.\n")
    
    # Create test directory
    test_dir = "/tmp/buffer_test"
    os.makedirs(test_dir, exist_ok=True)
    
    try:
        # Test 1: Initialize buffer
        print("--- Test 1: Initializing buffer ---")
        buffer = CircularBuffer()
        
        # Test 2: Start camera
        print("\n--- Test 2: Starting camera and buffers ---")
        buffer.start()
        print("✓ Camera started successfully")
        
        # Test 3: Wait for frames to be available
        print("\n--- Test 3: Waiting for frame capture ---")
        timeout = 10
        start_time = time.time()
        while time.time() - start_time < timeout:
            prev, curr = buffer.get_frames_for_detection()
            if prev is not None and curr is not None:
                print(f"✓ Frames available after {time.time() - start_time:.1f}s")
                print(f"  Previous frame shape: {prev.shape}")
                print(f"  Current frame shape: {curr.shape}")
                break
            time.sleep(0.5)
        else:
            print("✗ Timeout waiting for frames")
        
        # Test 4: Save current frame as image
        print("\n--- Test 4: Saving test image ---")
        test_image_path = os.path.join(test_dir, "test_frame.jpg")
        buffer.save_current_frame_as_image(test_image_path)
        
        if os.path.exists(test_image_path):
            size_mb = os.path.getsize(test_image_path) / (1024 * 1024)
            print(f"✓ Image saved: {test_image_path} ({size_mb:.2f} MB)")
        else:
            print("✗ Image file not created")
        
        # Test 5: Let buffer fill up (CRITICAL - DO NOT SKIP!)
        print("\n--- Test 5: Filling H.264 buffer ---")
        fill_time = config.CIRCULAR_BUFFER_SECONDS  # Use target duration from config
        print(f"Running for {fill_time} seconds to fill buffer...")
        print("(This ensures we capture sufficient pre-motion footage)")
        time.sleep(fill_time + 2)
        print("✓ Buffer should now be at operating capacity")
        
        # Test 6: Save video buffer as MP4 with continuation
        print("\n--- Test 6: Saving video buffer as MP4 (capacity-driven) ---")
        print("This will save pre-buffer + wait for post-buffer to fill (capacity-driven)")
        test_video_path = os.path.join(test_dir, "test_event.mp4")
        # Use continuation with capacity-driven approach
        buffer.save_h264_as_mp4(test_video_path, use_continuation=True)

        if os.path.exists(test_video_path):
            size_mb = os.path.getsize(test_video_path) / (1024 * 1024)
            print(f"✓ Video saved: {test_video_path} ({size_mb:.2f} MB)")
            
            # Verify .h264 was deleted
            test_h264_path = test_video_path.replace('.mp4', '.h264')
            if os.path.exists(test_h264_path):
                print("✗ Warning: Temporary .h264 file still exists")
            else:
                print("✓ Temporary .h264 file deleted")
            
            # Try to get video duration using ffprobe
            try:
                import subprocess
                result = subprocess.run([
                    'ffprobe', '-v', 'error',
                    '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    test_video_path
                ], capture_output=True, text=True, timeout=5)
                
                if result.returncode == 0:
                    duration = float(result.stdout.strip())
                    print(f"✓ Video duration: {duration:.1f} seconds")
                    
                    if duration < 28:
                        print(f"⚠ Warning: Video is shorter than expected "
                            f"(got {duration:.1f}s, expected ~30s)")
                    elif duration > 32:
                        print(f"⚠ Warning: Video is longer than expected "
                            f"(got {duration:.1f}s, expected ~30s)")
                    else:
                        print(f"✓ Video duration is correct! (~30s)")
            except Exception as e:
                print(f"(Could not verify video duration: {e})")
        else:
            print("✗ Video file not created")
        
        # Test 7: Test livestream frame access
        print("\n--- Test 7: Testing livestream frame access ---")
        stream_frame = buffer.get_latest_frame_for_livestream()
        if stream_frame is not None:
            print(f"✓ Livestream frame available: {stream_frame.shape}")
        else:
            print("✗ No livestream frame available")
        
        # Test 8: Stop buffer
        print("\n--- Test 8: Stopping buffer ---")
        buffer.stop()
        print("✓ Buffer stopped successfully")
        
        print("\n" + "="*60)
        print("✓ All tests completed successfully!")
        print(f"\nTest files saved to: {test_dir}")
        print(f"  Image: {test_image_path}")
        print(f"  Video: {test_video_path}")
        print("\nYou can view the video with:")
        print(f"  vlc {test_video_path}")
        print("  or")
        print(f"  ffplay {test_video_path}")
        print("\nOr in a web browser (MP4 is compatible):")
        print(f"  file://{test_video_path}")
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # Ensure cleanup
        try:
            buffer.stop()
        except:
            pass