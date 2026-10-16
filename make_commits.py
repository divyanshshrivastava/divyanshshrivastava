"""
make_commits.py - Engine for activity-adaptive GitHub profile README commits.

Features:
- Stateless deterministic quiet stretches (replay from 2026-01-01).
- Detection of real GitHub activity today (PushEvents) with fallback to 0 on failure.
- Commit scaling based on real commits and weekend reduction.
- Spread timing within the day (ISO 8601), asserting no dates in the future.
- Double-run guard to prevent exceeding target N on repeated runs.
- Message pool without repetitions.
- Dry-run support and in-memory 60-day simulation.
- Standard library only.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, List, Optional, Tuple

import update_readme

# ---------------------------------------------------------------------------
# Constants & Configuration
# ---------------------------------------------------------------------------
DEFAULT_USERNAME = "divyanshshrivastava"
START_DATE = datetime.date(2026, 1, 1)

# Quiet stretches
QUIET_STRETCH_CHANCE = 0.06       # 6% chance per eligible day
QUIET_STRETCH_MIN_DAYS = 2        # 2 to 4 consecutive days
QUIET_STRETCH_MAX_DAYS = 4
QUIET_STRETCH_COOLDOWN_DAYS = 5   # Must wait >= 5 days after previous stretch ends

# Activity thresholds
REAL_COMMITS_HIGH_THRESHOLD = 3   # >= 3 real commits -> 0 bot commits
REAL_COMMITS_LOW_THRESHOLD = 1    # 1-2 real commits -> cap at 0-2

# Count weights
NORMAL_WEIGHTS = {
    0: 0.20,
    1: 0.25,
    2: 0.25,
    3: 0.15,
    4: 0.10,
    5: 0.05,
}

LOW_ACTIVITY_WEIGHTS = {
    0: 0.40,
    1: 0.40,
    2: 0.20,
}

WEEKEND_REDUCTION = True
WEEKEND_WEIGHTS = {
    0: 0.50,
    1: 0.35,
    2: 0.15,
}

MESSAGE_POOL = [
    "chore: refresh recent repos",
    "docs: update activity section",
    "chore: refresh recent repos and activity",
    "docs: update recently worked on projects",
    "chore: update recent repos and timestamp",
    "docs: refresh recently worked on section",
    "chore: sync latest worked on repos",
    "docs: update profile recent repositories",
    "chore: update activity timestamp",
    "docs: refresh recent public repositories",
    "chore: refresh profile activity section",
    "docs: update recently worked on list",
    "chore: sync recent repositories in readme",
    "docs: update recent repos showcase",
]


# ---------------------------------------------------------------------------
# Stateless Quiet Stretch Engine (Deterministic Replay)
# ---------------------------------------------------------------------------
def simulate_quiet_history(
    target_day: datetime.date,
) -> Tuple[bool, Optional[datetime.date], Optional[datetime.date]]:
    """
    Deterministically walk day-by-day from START_DATE up to target_day.
    Returns:
        (is_quiet, quiet_until, last_end)
    """
    if target_day < START_DATE:
        return False, None, None

    quiet_until: Optional[datetime.date] = None
    last_end: Optional[datetime.date] = None

    cur = START_DATE
    while cur <= target_day:
        rng = random.Random(f"quiet-{cur.isoformat()}")

        if quiet_until is not None and cur <= quiet_until:
            # cur is within an ongoing quiet stretch
            pass
        else:
            # Outside of stretch, check cooldown
            if last_end is None or (cur - last_end).days >= QUIET_STRETCH_COOLDOWN_DAYS:
                if rng.random() < QUIET_STRETCH_CHANCE:
                    length = rng.randint(QUIET_STRETCH_MIN_DAYS, QUIET_STRETCH_MAX_DAYS)
                    quiet_until = cur + datetime.timedelta(days=length - 1)
                    last_end = quiet_until

        if cur == target_day:
            is_quiet = quiet_until is not None and cur <= quiet_until
            return is_quiet, quiet_until, last_end

        cur += datetime.timedelta(days=1)

    return False, quiet_until, last_end


def is_quiet_day(target_day: datetime.date) -> bool:
    """Check if target_day is inside a quiet stretch."""
    is_quiet, _, _ = simulate_quiet_history(target_day)
    return is_quiet


# ---------------------------------------------------------------------------
# Real Activity Detection
# ---------------------------------------------------------------------------
def get_real_commits_today(
    username: str,
    token: Optional[str] = None,
    today_utc: Optional[datetime.date] = None,
) -> int:
    """
    Query the GitHub Events API to count today's real commits in PushEvents,
    excluding pushes to the profile repo itself.
    Falls back cleanly to 0 on any network/API failure.
    Supports FAKE_REAL_COMMITS environment variable override for testing.
    """
    override = os.getenv("FAKE_REAL_COMMITS")
    if override is not None:
        try:
            return int(override)
        except ValueError:
            sys.stderr.write(f"[make_commits] Invalid FAKE_REAL_COMMITS value: '{override}'. Using 0.\n")
            return 0

    if today_utc is None:
        today_utc = datetime.datetime.now(datetime.timezone.utc).date()

    today_str = today_utc.isoformat()
    url = f"https://api.github.com/users/{username}/events/public"
    headers = {
        "User-Agent": f"{username}-profile-updater",
        "Accept": "application/vnd.github.v3+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            events = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"[make_commits] Warning: HTTP error fetching events ({e.code} {e.reason}). Falling back to 0.\n")
        return 0
    except urllib.error.URLError as e:
        sys.stderr.write(f"[make_commits] Warning: Network error fetching events ({e.reason}). Falling back to 0.\n")
        return 0
    except (json.JSONDecodeError, TimeoutError, OSError) as e:
        sys.stderr.write(f"[make_commits] Warning: Failed parsing events ({e}). Falling back to 0.\n")
        return 0

    if not isinstance(events, list):
        sys.stderr.write("[make_commits] Warning: Events API returned non-list. Falling back to 0.\n")
        return 0

    real_commits = 0
    profile_repo_suffix = f"/{username}".lower()

    for event in events:
        if event.get("type") != "PushEvent":
            continue

        created_at = event.get("created_at", "")
        if not created_at.startswith(today_str):
            continue

        repo_name = event.get("repo", {}).get("name", "").lower()
        if repo_name.endswith(profile_repo_suffix):
            # Exclude profile repository pushes
            continue

        payload = event.get("payload", {})
        commits = payload.get("commits", [])
        if isinstance(commits, list) and commits:
            real_commits += len(commits)
        else:
            real_commits += payload.get("size", 1)

    return real_commits


# ---------------------------------------------------------------------------
# Commit Count & Timing Determination
# ---------------------------------------------------------------------------
def weighted_sample(weights_dict: dict[int, float], rng: random.Random) -> int:
    """Sample an integer key from weights_dict using rng."""
    population = list(weights_dict.keys())
    weights = list(weights_dict.values())
    return rng.choices(population, weights=weights, k=1)[0]


def calculate_target_commits(
    today: datetime.date, real_commits: int, forced_n: Optional[int] = None
) -> int:
    """
    Calculate deterministic target commit count N for the day.
    """
    if forced_n is not None:
        return forced_n

    if is_quiet_day(today):
        return 0

    if real_commits >= REAL_COMMITS_HIGH_THRESHOLD:
        return 0

    # Deterministic RNG for this day
    day_rng = random.Random(f"target-{today.isoformat()}")

    if real_commits >= REAL_COMMITS_LOW_THRESHOLD:
        return weighted_sample(LOW_ACTIVITY_WEIGHTS, day_rng)

    # 0 real commits
    is_weekend = today.weekday() in (5, 6)
    if WEEKEND_REDUCTION and is_weekend:
        return weighted_sample(WEEKEND_WEIGHTS, day_rng)

    return weighted_sample(NORMAL_WEIGHTS, day_rng)


def generate_commit_timestamps(
    today: datetime.date,
    count: int,
    now_utc: datetime.datetime,
    rng: random.Random,
    parent_dt: Optional[datetime.datetime] = None,
) -> Tuple[List[datetime.datetime], int]:
    """
    Generate sorted timestamps strictly after parent_dt (if parent was authored today)
    and strictly before or at now_utc.

    If insufficient time remains between parent_dt and now_utc, reduces count rather
    than backdating before parent. Never generates timestamps in the future.

    Returns:
        (sorted_timestamps, adjusted_count)
    """
    if count <= 0:
        return [], 0

    midnight_utc = datetime.datetime.combine(today, datetime.time(0, 0, 0), tzinfo=datetime.timezone.utc)

    # If parent was authored today in UTC, new commits must be strictly after parent_dt
    if parent_dt is not None and parent_dt.tzinfo is not None and parent_dt.astimezone(datetime.timezone.utc).date() == today:
        start_dt = max(midnight_utc, parent_dt.astimezone(datetime.timezone.utc) + datetime.timedelta(seconds=1))
    else:
        start_dt = midnight_utc

    end_dt = now_utc

    if start_dt >= end_dt:
        # No time remains after parent commit today
        return [], 0

    available_seconds = int((end_dt - start_dt).total_seconds())
    if available_seconds <= 0:
        return [], 0

    # If remaining span is smaller than requested count, reduce count rather than backdating
    actual_count = min(count, available_seconds)

    if available_seconds > actual_count:
        chosen_offsets = sorted(rng.sample(range(available_seconds), actual_count))
    else:
        chosen_offsets = list(range(actual_count))

    timestamps: List[datetime.datetime] = []
    for offset in chosen_offsets:
        dt = start_dt + datetime.timedelta(seconds=offset)
        assert dt <= now_utc, f"Generated timestamp {dt} cannot be in the future (now: {now_utc})"
        if parent_dt is not None and parent_dt.astimezone(datetime.timezone.utc).date() == today:
            assert dt > parent_dt, f"Generated timestamp {dt} must be after parent commit {parent_dt}"
        timestamps.append(dt)

    return timestamps, actual_count


# ---------------------------------------------------------------------------
# Git Helpers & Double-Run Guard
# ---------------------------------------------------------------------------
def run_cmd(args: List[str], env: Optional[dict[str, str]] = None) -> str:
    """Run a shell command, return stdout, or raise RuntimeError if exit code != 0."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    proc = subprocess.run(args, capture_output=True, text=True, env=merged_env)
    if proc.returncode != 0:
        err_msg = (
            f"Command failed (code {proc.returncode}): {' '.join(args)}\n"
            f"stdout: {proc.stdout.strip()}\n"
            f"stderr: {proc.stderr.strip()}"
        )
        raise RuntimeError(err_msg)
    return proc.stdout.strip()


