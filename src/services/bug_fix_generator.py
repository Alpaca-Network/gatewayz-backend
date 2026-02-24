"""
Automated bug fix generator using Claude API.

Analyzes error patterns and generates fixes with explanations.
Integrates with git and GitHub for automated PR creation.

Improvements in this version:
- Comprehensive request/response logging with correlation IDs
- Retry logic with exponential backoff for transient failures
- Prompt sanitization and length validation
- Better error handling and reporting
- API key validation on initialization
"""

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config.config import Config
from src.services.error_monitor import ErrorPattern

logger = logging.getLogger(__name__)


# Maximum prompt size to prevent API errors (Claude has a large context window, but let's be safe)
MAX_PROMPT_LENGTH = 50000  # characters
MAX_ERROR_MESSAGE_LENGTH = 10000  # characters


@dataclass
class BugFix:
    """Represents a generated bug fix."""

    id: str
    error_pattern_id: str
    error_message: str
    error_category: str
    analysis: str
    proposed_fix: str
    code_changes: dict[str, str]  # file_path -> code
    files_affected: list[str]
    severity: str
    generated_at: datetime
    pr_url: str | None = None
    status: str = "pending"  # pending, testing, merged, failed

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "error_pattern_id": self.error_pattern_id,
            "error_message": self.error_message,
            "error_category": self.error_category,
            "analysis": self.analysis,
            "proposed_fix": self.proposed_fix,
            "code_changes": self.code_changes,
            "files_affected": self.files_affected,
            "severity": self.severity,
            "generated_at": self.generated_at.isoformat(),
            "pr_url": self.pr_url,
            "status": self.status,
        }


