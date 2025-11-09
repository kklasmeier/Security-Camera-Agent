"""
Security Camera System - MJPEG Streaming Server (Refactored for Session 8.5)
==============================================================================
Thread 4: Serves MJPEG stream on demand (API-controlled, not database-polled).

CHANGES FROM LEGACY VERSION:
- Removed database flag polling
- Removed auto-timeout logic (controlled by API)
- Added direct start_http_server() and stop_http_server() methods
- Removed auto-shutdown when clients disconnect (API controls lifecycle)

When API calls start_http_server():
- Starts HTTP server on port 8080
- Serves MJPEG stream to browsers at /stream.mjpg
- Buffer.start_streaming() already called by API

When API calls stop_http_server():
- Stops HTTP server
- Buffer.stop_streaming() already called by API
"""

import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from PIL import Image
from logger import log
from config import config


class MJPEGHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler for MJPEG stream.
    
    Serves a single endpoint: /stream.mjpg
    """
    
    def do_GET(self):
        """Handle HTTP GET requests."""
        # Strip query string for path matching (e.g., /stream.mjpg?t=123456)
        path = self.path.split('?')[0]
        
        if path == '/stream.mjpg':
            self.serve_mjpeg_stream()
        else:
            self.send_error(404, "Stream not found. Try /stream.mjpg")
    
    def serve_mjpeg_stream(self):
        """
        Serve MJPEG stream to client.
        
        MJPEG format:
        - HTTP response with multipart/x-mixed-replace content type
        - Each frame is a JPEG image with boundary marker
        - Browsers display as continuous video
        """
        # Notify server of new client
        mjpeg_server = getattr(self.server, "mjpeg_server", None)
        if mjpeg_server:
            mjpeg_server.client_connected()
        
        try:
            # Send HTTP headers
            self.send_response(200)
            self.send_header('Age', '0')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            
            log(f"Client connected to MJPEG stream: {self.client_address[0]}")
            
            # Stream frames until client disconnects
            frame_delay = 1.0 / config.LIVESTREAM_FRAMERATE
            frame_count = 0
            
            while True:
                # Get latest frame from circular buffer (use getattr to avoid attribute access on BaseServer)
                buffer_obj = getattr(self.server, "circular_buffer", None)
                if buffer_obj is None:
                    log(f"[STREAM DEBUG] No circular_buffer on server, waiting...", level="WARNING")
                    time.sleep(0.1)
                    continue
                frame = buffer_obj.get_latest_frame_for_livestream()
                
                if frame is None:
                    log(f"[STREAM DEBUG] Frame is None, waiting...", level="WARNING")
                    time.sleep(0.1)
                    continue
                
                # Convert frame to JPEG
                try:
                    # Convert BGR to RGB if needed (OpenCV uses BGR by default)
                    # If your frames are already RGB, you can remove this line
                    import cv2
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    
                    img = Image.fromarray(frame_rgb)
                    buffer = BytesIO()
                    img.save(buffer, format='JPEG', quality=config.LIVESTREAM_JPEG_QUALITY)
                    jpeg_bytes = buffer.getvalue()
                    buffer.close()
                except Exception as e:
                    log(f"[STREAM DEBUG] Error encoding JPEG: {e}", level="ERROR")
                    time.sleep(0.1)
                    continue
                
                try:
                    # Send frame in browser-compatible format
                    self.wfile.write(b'--FRAME\r\n')
                    self.wfile.write(b'Content-Type: image/jpeg\r\n')
                    self.wfile.write(b'Content-Length: ' + str(len(jpeg_bytes)).encode() + b'\r\n')
                    self.wfile.write(b'\r\n')
                    self.wfile.write(jpeg_bytes)
                    self.wfile.write(b'\r\n')
                    
                    frame_count += 1
                    
                    # Log every 100 frames
                    if frame_count % 100 == 0:
                        log(f"[STREAM DEBUG] Sent {frame_count} frames to {self.client_address[0]}")
                    
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    log(f"[STREAM DEBUG] Connection error after {frame_count} frames: {type(e).__name__}")
                    break
                except Exception as e:
                    log(f"[STREAM DEBUG] Unexpected error sending frame {frame_count}: {e}", level="ERROR")
                    break
                
                # Rate limiting
                time.sleep(frame_delay)
                
        except (BrokenPipeError, ConnectionResetError, OSError):
            log(f"Client disconnected from MJPEG stream: {self.client_address[0]}")
        except Exception as e:
            log(f"Error serving MJPEG stream: {e}", level="ERROR")
        finally:
            # Always notify server when client disconnects
            mjpeg_server = getattr(self.server, "mjpeg_server", None)
            if mjpeg_server:
                mjpeg_server.client_disconnected()
    
    def log_message(self, format, *args):
        """Suppress default HTTP logging (too verbose)."""
        pass


class MJPEGServer:
    """
    MJPEG streaming server (API-controlled).
    
    Provides direct start/stop methods called by Camera Control API.
    No database polling, no auto-timeout, no auto-shutdown.
    Lifecycle fully controlled by external API calls.
    """
    
    def __init__(self, circular_buffer):
        """
        Initialize MJPEG server.
        
        Args:
            circular_buffer: CircularBuffer instance for frame access
        """
        self.buffer = circular_buffer
        self.server = None
        self.server_thread = None
        self.active_clients = 0  # Track number of connected clients
        self.client_lock = threading.Lock()  # Thread-safe client counting
        
        log("MJPEGServer initialized (API-controlled mode)")
    
    def client_connected(self):
        """Called when a client connects to the stream."""
        with self.client_lock:
            self.active_clients += 1
            log(f"Client connected (total clients: {self.active_clients})")
    
    def client_disconnected(self):
        """
        Called when a client disconnects from the stream.
        
        Note: In API-controlled mode, we don't auto-stop streaming.
        The API must explicitly call stop_http_server().
        """
        with self.client_lock:
            self.active_clients -= 1
            log(f"Client disconnected (total clients: {self.active_clients})")
            
            if self.active_clients == 0:
                log("All clients disconnected - server still running (API-controlled)")
    
    def start_http_server(self):
        """
        Start HTTP server on LIVESTREAM_PORT.
        
        Called by Camera Control API when streaming is requested.
        """
        if self.server is not None:
            log("MJPEG HTTP server already running", level="WARNING")
            return
        
        try:
            # Create server
            self.server = HTTPServer(('0.0.0.0', config.LIVESTREAM_PORT), MJPEGHandler)
            # Use setattr to avoid static type checker errors when attaching custom attributes
            setattr(self.server, "circular_buffer", self.buffer)
            setattr(self.server, "mjpeg_server", self)  # Pass reference to self
            
            # Start server in background thread
            self.server_thread = threading.Thread(
                target=self.server.serve_forever,
                name="MJPEGServerHTTP",
                daemon=True
            )
            self.server_thread.start()
            
            log(f"✓ MJPEG HTTP server started on port {config.LIVESTREAM_PORT}")
            log(f"  Stream URL: http://<camera-ip>:{config.LIVESTREAM_PORT}/stream.mjpg")
            
        except Exception as e:
            log(f"Failed to start MJPEG HTTP server: {e}", level="ERROR")
            self.server = None
            raise
    
    def stop_http_server(self):
        """
        Stop HTTP server.
        
        Called by Camera Control API when streaming should stop.
        """
        if self.server is None:
            log("MJPEG HTTP server not running", level="WARNING")
            return
        
        try:
            # Capture server reference to avoid race where self.server becomes None
            server = self.server

            # Shutdown in separate thread to avoid blocking
            def shutdown_server():
                try:
                    if server is None:
                        return
                    server.shutdown()
                    server.server_close()
                except Exception as e:
                    log(f"Error in server shutdown: {e}", level="ERROR")
            
            shutdown_thread = threading.Thread(target=shutdown_server, daemon=True)
            shutdown_thread.start()
            shutdown_thread.join(timeout=3.0)  # Wait max 3 seconds
            
            log("✓ MJPEG HTTP server stopped")
            
        except Exception as e:
            log(f"Error stopping MJPEG HTTP server: {e}", level="ERROR")
        finally:
            self.server = None
            self.server_thread = None
            self.active_clients = 0  # Reset client count
    
    def is_running(self):
        """
        Check if HTTP server is currently running.
        
        Returns:
            bool: True if server is active, False otherwise
        """
        return self.server is not None


# ============================================================================
# STANDALONE TESTING
# ============================================================================

if __name__ == "__main__":
    """
    Test MJPEG server with mock objects.
    """
    print("MJPEG Server - Standalone Test (Session 8.5)")
    print("="*60)
    print("Note: This test requires CircularBuffer instance.")
    print("Run full system test via sec_cam_main.py instead.")
    print("="*60)
    
    print("\n✓ MJPEGServer class defined successfully")
    print("\nServer Features (API-Controlled):")
    print("  - Direct start_http_server() method")
    print("  - Direct stop_http_server() method")
    print("  - Serves MJPEG stream at /stream.mjpg")
    print(f"  - Port: {config.LIVESTREAM_PORT}")
    print("  - No database polling")
    print("  - No auto-timeout")
    print("  - Lifecycle controlled by Camera Control API")
    print("\nReady for integration testing!")