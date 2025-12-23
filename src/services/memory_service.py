"""
Memory service for cross-session AI memory.

This service handles:
- Extracting facts/preferences from conversations using LLM
- Retrieving relevant memories for context injection
- Formatting memories for system context
- Background memory extraction after conversations
"""

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from src.config import Config
from src.db.user_memory import (
    create_user_memory,
    get_user_memories,
    update_memory_access,
    check_duplicate_memory,
    MEMORY_CATEGORIES,
)
from src.services.connection_pool import get_pooled_async_client

logger = logging.getLogger(__name__)

# Memory extraction prompt template
EXTRACTION_PROMPT = """Analyze the conversation below and extract key facts about the user that would be helpful to remember for future conversations.

Focus on extracting:
- Personal preferences (coding style, tools, frameworks, languages)
- Professional context (job role, company type, tech stack)
- Communication preferences (verbosity, format preferences)
- Explicit instructions from the user
- Project details they're working on
- Their name if mentioned

Rules:
1. Only extract EXPLICIT, clearly stated facts - no inferences
2. Each fact should be a single, clear statement
3. Skip conversational filler or temporary context
4. Max 5 facts per extraction
5. Assign confidence 0.7-1.0 based on clarity:
   - 1.0: Explicitly stated ("I prefer TypeScript")
   - 0.9: Strongly implied ("I always use TypeScript for my projects")
   - 0.8: Reasonably clear from context
   - 0.7: Somewhat clear but could be temporary

Categories:
- preference: User's stated preferences (e.g., "prefers TypeScript over JavaScript")
- context: Professional/personal context (e.g., "works as a backend engineer")
- instruction: Explicit instructions (e.g., "wants code explained step by step")
- fact: Factual information (e.g., "their project uses PostgreSQL")
- name: Names mentioned (e.g., "user's name is Alex")
- project: Project details (e.g., "building an e-commerce application")

Conversation:
{conversation}

Return a JSON array only, no other text:
[{{"category": "preference|context|instruction|fact|name|project", "content": "concise fact statement", "confidence": 0.7-1.0}}]

If no extractable facts, return: []"""


# System context format template
MEMORY_CONTEXT_TEMPLATE = """Based on previous conversations, here's what I know about you:
{memories}

Use this context to provide more personalized and relevant responses."""


