"""Tests for the coding-agent benchmark harness.

The harness produces a public, adversarially-read report, so the properties
under test are about honesty: failures must not be averaged into results, and
silently-broken caching must raise a warning rather than disappear.
"""

from scripts.benchmarks import coding_benchmark as bench


def _sample(**kw):
    defaults = dict(task_id="t", model="m", route="gateway", ok=True)
    defaults.update(kw)
    return bench.Sample(**defaults)


class TestPercentile:
    def test_empty_returns_none(self):
        assert bench._percentile([], 50) is None

    def test_single_value(self):
        assert bench._percentile([12.0], 95) == 12.0

    def test_p50_of_sorted_values(self):
        assert bench._percentile([10.0, 20.0, 30.0], 50) == 20.0

    def test_p95_picks_near_the_top(self):
        values = [float(i) for i in range(1, 101)]
        assert bench._percentile(values, 95) == 95.0

    def test_unsorted_input_is_sorted_first(self):
        assert bench._percentile([30.0, 10.0, 20.0], 50) == 20.0


class TestSummarize:
    def test_errors_are_counted_not_averaged(self):
        samples = [
            _sample(ttft_ms=100.0, total_ms=200.0),
            _sample(ok=False, error="boom"),
        ]
        summary = bench.summarize(samples)["m::gateway"]
        assert summary["runs"] == 2
        assert summary["errors"] == 1
        # The failed run must not drag the latency figures anywhere.
        assert summary["ttft_ms_p50"] == 100.0

    def test_groups_by_model_and_route(self):
        samples = [
            _sample(route="gateway", ttft_ms=10.0, total_ms=20.0),
            _sample(route="direct", ttft_ms=30.0, total_ms=40.0),
        ]
        summary = bench.summarize(samples)
        assert "m::gateway" in summary
        assert "m::direct" in summary

    def test_cache_tokens_are_totalled(self):
        samples = [
            _sample(cache_read_tokens=100, cache_write_tokens=10),
            _sample(cache_read_tokens=200, cache_write_tokens=0),
        ]
        summary = bench.summarize(samples)["m::gateway"]
        assert summary["cache_read_tokens_total"] == 300
        assert summary["cache_write_tokens_total"] == 10

    def test_all_errors_yields_none_latencies_not_zero(self):
        """Zero would read as 'instant'; None reads as 'no data'."""
        summary = bench.summarize([_sample(ok=False)])["m::gateway"]
        assert summary["ttft_ms_p50"] is None
        assert summary["errors"] == 1

    def test_empty_sample_list(self):
        assert bench.summarize([]) == {}


class TestCacheWarnings:
    def test_warns_when_no_cache_reads_observed(self):
        report = bench.BenchmarkReport(generated_at=0, gateway_url="x")
        report.samples = [_sample(cache_read_tokens=0), _sample(cache_read_tokens=0)]
        bench._add_cache_warnings(report)
        assert report.warnings
        assert "cost-advantage" in report.warnings[0]

    def test_no_warning_when_cache_reads_present(self):
        report = bench.BenchmarkReport(generated_at=0, gateway_url="x")
        report.samples = [_sample(cache_read_tokens=0), _sample(cache_read_tokens=900)]
        bench._add_cache_warnings(report)
        assert report.warnings == []

    def test_single_run_does_not_warn(self):
        """One run cannot demonstrate a cache hit; warning would be noise."""
        report = bench.BenchmarkReport(generated_at=0, gateway_url="x")
        report.samples = [_sample(cache_read_tokens=0)]
        bench._add_cache_warnings(report)
        assert report.warnings == []

    def test_direct_route_ignored_for_cache_warning(self):
        report = bench.BenchmarkReport(generated_at=0, gateway_url="x")
        report.samples = [
            _sample(route="direct", cache_read_tokens=0),
            _sample(route="direct", cache_read_tokens=0),
        ]
        bench._add_cache_warnings(report)
        assert report.warnings == []


class TestMessageBuilding:
    def test_cache_marker_applied_when_requested(self):
        messages = bench._build_messages(bench.TASKS[0], use_cache_marker=True)
        assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_no_cache_marker_when_not_requested(self):
        messages = bench._build_messages(bench.TASKS[0], use_cache_marker=False)
        assert "cache_control" not in messages[0]["content"][0]

    def test_prefix_is_large_enough_to_be_cacheable(self):
        """Anthropic will not cache a prefix below its minimum token threshold."""
        messages = bench._build_messages(bench.TASKS[0], use_cache_marker=True)
        # ~4 chars/token; the prefix must comfortably clear the 1024-token floor.
        assert len(messages[0]["content"][0]["text"]) > 4096

    def test_instruction_is_the_user_turn(self):
        task = bench.TASKS[0]
        messages = bench._build_messages(task, use_cache_marker=True)
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == task["instruction"]


class TestChunkContentDetection:
    def test_detects_text_content(self):
        assert bench._chunk_has_content({"choices": [{"delta": {"content": "x"}}]}) is True

    def test_detects_tool_calls(self):
        chunk = {"choices": [{"delta": {"tool_calls": [{"index": 0}]}}]}
        assert bench._chunk_has_content(chunk) is True

    def test_role_only_chunk_is_not_content(self):
        """The opening role chunk must not be counted as first token."""
        assert bench._chunk_has_content({"choices": [{"delta": {"role": "assistant"}}]}) is False

    def test_empty_chunk(self):
        assert bench._chunk_has_content({}) is False


class TestReportShape:
    def test_report_dict_has_expected_keys(self):
        report = bench.BenchmarkReport(generated_at=123, gateway_url="https://x")
        report.samples = [_sample(ttft_ms=1.0, total_ms=2.0)]
        payload = report.to_dict()
        assert set(payload) == {"generated_at", "gateway_url", "summary", "warnings", "samples"}
        assert payload["generated_at"] == 123

    def test_samples_are_serializable(self):
        import json

        report = bench.BenchmarkReport(generated_at=1, gateway_url="x")
        report.samples = [_sample(ttft_ms=1.0)]
        json.dumps(report.to_dict())


class TestTaskDefinitions:
    def test_every_task_has_required_fields(self):
        for task in bench.TASKS:
            assert task["id"]
            assert task["instruction"]
            assert task["max_tokens"] > 0

    def test_task_ids_are_unique(self):
        ids = [t["id"] for t in bench.TASKS]
        assert len(ids) == len(set(ids))

    def test_at_least_one_task_exercises_tool_calling(self):
        assert any(t.get("tools") for t in bench.TASKS)


class TestToSample:
    def test_tokens_per_sec_computed(self):
        s = bench._to_sample(
            "t", "m", "gateway", 100.0, 1000.0, {"completion_tokens": 50, "prompt_tokens": 10}
        )
        assert s.tokens_per_sec == 50.0

    def test_zero_completion_tokens_yields_none_tps(self):
        s = bench._to_sample("t", "m", "gateway", 100.0, 1000.0, {"completion_tokens": 0})
        assert s.tokens_per_sec is None

    def test_cache_fields_carried_through(self):
        s = bench._to_sample(
            "t",
            "m",
            "gateway",
            10.0,
            100.0,
            {"cache_read_input_tokens": 900, "cache_creation_input_tokens": 50},
        )
        assert s.cache_read_tokens == 900
        assert s.cache_write_tokens == 50
