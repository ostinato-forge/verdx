"""Profile definitions — threshold mappings for validation stringency."""

from __future__ import annotations

PROFILES: dict[str, int] = {
    "quick": 60,
    "standard": 80,
    "strict": 95,
}

DEFAULT_PROFILE = "standard"


def resolve_threshold(profile: str) -> int:
    """Return the score threshold for a profile name, or raise ValueError."""
    try:
        return PROFILES[profile]
    except KeyError:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"unknown profile {profile!r} (valid: {valid})") from None