class BugFixGenerator:
    """Generates bug fixes using Claude API with improved reliability."""

    def __init__(self, github_token: str | None = None):
        self.anthropic_key = getattr(Config, "ANTHROPIC_API_KEY", None)
        if not self.anthropic_key:
            logger.error(
                "ANTHROPIC_API_KEY is not configured. "
                "Bug fix generation is disabled. "
                "Set ANTHROPIC_API_KEY environment variable to enable automated bug fixes."
            )
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not configured. "
                "Set this environment variable to enable automated bug fixes."
            )

        # Validate API key format
        if not self.anthropic_key.startswith("sk-ant-"):
            logger.warning(
                "ANTHROPIC_API_KEY does not start with 'sk-ant-'. "
                "This may indicate an invalid API key."
            )

        self.github_token = github_token or getattr(Config, "GITHUB_TOKEN", None)
        self.anthropic_url = "https://api.anthropic.com/v1"
        self.anthropic_model = getattr(Config, "ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        self.session: httpx.AsyncClient | None = None
        self.generated_fixes: dict[str, BugFix] = {}
        self.api_key_validated = False

    async def initialize(self):
        """Initialize the generator and validate API key."""
        self.session = httpx.AsyncClient(timeout=30.0)

        # Validate API key on initialization
        try:
            await self._validate_api_key()
            self.api_key_validated = True
            logger.info("✓ Claude API key validated successfully")
        except Exception as e:
            logger.error(f"Failed to validate Claude API key: {e}")
            logger.warning("Bug fix generation may fail due to invalid API key")

    async def _validate_api_key(self):
        """Validate the Claude API key with a minimal test request."""
        if not self.session:
            raise RuntimeError("Session not initialized. Call initialize() first.")

        try:
            response = await self.session.post(
                f"{self.anthropic_url}/messages",
                headers={
                    "x-api-key": self.anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.anthropic_model,
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "test"}],
                },
                timeout=10.0,
            )
            response.raise_for_status()
            logger.debug("API key validation successful")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise ValueError("Invalid ANTHROPIC_API_KEY: Authentication failed") from e
            elif e.response.status_code == 400:
                logger.debug(f"API key validation response: {e.response.text}")
                raise ValueError(
                    f"ANTHROPIC_API_KEY validation failed with 400 Bad Request. "
                    f"Response: {e.response.text[:200]}"
                ) from e
            else:
                raise
        except httpx.TimeoutException:
            logger.warning("API key validation timed out - Claude API may be slow")
            # Don't fail on timeout, API might just be slow
        except Exception as e:
            logger.error(f"Unexpected error validating API key: {e}")
            raise

    async def close(self):
        """Close the generator."""
        if self.session:
            await self.session.aclose()

    def _sanitize_text(self, text: str, max_length: int = MAX_ERROR_MESSAGE_LENGTH) -> str:
        """Sanitize text for API requests."""
        if not text:
            return ""

        # Truncate to max length
        if len(text) > max_length:
            text = text[:max_length] + f"\n... (truncated from {len(text)} chars)"

        # Escape special characters that might break JSON
        text = text.replace("\x00", "")  # Remove null bytes

        return text

    def _prepare_prompt(self, prompt: str) -> str:
        """Prepare and validate prompt before sending."""
        if len(prompt) > MAX_PROMPT_LENGTH:
            logger.warning(
                f"Prompt too long ({len(prompt)} chars), truncating to {MAX_PROMPT_LENGTH}"
            )
            prompt = prompt[:MAX_PROMPT_LENGTH] + "\n... (truncated due to length)"

        return prompt

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _make_claude_request(
        self, prompt: str, max_tokens: int = 1024, request_id: str | None = None
    ) -> dict:
        """Make a request to Claude API with retry logic and logging."""
        if not self.session:
            await self.initialize()

        request_id = request_id or str(uuid4())[:8]

        # Prepare request payload
        request_payload = {
            "model": self.anthropic_model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }

        logger.debug(
            f"[{request_id}] Sending request to Claude API "
            f"(prompt length: {len(prompt)} chars, max_tokens: {max_tokens})"
        )

        try:
            response = await self.session.post(
                f"{self.anthropic_url}/messages",
                headers={
                    "x-api-key": self.anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=request_payload,
                timeout=30.0,
            )

            logger.debug(f"[{request_id}] Response status: {response.status_code}")

            # Log error response body for debugging
            if response.status_code >= 400:
                error_body = response.text[:500]
                logger.error(
                    f"[{request_id}] Claude API error response: "
                    f"status={response.status_code}, body={error_body}"
                )

            response.raise_for_status()
            data = response.json()

            logger.debug(f"[{request_id}] Successfully received response from Claude API")
            return data

        except httpx.HTTPStatusError as e:
            # Log detailed error information
            logger.error(
                f"[{request_id}] HTTP error from Claude API: {e.response.status_code} - "
                f"{e.response.text[:200]}"
            )
            raise
        except httpx.TimeoutException as e:
            logger.warning(f"[{request_id}] Request to Claude API timed out: {e}")
            raise
        except httpx.ConnectError as e:
            logger.error(f"[{request_id}] Failed to connect to Claude API: {e}")
            raise
        except Exception as e:
            logger.error(f"[{request_id}] Unexpected error calling Claude API: {e}", exc_info=True)
            raise

    async def analyze_error(self, error: ErrorPattern) -> str:
        """Use Claude to analyze an error and determine root cause."""
        if not self.session:
            await self.initialize()

        # Sanitize error data
        sanitized_message = self._sanitize_text(error.message, MAX_ERROR_MESSAGE_LENGTH)
        sanitized_stack_trace = self._sanitize_text(
            error.stack_trace or "Not provided", MAX_ERROR_MESSAGE_LENGTH
        )

        prompt = f"""You are an expert Python developer and DevOps engineer. Analyze this error and determine the root cause.

Error Type: {error.error_type}
Message: {sanitized_message}
Category: {error.category.value}
Severity: {error.severity.value}
File: {error.file or 'unknown'}
Line: {error.line or 'unknown'}
Function: {error.function or 'unknown'}

Stack Trace:
{sanitized_stack_trace}

Error Count: {error.count}
Last Seen: {error.last_seen}

Provide a concise analysis of:
1. Root cause
2. Impact
3. Why this is happening
4. Which component is affected"""

        prompt = self._prepare_prompt(prompt)
        request_id = str(uuid4())[:8]

        try:
            logger.info(f"[{request_id}] Analyzing error: {error.message[:100]}...")
            data = await self._make_claude_request(prompt, max_tokens=1024, request_id=request_id)

            if data.get("content"):
                analysis = data["content"][0].get("text", "Analysis failed")
                logger.info(f"[{request_id}] Analysis completed successfully")
                return analysis

            logger.warning(f"[{request_id}] No content in Claude response")
            return "Analysis failed: No content in response"

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.error(
                    f"[{request_id}] Bad request to Claude API. This may indicate "
                    f"invalid prompt or API configuration."
                )
            elif e.response.status_code == 401:
                logger.error(
                    f"[{request_id}] Authentication failed. Check ANTHROPIC_API_KEY configuration."
                )
            return f"Error analysis failed: {e.response.status_code} - {str(e)[:100]}"
        except Exception as e:
            logger.error(f"[{request_id}] Error analyzing with Claude: {e}")
            return f"Error analysis failed: {str(e)[:100]}"

    async def generate_fix(self, error: ErrorPattern) -> BugFix | None:
        """Generate a fix for the error using Claude."""
        if not self.session:
            await self.initialize()

        request_id = str(uuid4())[:8]

        try:
            # First, analyze the error
            logger.info(f"[{request_id}] Starting fix generation for: {error.message[:100]}...")
            analysis = await self.analyze_error(error)

            if analysis.startswith("Error analysis failed"):
                logger.warning(
                    f"[{request_id}] Skipping fix generation due to failed analysis: {analysis}"
                )
                return None

            # Sanitize error data
            sanitized_message = self._sanitize_text(error.message, MAX_ERROR_MESSAGE_LENGTH)
            sanitized_file = self._sanitize_text(error.file or "unknown", 500)
            sanitized_analysis = self._sanitize_text(analysis, MAX_ERROR_MESSAGE_LENGTH)

            # Then generate a fix
            fix_prompt = f"""Based on this error analysis, generate a specific fix.

Error: {sanitized_message}
Category: {error.category.value}
File: {sanitized_file}

Analysis:
{sanitized_analysis}

Generate a fix that includes:
1. Root cause fix
2. Specific code changes needed
3. File paths to modify
4. Prevention measures

Format your response as JSON:
{{
  "title": "Brief title for the fix",
  "description": "What the fix does",
  "explanation": "Why this fixes the issue",
  "changes": [
    {{
      "file": "path/to/file.py",
      "type": "modify|add|delete",
      "change_description": "What changed",
      "code": "The actual code to add/modify (if modify, include surrounding context)"
    }}
  ]
}}"""

            fix_prompt = self._prepare_prompt(fix_prompt)

            logger.info(f"[{request_id}] Generating fix with Claude API...")
            data = await self._make_claude_request(
                fix_prompt, max_tokens=2048, request_id=request_id
            )

            if not data.get("content"):
                logger.error(f"[{request_id}] No content in Claude response")
                return None

            response_text = data["content"][0].get("text", "")
            logger.debug(
                f"[{request_id}] Received fix response (length: {len(response_text)} chars)"
            )

            # Extract JSON from response
            try:
                # Find JSON in the response
                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    fix_data = json.loads(response_text[json_start:json_end])
                    logger.debug(f"[{request_id}] Successfully parsed fix JSON")
                else:
                    logger.error(f"[{request_id}] No JSON found in Claude response")
                    logger.debug(f"[{request_id}] Response text: {response_text[:500]}")
                    return None
            except json.JSONDecodeError as e:
                logger.error(f"[{request_id}] Failed to parse Claude response as JSON: {e}")
                logger.debug(f"[{request_id}] Response text: {response_text[:500]}")
                return None

            # Create code changes mapping
            code_changes = {}
            files_affected = []

            for change in fix_data.get("changes", []):
                file_path = change.get("file")
                if file_path:
                    code_changes[file_path] = change.get("code", "")
                    files_affected.append(file_path)

            fix = BugFix(
                id=str(uuid4()),
                error_pattern_id=f"{error.category.value}:{error.message[:50]}",
                error_message=error.message,
                error_category=error.category.value,
                analysis=analysis,
                proposed_fix=fix_data.get("description", ""),
                code_changes=code_changes,
                files_affected=files_affected,
                severity=error.severity.value,
                generated_at=datetime.now(UTC),
            )

            self.generated_fixes[fix.id] = fix
            logger.info(
                f"[{request_id}] Successfully generated fix (ID: {fix.id}, "
                f"files affected: {len(files_affected)})"
            )
            return fix

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.error(
                    f"[{request_id}] Bad request to Claude API during fix generation. "
                    f"This may indicate invalid prompt or API configuration."
                )
            elif e.response.status_code == 401:
                logger.error(
                    f"[{request_id}] Authentication failed during fix generation. "
                    f"Check ANTHROPIC_API_KEY configuration."
                )
            else:
                logger.error(f"[{request_id}] HTTP error during fix generation: {e}")
            return None
        except Exception as e:
            logger.error(f"[{request_id}] Error generating fix with Claude: {e}", exc_info=True)
            return None

    async def create_branch_and_commit(
        self, fix: BugFix, repo_path: str = "/root/repo"
    ) -> str | None:
        """Create a git branch and commit the fix."""
        try:
            branch_name = f"auto-fix/{fix.error_category}/{uuid4().hex[:8]}"

            # Create branch
            subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )

            # Apply changes
            for file_path, code in fix.code_changes.items():
                full_path = f"{repo_path}/{file_path}"

                # Create directory if needed
                import os

                os.makedirs(os.path.dirname(full_path), exist_ok=True)

                with open(full_path, "w") as f:
                    f.write(code)

                # Stage file
                subprocess.run(
                    ["git", "add", file_path],
                    cwd=repo_path,
                    check=True,
                    capture_output=True,
                )

            # Commit
            commit_message = f"""fix: {fix.error_category} - Auto-generated fix

Error: {fix.error_message}
Severity: {fix.severity}

Analysis:
{fix.analysis}

Fix Description:
{fix.proposed_fix}

Files Modified:
{chr(10).join(f'- {f}' for f in fix.files_affected)}

Generated by Error Monitor Auto-Fix System
🤖 Generated with Claude API
"""

            subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )

            return branch_name

        except subprocess.CalledProcessError as e:
            logger.error(f"Git error: {e.stderr.decode()}")
            return None
        except Exception as e:
            logger.error(f"Error creating branch: {e}")
            return None

    async def create_pull_request(
        self,
        fix: BugFix,
        branch_name: str,
        repo: str = "terragon-labs/gatewayz",
        base_branch: str = "main",
    ) -> str | None:
        """Create a GitHub PR for the fix."""
        if not self.github_token:
            logger.warning("GitHub token not configured, skipping PR creation")
            return None

        try:
            # Create PR via GitHub API
            pr_data = {
                "title": f"[AUTO] Fix {fix.error_category}: {fix.error_message[:50]}",
                "body": f"""## Auto-Generated Bug Fix

**Error Category**: {fix.error_category}
**Severity**: {fix.severity}
**Error Count**: {fix.error_pattern_id}

### Analysis
{fix.analysis}

### Proposed Fix
{fix.proposed_fix}

### Files Modified
{chr(10).join(f'- `{f}`' for f in fix.files_affected)}

### Details
- **Generated**: {fix.generated_at.isoformat()}
- **Fix ID**: {fix.id}
- **Status**: Awaiting Review

> 🤖 This PR was automatically generated by the Error Monitor system using Claude API analysis.
> Please review carefully before merging.
""",
                "head": branch_name,
                "base": base_branch,
            }

            response = await self.session.post(
                f"https://api.github.com/repos/{repo}/pulls",
                headers={
                    "Authorization": f"token {self.github_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                json=pr_data,
            )

            if response.status_code in [201, 200]:
                pr_json = response.json()
                pr_url = pr_json.get("html_url")
                fix.pr_url = pr_url
                fix.status = "testing"
                logger.info(f"Created PR: {pr_url}")
                return pr_url
            else:
                logger.error(f"Failed to create PR: {response.text}")
                return None

        except Exception as e:
            logger.error(f"Error creating PR: {e}")
            return None

    async def process_error(self, error: ErrorPattern, create_pr: bool = True) -> BugFix | None:
        """Process an error end-to-end: analyze, fix, commit, and create PR."""
        try:
            logger.info(f"Processing error: {error.message}")

            # Generate fix
            fix = await self.generate_fix(error)
            if not fix:
                logger.error("Failed to generate fix")
                return None

            logger.info(f"Generated fix: {fix.id}")

            # Create branch and commit
            if fix.code_changes:
                branch_name = await self.create_branch_and_commit(fix)
                if not branch_name:
                    logger.error("Failed to create branch")
                    fix.status = "failed"
                    return fix

                logger.info(f"Created branch: {branch_name}")

                # Create PR if requested
                if create_pr:
                    pr_url = await self.create_pull_request(fix, branch_name)
                    if pr_url:
                        logger.info(f"Created PR: {pr_url}")
                    else:
                        logger.warning("Failed to create PR")

            return fix

        except Exception as e:
            logger.error(f"Error processing error: {e}", exc_info=True)
            return None

    async def process_multiple_errors(
        self, errors: list[ErrorPattern], create_prs: bool = True
    ) -> list[BugFix]:
        """Process multiple errors in parallel."""
        tasks = [self.process_error(error, create_pr=create_prs) for error in errors]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        fixes = []
        for result in results:
            if isinstance(result, BugFix):
                fixes.append(result)
            elif isinstance(result, Exception):
                logger.error(f"Error processing error: {result}")

        return fixes


# Singleton instance
_bug_fix_generator: BugFixGenerator | None = None


async def get_bug_fix_generator() -> BugFixGenerator:
    """Get or create the bug fix generator singleton."""
    global _bug_fix_generator
    if _bug_fix_generator is None:
        _bug_fix_generator = BugFixGenerator()
        await _bug_fix_generator.initialize()
    return _bug_fix_generator
