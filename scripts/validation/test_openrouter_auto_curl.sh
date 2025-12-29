#!/bin/bash
# End-to-end test for openrouter/auto model using curl
# This tests the OpenRouter API directly

set -e

echo "================================================================================"
echo "TESTING OPENROUTER/AUTO MODEL - END-TO-END REQUEST"
echo "================================================================================"

# Check if OPENROUTER_API_KEY is set
if [ -z "$OPENROUTER_API_KEY" ]; then
    echo ""
    echo "❌ OPENROUTER_API_KEY environment variable is not set"
    echo ""
    echo "To run this test, you need to set your OpenRouter API key:"
    echo "  export OPENROUTER_API_KEY='your-key-here'"
    echo ""
    echo "You can get an API key from: https://openrouter.ai/keys"
    exit 1
fi

echo "✅ OpenRouter API key found"
echo ""

# Test 1: Simple request
echo "[TEST 1] Sending a test prompt with openrouter/auto model..."
echo ""

RESPONSE=$(curl -s https://openrouter.ai/api/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "HTTP-Referer: https://github.com/terragon-labs/gatewayz" \
  -H "X-Title: Gatewayz Validation Test" \
  -d '{
    "model": "openrouter/auto",
    "messages": [
      {
        "role": "user",
        "content": "Say \"Hello from OpenRouter Auto!\" and tell me which model you are in one sentence."
      }
    ],
    "max_tokens": 100,
    "temperature": 0.7
  }')

# Check if response contains an error
if echo "$RESPONSE" | grep -q '"error"'; then
    echo "❌ Request failed with error:"
    echo "$RESPONSE" | python3.12 -m json.tool
    exit 1
fi

# Parse and display response
echo "✅ Request successful!"
echo ""

# Extract key information
MODEL_USED=$(echo "$RESPONSE" | python3.12 -c "import sys, json; print(json.load(sys.stdin).get('model', 'unknown'))")
CONTENT=$(echo "$RESPONSE" | python3.12 -c "import sys, json; data=json.load(sys.stdin); print(data['choices'][0]['message']['content'])")
PROMPT_TOKENS=$(echo "$RESPONSE" | python3.12 -c "import sys, json; data=json.load(sys.stdin); print(data.get('usage', {}).get('prompt_tokens', 'N/A'))")
COMPLETION_TOKENS=$(echo "$RESPONSE" | python3.12 -c "import sys, json; data=json.load(sys.stdin); print(data.get('usage', {}).get('completion_tokens', 'N/A'))")
TOTAL_TOKENS=$(echo "$RESPONSE" | python3.12 -c "import sys, json; data=json.load(sys.stdin); print(data.get('usage', {}).get('total_tokens', 'N/A'))")

echo "📊 Response Details:"
echo "   Model routed to: $MODEL_USED"
echo "   Prompt tokens: $PROMPT_TOKENS"
echo "   Completion tokens: $COMPLETION_TOKENS"
echo "   Total tokens: $TOTAL_TOKENS"
echo ""
echo "💬 Response content:"
echo "   $CONTENT"
echo ""

# Test 2: Different prompt to verify routing
echo "[TEST 2] Testing with a math prompt..."
echo ""

RESPONSE2=$(curl -s https://openrouter.ai/api/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "HTTP-Referer: https://github.com/terragon-labs/gatewayz" \
  -H "X-Title: Gatewayz Validation Test" \
  -d '{
    "model": "openrouter/auto",
    "messages": [
      {
        "role": "user",
        "content": "What is 42 * 137? Just give me the number."
      }
    ],
    "max_tokens": 20,
    "temperature": 0.0
  }')

if echo "$RESPONSE2" | grep -q '"error"'; then
    echo "❌ Second request failed"
    echo "$RESPONSE2" | python3.12 -m json.tool
    exit 1
fi

MODEL_USED2=$(echo "$RESPONSE2" | python3.12 -c "import sys, json; print(json.load(sys.stdin).get('model', 'unknown'))")
CONTENT2=$(echo "$RESPONSE2" | python3.12 -c "import sys, json; data=json.load(sys.stdin); print(data['choices'][0]['message']['content'])")

echo "✅ Second request successful!"
echo "   Model routed to: $MODEL_USED2"
echo "   Response: $CONTENT2"
echo ""

echo "================================================================================"
echo "✅ END-TO-END TEST PASSED!"
echo "================================================================================"
echo ""
echo "Summary:"
echo "  • openrouter/auto model is working correctly"
echo "  • Successfully routes requests to appropriate models"
echo "  • Request 1 routed to: $MODEL_USED"
echo "  • Request 2 routed to: $MODEL_USED2"
echo ""
echo "This demonstrates OpenRouter's intelligent auto-routing capability!"
echo ""
