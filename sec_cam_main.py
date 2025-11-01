#!/usr/bin/env python3
"""
Security Camera System - Main Orchestrator
===========================================
Initializes and coordinates all system components.

Architecture:
- Main Thread:  Orchestrator (this file)
- Thread 1:     Camera and Circular Buffer
- Thread 2:     Motion Detection (ACTIVE Session 1B-4)
- Thread 3:     Event Processing (ACTIVE Session 1B-5)
- Thread 4:     Web Server (future)
- Thread 5:     Transfer Manager (ACTIVE Session 1B-7)

Usage:
    python3 sec_cam_main.py
    
    or as systemd service:
    sudo systemctl start sec-cam
"""

import sys
import signal
import time
from config import (
    ensure_directories,
    validate_config,
    print_config
)
from api_client import APIClient
from logger import log, stop_logger
from circular_buffer import CircularBuffer
from motion_event import MotionEvent

# DEPRECATED: database.py removed in Session 1B-3
# Components now use APIClient for central server communication
# from database import EventDatabase

# UPDATED: Session 1B-4 - MotionDetector now uses APIClient
from motion_detector import MotionDetector

# UPDATED: Session 1B-5 - EventProcessor now uses APIClient (no database)
from event_processor import EventProcessor

# NEW: Session 1B-7 - TransferManager for NFS file transfers
from transfer_manager import TransferManager

# PENDING: These will be updated in future sessions to use api_client
# from mjpeg_server import MJPEGServer        # Future session


