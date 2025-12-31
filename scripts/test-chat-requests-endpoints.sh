#!/bin/bash

#########################################
# Chat Requests Monitoring Endpoints Test
# Tests:
# 1. GET /api/monitoring/chat-requests/counts
# 2. GET /api/monitoring/chat-requests/models
# 3. GET /api/monitoring/chat-requests
#########################################

set -e

# Configuration
API_URL="${API_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-}"
VERBOSE="${VERBOSE:-1}"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Helper functions
print_header() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

print_test() {
    echo -e "${YELLOW}[TEST] $1${NC}"
    ((TESTS_RUN++))
}

print_pass() {
    echo -e "${GREEN}✓ PASS: $1${NC}"
    ((TESTS_PASSED++))
}

print_fail() {
    echo -e "${RED}✗ FAIL: $1${NC}"
    ((TESTS_FAILED++))
}

print_info() {
    echo -e "${BLUE}ℹ INFO: $1${NC}"
}

# Function to test endpoint
test_endpoint() {
    local name=$1
    local method=$2
    local endpoint=$3
    local expected_status=$4

    print_test "$name"

    # Build curl command
    local curl_cmd="curl -s -w '\n%{http_code}' -X $method '$API_URL$endpoint'"

    if [ -n "$API_KEY" ]; then
        curl_cmd="curl -s -w '\n%{http_code}' -X $method -H 'Authorization: Bearer $API_KEY' '$API_URL$endpoint'"
    fi

    # Execute request
    local response=$(eval $curl_cmd)
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | sed '$d')

    # Check status code
    if [ "$http_code" = "$expected_status" ]; then
        print_pass "HTTP $http_code (expected $expected_status)"
    else
        print_fail "HTTP $http_code (expected $expected_status)"
        if [ $VERBOSE -eq 1 ]; then
            echo "Response: $body"
        fi
        return 1
    fi

    # Parse JSON and validate
    if [ $VERBOSE -eq 1 ]; then
        echo "Response (first 500 chars):"
        echo "$body" | head -c 500
        echo -e "\n"
    fi

    # Validate JSON structure
    if echo "$body" | jq . > /dev/null 2>&1; then
        print_pass "Valid JSON response"
    else
        print_fail "Invalid JSON response"
        return 1
    fi

    # Check success field
    local success=$(echo "$body" | jq -r '.success' 2>/dev/null)
    if [ "$success" = "true" ]; then
        print_pass "Response success=true"
    else
        print_fail "Response success=$success (expected true)"
        return 1
    fi

    # Check required fields
    local has_data=$(echo "$body" | jq 'has("data")' 2>/dev/null)
    local has_metadata=$(echo "$body" | jq 'has("metadata")' 2>/dev/null)

    if [ "$has_data" = "true" ] && [ "$has_metadata" = "true" ]; then
        print_pass "Response has required fields (data, metadata)"
    else
        print_fail "Response missing required fields"
        return 1
    fi

    echo ""
    return 0
}

# Function to test with parameters
test_endpoint_with_params() {
    local name=$1
    local endpoint=$2
    local expected_status=${3:-200}

    print_test "$name"

    local curl_cmd="curl -s -w '\n%{http_code}' -X GET '$API_URL$endpoint'"

    if [ -n "$API_KEY" ]; then
        curl_cmd="curl -s -w '\n%{http_code}' -X GET -H 'Authorization: Bearer $API_KEY' '$API_URL$endpoint'"
    fi

    local response=$(eval $curl_cmd)
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | sed '$d')

    if [ "$http_code" = "$expected_status" ]; then
        print_pass "HTTP $http_code"
    else
        print_fail "HTTP $http_code (expected $expected_status)"
        return 1
    fi

    if echo "$body" | jq . > /dev/null 2>&1; then
        print_pass "Valid JSON"
    else
        print_fail "Invalid JSON"
        return 1
    fi

    if [ $VERBOSE -eq 1 ]; then
        echo "Response (first 300 chars):"
        echo "$body" | jq . | head -c 300
        echo -e "\n"
    fi

    echo ""
    return 0
}

