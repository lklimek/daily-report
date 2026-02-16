"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_ai: mark test as requiring Claude Code (skipped when "
        "CLAUDE_CODE_OAUTH_TOKEN is not set)",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return
    skip_ai = pytest.mark.skip(
        reason="CLAUDE_CODE_OAUTH_TOKEN not set — skipping AI test",
    )
    for item in items:
        if "requires_ai" in item.keywords:
            item.add_marker(skip_ai)
