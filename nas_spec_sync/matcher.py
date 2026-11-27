"""Simple Wildcard/Glob and substring pattern matching."""

import re
from typing import Iterable


def _glob_to_regex(pattern: str, case_sensitive: bool) -> re.Pattern:
    """Convert a simple wildcard pattern (* and ?) into a compiled fullmatch regex.

    Brackets like [100] are escaped literally so folder names such as
    '[100]20261120_update' do not break glob matching.
    """
    parts = []
    for ch in pattern:
        if ch == "*":
            parts.append(".*")
        elif ch == "?":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile("^" + "".join(parts) + "$", flags)


def match_pattern(name: str, pattern: str, case_sensitive: bool = False) -> bool:
    """Match a name against a simple pattern.

    - If the pattern contains '*' or '?', performs wildcard full-name matching.
    - Otherwise, performs substring matching (checks if name contains pattern).
    """
    if not pattern:
        return False

    if "*" in pattern or "?" in pattern:
        return _glob_to_regex(pattern, case_sensitive).match(name) is not None

    if case_sensitive:
        return pattern in name
    return pattern.lower() in name.lower()


def match_any(name: str, patterns: Iterable[str], case_sensitive: bool = False) -> bool:
    """Return True if name matches at least one pattern."""
    return any(match_pattern(name, p, case_sensitive=case_sensitive) for p in patterns)
