#!/usr/bin/env python3
"""
Test Script for Session 1B-3: Camera Registration
==================================================
Tests the camera registration functionality without starting full system.

This script validates:
1. API Client initialization
2. Camera registration (blocking)
3. Clean shutdown

Usage:
    python3 test_registration.py
"""

import sys
import signal
from api_client import APIClient
from config import config, validate_config, print_config
from logger import log, stop_logger

# Global reference for signal handler
_client = None
_interrupted = False

def signal_handler(signum, frame):
    """Handle Ctrl+C during registration."""
    global _interrupted
    _interrupted = True
    print("\n\n⚠️  Registration test interrupted by user")
    print("Cleaning up...")
    
    if _client:
        try:
            _client.session.close()
            print("✓ API client closed")
        except:
            pass
    
    sys.exit(1)

def test_registration():
    """
    Test camera registration flow.
    
    Returns:
        bool: True if test passed, False otherwise
    """
    global _client
    
    print("\n" + "="*60)
    print("Session 1B-3 Registration Test")
    print("="*60 + "\n")
    
    try:
        # Step 1: Print configuration
        print("Step 1: Validating Configuration")
        print("-"*60)
        print_config()
        
        try:
            validate_config()
            print("✓ Configuration valid\n")
        except ValueError as e:
            print(f"✗ Configuration error: {e}\n")
            return False
        
        # Step 2: Initialize API Client
        print("Step 2: Initializing API Client")
        print("-"*60)
        _client = APIClient()
        print(f"✓ API Client initialized")
        print(f"  Base URL: {_client.base_url}")
        print(f"  Camera ID: {_client.camera_id}")
        print(f"  Camera Name: {_client.camera_name}")
        print(f"  Camera Location: {_client.camera_location}\n")
        
        # Step 3: Health Check (optional)
        print("Step 3: Checking Central Server Health")
        print("-"*60)
        healthy = _client.check_health()
        
        if healthy:
            print("✓ Central server is healthy and responding\n")
        else:
            print("⚠️  WARNING: Central server health check failed")
            print("   Server may be down or unreachable")
            print("   Registration will retry indefinitely...")
            print("   Press Ctrl+C to abort if needed\n")
        
        # Step 4: Register Camera (BLOCKING)
        print("Step 4: Registering Camera with Central Server")
        print("-"*60)
        print("This will block until registration succeeds...")
        print("Press Ctrl+C to abort\n")
        
        log("="*60, level="INFO")
        log("CAMERA REGISTRATION TEST", level="INFO")
        log("="*60, level="INFO")
        
        # This will retry indefinitely until successful
        _client.register_camera()
        
        print("\n✓ Camera registered successfully!")
        log("✓ Camera registration test passed", level="INFO")
        
        # Step 5: Verify registration on central server
        print("\nStep 5: Verification")
        print("-"*60)
        print("To verify registration, check the central server database:")
        print("")
        print(f"  SELECT * FROM cameras WHERE camera_id = '{_client.camera_id}';")
        print("")
        print("Expected result:")
        print(f"  camera_id: {_client.camera_id}")
        print(f"  name: {_client.camera_name}")
        print(f"  location: {_client.camera_location}")
        print(f"  status: online")
        print(f"  last_seen: (current timestamp)")
        
        # Step 6: Cleanup
        print("\nStep 6: Cleanup")
        print("-"*60)
        _client.session.close()
        print("✓ API client session closed")
        
        # Final summary
        print("\n" + "="*60)
        print("✓ Registration Test PASSED")
        print("="*60)
        print("\nNext Steps:")
        print("  1. Verify camera registration in central server database")
        print("  2. Ready for Session 1B-4 (Motion Detector)")
        print("="*60 + "\n")
        
        return True
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Test aborted by user (Ctrl+C)")
        return False
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Ensure cleanup
        if _client:
            try:
                _client.session.close()
            except:
                pass
        
        stop_logger()

def main():
    """Main entry point."""
    # Register signal handler
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run test
    success = test_registration()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()