class SecurityCameraSystem:
    """
    Main orchestrator for security camera system.
    
    Manages initialization, startup, and graceful shutdown of all components.
    """
    
    def __init__(self):
        """
        Initialize system components.
        """
        print("\n" + "="*60)
        print("Security Camera System - Multi-Camera Agent")
        print("="*60 + "\n")
        
        # Component references
        self.api_client = None
        self.circular_buffer = None
        self.motion_event = None
        self.motion_detector = None  # Session 1B-4: ACTIVE
        self.event_processor = None  # Session 1B-5: ACTIVE
        self.transfer_manager = None # Session 1B-7: ACTIVE
        
        # PENDING: These components will be added in future sessions
        # self.mjpeg_server = None      # Future session

        # System state
        self.running = False
        
        log("System initializing...")
    
    def initialize(self):
        """
        Initialize all system components in proper order.
        
        Order is critical:
        1. Validate configuration
        2. Create directories
        3. Initialize API client
        4. Register camera with central server (BLOCKING)
        5. Create coordination objects
        6. Initialize components
        
        Returns:
            bool: True if initialization successful, False otherwise
        """
        try:
            # Step 1: Validate configuration
            log("Validating configuration...")
            print_config()
            validate_config()
            
            # Step 2: Ensure directories exist
            log("Creating directories...")
            ensure_directories()
            
            # Step 3: Initialize API Client
            log("="*60, level="INFO")
            log("INITIALIZING API CLIENT", level="INFO")
            log("="*60, level="INFO")
            
            self.api_client = APIClient()
            log(f"API Client created: {self.api_client.base_url}", level="INFO")
            
            # Step 4: Register camera with central server (BLOCKING)
            log("="*60, level="INFO")
            log("CAMERA REGISTRATION", level="INFO")
            log("="*60, level="INFO")
            log(f"Attempting to register with: {self.api_client.base_url}", level="INFO")
            log(f"Camera ID: {self.api_client.camera_id}", level="INFO")
            log(f"Camera Name: {self.api_client.camera_name}", level="INFO")
            log(f"Camera Location: {self.api_client.camera_location}", level="INFO")
            log("", level="INFO")
            log("This will retry indefinitely until successful...", level="INFO")
            log("If registration fails repeatedly:", level="INFO")
            log(f"  1. Check central server is running", level="INFO")
            log(f"  2. Verify network connectivity: ping {self.api_client.base_url.split('//')[1].split(':')[0]}", level="INFO")
            log("  3. Check central server logs for errors", level="INFO")
            log("Press Ctrl+C to abort if needed", level="INFO")
            log("="*60, level="INFO")
            
            # This will retry indefinitely until successful
            # User can abort with Ctrl+C
            self.api_client.register_camera()
            
            log("✓ Camera registered successfully", level="INFO")
            log("="*60, level="INFO")
            
            # Step 5: Create motion event coordinator
            log("Creating motion event coordinator...")
            self.motion_event = MotionEvent()
            
            # Step 6: Initialize circular buffer (Thread 1)
            log("Initializing circular buffer...")
            self.circular_buffer = CircularBuffer()
            
            # ================================================================
            # Session 1B-4: Motion Detector now uses API Client
            # ================================================================
            
            log("="*60, level="INFO")
            log("COMPONENT MIGRATION STATUS", level="INFO")
            log("="*60, level="INFO")
            log("✓ API Client: Initialized and registered", level="INFO")
            log("✓ Circular Buffer: Ready", level="INFO")
            log("✓ Motion Detector: ACTIVE (Session 1B-4)", level="INFO")
            log("✓ Event Processor: ACTIVE (Session 1B-5)", level="INFO")
            log("✓ Transfer Manager: ACTIVE (Session 1B-7)", level="INFO")
            log("⚠ MJPEG Server: PENDING Future Session", level="WARNING")
            log("="*60, level="INFO")
            log("", level="INFO")
            log("System Status:", level="INFO")
            log("- Camera registration: ACTIVE", level="INFO")
            log("- Video capture: ACTIVE", level="INFO")
            log("- Motion detection: ACTIVE (Session 1B-4)", level="INFO")
            log("- Event processing: ACTIVE (Session 1B-5)", level="INFO")
            log("- File transfers: ACTIVE (Session 1B-7)", level="INFO")
            log("="*60, level="INFO")
            
            # Session 1B-4: Initialize motion detector with api_client
            log("Initializing motion detector...")
            self.motion_detector = MotionDetector(
                self.circular_buffer,
                self.motion_event,
                self.api_client  # NEW: api_client instead of database
            )
            log("✓ Motion detector initialized", level="INFO")
            
            # Link motion detector to circular buffer for streaming control
            self.circular_buffer.set_motion_detector(self.motion_detector)
            
            # Session 1B-5: Initialize event processor (no database parameter)
            log("Initializing event processor...")
            self.event_processor = EventProcessor(
                self.circular_buffer,
                self.motion_event  # REMOVED: database parameter
            )
            log("✓ Event processor initialized", level="INFO")
            
            # Session 1B-7: Initialize transfer manager
            log("Initializing transfer manager...")
            self.transfer_manager = TransferManager(
                self.api_client  # Pass api_client for file status updates
            )
            log("✓ Transfer manager initialized", level="INFO")
            
            # COMMENTED OUT - Future session will update MJPEGServer
            # log("Initializing MJPEG server...")
            # self.mjpeg_server = MJPEGServer(
            #     self.circular_buffer,
            #     self.api_client  # Changed from self.db
            # )

            log("Core initialization complete")
            return True
            
        except KeyboardInterrupt:
            log("\n\nRegistration aborted by user (Ctrl+C)", level="ERROR")
            log("Camera cannot operate without registration. Exiting.", level="ERROR")
            return False
            
        except Exception as e:
            log(f"Initialization failed: {e}", level="ERROR")
            print(f"\n✗ Initialization failed: {e}\n")
            import traceback
            traceback.print_exc()
            return False
    
    def start(self):
        """
        Start all system components in proper order.
        
        Order is critical:
        1. Start circular buffer (camera)
        2. Start motion detector
        3. Start event processor
        4. Start transfer manager
        5. Start MJPEG server (future)
        
        Returns:
            bool: True if startup successful, False otherwise
        """
        try:
            log("="*60, level="INFO")
            log("STARTING COMPONENTS", level="INFO")
            log("="*60, level="INFO")
            
            # Step 1: Start circular buffer (camera and H.264 recording)
            log("Starting circular buffer (camera)...")
            self.circular_buffer.start()
            log("✓ Circular buffer started", level="INFO")
            
            # ================================================================
            # Session 1B-4: Start Motion Detector
            # ================================================================
            
            # Session 1B-4: Start motion detector
            log("Starting motion detector...")
            self.motion_detector.start()
            log("✓ Motion detector started", level="INFO")
            
            # Session 1B-5: Start event processor
            log("Starting event processor...")
            self.event_processor.start()
            log("✓ Event processor started", level="INFO")
            
            # Session 1B-7: Start transfer manager
            log("Starting transfer manager...")
            self.transfer_manager.start()
            log("✓ Transfer manager started", level="INFO")
            
            # COMMENTED OUT - Future session: MJPEG Server
            # log("Starting MJPEG server...")
            # self.mjpeg_server.start()
            
            # COMMENTED OUT - Watchdog references self.db which no longer exists
            # self.start_camera_watchdog()
            
            # System is now running
            self.running = True
            
            log("="*60, level="INFO")
            log("✓ Camera System Running", level="INFO")
            log("="*60, level="INFO")
            log("Active Components:", level="INFO")
            log("  ✓ API Client (registered with central server)", level="INFO")
            log("  ✓ Circular Buffer (capturing video)", level="INFO")
            log("  ✓ Motion Detector (creating events on central server)", level="INFO")
            log("  ✓ Event Processor (saving files to pending directory)", level="INFO")
            log("  ✓ Transfer Manager (transferring files to NFS)", level="INFO")
            log("", level="INFO")
            log("Inactive Components (pending migration):", level="WARNING")
            log("  ⚠ MJPEG Server (Future session)", level="WARNING")
            log("", level="INFO")
            log("NOTE: Full pipeline active → Motion detected → Events created → Files saved → Transferred to NFS", level="INFO")
            log("", level="INFO")
            log("Press Ctrl+C to stop", level="INFO")
            log("="*60, level="INFO")
            
            print("\n" + "="*60)
            print("✓ Security Camera System Running")
            print("="*60)
            print("Active: Camera, Motion Detection, Event Processing, File Transfers")
            print("✓ Files automatically transferred to NFS")
            print("Press Ctrl+C to stop")
            print("="*60 + "\n")
            
            return True
            
        except Exception as e:
            log(f"Startup failed: {e}", level="ERROR")
            print(f"\n✗ Startup failed: {e}\n")
            import traceback
            traceback.print_exc()
            return False
    
    def stop(self):
        """
        Stop all system components gracefully.
        
        Shutdown sequence (reverse of startup):
        1. Stop motion detector
        2. Stop event processor
        3. Stop transfer manager
        4. Stop MJPEG server
        5. Stop circular buffer
        6. Close API client
        7. Flush logs
        """
        if not self.running:
            return
        
        log("System shutdown initiated...")
        print("\n" + "="*60)
        print("Security Camera System - Shutting Down")
        print("="*60)
        
        self.running = False
        
        try:
            # Session 1B-4: Stop motion detector
            if self.motion_detector:
                log("Stopping motion detector...")
                self.motion_detector.stop()
                log("✓ Motion detector stopped", level="INFO")
            
            # Session 1B-5: Stop event processor
            if self.event_processor:
                log("Stopping event processor...")
                self.event_processor.stop()
                log("✓ Event processor stopped", level="INFO")
            
            # Session 1B-7: Stop transfer manager
            if self.transfer_manager:
                log("Stopping transfer manager...")
                self.transfer_manager.stop()
                log("✓ Transfer manager stopped", level="INFO")
            
            # COMMENTED OUT - Future session: MJPEG Server
            # if self.mjpeg_server:
            #     log("Stopping MJPEG server...")
            #     self.mjpeg_server.stop()
            
            # Stop circular buffer (camera)
            if self.circular_buffer:
                log("Stopping circular buffer...")
                self.circular_buffer.stop()
                log("✓ Circular buffer stopped", level="INFO")
            
            # Close API client session
            if self.api_client:
                log("Closing API client connection...")
                try:
                    self.api_client.session.close()
                    log("✓ API client closed", level="INFO")
                except Exception as e:
                    log(f"Error closing API client: {e}", level="WARNING")
            
            # Flush logs
            log("System shutdown complete")
            stop_logger()
            
            print("✓ System stopped successfully\n")
            
        except Exception as e:
            print(f"Error during shutdown: {e}\n")
            log(f"Error during shutdown: {e}", level="ERROR")
    
    def run(self):
        """
        Main run loop - keeps system alive until interrupted.
        
        Returns:
            int: Exit code (0 for success, 1 for failure)
        """
        import psutil
        import os
        
        # Initialize
        if not self.initialize():
            return 1
        
        # Start
        if not self.start():
            self.stop()
            return 1
        
        # Run until interrupted
        try:
            proc = psutil.Process(os.getpid())
            loop_counter = 0
            
            while self.running:
                time.sleep(1.0)
                loop_counter += 1
                
                # Regular memory logging every 200 seconds
                if loop_counter % 200 == 0:
                    rss_mb = proc.memory_info().rss / (1024 * 1024)
                    log(f"[MEMDEBUG] RSS={rss_mb:.1f} MB")
                
                # COMMENTED OUT - Leak detection references self.db which no longer exists
                # The full leak detection code from the original will be restored
                # once all components are migrated
        
        except KeyboardInterrupt:
            print("\n\nReceived keyboard interrupt (Ctrl+C)")
        
        # Shutdown
        self.stop()
        
        return 0


# ============================================================================
# SIGNAL HANDLERS
# ============================================================================

# Global reference for signal handlers
_system = None

def signal_handler(signum, frame):
    """
    Handle shutdown signals (SIGTERM, SIGINT).
    
    This allows graceful shutdown when:
    - User presses Ctrl+C (SIGINT)
    - systemctl stop is called (SIGTERM)
    """
    global _system
    
    signal_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
    print(f"\n\nReceived {signal_name} signal")
    log(f"Received {signal_name} signal")
    
    if _system:
        _system.stop()


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """
    Main entry point for security camera system.
    """
    global _system
    
    # Create system instance
    _system = SecurityCameraSystem()
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Run system
    exit_code = _system.run()
    
    # Exit with appropriate code
    sys.exit(exit_code)


if __name__ == "__main__":
    main()