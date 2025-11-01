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
        return super().outputframe(frame, keyframe, timestamp, packet, audio)

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

    def save_event_with_continuation(self, filepath_h264, target_fill_percent=0.95, timeout_seconds=60):
        """
        Save pre-motion buffer + post-motion buffer using capacity-driven approach.
        
        Process:
        1. Dump current buffer to disk (pre-motion footage)
        2. Clear buffer
        3. Wait for buffer to refill to target percentage (post-motion footage)
        4. Dump buffer again to disk
        5. Result: concatenated H.264 file with both pre and post motion
        
        This approach works reliably even when buffer is at 100% capacity,
        fixing the "0 chunks captured" bug in the old time-based continuation logic.
        
        Args:
            filepath_h264 (str): Output H.264 file path
            target_fill_percent (float): Target buffer fill (0.0-1.0), default 0.95
            timeout_seconds (int): Maximum wait time for buffer to fill, default 60
            
        Returns:
            float: Estimated video duration in seconds (calculated from file size and bitrate)
        """
        import os, time, gc
        from pathlib import Path
        
        max_chunks = config.CIRCULAR_BUFFER_MAX_CHUNKS
        target_chunks = int(max_chunks * target_fill_percent)
        
        try:
            # Quick health check
            current_chunks = len(self.circular_output._circular)
            utilization = (current_chunks / max_chunks) * 100
            
            log(f"Starting save: buffer at {current_chunks}/{max_chunks} "
                f"chunks ({utilization:.1f}% full)")
            
            # Only warn if buffer is suspiciously empty (might indicate a problem)
            if current_chunks < (max_chunks * 0.3):
                log(f"WARNING: Buffer only {utilization:.1f}% full - may have insufficient "
                    f"pre-motion footage", level="WARNING")
            
            log("Starting capacity-driven save with buffer clear...")
            
            with open(filepath_h264, "wb", buffering=65536) as f:  # 64KB buffer
                
                # ================================================================
                # PHASE 1: Dump pre-motion buffer
                # ================================================================
                log("Phase 1: Dumping pre-motion buffer...")

                # Shallow snapshot (references only, not data)
                chunks_snapshot = tuple(self.circular_output._circular)
                pre_chunk_count = 0
                found_keyframe = False

                for chunk in chunks_snapshot:
                    if isinstance(chunk, tuple) and len(chunk) >= 2:
                        chunk_data = chunk[0]
                        is_keyframe = chunk[1] if len(chunk) > 1 else False
                        
                        # Skip chunks until we find a keyframe (ensures valid H.264 start)
                        if not found_keyframe:
                            if is_keyframe:
                                found_keyframe = True
                                log(f"Starting from keyframe at chunk {pre_chunk_count}")
                            else:
                                continue  # Skip non-keyframe chunks at start
                        
                        # Write chunk data
                        if isinstance(chunk_data, bytes):
                            f.write(chunk_data)
                            pre_chunk_count += 1
                            
                            # Periodic flush
                            if pre_chunk_count % 100 == 0:
                                f.flush()

                if not found_keyframe:
                    log("WARNING: No keyframe found in buffer - video may be unplayable", level="WARNING")
                
                log(f"Pre-motion buffer dumped ({pre_chunk_count} chunks)")
                
                # Critical: release snapshot immediately
                del chunks_snapshot
                f.flush()
                gc.collect()
                
                # ================================================================
                # PHASE 2: Clear buffer for post-motion recording
                # ================================================================
                log("Phase 2: Clearing buffer...")
                
                # Clear the circular buffer - encoder keeps running and refills it
                self.circular_output._circular.clear()
                
                log(f"Buffer cleared, waiting for {target_chunks} chunks ({target_fill_percent*100:.0f}% fill)...")
                gc.collect()
                
                # ================================================================
                # PHASE 3: Wait for buffer to refill
                # ================================================================
                log(f"Phase 3: Waiting for post-motion buffer to fill...")
                
                start_time = time.time()
                last_log_time = start_time
                
                while time.time() - start_time < timeout_seconds:
                    current_size = len(self.circular_output._circular)
                    
                    # Log progress every 5 seconds
                    if time.time() - last_log_time >= 5.0:
                        elapsed = time.time() - start_time
                        percent = (current_size / target_chunks) * 100
                        log(f"Buffer filling: {current_size}/{target_chunks} chunks "
                            f"({percent:.1f}%) - {elapsed:.1f}s elapsed")
                        last_log_time = time.time()
                    
                    # Check if we've reached target
                    if current_size >= target_chunks:
                        elapsed = time.time() - start_time
                        log(f"Buffer reached {current_size} chunks (target: {target_chunks}) "
                            f"in {elapsed:.1f}s")
                        break
                    
                    time.sleep(0.5)  # Check every 500ms
                else:
                    # Timeout reached
                    elapsed = time.time() - start_time
                    current_size = len(self.circular_output._circular)
                    log(f"WARNING: Timeout after {elapsed:.1f}s - buffer only at {current_size}/{target_chunks} chunks "
                        f"({(current_size/target_chunks)*100:.1f}%)", level="WARNING")
                    log("Dumping whatever we have...", level="WARNING")
                
                # ================================================================
                # PHASE 4: Dump post-motion buffer
                # ================================================================
                log("Phase 4: Dumping post-motion buffer...")
                
                # Shallow snapshot of post-motion buffer
                chunks_snapshot = tuple(self.circular_output._circular)
                post_chunk_count = 0
                found_keyframe = False
                
                for chunk in chunks_snapshot:
                    if isinstance(chunk, tuple) and len(chunk) >= 2:
                        chunk_data = chunk[0]
                        is_keyframe = chunk[1] if len(chunk) > 1 else False
                        
                        # Skip chunks until we find a keyframe (ensures valid H.264 continuation)
                        if not found_keyframe:
                            if is_keyframe:
                                found_keyframe = True
                                log(f"Post-motion starting from keyframe at chunk {post_chunk_count}")
                            else:
                                continue  # Skip non-keyframe chunks at start
                        
                        # Write chunk data
                        if isinstance(chunk_data, bytes):
                            f.write(chunk_data)
                            post_chunk_count += 1
                            
                            # Periodic flush
                            if post_chunk_count % 100 == 0:
                                f.flush()
                
                if not found_keyframe:
                    log("WARNING: No keyframe found in post-motion buffer", level="WARNING")
                
                log(f"Post-motion buffer dumped ({post_chunk_count} chunks)")
                
                # Critical: release snapshot immediately
                del chunks_snapshot
                
                # Final flush
                f.flush()
                os.fsync(f.fileno())
            
            # ================================================================
            # Verify and report
            # ================================================================
            if os.path.exists(filepath_h264):
                size_mb = os.path.getsize(filepath_h264) / (1024 * 1024)
                total_chunks = pre_chunk_count + post_chunk_count
                
                # Calculate actual duration from file size and bitrate
                size_bits = size_mb * 8 * 1024 * 1024
                estimated_duration = size_bits / config.VIDEO_BITRATE
                
                # Calculate average chunk size for diagnostics
                avg_chunk_kb = (size_mb * 1024) / total_chunks if total_chunks > 0 else 0
                
                log(f"Event saved: {size_mb:.2f} MB, {total_chunks} chunks, "
                    f"~{estimated_duration:.1f}s duration")
                log(f"  Pre-motion buffer: {pre_chunk_count} chunks "
                    f"(~{(pre_chunk_count/total_chunks)*estimated_duration:.1f}s)")
                log(f"  Post-motion buffer: {post_chunk_count} chunks "
                    f"(~{(post_chunk_count/total_chunks)*estimated_duration:.1f}s)")
                log(f"  Avg chunk size: {avg_chunk_kb:.1f} KB")
                
                # Force final cleanup
                gc.collect()
                
                # Return estimated duration for database storage
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
                
                # Capture frame
                frame = self.picam2.capture_array()
                self.last_frame_time = time.time()
                frame_count += 1
                
                # Debug log every 50 frames with timing info
                if frame_count % 50 == 0:
                    elapsed = time.time() - capture_start_time
                    avg_interval = elapsed / frame_count if frame_count > 0 else 0
                    log(f"[CAPTURE DEBUG] Frame #{frame_count}, "
                        f"config interval={self.capture_interval}s, "
                        f"actual avg={avg_interval:.3f}s "
                        f"(object id={id(self)})")
                
                # Update two-frame buffer
                with self.frame_lock:
                    old_previous = self.previous_frame
                    self.previous_frame = self.current_frame
                    self.current_frame = frame
                
                # Explicitly delete old frame reference
                if old_previous is not None:
                    del old_previous
                
                # Force GC every 10 frames
                if frame_count % 10 == 0:
                    gc.collect()
                
                # Responsive sleep that checks for interval changes
                # Read target at start of sleep period
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
        from PIL import Image
        import gc
        import numpy as np
        import cv2

        try:
            if not self.picam2:
                raise RuntimeError("Camera not initialized")

            log(f"[DEBUG] capture_color_still start: {filepath}")
            color_frame = self.picam2.capture_array("main")
            log(f"[DEBUG] dtype={color_frame.dtype}, shape={color_frame.shape}")

            # If grayscale (2D), convert to color using OpenCV
            if len(color_frame.shape) == 2:
                log("[DEBUG] Detected grayscale frame — converting to RGB for color snapshot")
                color_frame = cv2.cvtColor(color_frame, cv2.COLOR_GRAY2RGB)

            # Validate and normalize
            if color_frame.dtype != np.uint8:
                color_frame = color_frame.astype(np.uint8)

            img = Image.fromarray(color_frame, mode="RGB")
            img.save(filepath, "JPEG", quality=int(config.JPEG_QUALITY))
            log(f"Saved COLOR still: {filepath}")

        except Exception as e:
            log(f"Error capturing color still: {e}", level="ERROR")
            raise
        finally:
            if 'img' in locals():
                img.close()
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

    def save_h264_as_mp4(self, filepath_mp4, use_continuation=True, target_fill_percent=None, timeout_seconds=None):
        """
        Save event as .h264 file for later MP4 conversion.
        Adds .pending marker *after* final merge and flush.
        
        Uses capacity-driven approach: dumps pre-event buffer, clears it,
        waits for buffer to refill to target percentage, then dumps post-event buffer.
        
        Args:
            filepath_mp4 (str): Desired MP4 output path (will save as .h264 initially)
            use_continuation (bool): Whether to use continuation recording (default True)
            target_fill_percent (float, optional): Target buffer fill for post-motion
            timeout_seconds (int, optional): Timeout for buffer fill
            
        Returns:
            float or None: Estimated video duration in seconds, or None if use_continuation=False
        """
        import os
        import gc
        from pathlib import Path

        filepath_h264 = filepath_mp4.replace('.mp4', '.h264')
        pending_marker = filepath_h264 + ".pending"

        # Use config values if not specified
        if target_fill_percent is None:
            target_fill_percent = config.POST_MOTION_BUFFER_FILL_PERCENT
        if timeout_seconds is None:
            timeout_seconds = config.POST_MOTION_TIMEOUT_SECONDS
            
        target_chunks = int(config.CIRCULAR_BUFFER_MAX_CHUNKS * target_fill_percent)
        log(f"Using buffer fill target: {target_fill_percent*100:.0f}% ({target_chunks} chunks), timeout: {timeout_seconds}s")

        try:
            # Step 1: Write the H.264 file and get estimated duration
            estimated_duration = None
            
            if use_continuation:
                log(f"Saving event with capacity-driven continuation (target: {target_fill_percent*100:.0f}% fill)...")
                estimated_duration = self.save_event_with_continuation(filepath_h264, target_fill_percent, timeout_seconds)
            else:
                log(f"Saving buffer only (~17s)...")
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
        
        try:
            chunk_count = 0
            
            with open(filepath, 'wb', buffering=0) as f:
                # Direct iteration - no list copy, no encoder stop
                for chunk in self.circular_output._circular:
                    if isinstance(chunk, tuple) and len(chunk) > 0:
                        if isinstance(chunk[0], bytes):
                            f.write(chunk[0])
                            chunk_count += 1
                            
                            if chunk_count % 50 == 0:
                                f.flush()
                                os.fsync(f.fileno())
            
            log(f"Saved H.264 buffer: {filepath} ({chunk_count} chunks, no encoder restart)")
            gc.collect()
            
        except Exception as e:
            log(f"Error saving H.264 buffer: {e}", level="ERROR")
            raise

    def save_h264(self, output_path):
        """
        Save event video as raw H.264 file using continuation recording.
        
        This is the NEW method for Session 1B-5 multi-camera architecture.
        Saves raw H.264 (no MP4 conversion on camera).
        Central server will convert H.264 → MP4 in background.
        
        Uses capacity-driven recording:
        1. Dumps pre-event buffer contents
        2. Waits for buffer to refill to target percentage
        3. Dumps post-event buffer contents
        
        Args:
            output_path: Path to save .h264 file
        
        Returns:
            float: Estimated video duration in seconds
        """
        import os
        import gc
        
        try:
            log(f"Saving H.264 video with continuation: {output_path}")
            
            # Use save_event_with_continuation to get pre + post buffer
            # This returns estimated duration
            estimated_duration = self.save_event_with_continuation(
                output_path,
                target_fill_percent=config.POST_MOTION_BUFFER_FILL_PERCENT,
                timeout_seconds=config.POST_MOTION_TIMEOUT_SECONDS
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
            current = len(self.circular_output._circular)
            maximum = self.circular_output.max_chunks
            utilization = (current / maximum) * 100
            evictions = getattr(self.circular_output, '_chunk_count', 0)
            
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
        except:
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