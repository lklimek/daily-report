"""Local git repository operations for commit discovery and PR mapping."""

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class RepoInfo:
    """Information about a locally cloned git repository."""

    path: str  # absolute path to git repo
    org: str  # GitHub org (e.g. "dashpay")
    name: str  # GitHub repo name (e.g. "platform")


@dataclass
class GitCommit:
    """A git commit extracted from local git log."""

    sha: str
    subject: str
    author_email: str
    author_date: str  # ISO format YYYY-MM-DD...


# Pattern for SSH remote URLs: git@github.com:ORG/REPO.git
_SSH_PATTERN = re.compile(r"git@github\.com:([^/]+)/([^/.]+?)(?:\.git)?$")

# Pattern for HTTPS remote URLs: https://github.com/ORG/REPO.git
_HTTPS_PATTERN = re.compile(
    r"https?://github\.com/([^/]+)/([^/.]+?)(?:\.git)?$"
)

# Pattern for PR number in squash-merge commit subjects: (#NNN)
_PR_PATTERN = re.compile(r"\(#(\d+)\)")


def parse_remote_url(url: str) -> tuple[str, str] | None:
    """Parse a git remote URL to extract (org, repo_name).

    Supports SSH and HTTPS GitHub remote URL formats.

    Args:
        url: Git remote URL string.

    Returns:
        Tuple of (org, repo_name) or None if the URL cannot be parsed.
    """
    url = url.strip()
    match = _SSH_PATTERN.match(url)
    if match:
        return match.group(1), match.group(2)
    match = _HTTPS_PATTERN.match(url)
    if match:
        return match.group(1), match.group(2)
    return None


