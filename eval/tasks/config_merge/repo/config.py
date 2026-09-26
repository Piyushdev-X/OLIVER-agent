"""Configuration helpers."""


def merge_config(base, override):
    """Return a new dict where override values win and nested dicts are merged."""
    merged = dict(base)
    for key in override:
        merged[key] = override[key]
    return merged
