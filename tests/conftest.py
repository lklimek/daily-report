"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import os
import shutil

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_ai: mark test as requiring a Claude AI backend "
        "(ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, or claude CLI)",
    )


def _ai_backend_available() -> bool:
    """Check whether any AI backend is available for tests."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    # claude CLI is available but cannot be used inside a nested session
    if shutil.which("claude") and not os.environ.get("CLAUDECODE"):
        return True
    return False


def pytest_collection_modifyitems(config, items):
    if _ai_backend_available():
        return
    skip_ai = pytest.mark.skip(
        reason="No AI backend available (need ANTHROPIC_API_KEY, "
        "CLAUDE_CODE_OAUTH_TOKEN, or claude CLI)",
    )
    for item in items:
        if "requires_ai" in item.keywords:
            item.add_marker(skip_ai)
