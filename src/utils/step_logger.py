"""
Step-by-step logging utility for tracking complex operations.

Provides structured logging for multi-step processes like model catalog syncing,
making it easy to trace the entire flow from provider fetch to cache population.
"""

import logging
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class StepLogger:
    """
    Structured logger for tracking multi-step operations.

    Usage:
        step_logger = StepLogger("Model Sync", total_steps=5)
        step_logger.step(1, "Fetching from provider", provider="openrouter")
        # ... operation ...
        step_logger.success(models_count=150)

        step_logger.step(2, "Storing in database")
        # ... operation ...
        step_logger.success(rows_inserted=150)
    """

    def __init__(self, operation_name: str, total_steps: int | None = None, log_level: int = logging.INFO):
        """
        Initialize step logger.

        Args:
            operation_name: Name of the operation being tracked
            total_steps: Total number of steps (optional, for progress tracking)
            log_level: Logging level to use (default: INFO)
        """
        self.operation_name = operation_name
        self.total_steps = total_steps
        self.log_level = log_level
        self.current_step = 0
        self.start_time = time.time()
        self.step_start_time = None
        self._step_name = None
        self._step_metadata = {}

    def start(self, **metadata):
        """Log operation start with optional metadata."""
        self.start_time = time.time()
        metadata_str = self._format_metadata(metadata)
        logger.log(
            self.log_level,
            f"🚀 START: {self.operation_name}{metadata_str}"
        )

    def step(self, step_num: int, step_name: str, **metadata):
        """
        Log the start of a step.

        Args:
            step_num: Step number (1-indexed)
            step_name: Description of the step
            **metadata: Additional context (provider, gateway, table, etc.)
        """
        self.current_step = step_num
        self._step_name = step_name
        self._step_metadata = metadata
        self.step_start_time = time.time()

        progress = f"[{step_num}/{self.total_steps}]" if self.total_steps else f"[Step {step_num}]"
        metadata_str = self._format_metadata(metadata)

        logger.log(
            self.log_level,
            f"▶️  {progress} {step_name}{metadata_str}"
        )

    def success(self, **result_metadata):
        """
        Log successful completion of current step.

        Args:
            **result_metadata: Results to log (count, duration, etc.)
        """
        if self.step_start_time is None:
            logger.warning("success() called without step() - ignoring")
            return

        duration = time.time() - self.step_start_time
        result_metadata['duration_ms'] = f"{duration * 1000:.1f}"

        progress = f"[{self.current_step}/{self.total_steps}]" if self.total_steps else f"[Step {self.current_step}]"
        metadata_str = self._format_metadata(result_metadata)

        logger.log(
            self.log_level,
            f"✅ {progress} {self._step_name} - SUCCESS{metadata_str}"
        )

        self.step_start_time = None

    def skip(self, reason: str):
        """
        Log skipped step.

        Args:
            reason: Why the step was skipped
        """
        progress = f"[{self.current_step}/{self.total_steps}]" if self.total_steps else f"[Step {self.current_step}]"

        logger.log(
            self.log_level,
            f"⏭️  {progress} {self._step_name} - SKIPPED: {reason}"
        )

        self.step_start_time = None

    def failure(self, error: Exception, **metadata):
        """
        Log step failure.

        Args:
            error: The exception that occurred
            **metadata: Additional error context
        """
        if self.step_start_time:
            duration = time.time() - self.step_start_time
            metadata['duration_ms'] = f"{duration * 1000:.1f}"

        progress = f"[{self.current_step}/{self.total_steps}]" if self.total_steps else f"[Step {self.current_step}]"
        metadata_str = self._format_metadata(metadata)

        logger.error(
            f"❌ {progress} {self._step_name} - FAILED: {error}{metadata_str}"
        )

        self.step_start_time = None

    def complete(self, **summary):
        """
        Log operation completion with summary.

        Args:
            **summary: Summary statistics (total_items, total_duration, etc.)
        """
        total_duration = time.time() - self.start_time
        summary['total_duration_ms'] = f"{total_duration * 1000:.1f}"
        summary_str = self._format_metadata(summary)

        logger.log(
            self.log_level,
            f"🏁 COMPLETE: {self.operation_name}{summary_str}"
        )

    def _format_metadata(self, metadata: dict) -> str:
        """Format metadata dictionary as readable string."""
        if not metadata:
            return ""

        items = [f"{k}={v}" for k, v in metadata.items()]
        return f" ({', '.join(items)})"


@contextmanager
def log_step(step_num: int, step_name: str, logger_instance: StepLogger, **metadata):
    """
    Context manager for automatic step success/failure logging.

    Usage:
        step_logger = StepLogger("Model Sync", total_steps=3)

        with log_step(1, "Fetch models", step_logger, provider="openrouter"):
            models = fetch_models()
            yield {"count": len(models)}
    """
    logger_instance.step(step_num, step_name, **metadata)
    result = {}

    try:
        yield result
        logger_instance.success(**result)
    except Exception as e:
        logger_instance.failure(e, **metadata)
        raise


# Convenience function for quick step logging without class instance
def log_operation_step(
    step_num: int,
    step_name: str,
    operation_name: str = "Operation",
    total_steps: int | None = None,
    **metadata
):
    """
    Quick function to log a single step without creating StepLogger instance.

    Args:
        step_num: Step number
        step_name: Step description
        operation_name: Name of parent operation
        total_steps: Total steps in operation
        **metadata: Additional context
    """
    progress = f"[{step_num}/{total_steps}]" if total_steps else f"[Step {step_num}]"
    metadata_str = ""
    if metadata:
        items = [f"{k}={v}" for k, v in metadata.items()]
        metadata_str = f" ({', '.join(items)})"

    logger.info(f"▶️  {progress} {step_name}{metadata_str}")


def log_step_success(
    step_num: int,
    step_name: str,
    total_steps: int | None = None,
    **result_metadata
):
    """Log step success."""
    progress = f"[{step_num}/{total_steps}]" if total_steps else f"[Step {step_num}]"
    metadata_str = ""
    if result_metadata:
        items = [f"{k}={v}" for k, v in result_metadata.items()]
        metadata_str = f" ({', '.join(items)})"

    logger.info(f"✅ {progress} {step_name} - SUCCESS{metadata_str}")


def log_step_failure(
    step_num: int,
    step_name: str,
    error: Exception,
    total_steps: int | None = None,
    **metadata
):
    """Log step failure."""
    progress = f"[{step_num}/{total_steps}]" if total_steps else f"[Step {step_num}]"
    metadata_str = ""
    if metadata:
        items = [f"{k}={v}" for k, v in metadata.items()]
        metadata_str = f" ({', '.join(items)})"

    logger.error(f"❌ {progress} {step_name} - FAILED: {error}{metadata_str}")
