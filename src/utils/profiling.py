from contextlib import nullcontext


def tag_wrapper(tags: dict):
    """
    Return a pyroscope tag_wrapper context manager.

    If pyroscope is not installed or not initialised, returns a no-op
    nullcontext() so the calling code is unchanged.
    """
    try:
        from src.services.pyroscope_config import _initialized

        if not _initialized:
            return nullcontext()

        import pyroscope  # noqa: PLC0415

        return pyroscope.tag_wrapper(tags)
    except Exception:
        return nullcontext()
