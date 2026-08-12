"""Version reporting for the tracker MCP server.

The running version is read from the installed package metadata; the single
source of truth is the ``version`` field in ``pyproject.toml`` (baked into the
distribution at build time). Optionally, the latest published version is fetched
from the project's GitHub Releases so the server can report whether it is up to
date.
"""

import importlib.metadata
import os

import httpx

PACKAGE_NAME = "tracker"

# owner/repo whose GitHub Releases represent the canonical published versions.
# Overridable so a fork can point the up-to-date check at its own releases.
GITHUB_REPO = os.getenv("TRACKER_GITHUB_REPO", "ryanpeach-homelab/tracker")

# How long to wait on the GitHub Releases API before giving up (seconds).
RELEASE_CHECK_TIMEOUT = float(os.getenv("TRACKER_RELEASE_CHECK_TIMEOUT", "5"))


def current_version() -> str:
    """Return the installed tracker version, or a sentinel if not installed."""
    try:
        return importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def latest_release() -> str | None:
    """Return the latest GitHub release tag (e.g. ``v0.2.0``).

    Returns ``None`` when no release has been published yet. Raises on network
    or API errors so callers can distinguish "unreachable" from "none yet".
    """
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    resp = httpx.get(
        url,
        timeout=RELEASE_CHECK_TIMEOUT,
        headers={"Accept": "application/vnd.github+json"},
    )
    if resp.status_code == 404:
        # The repo has no published release yet.
        return None
    resp.raise_for_status()
    tag = resp.json().get("tag_name")
    return tag if isinstance(tag, str) else None


def _parse(version: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple of ints.

    Strips a leading ``v`` and any pre-release/build suffix, then reads the
    leading dot-separated numeric components. Non-numeric tails stop parsing so
    e.g. ``0.2.0-rc1`` compares as ``(0, 2, 0)``.
    """
    core = version.lstrip("v").split("+")[0].split("-")[0]
    parts: list[int] = []
    for segment in core.split("."):
        if not segment.isdigit():
            break
        parts.append(int(segment))
    return tuple(parts)


def compare(current: str, latest: str) -> str:
    """Compare two versions, returning 'up_to_date', 'update_available', or 'ahead'."""
    c, latest_parsed = _parse(current), _parse(latest)
    if c == latest_parsed:
        return "up_to_date"
    return "update_available" if c < latest_parsed else "ahead"