class MemoryService:
    """Service for managing cross-session AI memory."""

    def __init__(self):
        self._extraction_model = "openai/gpt-4o-mini"  # Fast and cheap for extraction
        self._max_extraction_tokens = 500
        self._max_context_tokens = 500

    async def extract_memories_from_messages(
        self,
        user_id: int,
        session_id: int,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Extract key facts/preferences from conversation messages.

        Args:
            user_id: The user's ID
            session_id: The chat session ID (for source tracking)
            messages: List of message dicts with 'role' and 'content'

        Returns:
            List of created memory dicts
        """
        if not messages:
            return []

        try:
            # Format conversation for extraction
            conversation_text = self._format_conversation(messages)

            # Skip if conversation is too short
            if len(conversation_text) < 100:
                logger.debug(f"Skipping memory extraction: conversation too short ({len(conversation_text)} chars)")
                return []

            # Call LLM for extraction
            extracted = await self._call_extraction_llm(conversation_text)

            if not extracted:
                logger.debug(f"No memories extracted from session {session_id}")
                return []

            # Save extracted memories (with deduplication)
            created_memories = []
            for item in extracted:
                try:
                    category = item.get("category", "general")
                    content = item.get("content", "").strip()
                    confidence = float(item.get("confidence", 0.8))

                    if not content:
                        continue

                    # Validate category
                    if category not in MEMORY_CATEGORIES:
                        category = "general"

                    # Check for duplicates
                    existing = check_duplicate_memory(user_id, content)
                    if existing:
                        logger.debug(f"Skipping duplicate memory: {content[:50]}...")
                        continue

                    # Create memory
                    memory = create_user_memory(
                        user_id=user_id,
                        category=category,
                        content=content,
                        source_session_id=session_id,
                        confidence=confidence,
                    )
                    created_memories.append(memory)
                    logger.info(f"Created memory for user {user_id}: {category} - {content[:50]}...")

                except Exception as e:
                    logger.warning(f"Failed to save extracted memory: {e}")
                    continue

            logger.info(f"Extracted {len(created_memories)} memories from session {session_id}")
            return created_memories

        except Exception as e:
            logger.error(f"Memory extraction failed for user {user_id}: {e}")
            return []

    async def get_relevant_memories(
        self,
        user_id: int,
        query: str = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Retrieve most relevant memories for context injection.

        Args:
            user_id: The user's ID
            query: Optional query for relevance (not currently used, for future semantic search)
            limit: Maximum memories to return

        Returns:
            List of memory dicts, ordered by relevance (access frequency + recency)
        """
        try:
            # Get memories ordered by access frequency and recency
            memories = get_user_memories(
                user_id=user_id,
                limit=limit,
                active_only=True,
            )

            # Update access count for retrieved memories (async, fire and forget)
            for memory in memories:
                try:
                    update_memory_access(memory["id"])
                except Exception:
                    pass  # Non-critical, don't fail the request

            logger.debug(f"Retrieved {len(memories)} memories for user {user_id}")
            return memories

        except Exception as e:
            logger.error(f"Failed to get relevant memories for user {user_id}: {e}")
            return []

    def format_memories_for_context(
        self,
        memories: list[dict[str, Any]],
        max_tokens: int = None,
    ) -> str:
        """
        Format memories as a system context string.

        Args:
            memories: List of memory dicts
            max_tokens: Maximum approximate token count (default: 500)

        Returns:
            Formatted context string for system message
        """
        if not memories:
            return ""

        max_tokens = max_tokens or self._max_context_tokens

        # Group memories by category for better organization
        by_category: dict[str, list[str]] = {}
        for memory in memories:
            category = memory.get("category", "general")
            content = memory.get("content", "")
            if content:
                if category not in by_category:
                    by_category[category] = []
                by_category[category].append(content)

        # Format as bullet points
        lines = []
        category_labels = {
            "preference": "Preferences",
            "context": "Background",
            "instruction": "Instructions",
            "fact": "Facts",
            "name": "Personal",
            "project": "Projects",
            "general": "Notes",
        }

        for category, items in by_category.items():
            label = category_labels.get(category, category.title())
            for item in items:
                lines.append(f"• {label}: {item}")

        memory_text = "\n".join(lines)

        # Rough token estimate (4 chars ≈ 1 token)
        estimated_tokens = len(memory_text) // 4
        if estimated_tokens > max_tokens:
            # Truncate if too long
            max_chars = max_tokens * 4
            memory_text = memory_text[:max_chars] + "..."

        return MEMORY_CONTEXT_TEMPLATE.format(memories=memory_text)

    def _format_conversation(self, messages: list[dict[str, Any]]) -> str:
        """Format messages into a conversation string."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # Handle multimodal content
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                content = " ".join(text_parts) if text_parts else "[multimodal content]"

            # Truncate very long messages
            if len(content) > 1000:
                content = content[:1000] + "..."

            role_label = "User" if role == "user" else "Assistant"
            lines.append(f"{role_label}: {content}")

        return "\n\n".join(lines)

    async def _call_extraction_llm(self, conversation_text: str) -> list[dict[str, Any]]:
        """Call LLM to extract facts from conversation."""
        try:
            # Get async client
            client = get_pooled_async_client(
                base_url="https://openrouter.ai/api/v1",
                api_key=Config.OPENROUTER_API_KEY,
            )

            prompt = EXTRACTION_PROMPT.format(conversation=conversation_text)

            response = await client.chat.completions.create(
                model=self._extraction_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self._max_extraction_tokens,
                temperature=0.3,  # Lower temperature for more consistent extraction
            )

            if not response.choices:
                return []

            result_text = response.choices[0].message.content.strip()

            # Parse JSON response
            try:
                # Handle potential markdown code blocks
                if result_text.startswith("```"):
                    result_text = result_text.split("```")[1]
                    if result_text.startswith("json"):
                        result_text = result_text[4:]
                    result_text = result_text.strip()

                extracted = json.loads(result_text)

                if not isinstance(extracted, list):
                    logger.warning(f"Extraction returned non-list: {type(extracted)}")
                    return []

                return extracted

            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse extraction result: {e}")
                logger.debug(f"Raw extraction result: {result_text}")
                return []

        except Exception as e:
            logger.error(f"LLM extraction call failed: {e}")
            return []


# Singleton instance
memory_service = MemoryService()


async def extract_memories_background(
    user_id: int,
    session_id: int,
    messages: list[dict[str, Any]],
) -> None:
    """
    Background task to extract memories from a conversation.
    Called after chat completion is finished.

    Args:
        user_id: The user's ID
        session_id: The chat session ID
        messages: Recent messages from the conversation
    """
    try:
        # Only extract if there's meaningful conversation (1+ messages per user request)
        if len(messages) < 1:
            return

        await memory_service.extract_memories_from_messages(
            user_id=user_id,
            session_id=session_id,
            messages=messages,
        )
    except Exception as e:
        # Don't let extraction failures affect anything
        logger.error(f"Background memory extraction failed: {e}")
