#!/usr/bin/env python3
"""
Integration tests for the web search tool implementation.

Tests:
1. Tool definitions are correctly structured
2. Tool executor handles web_search tool calls
3. Tavily client response formatting
4. SSE event generation for tool calls and results
"""

import sys
import os
import asyncio
from typing import Any

# Add the backend directory to the path
sys.path.insert(0, '/root/repo/backend')

# Set default config values for testing
os.environ.setdefault("TAVILY_API_KEY", "test-key")
os.environ.setdefault("WEB_SEARCH_ENABLED", "true")


def test_tool_definitions_import():
    """Test that tool definitions module imports correctly"""
    print("Test 1: Tool Definitions Import")
    print("-" * 60)

    try:
        from src.services.tool_definitions import (
            WEB_SEARCH_TOOL,
            get_enabled_tools,
            is_server_side_tool,
            get_tool_by_name,
        )
        print("✓ tool_definitions module imported successfully")

        # Verify WEB_SEARCH_TOOL structure
        assert WEB_SEARCH_TOOL["type"] == "function", "Tool type should be 'function'"
        assert "function" in WEB_SEARCH_TOOL, "Tool should have 'function' key"
        assert WEB_SEARCH_TOOL["function"]["name"] == "web_search", "Tool name should be 'web_search'"
        assert "parameters" in WEB_SEARCH_TOOL["function"], "Tool should have parameters"
        print("✓ WEB_SEARCH_TOOL has correct structure")

        # Verify get_enabled_tools
        tools = get_enabled_tools(enable_web_search=True)
        assert len(tools) == 1, "Should have 1 tool when web search is enabled"
        assert tools[0]["function"]["name"] == "web_search"
        print("✓ get_enabled_tools returns web_search when enabled")

        tools_disabled = get_enabled_tools(enable_web_search=False)
        assert len(tools_disabled) == 0, "Should have 0 tools when web search is disabled"
        print("✓ get_enabled_tools returns empty list when disabled")

        # Verify is_server_side_tool
        assert is_server_side_tool("web_search") is True
        assert is_server_side_tool("unknown_tool") is False
        print("✓ is_server_side_tool works correctly")

        # Verify get_tool_by_name
        tool = get_tool_by_name("web_search")
        assert tool is not None
        assert tool["function"]["name"] == "web_search"
        print("✓ get_tool_by_name works correctly")

        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_tool_executor_import():
    """Test that tool executor module imports correctly"""
    print("\nTest 2: Tool Executor Import")
    print("-" * 60)

    try:
        from src.services.tool_executor import (
            ToolExecutionResult,
            execute_tool_call,
            execute_tool_calls,
            build_tool_messages,
            create_tool_call_sse_event,
        )
        print("✓ tool_executor module imported successfully")

        # Test ToolExecutionResult
        result = ToolExecutionResult(
            tool_call_id="test-123",
            name="web_search",
            success=True,
            result={"query": "test", "results": []}
        )
        assert result.tool_call_id == "test-123"
        assert result.success is True
        print("✓ ToolExecutionResult dataclass works correctly")

        # Test to_tool_message
        tool_message = result.to_tool_message()
        assert tool_message["role"] == "tool"
        assert tool_message["tool_call_id"] == "test-123"
        assert tool_message["name"] == "web_search"
        print("✓ to_tool_message creates correct structure")

        # Test to_sse_event
        sse_event = result.to_sse_event()
        assert sse_event["type"] == "tool_result"
        assert sse_event["tool_call_id"] == "test-123"
        assert sse_event["success"] is True
        print("✓ to_sse_event creates correct structure")

        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_tavily_client_import():
    """Test that Tavily client module imports correctly"""
    print("\nTest 3: Tavily Client Import")
    print("-" * 60)

    try:
        from src.services.tavily_client import (
            search_web,
            format_search_results_for_llm,
            is_web_search_available,
        )
        print("✓ tavily_client module imported successfully")

        # Test is_web_search_available (should be True with test key set)
        available = is_web_search_available()
        print(f"  is_web_search_available: {available}")
        print("✓ is_web_search_available function works")

        # Test format_search_results_for_llm
        test_response = {
            "query": "test query",
            "results": [
                {
                    "title": "Test Result",
                    "url": "https://example.com",
                    "content": "Test content",
                    "score": 0.95
                }
            ],
            "answer": "This is an answer"
        }
        formatted = format_search_results_for_llm(test_response)
        assert "test query" in formatted
        assert "Test Result" in formatted
        assert "https://example.com" in formatted
        print("✓ format_search_results_for_llm works correctly")

        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_stream_normalizer_sse_functions():
    """Test SSE helper functions in stream normalizer"""
    print("\nTest 4: Stream Normalizer SSE Functions")
    print("-" * 60)

    try:
        from src.services.stream_normalizer import (
            create_tool_call_sse,
            create_tool_result_sse,
        )
        print("✓ stream_normalizer SSE functions imported successfully")

        # Test create_tool_call_sse
        tool_call_sse = create_tool_call_sse(
            tool_call_id="call-123",
            name="web_search",
            arguments={"query": "test"}
        )
        assert "data:" in tool_call_sse
        assert "tool_call" in tool_call_sse
        assert "call-123" in tool_call_sse
        print("✓ create_tool_call_sse creates valid SSE")

        # Test create_tool_result_sse
        tool_result_sse = create_tool_result_sse(
            tool_call_id="call-123",
            name="web_search",
            result={"query": "test", "results": []}
        )
        assert "data:" in tool_result_sse
        assert "tool_result" in tool_result_sse
        assert "call-123" in tool_result_sse
        print("✓ create_tool_result_sse creates valid SSE")

        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_proxy_schema_has_enable_web_search():
    """Test that ProxyRequest schema has enable_web_search field"""
    print("\nTest 5: ProxyRequest Schema")
    print("-" * 60)

    try:
        from src.schemas.proxy import ProxyRequest, ResponseRequest

        # Test ProxyRequest
        req = ProxyRequest(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
            enable_web_search=True
        )
        assert hasattr(req, "enable_web_search")
        assert req.enable_web_search is True
        print("✓ ProxyRequest has enable_web_search field")

        # Test default value
        req_default = ProxyRequest(
            model="test-model",
            messages=[{"role": "user", "content": "test"}]
        )
        assert req_default.enable_web_search is False or req_default.enable_web_search is None
        print("✓ ProxyRequest enable_web_search defaults correctly")

        # Test ResponseRequest
        resp_req = ResponseRequest(
            model="test-model",
            input="test input",
            enable_web_search=True
        )
        assert hasattr(resp_req, "enable_web_search")
        print("✓ ResponseRequest has enable_web_search field")

        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_tool_execution_async():
    """Test async tool execution (mocked)"""
    print("\nTest 6: Async Tool Execution (Unit Test)")
    print("-" * 60)

    try:
        from src.services.tool_executor import ToolExecutionResult

        # Create a mock tool execution result
        result = ToolExecutionResult(
            tool_call_id="call-456",
            name="web_search",
            success=True,
            result={
                "query": "Python programming",
                "results": [
                    {
                        "title": "Python.org",
                        "url": "https://python.org",
                        "content": "Python programming language",
                        "score": 0.99
                    }
                ],
                "answer": "Python is a programming language"
            }
        )

        # Verify tool message format
        msg = result.to_tool_message()
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call-456"
        assert msg["name"] == "web_search"
        assert "Python programming" in msg["content"]
        print("✓ Tool message format is correct")

        # Verify SSE event format
        sse = result.to_sse_event()
        assert sse["type"] == "tool_result"
        assert sse["success"] is True
        assert sse["result"]["query"] == "Python programming"
        print("✓ SSE event format is correct")

        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("=" * 60)
    print("Web Search Tool Integration Tests")
    print("=" * 60)
    print()

    results = []

    results.append(("Tool Definitions Import", test_tool_definitions_import()))
    results.append(("Tool Executor Import", test_tool_executor_import()))
    results.append(("Tavily Client Import", test_tavily_client_import()))
    results.append(("Stream Normalizer SSE Functions", test_stream_normalizer_sse_functions()))
    results.append(("ProxyRequest Schema", test_proxy_schema_has_enable_web_search()))
    results.append(("Async Tool Execution", test_tool_execution_async()))

    print()
    print("=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    failed = len(results) - passed

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")

    print()
    print(f"Total: {passed} passed, {failed} failed")
    print()

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