def get_existing_commits_info_today(
    today: datetime.date,
) -> Tuple[int, Optional[datetime.datetime]]:
    """
    Count commits whose author date in UTC falls on `today`.
    Also return the author datetime in UTC of the most recent commit (HEAD).
    """
    try:
        output = run_cmd(["git", "log", "--format=%at"])
    except RuntimeError:
        return 0, None

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return 0, None

    # Latest commit (HEAD) author datetime in UTC
    latest_ts = int(lines[0])
    latest_dt = datetime.datetime.fromtimestamp(latest_ts, tz=datetime.timezone.utc)

    # Count commits authored today in UTC
    count_today = 0
    for line in lines:
        ts = int(line)
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        if dt.date() == today:
            count_today += 1

    return count_today, latest_dt


# ---------------------------------------------------------------------------
# 60-Day In-Memory Simulation
# ---------------------------------------------------------------------------
def simulate_60_days(start_date: Optional[datetime.date] = None) -> None:
    """
    Run a full 60-day in-memory simulation in DRY_RUN mode.
    Outputs commit-per-day distribution, quiet stretches, and asserts constraints.
    """
    if start_date is None:
        start_date = datetime.datetime.now(datetime.timezone.utc).date()

    print(f"\n================ 60-DAY IN-MEMORY SIMULATION ================")
    print(f"Simulating 60 days starting from {start_date.isoformat()}...\n")

    distribution: dict[int, int] = {i: 0 for i in range(6)}
    quiet_stretches: List[Tuple[datetime.date, datetime.date, int]] = []
    current_stretch_start: Optional[datetime.date] = None
    last_stretch_day: Optional[datetime.date] = None

    for day_offset in range(60):
        d = start_date + datetime.timedelta(days=day_offset)
        quiet = is_quiet_day(d)

        if quiet:
            distribution[0] += 1
            if current_stretch_start is None:
                current_stretch_start = d
            last_stretch_day = d
        else:
            if current_stretch_start is not None and last_stretch_day is not None:
                length = (last_stretch_day - current_stretch_start).days + 1
                quiet_stretches.append((current_stretch_start, last_stretch_day, length))
                current_stretch_start = None
                last_stretch_day = None

            # 0 real commits simulated for baseline distribution
            n = calculate_target_commits(d, real_commits=0)
            distribution[n] += 1

    if current_stretch_start is not None and last_stretch_day is not None:
        length = (last_stretch_day - current_stretch_start).days + 1
        quiet_stretches.append((current_stretch_start, last_stretch_day, length))

    print("Commit Distribution Across 60 Days:")
    for count, days in sorted(distribution.items()):
        bar = "#" * days
        pct = (days / 60) * 100
        print(f"  {count} commits/day: {days:2d} days ({pct:4.1f}%) | {bar}")

    print(f"\nDetected Quiet Stretches ({len(quiet_stretches)} stretches):")
    prev_end: Optional[datetime.date] = None
    for start, end, length in quiet_stretches:
        gap_str = f"(gap: {(start - prev_end).days} days)" if prev_end else "(first stretch)"
        print(f"  - {start.isoformat()} to {end.isoformat()} ({length} days) {gap_str}")

        # Assertions
        assert QUIET_STRETCH_MIN_DAYS <= length <= QUIET_STRETCH_MAX_DAYS, (
            f"Stretch {start}..{end} length {length} violates [{QUIET_STRETCH_MIN_DAYS}, {QUIET_STRETCH_MAX_DAYS}]"
        )
        if prev_end is not None:
            gap = (start - prev_end).days
            assert gap >= QUIET_STRETCH_COOLDOWN_DAYS, (
                f"Stretch {start}..{end} started only {gap} days after previous stretch ended (cooldown {QUIET_STRETCH_COOLDOWN_DAYS})"
            )
        prev_end = end

    print("\nAll constraints verified successfully:")
    print("  [PASS] No stretch is shorter than 2 or longer than 4 days.")
    print("  [PASS] At least 5 days between consecutive quiet stretches.")
    print("=============================================================\n")


