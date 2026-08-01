"""Tests for the GTM agent stack.

The properties that matter are behavioural guardrails, not output quality:
nothing publishes, the analyst never gets asked to interpret absent data, the
support agent escalates rather than guessing, and truncated lists announce
themselves.
"""

import json
from unittest.mock import Mock, patch

import pytest

from agents.amplifier import PLATFORMS, Amplifier, SourcePiece
from agents.base import Agent, AgentRun, UsageLedger, UsageRecord
from agents.concierge import Concierge, SupportQuestion
from agents.dogfood_report import render_markdown
from agents.prospector import Prospector, Signal
from agents.publisher import ContentBrief, Publisher
from agents.scorekeeper import Scorekeeper


def _fake_client(response_text="drafted content"):
    client = Mock()
    client.complete.return_value = (
        response_text,
        UsageRecord(
            agent="",
            model="anthropic/claude-sonnet-4",
            prompt_tokens=100,
            completion_tokens=50,
            cache_read_tokens=80,
            cost_usd=0.001,
            timestamp=1,
        ),
    )
    return client


def _ledger(tmp_path):
    return UsageLedger(path=tmp_path / "ledger.jsonl")


class TestUsageLedger:
    def test_records_and_summarizes(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.record(UsageRecord("publisher", "m", 100, 50, 40, 0.01, 1000))
        ledger.record(UsageRecord("publisher", "m", 200, 60, 0, 0.02, 1001))
        summary = ledger.summarize()
        assert summary["records"] == 2
        assert summary["total_cost_usd"] == pytest.approx(0.03)
        assert summary["by_agent"]["publisher"]["calls"] == 2

    def test_missing_ledger_reports_no_runs(self, tmp_path):
        summary = UsageLedger(path=tmp_path / "absent.jsonl").summarize()
        assert summary["records"] == 0
        assert "no runs" in summary["note"]

    def test_since_filter(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.record(UsageRecord("a", "m", 10, 10, 0, 0.01, 1000))
        ledger.record(UsageRecord("a", "m", 10, 10, 0, 0.01, 5000))
        assert ledger.summarize(since=2000)["records"] == 1

    def test_corrupt_line_is_skipped_not_fatal(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.record(UsageRecord("a", "m", 10, 10, 0, 0.01, 1000))
        with ledger.path.open("a") as fh:
            fh.write("not json\n")
        assert ledger.summarize()["records"] == 1


class TestScorekeeper:
    def test_skips_interpretation_when_there_are_no_payers(self, tmp_path):
        """Asking a model to interpret all-zeros produces confident nonsense."""
        agent = Scorekeeper(
            client=_fake_client(),
            ledger=_ledger(tmp_path),
            scorecard={"total_paying_accounts": 0, "credit_revenue_usd": 0},
        )
        run = agent.run()
        agent.client.complete.assert_not_called()
        assert any("Stripe webhook" in n or "plumbing" in n for n in run.notes)

    def test_interprets_when_there_is_data(self, tmp_path):
        agent = Scorekeeper(
            client=_fake_client("Payers up 20%."),
            ledger=_ledger(tmp_path),
            scorecard={"total_paying_accounts": 12, "credit_revenue_usd": 400.0},
        )
        run = agent.run()
        kinds = [o["kind"] for o in run.outputs]
        assert "scorecard" in kinds
        assert "commentary" in kinds

    def test_scorecard_is_emitted_verbatim(self, tmp_path):
        scorecard = {"total_paying_accounts": 5, "credit_revenue_usd": 99.5}
        agent = Scorekeeper(
            client=_fake_client(), ledger=_ledger(tmp_path), scorecard=scorecard
        )
        run = agent.run()
        emitted = next(o for o in run.outputs if o["kind"] == "scorecard")
        assert emitted["data"] == scorecard

    def test_interpretation_failure_is_recorded_not_swallowed(self, tmp_path):
        client = _fake_client()
        client.complete.side_effect = RuntimeError("gateway down")
        agent = Scorekeeper(
            client=client,
            ledger=_ledger(tmp_path),
            scorecard={"total_paying_accounts": 3},
        )
        run = agent.run()
        assert run.errors


class TestPublisher:
    def test_produces_drafts_not_published_content(self, tmp_path):
        agent = Publisher(
            client=_fake_client("# A draft"),
            ledger=_ledger(tmp_path),
            briefs=[ContentBrief("s", "editorial", "T", "q", "angle")],
        )
        run = agent.run()
        assert run.outputs[0]["status"] == "draft-awaiting-review"

    def test_flags_needs_data_placeholders(self, tmp_path):
        agent = Publisher(
            client=_fake_client("Cost is [NEEDS DATA: measure TTFT]"),
            ledger=_ledger(tmp_path),
            briefs=[ContentBrief("s", "editorial", "T", "q", "angle")],
        )
        run = agent.run()
        assert any("NEEDS DATA" in n for n in run.notes)

    def test_empty_output_is_called_out(self, tmp_path):
        client = _fake_client()
        client.complete.side_effect = RuntimeError("boom")
        agent = Publisher(
            client=client,
            ledger=_ledger(tmp_path),
            briefs=[ContentBrief("s", "editorial", "T", "q", "angle")],
        )
        run = agent.run()
        assert run.errors
        assert any("quiet week" in n for n in run.notes)


class TestAmplifier:
    def test_drafts_every_platform_separately(self, tmp_path):
        piece = SourcePiece("T", "https://x", "summary", ["point"])
        agent = Amplifier(client=_fake_client(), ledger=_ledger(tmp_path), piece=piece)
        run = agent.run()
        platforms = {o["platform"] for o in run.outputs}
        assert platforms == set(PLATFORMS)

    def test_every_output_is_marked_manual(self, tmp_path):
        """Auto-posting to Reddit or HN gets accounts banned."""
        piece = SourcePiece("T", "https://x", "s", [])
        agent = Amplifier(client=_fake_client(), ledger=_ledger(tmp_path), piece=piece)
        run = agent.run()
        assert all(o["status"] == "draft-post-manually" for o in run.outputs)

    def test_no_piece_is_an_error_not_invention(self, tmp_path):
        agent = Amplifier(client=_fake_client(), ledger=_ledger(tmp_path), piece=None)
        run = agent.run()
        assert run.errors
        assert run.outputs == []


class TestProspector:
    def _signal(self, url="https://reddit.com/1"):
        return Signal("reddit", url, "user", "claude code is too expensive", ["cost"])

    def test_no_fetchers_is_an_error(self, tmp_path):
        agent = Prospector(client=_fake_client(), ledger=_ledger(tmp_path))
        run = agent.run()
        assert run.errors

    def test_deduplicates_by_url(self, tmp_path):
        sig = self._signal()
        agent = Prospector(
            client=_fake_client(json.dumps({"should_reply": True, "score": 90, "draft": "d"})),
            ledger=_ledger(tmp_path),
            fetchers=[lambda kw: [sig, sig]],
        )
        run = agent.run()
        assert len(run.outputs) == 1

    def test_ranks_by_score(self, tmp_path):
        responses = [
            json.dumps({"should_reply": True, "score": 10, "draft": "low"}),
            json.dumps({"should_reply": True, "score": 90, "draft": "high"}),
        ]
        client = _fake_client()
        client.complete.side_effect = [
            (r, UsageRecord("", "m", 1, 1, 0, 0.0, 1)) for r in responses
        ]
        agent = Prospector(
            client=client,
            ledger=_ledger(tmp_path),
            fetchers=[
                lambda kw: [self._signal("https://a"), self._signal("https://b")]
            ],
        )
        run = agent.run()
        assert run.outputs[0]["score"] == 90

    def test_truncation_is_announced(self, tmp_path):
        """A silently truncated list reads as 'that was everything'."""
        client = _fake_client(json.dumps({"should_reply": True, "score": 50, "draft": "d"}))
        agent = Prospector(
            client=client,
            ledger=_ledger(tmp_path),
            fetchers=[lambda kw: [self._signal(f"https://{i}") for i in range(5)]],
            max_replies=2,
        )
        run = agent.run()
        assert len(run.outputs) == 2
        assert any("not shown" in n for n in run.notes)

    def test_declining_to_reply_is_not_an_error(self, tmp_path):
        client = _fake_client(json.dumps({"should_reply": False, "score": 5, "reason": "noise"}))
        agent = Prospector(
            client=client, ledger=_ledger(tmp_path), fetchers=[lambda kw: [self._signal()]]
        )
        run = agent.run()
        assert run.outputs == []
        assert run.errors == []
        assert any("deliberate, not an error" in n for n in run.notes)

    def test_unparseable_triage_is_recorded(self, tmp_path):
        agent = Prospector(
            client=_fake_client("not json"),
            ledger=_ledger(tmp_path),
            fetchers=[lambda kw: [self._signal()]],
        )
        run = agent.run()
        assert run.errors


class TestConcierge:
    DOCS = {
        "setup": "To configure Claude Code set ANTHROPIC_BASE_URL to the gateway url",
        "billing": "Credits are purchased through Stripe checkout",
    }

    def test_escalates_when_no_docs_match(self, tmp_path):
        agent = Concierge(client=_fake_client(), ledger=_ledger(tmp_path), docs=self.DOCS)
        result = agent.answer(SupportQuestion("discord", "u", "zzzz qqqq wwww"))
        assert result["status"] == "escalated"
        agent.client.complete.assert_not_called()

    def test_escalates_repeat_questions_without_a_call(self, tmp_path):
        """A second automated answer to the same question erodes trust."""
        agent = Concierge(client=_fake_client(), ledger=_ledger(tmp_path), docs=self.DOCS)
        result = agent.answer(
            SupportQuestion("discord", "u", "configure claude code", is_followup=True)
        )
        assert result["status"] == "escalated"
        agent.client.complete.assert_not_called()

    def test_answers_when_docs_cover_the_question(self, tmp_path):
        client = _fake_client(
            json.dumps({"can_answer": True, "answer": "Set ANTHROPIC_BASE_URL", "citations": ["setup"]})
        )
        agent = Concierge(client=client, ledger=_ledger(tmp_path), docs=self.DOCS)
        result = agent.answer(SupportQuestion("discord", "u", "how do I configure claude code"))
        assert result["status"] == "answered"
        assert result["citations"] == ["setup"]

    def test_model_declining_results_in_escalation(self, tmp_path):
        client = _fake_client(
            json.dumps({"can_answer": False, "escalate_reason": "docs unclear"})
        )
        agent = Concierge(client=client, ledger=_ledger(tmp_path), docs=self.DOCS)
        result = agent.answer(SupportQuestion("discord", "u", "configure claude code"))
        assert result["status"] == "escalated"

    def test_handle_counts_escalations(self, tmp_path):
        agent = Concierge(client=_fake_client(), ledger=_ledger(tmp_path), docs=self.DOCS)
        run = agent.handle([SupportQuestion("discord", "u", "zzzz qqqq")])
        assert any("escalated" in n for n in run.notes)

    def test_retrieval_prefers_higher_overlap(self, tmp_path):
        agent = Concierge(client=_fake_client(), ledger=_ledger(tmp_path), docs=self.DOCS)
        docs = agent._relevant_docs("stripe credits purchased")
        assert "billing" in docs


class TestDogfoodReport:
    def test_empty_ledger_says_so(self):
        md = render_markdown({"records": 0}, "July")
        assert "No agent runs recorded" in md

    def test_reports_totals(self):
        summary = {
            "records": 10,
            "total_cost_usd": 12.34,
            "total_tokens": 100000,
            "cache_read_tokens": 60000,
            "by_agent": {"publisher": {"calls": 5, "tokens": 50000, "cost_usd": 8.0}},
        }
        md = render_markdown(summary, "July")
        assert "$12.34" in md
        assert "publisher" in md

    def test_zero_cache_reads_is_flagged_not_hidden(self):
        """An absent cache figure in a post about caching is what a sceptic checks."""
        summary = {
            "records": 3,
            "total_cost_usd": 1.0,
            "total_tokens": 1000,
            "cache_read_tokens": 0,
            "by_agent": {},
        }
        md = render_markdown(summary, "July")
        assert "should be non-zero" in md

    def test_states_that_figures_are_actuals_not_projections(self):
        summary = {
            "records": 1,
            "total_cost_usd": 1.0,
            "total_tokens": 10,
            "cache_read_tokens": 5,
            "by_agent": {},
        }
        md = render_markdown(summary, "July").lower()
        assert "no projections or annualised numbers" in md
        assert "this is what the month cost" in md

    def test_reports_only_ledger_figures(self):
        """Every dollar figure in the report must trace to the summary."""
        summary = {
            "records": 1,
            "total_cost_usd": 7.77,
            "total_tokens": 10,
            "cache_read_tokens": 5,
            "by_agent": {"publisher": {"calls": 1, "tokens": 10, "cost_usd": 7.77}},
        }
        md = render_markdown(summary, "July")
        dollar_figures = {
            token.strip("|").strip() for token in md.split() if token.startswith("$")
        }
        assert dollar_figures == {"$7.77"}


class TestAgentRunRecord:
    def test_total_cost_sums_usage(self):
        run = AgentRun(agent="a", started_at=0)
        run.usage = [
            UsageRecord("a", "m", 1, 1, 0, 0.01, 1),
            UsageRecord("a", "m", 1, 1, 0, 0.02, 2),
        ]
        assert run.total_cost_usd == pytest.approx(0.03)

    def test_serializes(self):
        run = AgentRun(agent="a", started_at=1)
        run.notes.append("n")
        payload = run.to_dict()
        assert payload["agent"] == "a"
        assert payload["notes"] == ["n"]
