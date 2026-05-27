from contextlib import nullcontext


def tag_wrapper(tags: dict):
    """No-op tag_wrapper kept for call-site compatibility after Pyroscope removal."""
    return nullcontext()
