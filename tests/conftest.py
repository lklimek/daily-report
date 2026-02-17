"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_ai: mark test as requiring a Claude AI backend "
        "(ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, or claude-agent-sdk)",
    )


def _ai_backend_available() -> bool:
    """Check whether any AI backend is available for tests."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    # claude-agent-sdk available and not inside a nested Claude Code session
    if not os.environ.get("CLAUDECODE") and _claude_sdk_available():
        return True
    return False


def _claude_sdk_available() -> bool:
    """Check whether claude-agent-sdk is installed and importable."""
    try:
        from claude_agent_sdk import query  # noqa: F401

        return True
    except ImportError:
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
