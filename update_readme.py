"""
update_readme.py - Module for fetching public repositories and updating README.md markers.

Designed for standard library Python 3.10+ / 3.12 with no side-effects on import.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any, List, Optional, Tuple

START_MARKER = "<!-- AUTO-UPDATE:START -->"
END_MARKER = "<!-- AUTO-UPDATE:END -->"
DEFAULT_USERNAME = "divyanshshrivastava"


def fetch_repos(
    username: str, token: Optional[str] = None, per_page: int = 10
) -> List[dict[str, Any]]:
    """
    Fetch the most recently pushed public repositories for a GitHub user.

    Filters out forked repositories and the profile repository itself,
    returning up to the top 5 remaining repositories.
    """
    url = f"https://api.github.com/users/{username}/repos?sort=pushed&per_page={per_page}"
    headers = {
        "User-Agent": f"{username}-profile-updater",
        "Accept": "application/vnd.github.v3+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"[update_readme] HTTP error fetching repos: {e.code} {e.reason}\n")
        raise
    except urllib.error.URLError as e:
        sys.stderr.write(f"[update_readme] Network error fetching repos: {e.reason}\n")
        raise
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[update_readme] Malformed JSON response: {e}\n")
        raise

    if not isinstance(data, list):
        sys.stderr.write("[update_readme] Unexpected API response format (expected a list).\n")
        return []

    filtered: List[dict[str, Any]] = []
    for repo in data:
        if repo.get("fork") is True:
            continue
        repo_name = repo.get("name", "")
        if repo_name.lower() == username.lower():
            # Exclude the profile repo itself
            continue
        filtered.append(repo)
        if len(filtered) == 5:
            break

    return filtered


def build_markdown_block(
    repos: List[dict[str, Any]],
    timestamp: Optional[datetime.datetime] = None,
    run_info: Optional[str] = None,
) -> str:
    """
    Build the markdown block to place between AUTO-UPDATE markers.
    """
    if timestamp is None:
        timestamp = datetime.datetime.now(datetime.timezone.utc)
    elif timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=datetime.timezone.utc)

    lines: List[str] = ["### Recently worked on"]

    if not repos:
        lines.append("- _No recent public repositories found._")
    else:
        for repo in repos:
            name = repo.get("name", "unnamed")
            url = repo.get("html_url", "#")
            desc = repo.get("description")
            desc_text = desc.strip() if desc and desc.strip() else "no description"
            lines.append(f"- [{name}]({url}) - {desc_text}")

    # Format: 16 Oct 2026, 09:35:12 UTC (run 2/3)
    formatted_ts = timestamp.strftime("%d %b %Y, %H:%M:%S UTC")
    if run_info:
        lines.append(f"\n_Last updated: {formatted_ts} ({run_info})_")
    else:
        lines.append(f"\n_Last updated: {formatted_ts}_")

    return "\n".join(lines)


def replace_marker_section(readme_text: str, new_block: str) -> str:
    """
    Replace the text between START_MARKER and END_MARKER, preserving original
    line endings and keeping everything outside the markers byte-for-byte unchanged.
    """
    # Detect line endings
    crlf = "\r\n" in readme_text
    sep = "\r\n" if crlf else "\n"

    # Normalize new_block to target line ending
    normalized_block = new_block.replace("\r\n", "\n").replace("\n", sep)

    # Use regex to find and replace section
    pattern = re.compile(
        re.escape(START_MARKER) + r"(.*?)" + re.escape(END_MARKER),
        flags=re.DOTALL,
    )

    match = pattern.search(readme_text)
    if not match:
        raise ValueError(
            f"Markers not found. Ensure both {START_MARKER} and {END_MARKER} exist in README.md."
        )

    replacement = f"{START_MARKER}{sep}{normalized_block}{sep}{END_MARKER}"
    return readme_text[: match.start()] + replacement + readme_text[match.end() :]


def update_readme_file(
    filepath: str,
    username: Optional[str] = None,
    token: Optional[str] = None,
    timestamp: Optional[datetime.datetime] = None,
    run_info: Optional[str] = None,
) -> bool:
    """
    High-level function to read README.md, fetch repos, replace marker content, and save.
    """
    if not username:
        username = os.getenv("GH_USERNAME", DEFAULT_USERNAME)
    if token is None:
        token = os.getenv("GITHUB_TOKEN")

    if not os.path.isfile(filepath):
        sys.stderr.write(f"[update_readme] File not found: {filepath}\n")
        return False

    with open(filepath, "r", encoding="utf-8", newline="") as f:
        content = f.read()

    # Pre-validate markers before network call
    if START_MARKER not in content or END_MARKER not in content:
        sys.stderr.write(
            f"[update_readme] Error: Markers missing in {filepath}. "
            f"Expected {START_MARKER} and {END_MARKER}.\n"
        )
        return False

    try:
        repos = fetch_repos(username=username, token=token)
    except Exception as e:
        sys.stderr.write(f"[update_readme] Failed to fetch repositories: {e}\n")
        return False

    block = build_markdown_block(repos, timestamp=timestamp, run_info=run_info)

    try:
        updated_content = replace_marker_section(content, block)
    except ValueError as e:
        sys.stderr.write(f"[update_readme] {e}\n")
        return False

    with open(filepath, "w", encoding="utf-8", newline="") as f:
        f.write(updated_content)

    return True


def main() -> None:
    """CLI entrypoint for standalone runs."""
    username = os.getenv("GH_USERNAME", DEFAULT_USERNAME)
    token = os.getenv("GITHUB_TOKEN")
    readme_path = os.path.join(os.getcwd(), "README.md")

    sys.stdout.write(f"[update_readme] Updating {readme_path} for user '{username}'...\n")
    success = update_readme_file(readme_path, username=username, token=token)
    if not success:
        sys.exit(1)
    sys.stdout.write("[update_readme] Successfully updated README.md\n")


if __name__ == "__main__":
    main()
