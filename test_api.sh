#!/bin/bash
#
# Camera Control API Test Script
# ===============================
# Tests all API endpoints for Session 8.5
#
# Usage:
#   chmod +x test_api.sh
#   ./test_api.sh [camera_ip]
#
# Example:
#   ./test_api.sh 192.168.1.21
#   ./test_api.sh localhost  (for local testing)

# Default to localhost if no IP provided
CAMERA_IP=${1:-localhost}
API_PORT=5000
BASE_URL="http://${CAMERA_IP}:${API_PORT}"

echo "======================================================================"
echo "Camera Control API Test"
echo "======================================================================"
echo "Target: ${BASE_URL}"
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counter
TESTS_PASSED=0
TESTS_FAILED=0

# Helper function to test endpoint
test_endpoint() {
    local test_name="$1"
    local method="$2"
    local endpoint="$3"
    local expected_status="$4"
    
    echo -n "Testing: ${test_name}... "
    
    if [ "$method" = "GET" ]; then
        response=$(curl -s -w "\n%{http_code}" "${BASE_URL}${endpoint}")
    else
        response=$(curl -s -w "\n%{http_code}" -X POST "${BASE_URL}${endpoint}")
    fi
    
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "$expected_status" ]; then
        echo -e "${GREEN}✓ PASS${NC} (HTTP $http_code)"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        echo ""
        ((TESTS_PASSED++))
    else
        echo -e "${RED}✗ FAIL${NC} (Expected $expected_status, got $http_code)"
        echo "$body"
        echo ""
        ((TESTS_FAILED++))
    fi
}

# Test 1: Ping endpoint
test_endpoint "Ping" "GET" "/api/ping" "200"

# Test 2: Health check (before streaming)
test_endpoint "Health Check (idle)" "GET" "/api/health" "200"

# Test 3: Start streaming
test_endpoint "Start Streaming" "POST" "/api/stream?action=start" "200"

# Wait for streaming to stabilize
echo -e "${YELLOW}Waiting 2 seconds for streaming to stabilize...${NC}"
sleep 2
echo ""

# Test 4: Health check (during streaming)
test_endpoint "Health Check (streaming)" "GET" "/api/health" "200"

# Test 5: Try to start again (should fail)
test_endpoint "Start Streaming Again (should fail)" "POST" "/api/stream?action=start" "400"

# Test 6: Check MJPEG stream is accessible
echo -n "Testing: MJPEG Stream Accessibility... "
STREAM_PORT=8080
stream_response=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://${CAMERA_IP}:${STREAM_PORT}/stream.mjpg")
if [ "$stream_response" = "200" ]; then
    echo -e "${GREEN}✓ PASS${NC} (Stream is accessible on port ${STREAM_PORT})"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ FAIL${NC} (Stream not accessible, HTTP $stream_response)"
    ((TESTS_FAILED++))
fi
echo ""

# Wait before stopping
echo -e "${YELLOW}Waiting 2 seconds before stopping...${NC}"
sleep 2
echo ""

# Test 7: Stop streaming
test_endpoint "Stop Streaming" "POST" "/api/stream?action=stop" "200"

# Wait for streaming to stop
echo -e "${YELLOW}Waiting 1 second for streaming to stop...${NC}"
sleep 1
echo ""

# Test 8: Health check (after stopping)
test_endpoint "Health Check (stopped)" "GET" "/api/health" "200"

# Test 9: Try to stop again (should fail)
test_endpoint "Stop Streaming Again (should fail)" "POST" "/api/stream?action=stop" "400"

# Test 10: Invalid action parameter
test_endpoint "Invalid Action" "POST" "/api/stream?action=invalid" "400"

# Summary
echo "======================================================================"
echo "Test Summary"
echo "======================================================================"
echo -e "Tests Passed: ${GREEN}${TESTS_PASSED}${NC}"
echo -e "Tests Failed: ${RED}${TESTS_FAILED}${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi