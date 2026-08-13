"""Version reporting for the tracker MCP server.

The running version comes from the ``TRACKER_VERSION`` environment variable,
which the container image bakes in at build time from the release tag (see the
Dockerfile and the release workflow). Releases are git tags — that is the single
source of truth — so nothing writes a version back into the repository.

When ``TRACKER_VERSION`` is unset (e.g. a local ``uv run`` from source), we fall
back to the installed package metadata, then to a sentinel. Optionally, the
latest published version is fetched from the project's GitHub Releases so the
server can report whether it is up to date.
"""

import enum
import importlib.metadata
import os

import httpx

PACKAGE_NAME = "tracker"

# Env var carrying the running server's version, baked into the image at build
# time from the release tag. This is the authoritative runtime version.
VERSION_ENV = "TRACKER_VERSION"

# owner/repo whose GitHub Releases represent the canonical published versions.
# Overridable so a fork can point the up-to-date check at its own releases.
GITHUB_REPO = os.getenv("TRACKER_GITHUB_REPO", "ryanpeach-homelab/tracker")

# How long to wait on the GitHub Releases API before giving up (seconds).
RELEASE_CHECK_TIMEOUT = float(os.getenv("TRACKER_RELEASE_CHECK_TIMEOUT", "5"))


def current_version() -> str:
    """Return the running tracker version.

    Prefers ``TRACKER_VERSION`` (set in the image from the release tag); falls
    back to the installed package metadata, then to a sentinel for source runs
    where neither is meaningful.
    """
    env_version = os.getenv(VERSION_ENV)
    if env_version:
        return env_version
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


class VersionStatus(enum.Enum):
    """How the running version relates to the latest published release."""

    UP_TO_DATE = enum.auto()
    UPDATE_AVAILABLE = enum.auto()
    AHEAD = enum.auto()


def compare(current: str, latest: str) -> VersionStatus:
    """Compare the running version against the latest release."""
    c, latest_parsed = _parse(current), _parse(latest)
    if c == latest_parsed:
        return VersionStatus.UP_TO_DATE
    if c < latest_parsed:
        return VersionStatus.UPDATE_AVAILABLE
    return VersionStatus.AHEAD