def _get_remote_url(repo_path: str) -> str | None:
    """Get the origin remote URL for a git repository.

    Args:
        repo_path: Absolute path to the git repository.

    Returns:
        The remote URL string, or None on failure.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        print(
            f"Warning: failed to get remote URL for {repo_path}: {e}",
            file=sys.stderr,
        )
    return None


def discover_repos(repos_dir: str, target_org: str) -> list[RepoInfo]:
    """Scan a directory for git repos belonging to a target GitHub organization.

    Scans immediate subdirectories of repos_dir for .git/ directories, reads
    the origin remote URL, and includes only repos whose org matches target_org
    (case-insensitive).

    Args:
        repos_dir: Path to the directory containing git repositories.
        target_org: GitHub organization to filter by (e.g. "dashpay").

    Returns:
        List of RepoInfo for matching repositories.
    """
    repos_dir = os.path.expanduser(repos_dir)
    repos_dir = os.path.abspath(repos_dir)
    if not os.path.isdir(repos_dir):
        print(
            f"Warning: repos directory does not exist: {repos_dir}",
            file=sys.stderr,
        )
        return []

    results: list[RepoInfo] = []
    target_org_lower = target_org.lower()

    try:
        entries = sorted(os.listdir(repos_dir))
    except OSError as e:
        print(
            f"Warning: cannot list directory {repos_dir}: {e}",
            file=sys.stderr,
        )
        return []

    for entry in entries:
        entry_path = os.path.join(repos_dir, entry)

        # Follow symlinks for the entry itself
        if not os.path.isdir(entry_path):
            continue

        # Check for .git directory (also follows symlinks)
        git_dir = os.path.join(entry_path, ".git")
        if not os.path.exists(git_dir):
            continue

        # Resolve symlinks to get the real path for git operations
        real_path = os.path.realpath(entry_path)

        remote_url = _get_remote_url(real_path)
        if not remote_url:
            continue

        parsed = parse_remote_url(remote_url)
        if not parsed:
            continue

        org, name = parsed
        if org.lower() == target_org_lower:
            results.append(RepoInfo(path=real_path, org=org, name=name))

    return results


def _fetch_single_repo(
    repo: RepoInfo, timeout: int
) -> tuple[str, bool]:
    """Fetch a single repo. Returns (repo_name, success)."""
    try:
        subprocess.run(
            ["git", "-C", repo.path, "fetch", "--all", "--quiet"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return repo.name, True
    except subprocess.TimeoutExpired:
        print(
            f"Warning: git fetch timed out for {repo.name} after {timeout}s",
            file=sys.stderr,
        )
        return repo.name, False
    except OSError as e:
        print(
            f"Warning: git fetch failed for {repo.name}: {e}",
            file=sys.stderr,
        )
        return repo.name, False


def fetch_repos(
    repos: list[RepoInfo], timeout: int = 30
) -> dict[str, bool]:
    """Fetch all remotes for a list of repos in parallel.

    Runs ``git fetch --all --quiet`` for each repo using a thread pool.
    Failures are logged to stderr and do not prevent other repos from being
    fetched.

    Args:
        repos: List of RepoInfo to fetch.
        timeout: Maximum seconds to wait for each fetch operation.

    Returns:
        Dict mapping repo name to success boolean.
    """
    if not repos:
        return {}

    results: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=len(repos)) as executor:
        futures = {
            executor.submit(_fetch_single_repo, repo, timeout): repo
            for repo in repos
        }
        for future in as_completed(futures):
            name, success = future.result()
            results[name] = success

    return results


def find_commits(
    repo_path: str,
    author: str,
    date_from: str,
    date_to: str,
    git_emails: list[str] | None = None,
) -> list[GitCommit]:
    """Find commits by an author within a date range using local git log.

    The date range is expanded by 1 day on each side to handle timezone edge
    cases, then filtered precisely in Python using the ISO author date.

    Args:
        repo_path: Absolute path to the git repository.
        author: Git author name or email to search for.
        date_from: Start date in YYYY-MM-DD format (inclusive).
        date_to: End date in YYYY-MM-DD format (inclusive).
        git_emails: Optional list of additional email addresses to search.

    Returns:
        List of unique GitCommit objects, deduplicated by SHA.
    """
    # Expand date range by 1 day on each side for timezone edge cases
    from_dt = datetime.strptime(date_from, "%Y-%m-%d")
    to_dt = datetime.strptime(date_to, "%Y-%m-%d")
    after_date = (from_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    before_date = (to_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    # Collect all authors to search for
    authors = [author]
    if git_emails:
        authors.extend(git_emails)

    seen_shas: set[str] = set()
    commits: list[GitCommit] = []

    for auth in authors:
        raw_commits = _run_git_log(repo_path, auth, after_date, before_date)
        for commit in raw_commits:
            if commit.sha in seen_shas:
                continue
            # Precise date filtering using the ISO author date
            commit_date = commit.author_date[:10]
            if date_from <= commit_date <= date_to:
                seen_shas.add(commit.sha)
                commits.append(commit)

    return commits


def _run_git_log(
    repo_path: str, author: str, after_date: str, before_date: str
) -> list[GitCommit]:
    """Run git log and parse the output into GitCommit objects.

    Args:
        repo_path: Absolute path to the git repository.
        author: Git author to filter by.
        after_date: Expanded after date for git log --after.
        before_date: Expanded before date for git log --before.

    Returns:
        List of parsed GitCommit objects (may contain duplicates).
    """
    cmd = [
        "git",
        "-C",
        repo_path,
        "log",
        "--remotes",
        "--all",
        f"--author={author}",
        "--no-merges",
        f"--after={after_date}",
        f"--before={before_date}",
        "--format=%H|%s|%ae|%aI",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print(
            f"Warning: git log timed out for {repo_path}",
            file=sys.stderr,
        )
        return []
    except OSError as e:
        print(
            f"Warning: git log failed for {repo_path}: {e}",
            file=sys.stderr,
        )
        return []

    if result.returncode != 0:
        print(
            f"Warning: git log returned non-zero for {repo_path}: "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        return []

    commits: list[GitCommit] = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        # Format: SHA|subject|author_email|author_date_ISO
        # Subject may contain '|', so split with maxsplit=3
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        commits.append(
            GitCommit(
                sha=parts[0],
                subject=parts[1],
                author_email=parts[2],
                author_date=parts[3],
            )
        )

    return commits


def extract_pr_numbers(
    commits: list[GitCommit],
) -> tuple[dict[int, list[GitCommit]], list[GitCommit]]:
    """Extract PR numbers from commit subjects.

    Squash-merged commits typically contain the PR number in the format
    ``(#NNN)`` in the commit subject line.

    Args:
        commits: List of GitCommit objects to process.

    Returns:
        A tuple of:
        - dict mapping PR number to list of commits associated with that PR
        - list of commits that could not be mapped to a PR number
    """
    pr_map: dict[int, list[GitCommit]] = {}
    unmapped: list[GitCommit] = []

    for commit in commits:
        match = _PR_PATTERN.search(commit.subject)
        if match:
            pr_number = int(match.group(1))
            pr_map.setdefault(pr_number, []).append(commit)
        else:
            unmapped.append(commit)

    return pr_map, unmapped
