#!/usr/bin/env python3
"""
Security Camera System - Camera Control API
============================================
Flask-based HTTP API for remote control of camera streaming and motion detection.

Thread 6: API Server
- Listens on port 5000
- Provides endpoints for central server to control streaming
- Directly calls circular_buffer and mjpeg_server methods

Endpoints:
- GET  /api/ping    - Connectivity test
- GET  /api/health  - Camera status
- POST /api/stream  - Start/stop streaming (action=start|stop)
"""

from flask import Flask, request, jsonify
import threading
import time
from datetime import datetime
from config import config
from logger import log


class CameraControlAPI:
    """
    Flask-based API server for camera control.
    
    Provides HTTP endpoints for remote control of:
    - Streaming start/stop
    - Camera health monitoring
    - Status queries
    """
    
    def __init__(self, circular_buffer, mjpeg_server, event_processor):
        """
        Initialize API server.
        
        Args:
            circular_buffer: CircularBuffer instance for streaming control
            mjpeg_server: MJPEGServer instance for HTTP server control
            event_processor: EventProcessor instance for abort control
        """
        self.circular_buffer = circular_buffer
        self.mjpeg_server = mjpeg_server
        self.event_processor = event_processor
        self.start_time = time.time()
        self.streaming = False  # Track streaming state
        self.transitioning = False  # Track state transitions
        
        # Heartbeat timeout management
        self.heartbeat_timer = None  # Timer for heartbeat timeout
        self.stream_start_time = None  # Timestamp when streaming started
        self.heartbeat_lock = threading.Lock()  # Thread-safe timer management
        
        # Create Flask app
        self.app = Flask(__name__)
        self._setup_routes()
        
        # Server thread
        self.server_thread = None
        self.running = False
        
        log(f"CameraControlAPI initialized on port {config.API_CONTROL_PORT}")
    
    def _start_heartbeat_timer(self):
        """
        Start or restart the heartbeat timeout timer.
        Called when streaming starts or when heartbeat is received.
        """
        with self.heartbeat_lock:
            # Cancel existing timer if any
            if self.heartbeat_timer:
                self.heartbeat_timer.cancel()
            
            # Start new timer
            self.heartbeat_timer = threading.Timer(
                config.STREAM_HEARTBEAT_TIMEOUT,
                self._on_heartbeat_timeout
            )
            self.heartbeat_timer.daemon = True
            self.heartbeat_timer.start()
    
    def _cancel_heartbeat_timer(self):
        """
        Cancel the heartbeat timeout timer.
        Called when streaming stops normally.
        """
        with self.heartbeat_lock:
            if self.heartbeat_timer:
                self.heartbeat_timer.cancel()
                self.heartbeat_timer = None
    
    def _on_heartbeat_timeout(self):
        """
        Called when heartbeat timer expires (no heartbeat received for 30 seconds).
        Automatically stops streaming and resumes motion detection.
        """
        log("WARNING: Stream heartbeat timeout - no keepalive received for "
            f"{config.STREAM_HEARTBEAT_TIMEOUT}s, auto-stopping stream", level="WARNING")
        
        # Set transitioning state
        self.transitioning = True
        
        try:
            # Stop streaming sequence
            self.mjpeg_server.stop_http_server()
            self.circular_buffer.stop_streaming()
            self.streaming = False
            self.stream_start_time = None
            
            log("✓ Stream auto-stopped due to heartbeat timeout")
            
        except Exception as e:
            log(f"Error during heartbeat timeout auto-stop: {e}", level="ERROR")
            # Force cleanup
            try:
                self.circular_buffer.stop_streaming()
                self.mjpeg_server.stop_http_server()
            except:
                pass
            self.streaming = False
            self.stream_start_time = None
        finally:
            self.transitioning = False
    
    def _check_max_duration(self):
        """
        Check if streaming has exceeded maximum duration (30 minutes).
        
        Returns:
            bool: True if max duration exceeded, False otherwise
        """
        if not self.stream_start_time:
            return False
        
        elapsed = time.time() - self.stream_start_time
        return elapsed >= config.STREAM_MAX_DURATION_SECONDS
    
    def _setup_routes(self):
        """Setup Flask routes."""
        
        @self.app.route('/api/ping', methods=['GET'])
        def ping():
            """Simple connectivity test."""
            return jsonify({
                'success': True,
                'camera_id': config.CAMERA_ID,
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            })
        
        @self.app.route('/api/version', methods=['GET'])
        def get_version():
            """
            Get camera version and basic info.
            Used by central server footer to display camera status.
            """
            uptime = int(time.time() - self.start_time)
            
            return jsonify({
                'success': True,
                'camera_id': config.CAMERA_ID,
                'camera_name': config.CAMERA_NAME,
                'location': config.CAMERA_LOCATION,
                'version': config.SYSTEM_VERSION,
                'status': 'online',
                'uptime_seconds': uptime,
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            })
        
        @self.app.route('/api/health', methods=['GET'])
        def health():
            """Camera health and status check."""
            uptime = int(time.time() - self.start_time)
            
            # Determine motion state
            if self.streaming:
                motion_state = 'paused'
            else:
                motion_state = 'idle'
            
            response = {
                'camera_id': config.CAMERA_ID,
                'name': config.CAMERA_NAME,
                'status': 'online',
                'motion_state': motion_state,
                'streaming': self.streaming,
                'capture_interval': self.circular_buffer.capture_interval,
                'uptime_seconds': uptime,
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            }
            
            if self.streaming:
                response['stream_port'] = config.LIVESTREAM_PORT
                response['stream_url'] = f"http://{config.CAMERA_ID}:{config.LIVESTREAM_PORT}/stream.mjpg"
            
            return jsonify(response)
        
        @self.app.route('/api/stream', methods=['POST'])
        def control_stream():
            """
            Control streaming state with abort support and heartbeat.
            
            Query Parameters:
                action: 'start', 'stop', or 'heartbeat'
            
            Returns:
                JSON response with success status and details
            """
            action = request.args.get('action', '').lower()
            
            if action not in ['start', 'stop', 'heartbeat']:
                return jsonify({
                    'success': False,
                    'message': "Invalid action parameter. Use 'start', 'stop', or 'heartbeat'."
                }), 400
            
            # ===== HANDLE HEARTBEAT =====
            if action == 'heartbeat':
                # Heartbeat can only be sent when streaming
                if not self.streaming:
                    return jsonify({
                        'success': False,
                        'message': 'No active stream to send heartbeat to.'
                    }), 400
                
                # Check if max duration exceeded
                if self._check_max_duration():
                    log(f"WARNING: Stream exceeded maximum duration "
                        f"({config.STREAM_MAX_DURATION_SECONDS}s), force-stopping", level="WARNING")
                    
                    # Set transition state
                    self.transitioning = True
                    
                    try:
                        # Cancel heartbeat timer
                        self._cancel_heartbeat_timer()
                        
                        # Stop streaming
                        self.mjpeg_server.stop_http_server()
                        self.circular_buffer.stop_streaming()
                        self.streaming = False
                        self.stream_start_time = None
                        
                        log("✓ Stream force-stopped due to max duration exceeded")
                        
                        return jsonify({
                            'success': False,
                            'message': 'Stream exceeded maximum duration and was automatically stopped.',
                            'max_duration_exceeded': True
                        }), 400
                        
                    except Exception as e:
                        log(f"Error stopping stream after max duration: {e}", level="ERROR")
                        # Force cleanup
                        try:
                            self.circular_buffer.stop_streaming()
                            self.mjpeg_server.stop_http_server()
                        except:
                            pass
                        self.streaming = False
                        self.stream_start_time = None
                        
                        return jsonify({
                            'success': False,
                            'message': f'Error stopping stream: {str(e)}'
                        }), 500
                    finally:
                        self.transitioning = False
                
                # Reset heartbeat timer
                self._start_heartbeat_timer()
                
                # Calculate elapsed time
                elapsed = int(time.time() - self.stream_start_time) if self.stream_start_time else 0
                
                return jsonify({
                    'success': True,
                    'action': 'heartbeat',
                    'message': 'Heartbeat received',
                    'streaming': True,
                    'elapsed_seconds': elapsed,
                    'camera_id': config.CAMERA_ID
                })
            # ============================
            
            # ===== CHECK IF TRANSITIONING =====
            if self.transitioning:
                return jsonify({
                    'success': False,
                    'message': 'Stream state change in progress, try again in 1 second'
                }), 409
            # ==================================
            
            if action == 'start':
                # Check if already streaming
                if self.streaming:
                    return jsonify({
                        'success': False,
                        'message': 'Stream already running.'
                    }), 400
                
                try:
                    # ===== SET TRANSITION STATE =====
                    self.transitioning = True
                    log("API: Starting streaming mode (transition started)")
                    # ================================
                    
                    # ===== CHECK AND ABORT EVENT PROCESSING =====
                    if self.event_processor.is_processing():
                        log("API: Event processing in progress - initiating abort")
                        
                        # Request abort and wait for completion
                        abort_success = self.event_processor.abort_current_event(
                            timeout=config.ABORT_TIMEOUT_SECONDS
                        )
                        
                        if not abort_success:
                            log(f"API: Event abort timed out after {config.ABORT_TIMEOUT_SECONDS}s", 
                                level="WARNING")
                            # Continue anyway - streaming is priority
                        else:
                            log("API: Event processing aborted successfully")
                    # ==============================================
                    
                    # Start streaming sequence
                    # 1. Start circular buffer streaming (fast capture, pause motion)
                    self.circular_buffer.start_streaming()
                    
                    # 2. Start MJPEG HTTP server
                    self.mjpeg_server.start_http_server()
                    
                    # 3. Update state and start heartbeat timer
                    self.streaming = True
                    self.stream_start_time = time.time()
                    self._start_heartbeat_timer()
                    
                    log(f"✓ Streaming started on port {config.LIVESTREAM_PORT}")
                    log(f"  Heartbeat timeout: {config.STREAM_HEARTBEAT_TIMEOUT}s")
                    log(f"  Max duration: {config.STREAM_MAX_DURATION_SECONDS}s")
                    
                    return jsonify({
                        'success': True,
                        'action': 'start',
                        'message': 'Streaming started. Motion detection paused.',
                        'stream_port': config.LIVESTREAM_PORT,
                        'stream_url': f"http://{config.CAMERA_ID}:{config.LIVESTREAM_PORT}/stream.mjpg",
                        'capture_interval': config.STREAMING_CAPTURE_INTERVAL,
                        'camera_id': config.CAMERA_ID,
                        'camera_name': config.CAMERA_NAME,
                        'heartbeat_timeout': config.STREAM_HEARTBEAT_TIMEOUT,
                        'max_duration': config.STREAM_MAX_DURATION_SECONDS
                    })
                    
                except Exception as e:
                    log(f"Error starting streaming: {e}", level="ERROR")
                    # Rollback on error
                    try:
                        self._cancel_heartbeat_timer()
                        self.circular_buffer.stop_streaming()
                        self.mjpeg_server.stop_http_server()
                    except:
                        pass
                    self.streaming = False
                    self.stream_start_time = None
                    
                    return jsonify({
                        'success': False,
                        'message': f'Failed to start streaming: {str(e)}'
                    }), 500
                
                finally:
                    # ===== CLEAR TRANSITION STATE =====
                    self.transitioning = False
                    log("API: Streaming mode transition complete")
                    # ==================================
            
            else:  # action == 'stop'
                # Check if not streaming
                if not self.streaming:
                    return jsonify({
                        'success': False,
                        'message': 'Stream is not running.'
                    }), 400
                
                try:
                    # ===== SET TRANSITION STATE =====
                    self.transitioning = True
                    log("API: Stopping streaming mode (transition started)")
                    # ================================
                    
                    # Cancel heartbeat timer
                    self._cancel_heartbeat_timer()
                    
                    # Stop streaming sequence
                    # 1. Stop MJPEG HTTP server
                    self.mjpeg_server.stop_http_server()
                    
                    # 2. Stop circular buffer streaming (normal capture, resume motion)
                    self.circular_buffer.stop_streaming()
                    
                    # 3. Update state
                    self.streaming = False
                    self.stream_start_time = None
                    
                    log("✓ Streaming stopped")
                    
                    return jsonify({
                        'success': True,
                        'action': 'stop',
                        'message': 'Streaming stopped. Motion detection resumed.',
                        'capture_interval': config.NORMAL_CAPTURE_INTERVAL,
                        'camera_id': config.CAMERA_ID,
                        'camera_name': config.CAMERA_NAME
                    })
                    
                except Exception as e:
                    log(f"Error stopping streaming: {e}", level="ERROR")
                    # Try to clean up anyway
                    try:
                        self._cancel_heartbeat_timer()
                        self.circular_buffer.stop_streaming()
                        self.mjpeg_server.stop_http_server()
                    except:
                        pass
                    self.streaming = False
                    self.stream_start_time = None
                    
                    return jsonify({
                        'success': False,
                        'message': f'Failed to stop streaming cleanly: {str(e)}'
                    }), 500
                
                finally:
                    # ===== CLEAR TRANSITION STATE =====
                    self.transitioning = False
                    log("API: Stop streaming mode transition complete")
                    # ==================================
    
    def start(self):
        """Start API server in background thread."""
        if self.running:
            log("API server already running", level="WARNING")
            return
        
        self.running = True
        
        def run_server():
            log(f"Flask server starting on {config.API_CONTROL_HOST}:{config.API_CONTROL_PORT}")
            self.app.run(
                host=config.API_CONTROL_HOST,
                port=config.API_CONTROL_PORT,
                debug=False,
                use_reloader=False,
                threaded=True
            )
        
        self.server_thread = threading.Thread(
            target=run_server,
            name="CameraControlAPI",
            daemon=True
        )
        self.server_thread.start()
        
        log(f"✓ Camera Control API started: http://{config.API_CONTROL_HOST}:{config.API_CONTROL_PORT}")
    
    def stop(self):
        """Stop API server."""
        self.running = False
        
        # Cancel heartbeat timer
        self._cancel_heartbeat_timer()
        
        # Ensure streaming is stopped
        if self.streaming:
            try:
                self.mjpeg_server.stop_http_server()
                self.circular_buffer.stop_streaming()
                self.streaming = False
                self.stream_start_time = None
            except Exception as e:
                log(f"Error stopping streaming during API shutdown: {e}", level="ERROR")
        
        log("Camera Control API stopped")


# ============================================================================
# STANDALONE TESTING
# ============================================================================

if __name__ == "__main__":
    """
    Test camera control API with mock objects.
    """
    print("Camera Control API - Standalone Test")
    print("="*60)
    print("Note: This test requires CircularBuffer and MJPEGServer instances.")
    print("Run full system test via sec_cam_main.py instead.")
    print("="*60)
    
    print("\n✓ CameraControlAPI class defined successfully")
    print("\nAPI Endpoints:")
    print("  GET  /api/ping              - Connectivity test")
    print("  GET  /api/health            - Camera status")
    print("  POST /api/stream?action=start - Start streaming")
    print("  POST /api/stream?action=stop  - Stop streaming")
    print(f"\nDefault port: {config.API_CONTROL_PORT}")
    print("\nReady for integration testing!")