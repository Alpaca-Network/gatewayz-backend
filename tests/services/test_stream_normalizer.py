import os
import sys
import unittest

# Add src to path
# Assuming backend is the root for running tests, we need to add src to path so 'from src...' works
# But wait, if I run from backend, 'src' is a folder there.
# If I run `python tests/services/test_stream_normalizer.py` from backend folder,
# current directory is `backend`, so `import src...` should work if `backend` is in path?
# No, usually `.` is in path. So `import src.services...` works if `.` is `backend`.
sys.path.append(os.getcwd())

from src.services.stream_normalizer import StreamNormalizer


class MockChunk:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestStreamNormalizer(unittest.TestCase):
    def test_initialization(self):
        normalizer = StreamNormalizer(provider="openai", model="gpt-4")
        self.assertEqual(normalizer.provider, "openai")
        self.assertEqual(normalizer.model, "gpt-4")

    def test_openai_format(self):
        normalizer = StreamNormalizer("openai", "gpt-4")
        chunk = {
            "id": "test-id",
            "created": 123,
            "model": "gpt-4",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "Hello"},
                    "finish_reason": None,
                }
            ],
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertEqual(normalized.id, "test-id")
        self.assertEqual(normalized.choices[0]["delta"]["content"], "Hello")
        self.assertEqual(normalizer.get_accumulated_content(), "Hello")

    def test_gemini_format(self):
        normalizer = StreamNormalizer("google", "gemini-pro")
        chunk = {
            "candidates": [
                {"content": {"parts": [{"text": "Gemini content"}]}, "finishReason": "STOP"}
            ]
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertEqual(normalized.choices[0]["delta"]["content"], "Gemini content")
        self.assertEqual(normalized.choices[0]["finish_reason"], "stop")
        self.assertEqual(normalizer.get_accumulated_content(), "Gemini content")

    def test_anthropic_format(self):
        normalizer = StreamNormalizer("anthropic", "claude-3")
        chunk = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Claude content"},
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertEqual(normalized.choices[0]["delta"]["content"], "Claude content")
        self.assertEqual(normalizer.get_accumulated_content(), "Claude content")

    def test_reasoning_extraction(self):
        normalizer = StreamNormalizer("deepseek", "deepseek-r1")
        chunk = {"choices": [{"delta": {"content": "Answer", "reasoning": "Thinking process"}}]}
        normalized = normalizer.normalize_chunk(chunk)
        self.assertEqual(normalized.choices[0]["delta"]["content"], "Answer")
        self.assertEqual(normalized.choices[0]["delta"]["reasoning_content"], "Thinking process")
        self.assertEqual(normalizer.get_accumulated_reasoning(), "Thinking process")

    def test_reasoning_details_array_extraction(self):
        """Test Grok 4.1 reasoning_details array format"""
        normalizer = StreamNormalizer("xai", "grok-4.1-fast")
        chunk = {
            "choices": [
                {
                    "delta": {
                        "content": "The answer is 42.",
                        "reasoning_details": [
                            {"content": "First, let me analyze the question."},
                            {"content": "Now I'll compute the result."},
                        ],
                    }
                }
            ]
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertEqual(normalized.choices[0]["delta"]["content"], "The answer is 42.")
        reasoning = normalized.choices[0]["delta"]["reasoning_content"]
        self.assertIn("First, let me analyze the question.", reasoning)
        self.assertIn("Now I'll compute the result.", reasoning)

    def test_reasoning_details_string_array_extraction(self):
        """Test reasoning_details as array of strings"""
        normalizer = StreamNormalizer("xai", "grok-4.1-fast")
        chunk = {
            "choices": [
                {
                    "delta": {
                        "content": "Result",
                        "reasoning_details": ["Step 1", "Step 2", "Step 3"],
                    }
                }
            ]
        }
        normalized = normalizer.normalize_chunk(chunk)
        reasoning = normalized.choices[0]["delta"]["reasoning_content"]
        self.assertIn("Step 1", reasoning)
        self.assertIn("Step 2", reasoning)
        self.assertIn("Step 3", reasoning)

    def test_reasoning_details_text_field(self):
        """Test reasoning_details with 'text' field instead of 'content'"""
        normalizer = StreamNormalizer("xai", "grok-4.1-fast")
        chunk = {
            "choices": [
                {
                    "delta": {
                        "content": "Answer",
                        "reasoning_details": [
                            {"text": "Thinking step 1"},
                            {"text": "Thinking step 2"},
                        ],
                    }
                }
            ]
        }
        normalized = normalizer.normalize_chunk(chunk)
        reasoning = normalized.choices[0]["delta"]["reasoning_content"]
        self.assertIn("Thinking step 1", reasoning)
        self.assertIn("Thinking step 2", reasoning)

    def test_anthropic_thinking_delta(self):
        """Test Anthropic extended thinking delta events (Claude Sonnet 4, etc.)"""
        normalizer = StreamNormalizer("anthropic", "claude-sonnet-4")
        chunk = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Let me analyze this step by step..."},
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertIsNotNone(normalized)
        self.assertEqual(
            normalized.choices[0]["delta"]["reasoning_content"],
            "Let me analyze this step by step...",
        )
        self.assertEqual(
            normalizer.get_accumulated_reasoning(), "Let me analyze this step by step..."
        )

    def test_anthropic_text_delta(self):
        """Test Anthropic text_delta events with type field"""
        normalizer = StreamNormalizer("anthropic", "claude-sonnet-4")
        chunk = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Here is my response."},
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized.choices[0]["delta"]["content"], "Here is my response.")
        self.assertEqual(normalizer.get_accumulated_content(), "Here is my response.")

    def test_anthropic_signature_delta(self):
        """Test Anthropic signature delta events for extended thinking verification"""
        normalizer = StreamNormalizer("anthropic", "claude-sonnet-4")
        chunk = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "abc123sig"},
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized.choices[0]["delta"]["signature"], "abc123sig")

    def test_anthropic_content_block_start_thinking(self):
        """Test Anthropic content_block_start event with thinking type"""
        normalizer = StreamNormalizer("anthropic", "claude-sonnet-4")
        chunk = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "Initial thought..."},
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized.choices[0]["delta"]["reasoning_content"], "Initial thought...")

    def test_anthropic_content_block_start_text(self):
        """Test Anthropic content_block_start event with text type"""
        normalizer = StreamNormalizer("anthropic", "claude-sonnet-4")
        chunk = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }
        normalized = normalizer.normalize_chunk(chunk)
        # Empty text should return None
        self.assertIsNone(normalized)

    def test_anthropic_message_delta_stop_reason(self):
        """Test Anthropic message_delta event with stop_reason"""
        normalizer = StreamNormalizer("anthropic", "claude-sonnet-4")
        chunk = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 100},
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized.choices[0]["finish_reason"], "stop")

    def test_anthropic_message_stop(self):
        """Test Anthropic message_stop event"""
        normalizer = StreamNormalizer("anthropic", "claude-sonnet-4")
        chunk = {"type": "message_stop"}
        normalized = normalizer.normalize_chunk(chunk)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized.choices[0]["finish_reason"], "stop")

    def test_anthropic_tool_input_delta(self):
        """Test Anthropic input_json_delta for tool use streaming"""
        normalizer = StreamNormalizer("anthropic", "claude-sonnet-4")
        chunk = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"query": "test'},
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized.choices[0]["delta"]["tool_input_delta"], '{"query": "test')

    def test_anthropic_ping_ignored(self):
        """Test that Anthropic ping events are ignored"""
        normalizer = StreamNormalizer("anthropic", "claude-sonnet-4")
        chunk = {"type": "ping"}
        normalized = normalizer.normalize_chunk(chunk)
        self.assertIsNone(normalized)

    def test_anthropic_message_start_ignored(self):
        """Test that Anthropic message_start events are ignored"""
        normalizer = StreamNormalizer("anthropic", "claude-sonnet-4")
        chunk = {
            "type": "message_start",
            "message": {
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4",
            },
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertIsNone(normalized)

    def test_anthropic_content_block_stop_ignored(self):
        """Test that Anthropic content_block_stop events are ignored"""
        normalizer = StreamNormalizer("anthropic", "claude-sonnet-4")
        chunk = {"type": "content_block_stop", "index": 0}
        normalized = normalizer.normalize_chunk(chunk)
        self.assertIsNone(normalized)

    def test_anthropic_full_extended_thinking_flow(self):
        """Test a complete extended thinking flow with multiple events"""
        normalizer = StreamNormalizer("anthropic", "claude-sonnet-4")

        # 1. Thinking block start
        chunk1 = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        }
        normalized1 = normalizer.normalize_chunk(chunk1)
        # Empty thinking block start returns None
        self.assertIsNone(normalized1)

        # 2. Thinking delta
        chunk2 = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Step 1: "},
        }
        normalized2 = normalizer.normalize_chunk(chunk2)
        self.assertIsNotNone(normalized2)

        # 3. More thinking
        chunk3 = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Step 2"},
        }
        normalizer.normalize_chunk(chunk3)

        # 4. Text delta
        chunk4 = {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "Here is the answer."},
        }
        normalizer.normalize_chunk(chunk4)

        # 5. Message stop
        chunk5 = {"type": "message_stop"}
        normalized5 = normalizer.normalize_chunk(chunk5)

        # Verify accumulated content
        self.assertEqual(normalizer.get_accumulated_reasoning(), "Step 1: Step 2")
        self.assertEqual(normalizer.get_accumulated_content(), "Here is the answer.")
        self.assertEqual(normalized5.choices[0]["finish_reason"], "stop")

    def test_finish_reason_normalization(self):
        normalizer = StreamNormalizer("test", "test")
        self.assertEqual(normalizer._normalize_finish_reason("stop_sequence"), "stop")
        self.assertEqual(normalizer._normalize_finish_reason("max_tokens"), "length")
        self.assertEqual(normalizer._normalize_finish_reason("safety"), "error")

    def test_object_chunk(self):
        normalizer = StreamNormalizer("openai", "gpt-4")

        class Choice:
            def __init__(self, delta, finish_reason, index):
                self.delta = delta
                self.finish_reason = finish_reason
                self.index = index

        class Delta:
            def __init__(self, content):
                self.content = content

        chunk = MockChunk(
            id="test-obj",
            created=123,
            model="gpt-4",
            choices=[Choice(Delta("Object Content"), None, 0)],
        )
        normalized = normalizer.normalize_chunk(chunk)
        self.assertEqual(normalized.choices[0]["delta"]["content"], "Object Content")


if __name__ == "__main__":
    unittest.main()
