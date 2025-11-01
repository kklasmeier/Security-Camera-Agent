"""
Security Camera System - API Client Module
===========================================
API client for communicating with central server.
Replaces database.py in multi-camera architecture.

This module provides the communication layer between cameras and the central server.
All event creation, file tracking, and logging now goes through REST API calls.

Retry Strategy:
- register_camera(): Retry forever (camera can't operate without registration)
- create_event(): Retry forever (events are critical, can't be lost)
- update_file(): Retry 3x with backoff (best-effort, files already on NFS)
- send_logs(): Single attempt (best-effort, local logging as fallback)
- check_health(): Single attempt (informational only)
"""

import requests
import time
import socket
from typing import Optional, Dict, List, Any
from datetime import datetime
from config import config


class APIClient:
    """
    Client for communicating with central server API.
    
    This class handles all REST API communication between the camera and
    central server, including camera registration, event creation, file
    status updates, and log transmission.
    
    Network failures are handled gracefully with appropriate retry logic
    based on the criticality of each operation.
    """
    
    def __init__(self):
        """
        Initialize API client with configuration from config module.
        
        Sets up:
        - Base URL from config
        - Camera identification
        - HTTP session for connection pooling
        - Default headers
        - Timeout values
        """
        self.base_url = config.CENTRAL_SERVER_API_BASE
        self.camera_id = config.CAMERA_ID
        self.camera_name = config.CAMERA_NAME
        self.camera_location = config.CAMERA_LOCATION
        
        # Timeouts: (connect_timeout, read_timeout)
        # Connect timeout: 5s allows for WiFi latency on Pi Zero
        # Read timeout: 30s allows for server processing time
        self.timeout = (5, 30)
        
        # Create session for connection pooling
        # This reuses TCP connections for better performance
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': f'SecurityCamera/{self.camera_id}'
        })
        
        # Don't use logger in __init__ to avoid circular dependency
        # Logger's __init__ creates APIClient, so we can't call logger here
        print(f"[INFO] API Client initialized: {self.base_url}")
    
    def _get_local_ip(self) -> str:
        """
        Get local IP address of this camera.
        
        Uses a clever trick: opens a UDP socket to 8.8.8.8 which doesn't
        actually connect but causes the OS to determine the route and
        select the correct local interface.
        
        Returns:
            str: Local IP address (e.g., "192.168.1.201") or "127.0.0.1" on failure
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))  # Doesn't actually connect
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"  # Fallback to localhost
    
    def register_camera(self) -> bool:
        """
        Register camera with central server.
        
        CRITICAL OPERATION: Retries indefinitely until successful.
        Camera cannot operate without being registered with the central server.
        
        Retry strategy:
        - Attempt 1: Immediate
        - Attempt 2: Wait 5 seconds
        - Attempt 3: Wait 10 seconds
        - Attempt 4+: Wait 30 seconds between attempts
        
        Returns:
            bool: True when registration succeeds (never returns False)
        """
        # Lazy import to avoid circular dependency
        from logger import log
        
        attempt = 0
        
        while True:
            attempt += 1
            
            try:
                # Get current local IP address
                ip_address = self._get_local_ip()
                
                payload = {
                    "camera_id": self.camera_id,
                    "name": self.camera_name,
                    "location": self.camera_location,
                    "ip_address": ip_address
                }
                
                log(f"Attempting camera registration (attempt {attempt})...", level="INFO")
                
                response = self.session.post(
                    f"{self.base_url}/cameras/register",
                    json=payload,
                    timeout=self.timeout
                )
                
                if response.status_code in [200, 201]:
                    log(f"Camera registered successfully: {self.camera_id}", level="INFO")
                    return True
                else:
                    log(f"Registration failed (attempt {attempt}): HTTP {response.status_code} - {response.text}", 
                        level="WARNING")
            
            except requests.exceptions.Timeout:
                log(f"Registration timeout (attempt {attempt}): Server not responding", level="WARNING")
            
            except requests.exceptions.ConnectionError:
                log(f"Registration failed (attempt {attempt}): Cannot connect to server at {self.base_url}", 
                    level="WARNING")
            
            except requests.exceptions.RequestException as e:
                log(f"Registration failed (attempt {attempt}): {e}", level="WARNING")
            
            except Exception as e:
                log(f"Unexpected error during registration (attempt {attempt}): {e}", level="ERROR")
            
            # Calculate retry delay with progressive backoff
            if attempt == 1:
                delay = 0  # Immediate retry
            elif attempt == 2:
                delay = 5
            elif attempt == 3:
                delay = 10
            else:
                delay = 30  # All subsequent attempts wait 30s
            
            if delay > 0:
                log(f"Retrying registration in {delay} seconds...", level="INFO")
                time.sleep(delay)
    
    def create_event(self, timestamp: str, motion_score: float) -> Optional[int]:
        """
        Create new motion event on central server.
        
        CRITICAL OPERATION: Retries indefinitely until successful.
        Events cannot be lost - motion detector will block until event is created.
        
        This is the most important API call in the system. If the central server
        is unavailable, the camera will keep retrying rather than losing the event.
        
        Retry strategy: Same as register_camera (immediate, 5s, 10s, 30s...)
        
        Args:
            timestamp: ISO 8601 timestamp string (e.g., "2025-10-30T14:30:22.186476")
            motion_score: Motion detection score (0-100)
        
        Returns:
            int: event_id from central server (never returns None, retries forever)
        """
        # Lazy import to avoid circular dependency
        from logger import log
        
        attempt = 0
        
        while True:
            attempt += 1
            
            try:
                payload = {
                    "camera_id": self.camera_id,
                    "timestamp": timestamp,
                    "motion_score": motion_score
                }
                
                if attempt == 1:
                    log(f"Creating event: motion_score={motion_score:.1f}", level="INFO")
                else:
                    log(f"Creating event (attempt {attempt}): motion_score={motion_score:.1f}", level="INFO")
                
                response = self.session.post(
                    f"{self.base_url}/events",
                    json=payload,
                    timeout=self.timeout
                )
                
                if response.status_code in [200, 201]:
                    data = response.json()
                    event_id = data.get("id")
                    
                    if event_id is None:
                        log(f"Event creation response missing 'id' field: {data}", level="ERROR")
                        raise ValueError("Response missing event_id")
                    
                    log(f"Event created successfully: event_id={event_id}", level="INFO")
                    return event_id
                else:
                    log(f"Event creation failed (attempt {attempt}): HTTP {response.status_code} - {response.text}", 
                        level="WARNING")
            
            except requests.exceptions.Timeout:
                log(f"Event creation timeout (attempt {attempt}): Server not responding", level="WARNING")
            
            except requests.exceptions.ConnectionError:
                log(f"Event creation failed (attempt {attempt}): Cannot connect to server", level="WARNING")
            
            except requests.exceptions.RequestException as e:
                log(f"Event creation failed (attempt {attempt}): {e}", level="WARNING")
            
            except ValueError as e:
                log(f"Event creation failed (attempt {attempt}): {e}", level="ERROR")
            
            except Exception as e:
                log(f"Unexpected error creating event (attempt {attempt}): {e}", level="ERROR")
            
            # Calculate retry delay with progressive backoff
            if attempt == 1:
                delay = 0  # Immediate retry
            elif attempt == 2:
                delay = 5
            elif attempt == 3:
                delay = 10
            else:
                delay = 30  # All subsequent attempts wait 30s
            
            if delay > 0:
                log(f"Retrying event creation in {delay} seconds...", level="INFO")
                time.sleep(delay)
    
    def update_file(self, event_id: int, file_type: str, file_path: str,
                    transferred: bool = True, video_duration: Optional[float] = None) -> bool:
        """
        Update file transfer status for an event.
        
        BEST-EFFORT OPERATION: Retries up to 3 times with exponential backoff.
        Files are already on NFS, so this is just updating metadata.
        If all retries fail, logs error and returns False (non-blocking).
        
        Retry strategy: 1s, 2s, 4s (exponential backoff)
        
        Args:
            event_id: Event ID from create_event()
            file_type: One of: "image_a", "image_b", "thumbnail", "video"
            file_path: Relative path on NFS (e.g., "camera_1/pictures/42_20251030_143022_a.jpg")
            transferred: Always True (indicates successful transfer)
            video_duration: Duration in seconds (only for video file_type)
        
        Returns:
            bool: True if update succeeded, False if all retries failed
        """
        # Lazy import to avoid circular dependency
        from logger import log
        
        payload = {
            "file_type": file_type,
            "file_path": file_path,
            "transferred": transferred
        }
        
        # Add video_duration only for video files
        if video_duration is not None:
            payload["video_duration"] = video_duration
        
        max_retries = 3
        
        for attempt in range(1, max_retries + 1):
            try:
                log(f"Updating file status: event_id={event_id}, type={file_type} (attempt {attempt})", 
                    level="DEBUG")
                
                response = self.session.patch(
                    f"{self.base_url}/events/{event_id}/files",
                    json=payload,
                    timeout=self.timeout
                )
                
                if response.status_code in [200, 201]:
                    log(f"File status updated: event_id={event_id}, type={file_type}", level="INFO")
                    return True
                else:
                    log(f"File update failed (attempt {attempt}): HTTP {response.status_code} - {response.text}", 
                        level="WARNING")
            
            except requests.exceptions.Timeout:
                log(f"File update timeout (attempt {attempt}): event_id={event_id}, type={file_type}", 
                    level="WARNING")
            
            except requests.exceptions.RequestException as e:
                log(f"File update failed (attempt {attempt}): {e}", level="WARNING")
            
            except Exception as e:
                log(f"Unexpected error updating file (attempt {attempt}): {e}", level="ERROR")
            
            # Exponential backoff: 1s, 2s, 4s
            if attempt < max_retries:
                delay = 2 ** (attempt - 1)
                time.sleep(delay)
        
        log(f"File update failed after {max_retries} attempts: event_id={event_id}, type={file_type}", 
            level="ERROR")
        return False
    
    def send_logs(self, log_entries: List[Dict[str, Any]]) -> bool:
        """
        Send batch of log entries to central server.
        
        BEST-EFFORT OPERATION: Retries up to 2 times.
        Logs are not critical - local logging is primary, API is secondary.
        If all retries fail, logs remain local only.
        
        Retry strategy: 1s, 5s delays
        
        Args:
            log_entries: List of dicts with keys: source, timestamp, level, message
                Example: [
                    {
                        "source": "camera_1",
                        "timestamp": "2025-10-30T14:30:22.186476",
                        "level": "INFO",
                        "message": "Motion detected"
                    }
                ]
        
        Returns:
            bool: True if logs sent successfully, False if failed
        """
        # Lazy import to avoid circular dependency
        from logger import log
        
        if not log_entries:
            return True  # Nothing to send
        
        max_retries = 2
        retry_delays = [1, 5]
        
        for attempt in range(1, max_retries + 1):
            try:
                response = self.session.post(
                    f"{self.base_url}/logs",
                    json=log_entries,
                    timeout=self.timeout
                )
                
                if response.status_code in [200, 201]:
                    data = response.json()
                    count = data.get("logs_inserted", len(log_entries))
                    log(f"Sent {count} log entries to central server", level="DEBUG")
                    return True
                else:
                    log(f"Log send failed (attempt {attempt}): HTTP {response.status_code}", level="WARNING")
            
            except requests.exceptions.RequestException as e:
                log(f"Log send failed (attempt {attempt}): {e}", level="WARNING")
            
            except Exception as e:
                log(f"Unexpected error sending logs (attempt {attempt}): {e}", level="ERROR")
            
            # Retry with delay
            if attempt < max_retries:
                time.sleep(retry_delays[attempt - 1])
        
        log(f"Failed to send {len(log_entries)} log entries after {max_retries} attempts", level="ERROR")
        return False
    
    def check_health(self) -> bool:
        """
        Check if central server API is responsive.
        
        INFORMATIONAL OPERATION: Single attempt, no retries.
        Used for monitoring and diagnostics only.
        
        Returns:
            bool: True if server is healthy, False otherwise
        """
        try:
            response = self.session.get(
                f"{self.base_url}/health",
                timeout=(2, 3)  # Shorter timeout for health check
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "healthy":
                    return True
            
            return False
        
        except requests.exceptions.RequestException:
            return False
        
        except Exception:
            return False


# ============================================================================
# STANDALONE TESTING
# ============================================================================

if __name__ == "__main__":
    """
    Test API client functionality.
    
    Prerequisites:
    - Central server must be running at CENTRAL_SERVER_API_BASE
    - Config must be properly set up
    
    Usage:
        python api_client.py
    """
    print("="*60)
    print("Testing API Client")
    print("="*60)
    
    # Initialize client
    print("\nInitializing API Client...")
    client = APIClient()
    print(f"  Camera ID: {client.camera_id}")
    print(f"  API Base: {client.base_url}")
    
    # Test 1: Health check
    print("\n" + "-"*60)
    print("Test 1: Health Check")
    print("-"*60)
    healthy = client.check_health()
    print(f"  Server healthy: {healthy}")
    
    if not healthy:
        print("\n⚠️  WARNING: Central server is not responding!")
        print("   Make sure the server is running before continuing.")
        print("   Some tests will retry indefinitely if server is down.")
        
        user_input = input("\nContinue anyway? (y/n): ")
        if user_input.lower() != 'y':
            print("Exiting tests.")
            exit(0)
    
    # Test 2: Camera registration
    print("\n" + "-"*60)
    print("Test 2: Camera Registration")
    print("-"*60)
    print("  Note: This will retry forever if server is down...")
    success = client.register_camera()
    print(f"  Registration successful: {success}")
    
    # Test 3: Event creation
    print("\n" + "-"*60)
    print("Test 3: Event Creation")
    print("-"*60)
    timestamp = datetime.now().isoformat()
    print(f"  Timestamp: {timestamp}")
    print(f"  Motion score: 75.3")
    print("  Note: This will retry forever if server is down...")
    event_id = client.create_event(timestamp, 75.3)
    print(f"  Event ID: {event_id}")
    
    # Test 4: File updates (if event was created)
    if event_id:
        print("\n" + "-"*60)
        print("Test 4: File Updates")
        print("-"*60)
        
        # Update image_a
        print("  Updating image_a...")
        success = client.update_file(
            event_id=event_id,
            file_type="image_a",
            file_path=f"{client.camera_id}/pictures/{event_id}_test_a.jpg"
        )
        print(f"    Result: {success}")
        
        # Update image_b
        print("  Updating image_b...")
        success = client.update_file(
            event_id=event_id,
            file_type="image_b",
            file_path=f"{client.camera_id}/pictures/{event_id}_test_b.jpg"
        )
        print(f"    Result: {success}")
        
        # Update thumbnail
        print("  Updating thumbnail...")
        success = client.update_file(
            event_id=event_id,
            file_type="thumbnail",
            file_path=f"{client.camera_id}/thumbs/{event_id}_thumb.jpg"
        )
        print(f"    Result: {success}")
        
        # Update video
        print("  Updating video...")
        success = client.update_file(
            event_id=event_id,
            file_type="video",
            file_path=f"{client.camera_id}/videos/{event_id}_motion.h264",
            video_duration=30.5
        )
        print(f"    Result: {success}")
    
    # Test 5: Log sending
    print("\n" + "-"*60)
    print("Test 5: Log Sending")
    print("-"*60)
    logs = [
        {
            "source": client.camera_id,
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": "API client test log entry 1"
        },
        {
            "source": client.camera_id,
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": "API client test log entry 2"
        },
        {
            "source": client.camera_id,
            "timestamp": datetime.now().isoformat(),
            "level": "WARNING",
            "message": "API client test warning"
        }
    ]
    print(f"  Sending {len(logs)} log entries...")
    success = client.send_logs(logs)
    print(f"  Logs sent: {success}")
    
    # Final health check
    print("\n" + "-"*60)
    print("Test 6: Final Health Check")
    print("-"*60)
    healthy = client.check_health()
    print(f"  Server healthy: {healthy}")
    
    print("\n" + "="*60)
    print("✓ API Client tests complete")
    print("="*60)
    print("\nNext steps:")
    print("  1. Check central server database for:")
    print(f"     - Camera registration: {client.camera_id}")
    if event_id:
        print(f"     - Event record: event_id={event_id}")
        print(f"     - File records: 4 files for event {event_id}")
    print(f"     - Log entries: {len(logs)} logs from {client.camera_id}")
    print("  2. Verify all data is correct")
    print("  3. Ready for Session 1B-3 (Camera Registration on Startup)")