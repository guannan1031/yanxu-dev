def paginate(items, page, size):
    """Return a one-based page; reject non-positive page and size."""
    if page < 1 or size < 1:
        raise ValueError("page and size must be positive")
    start = (page - 1) * size
    return items[start:start + size]
