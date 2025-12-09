import unittest
import sys
import os

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
                    "finish_reason": None
                }
            ]
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertEqual(normalized.id, "test-id")
        self.assertEqual(normalized.choices[0]["delta"]["content"], "Hello")
        self.assertEqual(normalizer.get_accumulated_content(), "Hello")

    def test_gemini_format(self):
        normalizer = StreamNormalizer("google", "gemini-pro")
        chunk = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Gemini content"}]},
                    "finishReason": "STOP"
                }
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
            "delta": {"type": "text_delta", "text": "Claude content"}
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertEqual(normalized.choices[0]["delta"]["content"], "Claude content")
        self.assertEqual(normalizer.get_accumulated_content(), "Claude content")

    def test_reasoning_extraction(self):
        normalizer = StreamNormalizer("deepseek", "deepseek-r1")
        chunk = {
            "choices": [
                {
                    "delta": {"content": "Answer", "reasoning": "Thinking process"}
                }
            ]
        }
        normalized = normalizer.normalize_chunk(chunk)
        self.assertEqual(normalized.choices[0]["delta"]["content"], "Answer")
        self.assertEqual(normalized.choices[0]["delta"]["reasoning_content"], "Thinking process")
        self.assertEqual(normalizer.get_accumulated_reasoning(), "Thinking process")

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
            choices=[Choice(Delta("Object Content"), None, 0)]
        )
        normalized = normalizer.normalize_chunk(chunk)
        self.assertEqual(normalized.choices[0]["delta"]["content"], "Object Content")

if __name__ == '__main__':
    unittest.main()