"""A5 — the Concierge.

First-responder for community setup questions. Answers from the docs, escalates
anything it cannot answer with a citation, and drafts the weekly changelog post.

The escalation rule is the whole design. Activation drives the second top-up,
which is the retention metric the raise turns on, so a wrong answer that stalls
someone's setup costs more than a slow one. When the docs do not clearly cover a
question, the agent says so and hands off rather than reasoning its way to a
plausible guess.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from typing import Any

from agents.base import Agent, AgentRun

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the first-line support agent for Gatewayz, an AI
inference gateway for coding agents.

You will be given a user's question and relevant documentation excerpts.

Answer ONLY from the supplied excerpts. If they do not clearly and completely
answer the question, escalate instead of answering.

Escalate when:
- The excerpts do not cover the question.
- The question involves billing, refunds, an outage, or account access.
- The user is already frustrated, or this is their second time asking.
- Answering would require you to guess at a version, a limit, or a price.

A wrong answer stalls someone's setup and costs us the account. A fast handoff
does not. Escalating is not a failure.

Return strict JSON:
{"can_answer": bool, "answer": "...", "citations": ["..."], "escalate_reason": "..."}
"""


@dataclass
class SupportQuestion:
    channel: str  # "discord" | "telegram" | "email"
    user: str
    text: str
    is_followup: bool = False


class Concierge(Agent):
    name = "concierge"
    system_prompt = SYSTEM_PROMPT

    def __init__(self, *args, docs: dict[str, str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        # Doc slug -> content. Supplied by the caller so this is testable and so
        # the retrieval strategy can change without touching the agent.
        self.docs = docs or {}

    def _relevant_docs(self, question: str, limit: int = 4) -> dict[str, str]:
        """Cheap lexical retrieval over the doc set.

        Deliberately simple: with a doc set this small, term overlap beats the
        operational cost of an embedding index, and it fails in an obvious way
        (no matches) rather than a subtle one (confidently wrong neighbours).
        """
        terms = {t for t in question.lower().split() if len(t) > 3}
        scored: list[tuple[int, str]] = []
        for slug, content in self.docs.items():
            lowered = content.lower()
            score = sum(1 for term in terms if term in lowered)
            if score:
                scored.append((score, slug))
        scored.sort(reverse=True)
        return {slug: self.docs[slug] for _, slug in scored[:limit]}

    def answer(self, question: SupportQuestion) -> dict[str, Any] | None:
        excerpts = self._relevant_docs(question.text)

        if not excerpts:
            # No docs matched at all — escalate without spending a call.
            return {
                "question": asdict(question),
                "can_answer": False,
                "answer": "",
                "citations": [],
                "escalate_reason": "No documentation matched this question.",
                "status": "escalated",
            }

        if question.is_followup:
            return {
                "question": asdict(question),
                "can_answer": False,
                "answer": "",
                "citations": [],
                "escalate_reason": "Repeat question — a second automated answer erodes trust.",
                "status": "escalated",
            }

        doc_text = "\n\n".join(f"### {slug}\n{content[:4000]}" for slug, content in excerpts.items())
        prompt = f"""User question ({question.channel}, from {question.user}):
{question.text}

Documentation excerpts:
{doc_text}

Return the JSON object.
"""
        try:
            raw = self.think(prompt, max_tokens=900, response_json=True)
            verdict = json.loads(raw)
        except json.JSONDecodeError:
            self.fail(f"Unparseable JSON answering question from {question.user}")
            return None
        except Exception as e:
            self.fail(f"Answer failed for {question.user}: {e}")
            return None

        can_answer = bool(verdict.get("can_answer"))
        return {
            "question": asdict(question),
            "can_answer": can_answer,
            "answer": verdict.get("answer", ""),
            "citations": verdict.get("citations", []),
            "escalate_reason": verdict.get("escalate_reason", ""),
            "status": "answered" if can_answer else "escalated",
        }

    def handle(self, questions: list[SupportQuestion]) -> AgentRun:
        escalations = 0
        for question in questions:
            result = self.answer(question)
            if not result:
                continue
            self.emit("support_response", result)
            if result["status"] == "escalated":
                escalations += 1

        if escalations:
            self.note(
                f"{escalations} question(s) escalated to a human. Activation blockers "
                "jump the queue over everything else."
            )
        return self.run_record

    def run(self) -> AgentRun:
        self.fail(
            "Concierge.run() needs a question source. Call handle(questions) with the "
            "channel adapter's output."
        )
        return self.run_record
