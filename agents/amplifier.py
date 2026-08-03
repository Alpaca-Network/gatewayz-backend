"""A2 — the Amplifier.

Turns a published piece into platform-native drafts: an X thread, a Reddit post,
a LinkedIn post, a Hacker News title.

**Drafts only, always.** Auto-posting to Reddit or Hacker News gets accounts
banned and destroys the founder-led credibility this motion runs on. This agent
removes the writing time; the human still does the showing up, which is the part
that actually converts.

Each platform gets genuinely different copy rather than the same text reflowed.
Cross-posting identical text is the single most recognisable marketing tell on
every one of these platforms.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agents.base import Agent, AgentRun

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You write social drafts for the founder of Gatewayz, an AI
inference gateway for coding agents. Everything you write will be posted by a
human, from their personal account, in their own voice.

Voice: an engineer talking to other engineers. Specific, understated, happy to
admit limitations. Never markety.

Absolute rules per platform:
- Reddit: you are a guest in someone's community. Lead with the useful thing.
  Mention Gatewayz once, late, and only if it is genuinely relevant. A post that
  reads as an ad gets removed and the account flagged. If the piece has nothing
  useful to that subreddit, say so instead of writing a post.
- Hacker News: title only, under 80 characters, no adjectives, no clickbait.
  State what the thing is.
- X: a thread. First post must stand alone and be worth reading if nobody clicks.
  No engagement bait, no "a thread 🧵" theatre.
- LinkedIn: professional but not corporate. No "thrilled to announce".

Never fabricate a number or a result. If the source piece has no figures, write
copy that does not need any.
"""

PLATFORMS = ("reddit", "hackernews", "x", "linkedin")


@dataclass
class SourcePiece:
    title: str
    url: str
    summary: str
    key_points: list[str]


class Amplifier(Agent):
    name = "amplifier"
    system_prompt = SYSTEM_PROMPT

    def __init__(self, *args, piece: SourcePiece | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.piece = piece

    def _prompt_for(self, piece: SourcePiece, platform: str) -> str:
        points = "\n".join(f"- {p}" for p in piece.key_points)
        target = {
            "reddit": (
                "Write a Reddit post for r/ClaudeAI, r/ChatGPTCoding or r/LocalLLaMA. "
                "Name which subreddit it suits and why. If it suits none, say that."
            ),
            "hackernews": "Write 3 candidate Show HN / submission titles, best first.",
            "x": "Write a thread of 4-6 posts. Number them.",
            "linkedin": "Write a single LinkedIn post, 150-250 words.",
        }[platform]

        return f"""Source piece:
Title: {piece.title}
URL: {piece.url}
Summary: {piece.summary}
Key points:
{points}

{target}
"""

    def draft_for(self, piece: SourcePiece, platform: str) -> dict[str, Any] | None:
        try:
            text = self.think(self._prompt_for(piece, platform), max_tokens=1200)
        except Exception as e:
            self.fail(f"{platform} draft failed: {e}")
            return None
        return {
            "platform": platform,
            "source_url": piece.url,
            "text": text,
            "status": "draft-post-manually",
        }

    def run(self) -> AgentRun:
        if self.piece is None:
            self.fail(
                "No source piece supplied. The Amplifier turns published content into "
                "drafts; it does not invent the content."
            )
            return self.run_record

        for platform in PLATFORMS:
            draft = self.draft_for(self.piece, platform)
            if draft:
                self.emit("social_draft", draft)

        self.note(
            "All output is a draft. Post from the founder's own account — brand "
            "accounts and automation convert far worse here and risk bans."
        )
        return self.run_record


def main() -> int:
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--piece", required=True, help="JSON file describing the source piece")
    args = parser.parse_args()

    with open(args.piece, encoding="utf-8") as fh:
        data = _json.load(fh)

    piece = SourcePiece(
        title=data["title"],
        url=data["url"],
        summary=data["summary"],
        key_points=data.get("key_points", []),
    )

    agent = Amplifier(piece=piece)
    run = agent.run()
    path = agent.save()
    print(f"Amplifier run written to {path}")
    print(f"{len(run.outputs)} draft(s), cost ${run.total_cost_usd:.4f}")
    for error in run.errors:
        print(f"ERROR: {error}")
    return 1 if run.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
