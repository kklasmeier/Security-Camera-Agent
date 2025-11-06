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
        
        # Create Flask app
        self.app = Flask(__name__)
        self._setup_routes()
        
        # Server thread
        self.server_thread = None
        self.running = False
        
        log(f"CameraControlAPI initialized on port {config.API_CONTROL_PORT}")
    
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
            Control streaming state with abort support.
            
            Query Parameters:
                action: 'start' or 'stop'
            
            Returns:
                JSON response with success status and details
            """
            action = request.args.get('action', '').lower()
            
            if action not in ['start', 'stop']:
                return jsonify({
                    'success': False,
                    'message': "Invalid action parameter. Use 'start' or 'stop'."
                }), 400
            
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
                    
                    # 3. Update state
                    self.streaming = True
                    
                    log(f"✓ Streaming started on port {config.LIVESTREAM_PORT}")
                    
                    return jsonify({
                        'success': True,
                        'action': 'start',
                        'message': 'Streaming started. Motion detection paused.',
                        'stream_port': config.LIVESTREAM_PORT,
                        'stream_url': f"http://{config.CAMERA_ID}:{config.LIVESTREAM_PORT}/stream.mjpg",
                        'capture_interval': config.STREAMING_CAPTURE_INTERVAL,
                        'camera_id': config.CAMERA_ID,
                        'camera_name': config.CAMERA_NAME
                    })
                    
                except Exception as e:
                    log(f"Error starting streaming: {e}", level="ERROR")
                    # Rollback on error
                    try:
                        self.circular_buffer.stop_streaming()
                        self.mjpeg_server.stop_http_server()
                    except:
                        pass
                    self.streaming = False
                    
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
                    
                    # Stop streaming sequence
                    # 1. Stop MJPEG HTTP server
                    self.mjpeg_server.stop_http_server()
                    
                    # 2. Stop circular buffer streaming (normal capture, resume motion)
                    self.circular_buffer.stop_streaming()
                    
                    # 3. Update state
                    self.streaming = False
                    
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
                        self.circular_buffer.stop_streaming()
                        self.mjpeg_server.stop_http_server()
                    except:
                        pass
                    self.streaming = False
                    
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
        
        # Ensure streaming is stopped
        if self.streaming:
            try:
                self.mjpeg_server.stop_http_server()
                self.circular_buffer.stop_streaming()
                self.streaming = False
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