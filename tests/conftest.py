"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import json
import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_ai: mark test as requiring a Claude AI backend "
        "(ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, or claude-agent-sdk with auth)",
    )


def _ai_backend_available() -> bool:
    """Check whether any AI backend is available for tests."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    # claude-agent-sdk: must be importable, not nested, and have credentials
    if (
        not os.environ.get("CLAUDECODE")
        and _claude_sdk_importable()
        and _claude_has_credentials()
    ):
        return True
    return False


def _claude_sdk_importable() -> bool:
    """Check whether claude-agent-sdk is installed and importable."""
    try:
        from claude_agent_sdk import query  # noqa: F401

        return True
    except ImportError:
        return False


def _claude_has_credentials() -> bool:
    """Check whether Claude Code has stored credentials."""
    creds_path = os.path.expanduser("~/.claude/.credentials.json")
    try:
        with open(creds_path) as f:
            creds = json.load(f)
        token = creds.get("claudeAiOauth", {}).get("accessToken")
        return bool(token)
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return False


def pytest_collection_modifyitems(config, items):
    if _ai_backend_available():
        return
    skip_ai = pytest.mark.skip(
        reason="No AI backend available (need ANTHROPIC_API_KEY, "
        "CLAUDE_CODE_OAUTH_TOKEN, or claude-agent-sdk)",
    )
    for item in items:
        if "requires_ai" in item.keywords:
            item.add_marker(skip_ai)