# Main test execution
main() {
    print_header "Chat Requests Monitoring Endpoints Test Suite"

    print_info "API URL: $API_URL"
    print_info "API Key: ${API_KEY:-(not provided)}"
    echo ""

    # Check if API is accessible
    print_test "API Server Connectivity"
    if curl -s "$API_URL/health" > /dev/null 2>&1; then
        print_pass "API server is accessible"
    else
        print_fail "Cannot reach API server at $API_URL"
        echo "Make sure the server is running: python src/main.py"
        exit 1
    fi
    echo ""

    # ============================================
    # Test 1: GET /api/monitoring/chat-requests/counts
    # ============================================
    print_header "Test 1: /api/monitoring/chat-requests/counts"

    test_endpoint \
        "Basic request to counts endpoint" \
        "GET" \
        "/api/monitoring/chat-requests/counts" \
        "200"

    # Validate response data
    print_test "Response contains metadata fields"
    local response=$(curl -s "$API_URL/api/monitoring/chat-requests/counts")

    if echo "$response" | jq -e '.metadata.total_models' > /dev/null 2>&1; then
        print_pass "Has total_models field"
    else
        print_fail "Missing total_models in metadata"
    fi

    if echo "$response" | jq -e '.metadata.total_requests' > /dev/null 2>&1; then
        print_pass "Has total_requests field"
    else
        print_fail "Missing total_requests in metadata"
    fi

    if echo "$response" | jq -e '.metadata.timestamp' > /dev/null 2>&1; then
        print_pass "Has timestamp field"
    else
        print_fail "Missing timestamp in metadata"
    fi

    echo ""

    # ============================================
    # Test 2: GET /api/monitoring/chat-requests/models
    # ============================================
    print_header "Test 2: /api/monitoring/chat-requests/models"

    test_endpoint \
        "Basic request to models endpoint" \
        "GET" \
        "/api/monitoring/chat-requests/models" \
        "200"

    # Validate response data
    print_test "Response contains model data"
    response=$(curl -s "$API_URL/api/monitoring/chat-requests/models")

    local data_count=$(echo "$response" | jq '.data | length' 2>/dev/null)
    print_info "Found $data_count models in response"

    if [ "$data_count" -gt 0 ]; then
        print_pass "Response contains model data"

        print_test "First model has required fields"
        local first_model=$(echo "$response" | jq '.data[0]' 2>/dev/null)

        if echo "$first_model" | jq -e '.model_id' > /dev/null 2>&1; then
            print_pass "Has model_id field"
        else
            print_fail "Missing model_id"
        fi

        if echo "$first_model" | jq -e '.stats' > /dev/null 2>&1; then
            print_pass "Has stats field"

            if echo "$first_model" | jq -e '.stats.total_requests' > /dev/null 2>&1; then
                print_pass "Stats has total_requests"
            fi

            if echo "$first_model" | jq -e '.stats.total_tokens' > /dev/null 2>&1; then
                print_pass "Stats has total_tokens"
            fi
        else
            print_fail "Missing stats field"
        fi
    else
        print_fail "No model data in response (empty data array)"
    fi

    echo ""

    # ============================================
    # Test 3: GET /api/monitoring/chat-requests
    # ============================================
    print_header "Test 3: /api/monitoring/chat-requests"

    test_endpoint \
        "Basic request to chat-requests endpoint" \
        "GET" \
        "/api/monitoring/chat-requests" \
        "200"

    # Test with pagination parameters
    print_header "Test 3a: Pagination Parameters"

    test_endpoint_with_params \
        "Request with limit parameter" \
        "/api/monitoring/chat-requests?limit=10"

    test_endpoint_with_params \
        "Request with offset parameter" \
        "/api/monitoring/chat-requests?limit=10&offset=5"

    test_endpoint_with_params \
        "Request with max limit" \
        "/api/monitoring/chat-requests?limit=1000"

    echo ""

    # Test with filter parameters
    print_header "Test 3b: Filter Parameters"

    test_endpoint_with_params \
        "Filter by model name" \
        "/api/monitoring/chat-requests?model_name=gpt&limit=10"

    test_endpoint_with_params \
        "Request with model_name filter" \
        "/api/monitoring/chat-requests?model_name=claude&limit=10"

    echo ""

    # ============================================
    # Summary
    # ============================================
    print_header "Test Summary"

    echo "Total Tests Run:   $TESTS_RUN"
    echo "Tests Passed:      ${GREEN}$TESTS_PASSED${NC}"
    echo "Tests Failed:      ${RED}$TESTS_FAILED${NC}"

    if [ $TESTS_FAILED -eq 0 ]; then
        echo -e "\n${GREEN}All tests passed! ✓${NC}\n"
        return 0
    else
        echo -e "\n${RED}Some tests failed! ✗${NC}\n"
        return 1
    fi
}

# Run main
main "$@"
