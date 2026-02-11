"""Configuration loader for daily-report.

Reads YAML configuration from ~/.config/daily-report/repos.yaml (or a custom
path) and provides dataclasses for repo and application configuration.

Requires PyYAML (pip install pyyaml).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


# Default bots to exclude from reviewer lists, matching AI_BOTS in daily_report.py
DEFAULT_EXCLUDED_BOTS: list[str] = [
    "coderabbitai",
    "copilot-pull-request-reviewer",
    "github-actions",
    "copilot-swe-agent",
]

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/daily-report/repos.yaml")

# Patterns for extracting org/name from git remote URLs
_SSH_REMOTE_RE = re.compile(r"git@[^:]+:([^/]+)/([^/]+?)(?:\.git)?$")
_HTTPS_REMOTE_RE = re.compile(r"https?://[^/]+/([^/]+)/([^/]+?)(?:\.git)?$")


@dataclass
class RepoConfig:
    """Configuration for a single local git repository."""

    path: str
    org: str = ""
    name: str = ""


@dataclass
class Config:
    """Top-level application configuration."""

    repos: List[RepoConfig] = field(default_factory=list)
    default_org: str = ""
    default_user: str = ""
    git_emails: List[str] = field(default_factory=list)
    excluded_bots: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_BOTS))
    repos_dir: str = ""


def parse_remote_url(url: str) -> tuple[str, str]:
    """Extract (org, name) from a git remote URL.

    Supports SSH (git@github.com:org/repo.git) and HTTPS
    (https://github.com/org/repo.git) formats.

    Returns:
        A tuple of (org, name). Both empty strings if the URL cannot be parsed.
    """
    for pattern in (_SSH_REMOTE_RE, _HTTPS_REMOTE_RE):
        match = pattern.match(url)
        if match:
            return match.group(1), match.group(2)
    return "", ""


def _detect_org_name(repo_path: str) -> tuple[str, str]:
    """Auto-detect org and name from the git remote 'origin' URL.

    Args:
        repo_path: Absolute path to the git repository.

    Returns:
        A tuple of (org, name). Both empty strings on failure.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        url = result.stdout.strip()
        if url:
            return parse_remote_url(url)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return "", ""


def _expand_path(path: str) -> str:
    """Expand ~ and environment variables in a path."""
    return os.path.expanduser(os.path.expandvars(path))


def _validate_repo(raw: dict, default_org: str) -> Optional[RepoConfig]:
    """Validate and build a RepoConfig from a raw YAML dict entry.

    Args:
        raw: Dictionary from the YAML repos list.
        default_org: Fallback organization name.

    Returns:
        A RepoConfig instance, or None if the entry is invalid (missing path).
    """
    if not isinstance(raw, dict):
        return None

    path = raw.get("path", "")
    if not path:
        return None

    path = _expand_path(path)

    org = raw.get("org", "")
    name = raw.get("name", "")

    # Auto-detect org and name from remote URL when not specified
    if not org or not name:
        detected_org, detected_name = _detect_org_name(path)
        if not org:
            org = detected_org or default_org
        if not name:
            name = detected_name

    return RepoConfig(path=path, org=org, name=name)


def load_config(config_path: Optional[str] = None) -> Config:
    """Load configuration from a YAML file.

    Args:
        config_path: Path to the YAML config file. Defaults to
            ~/.config/daily-report/repos.yaml.

    Returns:
        A Config instance. If the config file does not exist, returns a
        default Config with empty repos list (graceful degradation).
    """
    path = _expand_path(config_path) if config_path else DEFAULT_CONFIG_PATH

    if not os.path.isfile(path):
        return Config()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        return Config()

    default_org = data.get("default_org", "")
    default_user = data.get("default_user", "")
    git_emails = data.get("git_emails", [])
    repos_dir = data.get("repos_dir", "")

    if repos_dir:
        repos_dir = _expand_path(repos_dir)

    if not isinstance(git_emails, list):
        git_emails = []

    excluded_bots = data.get("excluded_bots")
    if excluded_bots is None:
        excluded_bots = list(DEFAULT_EXCLUDED_BOTS)
    elif not isinstance(excluded_bots, list):
        excluded_bots = list(DEFAULT_EXCLUDED_BOTS)

    repos: List[RepoConfig] = []
    raw_repos = data.get("repos", [])
    if isinstance(raw_repos, list):
        for raw in raw_repos:
            repo = _validate_repo(raw, default_org)
            if repo is not None:
                repos.append(repo)

    return Config(
        repos=repos,
        default_org=default_org,
        default_user=default_user,
        git_emails=git_emails,
        excluded_bots=excluded_bots,
        repos_dir=repos_dir,
    )