# ---------------------------------------------------------------------------
# Main Runner
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-commit generator for GitHub profile README.")
    parser.add_argument("--dry-run", action="store_true", help="Calculate decisions without git commits/push.")
    parser.add_argument("--simulate-days", type=int, default=0, help="Run an in-memory N-day simulation.")
    parser.add_argument("--force-n", type=int, default=None, help="Force N commits (for testing).")
    parser.add_argument("--repo-path", type=str, default=".", help="Target repository directory.")
    args = parser.parse_args()

    dry_run = args.dry_run or (os.getenv("DRY_RUN", "0") in ("1", "true", "True"))

    if args.simulate_days > 0:
        simulate_60_days()
        return

    repo_dir = os.path.abspath(args.repo_path)
    readme_path = os.path.join(repo_dir, "README.md")
    username = os.getenv("GH_USERNAME", DEFAULT_USERNAME)
    token = os.getenv("GITHUB_TOKEN")

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today = now_utc.date()

    # 1. Quiet stretch status
    is_quiet, quiet_until, last_end = simulate_quiet_history(today)
    quiet_status_str = f"Active (until {quiet_until.isoformat()})" if is_quiet else f"Inactive (last ended: {last_end.isoformat() if last_end else 'None'})"

    # 2. Real commits today
    real_commits = get_real_commits_today(username=username, token=token, today_utc=today)

    # 3. Determine target N
    target_n = calculate_target_commits(today=today, real_commits=real_commits, forced_n=args.force_n)

    # 4. Check existing commits today (Double-run guard and parent commit date)
    existing_commits = 0
    latest_commit_dt: Optional[datetime.datetime] = None
    if not dry_run and os.path.isdir(os.path.join(repo_dir, ".git")):
        orig_cwd = os.getcwd()
        os.chdir(repo_dir)
        try:
            existing_commits, latest_commit_dt = get_existing_commits_info_today(today)
        finally:
            os.chdir(orig_cwd)

    requested_commits = max(0, target_n - existing_commits)

    # 5. Generate sorted timestamps strictly after parent commit and not in future
    day_rng = random.Random(f"run-{today.isoformat()}")
    timestamps, commits_to_make = generate_commit_timestamps(
        today, requested_commits, now_utc, day_rng, parent_dt=latest_commit_dt
    )
    commit_messages = day_rng.sample(MESSAGE_POOL, commits_to_make) if commits_to_make > 0 else []

    # One-line logging summary
    print(
        f"[bot] Real commits: {real_commits} | "
        f"Quiet stretch: {quiet_status_str} | "
        f"Target N: {target_n} | "
        f"Existing today: {existing_commits} | "
        f"Bot commits to make: {commits_to_make}"
    )

    if commits_to_make <= 0:
        print("[bot] No commits required today. Exiting cleanly.")
        return

    if dry_run:
        print(f"[dry-run] Planned {commits_to_make} commit(s):")
        for i, (ts, msg) in enumerate(zip(timestamps, commit_messages), start=1):
            run_idx = existing_commits + i
            print(f"  Commit {i}/{commits_to_make} (run {run_idx}/{target_n}):")
            print(f"    Timestamp: {ts.isoformat()}")
            print(f"    Message:   {msg}")
        return

    # 6. Execute Commits
    orig_cwd = os.getcwd()
    os.chdir(repo_dir)
    try:
        # Pre-fetch repos once or refresh per commit
        for i, (ts, msg) in enumerate(zip(timestamps, commit_messages), start=1):
            run_idx = existing_commits + i
            run_info = f"run {run_idx}/{target_n}"

            # Update README with current run info and timestamp
            success = update_readme.update_readme_file(
                readme_path, username=username, token=token, timestamp=ts, run_info=run_info
            )
            if not success:
                raise RuntimeError(f"Failed to update {readme_path}")

            # Git add and commit with specific author & committer date
            run_cmd(["git", "add", "README.md"])

            # Check if there is a diff
            try:
                run_cmd(["git", "diff", "--cached", "--quiet"])
                print(f"[bot] Notice: No diff detected for commit {i}/{commits_to_make}. Skipping empty commit.")
                continue
            except RuntimeError:
                # Diff exists (exit code != 0 from --quiet), proceed to commit
                pass

            iso_date = ts.isoformat()
            env = {
                "GIT_AUTHOR_DATE": iso_date,
                "GIT_COMMITTER_DATE": iso_date,
            }
            run_cmd(["git", "commit", "-m", msg], env=env)
            print(f"[bot] Created commit {run_idx}/{target_n}: '{msg}' @ {iso_date}")

        # 8. Single final push
        print("[bot] Pushing commits to remote...")
        run_cmd(["git", "push"])
        print("[bot] All commits pushed successfully.")

    except Exception as e:
        sys.stderr.write(f"[bot] Git operation failed: {e}\n")
        sys.stderr.write("[bot] Stopping immediately without further push.\n")
        sys.exit(1)
    finally:
        os.chdir(orig_cwd)


if __name__ == "__main__":
    main()
