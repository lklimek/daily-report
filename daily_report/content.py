"""Content preparation layer for daily-report.

Transforms raw PR lists (authored_prs, reviewed_prs, waiting_prs) into
renderer-agnostic RepoContent structures. Supports two modes:

- prepare_default_content(): groups PRs by repo with semantic ContentItems
- prepare_consolidated_content(): AI-powered summarisation via Claude API
- prepare_ai_summary(): AI-powered one-line summary (<320 chars)

Authentication for consolidation (resolution order):
1. ANTHROPIC_API_KEY env var  → uses anthropic Python SDK directly
2. claude-agent-sdk          → uses whatever auth Claude Code has configured
   (subscription, CLAUDE_CODE_OAUTH_TOKEN, etc.)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger("daily_report.content")

from daily_report.report_data import (
    AuthoredPR,
    ContentBlock,
    ContentItem,
    RepoContent,
    ReportData,
    ReviewedPR,
    WaitingPR,
)

_SUMMARY_FORMAT = (
    "Max 200 characters. "
    "Return ONLY the summary text, nothing else — no quotes, no labels, no JSON."
)

_CONSOLIDATION_FORMAT = (
    "Reference PR numbers. Return valid JSON only, no markdown fences, no explanation. "
    "Preserve the exact group and subgroup keys from the input in your output. "
    'Format: {"group": {"subgroup": [{"title": "summary line", "numbers": [1,2,3]}, ...]}, ...}. '
    "A JSON schema for the expected output will be provided alongside the data."
)


_prompt_cache: dict[str, str] = {}


def _load_prompt(name: str) -> str:
    """Load and cache a behavioral prompt from ``prompts/{name}.md``."""
    if name not in _prompt_cache:
        prompt_path = Path(__file__).parent / "prompts" / f"{name}.md"
        with open(prompt_path) as f:
            _prompt_cache[name] = f.read().strip()
    return _prompt_cache[name]



def _dedup_pr_lists(
    report: ReportData,
) -> tuple[list[AuthoredPR], list[ReviewedPR], list[WaitingPR]]:
    """Deduplicate PR lists by (repo, number) with priority: waiting > authored > reviewed.

    Returns:
        Tuple of (authored_prs, reviewed_prs, waiting_prs) with duplicates removed.
    """
    waiting_keys = {(pr.repo, pr.number) for pr in report.waiting_prs}

    authored_prs = [pr for pr in report.authored_prs
                    if (pr.repo, pr.number) not in waiting_keys]
    authored_keys = {(pr.repo, pr.number) for pr in authored_prs}

    reviewed_prs = [pr for pr in report.reviewed_prs
                    if (pr.repo, pr.number) not in waiting_keys
                    and (pr.repo, pr.number) not in authored_keys]

    return authored_prs, reviewed_prs, report.waiting_prs


def prepare_default_content(report: ReportData) -> list[RepoContent]:
    """Build RepoContent list from raw PR lists, grouped by repo.

    Args:
        report: Complete report data with populated PR lists.

    Returns:
        Alphabetically sorted list of RepoContent objects.
    """
    authored_prs, reviewed_prs, waiting_prs = _dedup_pr_lists(report)

    # Group PRs by repo
    authored_by_repo: dict[str, list] = defaultdict(list)
    reviewed_by_repo: dict[str, list] = defaultdict(list)
    waiting_by_repo: dict[str, list] = defaultdict(list)

    for pr in authored_prs:
        authored_by_repo[pr.repo].append(pr)
    for pr in reviewed_prs:
        reviewed_by_repo[pr.repo].append(pr)
    for pr in waiting_prs:
        waiting_by_repo[pr.repo].append(pr)

    all_repos = sorted(
        set(authored_by_repo) | set(reviewed_by_repo) | set(waiting_by_repo)
    )

    result: list[RepoContent] = []
    for repo_name in all_repos:
        blocks: list[ContentBlock] = []

        # Authored / Contributed
        authored = authored_by_repo.get(repo_name, [])
        if authored:
            items: list[ContentItem] = []
            for pr in authored:
                items.append(ContentItem(
                    title=pr.title,
                    numbers=[pr.number],
                    status=pr.status,
                    additions=pr.additions,
                    deletions=pr.deletions,
                    author=pr.original_author if pr.contributed else "",
                ))
            blocks.append(ContentBlock(heading="Authored / Contributed", items=items))

        # Reviewed
        reviewed = reviewed_by_repo.get(repo_name, [])
        if reviewed:
            items = []
            for pr in reviewed:
                items.append(ContentItem(
                    title=pr.title,
                    numbers=[pr.number],
                    status=pr.status,
                    author=pr.author,
                ))
            blocks.append(ContentBlock(heading="Reviewed", items=items))

        # Waiting for Review
        waiting = waiting_by_repo.get(repo_name, [])
        if waiting:
            items = []
            for pr in waiting:
                items.append(ContentItem(
                    title=pr.title,
                    numbers=[pr.number],
                    reviewers=list(pr.reviewers),
                    days_waiting=pr.days_waiting,
                ))
            blocks.append(ContentBlock(heading="Waiting for Review", items=items))

        if blocks:
            result.append(RepoContent(repo_name=repo_name, blocks=blocks))

    return result


_STATUS_ORDER = ["Open", "Draft", "Merged", "Closed", "Waiting for Review"]


def regroup_content(report: ReportData, group_by: str = "contribution") -> list[RepoContent]:
    """Regroup content by the specified grouping mode.

    Args:
        report: Complete report data with populated PR lists.
        group_by: Grouping mode — "project", "status", or "contribution".

    Returns:
        List of RepoContent objects organized by the requested grouping.
    """
    if group_by == "project":
        return _regroup_by_project(report)
    elif group_by == "status":
        return _regroup_by_status(report)
    else:
        return _regroup_by_contribution(report)


def _make_authored_item(pr) -> ContentItem:
    """Create a ContentItem from an AuthoredPR."""
    return ContentItem(
        title=pr.title,
        numbers=[pr.number],
        status=pr.status,
        additions=pr.additions,
        deletions=pr.deletions,
        author=pr.original_author if pr.contributed else "",
    )


def _make_reviewed_item(pr) -> ContentItem:
    """Create a ContentItem from a ReviewedPR."""
    return ContentItem(
        title=pr.title,
        numbers=[pr.number],
        status=pr.status,
        author=pr.author,
    )


def _make_waiting_item(pr) -> ContentItem:
    """Create a ContentItem from a WaitingPR."""
    return ContentItem(
        title=pr.title,
        numbers=[pr.number],
        reviewers=list(pr.reviewers),
        days_waiting=pr.days_waiting,
    )


def _regroup_by_contribution(report: ReportData) -> list[RepoContent]:
    """Group by contribution type, then by project within each type."""
    authored_prs, reviewed_prs, waiting_prs = _dedup_pr_lists(report)
    result: list[RepoContent] = []

    # Authored / Contributed
    authored_by_repo: dict[str, list[ContentItem]] = defaultdict(list)
    for pr in authored_prs:
        authored_by_repo[pr.repo].append(_make_authored_item(pr))
    if authored_by_repo:
        blocks = [
            ContentBlock(heading=repo, items=items)
            for repo, items in sorted(authored_by_repo.items())
        ]
        result.append(RepoContent(repo_name="Authored / Contributed", blocks=blocks))

    # Reviewed
    reviewed_by_repo: dict[str, list[ContentItem]] = defaultdict(list)
    for pr in reviewed_prs:
        reviewed_by_repo[pr.repo].append(_make_reviewed_item(pr))
    if reviewed_by_repo:
        blocks = [
            ContentBlock(heading=repo, items=items)
            for repo, items in sorted(reviewed_by_repo.items())
        ]
        result.append(RepoContent(repo_name="Reviewed", blocks=blocks))

    # Waiting for Review
    waiting_by_repo: dict[str, list[ContentItem]] = defaultdict(list)
    for pr in waiting_prs:
        waiting_by_repo[pr.repo].append(_make_waiting_item(pr))
    if waiting_by_repo:
        blocks = [
            ContentBlock(heading=repo, items=items)
            for repo, items in sorted(waiting_by_repo.items())
        ]
        result.append(RepoContent(repo_name="Waiting for Review", blocks=blocks))

    return result


def _regroup_by_project(report: ReportData) -> list[RepoContent]:
    """Group by project, then by status within each project."""
    authored_prs, reviewed_prs, waiting_prs = _dedup_pr_lists(report)

    # Collect all PRs by (repo, status)
    repo_status: dict[str, dict[str, list[ContentItem]]] = defaultdict(lambda: defaultdict(list))

    for pr in authored_prs:
        repo_status[pr.repo][pr.status].append(_make_authored_item(pr))

    for pr in reviewed_prs:
        repo_status[pr.repo][pr.status].append(_make_reviewed_item(pr))

    for pr in waiting_prs:
        repo_status[pr.repo]["Waiting for Review"].append(_make_waiting_item(pr))

    result: list[RepoContent] = []
    for repo in sorted(repo_status):
        blocks: list[ContentBlock] = []
        statuses = repo_status[repo]
        for status in _STATUS_ORDER:
            items = statuses.get(status)
            if items:
                blocks.append(ContentBlock(heading=status, items=items))
        if blocks:
            result.append(RepoContent(repo_name=repo, blocks=blocks))

    return result


def _regroup_by_status(report: ReportData) -> list[RepoContent]:
    """Group by status, then by project within each status."""
    authored_prs, reviewed_prs, waiting_prs = _dedup_pr_lists(report)

    # Collect all PRs by (status, repo)
    status_repo: dict[str, dict[str, list[ContentItem]]] = defaultdict(lambda: defaultdict(list))

    for pr in authored_prs:
        item = _make_authored_item(pr)
        # Clear status on item since the parent group IS the status
        item.status = ""
        status_repo[pr.status][pr.repo].append(item)

    for pr in reviewed_prs:
        item = _make_reviewed_item(pr)
        item.status = ""
        status_repo[pr.status][pr.repo].append(item)

    for pr in waiting_prs:
        status_repo["Waiting for Review"][pr.repo].append(_make_waiting_item(pr))

    result: list[RepoContent] = []
    for status in _STATUS_ORDER:
        repos = status_repo.get(status)
        if not repos:
            continue
        blocks = [
            ContentBlock(heading=repo, items=items)
            for repo, items in sorted(repos.items())
        ]
        result.append(RepoContent(repo_name=status, blocks=blocks))

    return result


def _serialize_grouped_content(content: list[RepoContent]) -> dict:
    """Convert RepoContent list to a nested dict for AI input.

    Each RepoContent.repo_name → top key, each ContentBlock.heading → sub-key,
    each ContentItem → serialized dict with non-empty fields only.
    """
    result: dict = {}
    for rc in content:
        group: dict = {}
        for block in rc.blocks:
            items: list[dict] = []
            for item in block.items:
                entry: dict = {"title": item.title}
                if item.numbers:
                    entry["numbers"] = item.numbers
                if item.status:
                    entry["status"] = item.status
                if item.additions:
                    entry["additions"] = item.additions
                if item.deletions:
                    entry["deletions"] = item.deletions
                if item.author:
                    entry["author"] = item.author
                if item.reviewers:
                    entry["reviewers"] = item.reviewers
                if item.days_waiting:
                    entry["days_waiting"] = item.days_waiting
                items.append(entry)
            if items:
                group[block.heading] = items
        if group:
            result[rc.repo_name] = group
    return result


def prepare_consolidated_content(
    report: ReportData,
    model: str = "claude-haiku-4-5-20251001",
    prompt: str | None = None,
    group_by: str = "contribution",
) -> list[RepoContent]:
    """Build AI-consolidated RepoContent list using the Claude API.

    Groups PRs by repo, sends all repos in one Claude API call, and
    returns summarised RepoContent objects.  On JSON parse/validation
    failure, retries once with the error and the expected JSON schema.

    Authentication: uses ANTHROPIC_API_KEY with the SDK when available,
    otherwise falls back to the ``claude`` CLI (which handles subscription
    and OAuth tokens natively).

    Args:
        report: Complete report data with populated PR lists.
        model: Claude model ID or alias (e.g. "sonnet") for consolidation.
        prompt: Custom system prompt. Uses default if None.

    Returns:
        List of RepoContent objects with summarised content.

    Raises:
        RuntimeError: If the API call fails or no auth method is available.
    """
    grouped = regroup_content(report, group_by)
    grouped_data = _serialize_grouped_content(grouped)
    if not grouped_data:
        logger.debug("No grouped data to consolidate — returning empty list")
        return []

    logger.debug(
        "Consolidation input: %d groups, %d chars of JSON",
        len(grouped_data),
        len(json.dumps(grouped_data)),
    )

    schema = _load_schema()
    if prompt:
        system_prompt: str | list[dict] = prompt
        logger.debug("Using custom consolidation prompt (%d chars)", len(prompt))
    else:
        system_prompt = [
            {"type": "text", "text": _load_prompt("consolidation")},
            {"type": "text", "text": _CONSOLIDATION_FORMAT},
        ]
        logger.debug("Using default consolidation prompt")

    # Include schema in user message so the AI can self-validate
    user_message = (
        json.dumps(grouped_data, indent=2)
        + "\n\n---\nExpected JSON schema for your response:\n"
        + json.dumps(schema, indent=2)
    )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    logger.debug(
        "Auth method: %s, model: %s",
        "ANTHROPIC_API_KEY" if api_key else "claude-agent-sdk",
        model,
    )
    return _call_with_retry(api_key, model, system_prompt, user_message, schema)


def prepare_ai_summary(
    report: ReportData,
    model: str = "claude-haiku-4-5-20251001",
    prompt: str | None = None,
) -> str:
    """Generate a short AI-powered summary of the report (<320 chars).

    Uses the same dual-backend as consolidation (SDK or CLI).

    Args:
        report: Complete report data with populated PR lists.
        model: Claude model ID for summarisation.
        prompt: Custom system prompt. Uses default if None.

    Returns:
        Summary string (AI is prompted to stay under 320 characters).

    Raises:
        RuntimeError: If the API call fails.
    """
    repos_data = _build_repos_data(report)
    if not repos_data:
        logger.debug("No repos data for AI summary — returning empty string")
        return ""

    if prompt:
        system_prompt: str | list[dict] = prompt
        logger.debug("Using custom summary prompt (%d chars)", len(prompt))
    else:
        system_prompt = [
            {"type": "text", "text": _load_prompt("summary")},
            {"type": "text", "text": _SUMMARY_FORMAT},
        ]
        logger.debug("Using default summary prompt")
    user_message = json.dumps(repos_data, indent=2)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    logger.debug(
        "AI summary: auth=%s, model=%s, input=%d chars",
        "ANTHROPIC_API_KEY" if api_key else "claude-agent-sdk",
        model,
        len(user_message),
    )
    text = _call_backend(api_key, model, system_prompt, user_message)
    logger.debug("AI summary response: %d chars", len(text.strip()))
    return text.strip()


def _call_via_sdk(
    api_key: str, model: str, system_prompt: str | list[dict], user_message: str,
) -> str:
    """Call Claude via the anthropic Python SDK (API key auth)."""
    import anthropic  # lazy import

    logger.debug("Calling Claude SDK (anthropic) with model=%s", model)
    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            timeout=120.0,
            messages=[{"role": "user", "content": user_message}],
            system=system_prompt,
        )
    except anthropic.APIError as e:
        logger.debug("Claude SDK API error: %s (%s)", type(e).__name__, e)
        raise RuntimeError(f"Claude API call failed: {e}") from e

    text = ""
    for block in response.content:
        if block.type == "text":
            text += block.text
    logger.debug(
        "Claude SDK response: model=%s, stop=%s, usage=%s, %d chars",
        response.model,
        response.stop_reason,
        f"in={response.usage.input_tokens}/out={response.usage.output_tokens}",
        len(text),
    )
    return text


def _call_via_sdk_agent(
    model: str, system_prompt: str | list[dict], user_message: str,
) -> str:
    """Call Claude via ``claude-agent-sdk`` (subscription / OAuth auth).

    Uses the Claude Agent SDK which handles whatever authentication
    the user has configured (subscription, OAuth token, etc.).
    """
    logger.debug("Calling Claude Agent SDK with model=%s", model)
    from claude_agent_sdk import (  # lazy import
        ClaudeAgentOptions,
        ResultMessage,
        query,
    )

    if isinstance(system_prompt, list):
        system_text = "\n\n".join(block["text"] for block in system_prompt)
    else:
        system_text = system_prompt
    full_prompt = f"{system_text}\n\n{user_message}"
    logger.debug("Agent SDK prompt length: %d chars", len(full_prompt))
    options = ClaudeAgentOptions(
        model=model,
        max_turns=1,
        allowed_tools=[],
    )

    async def _run() -> str:
        result_text = ""
        try:
            async for message in query(prompt=full_prompt, options=options):
                logger.debug("Agent SDK message: %s", type(message).__name__)
                if isinstance(message, ResultMessage):
                    result_text = message.result or ""
        except Exception as e:
            logger.debug("Agent SDK error: %s (%s)", type(e).__name__, e)
            raise RuntimeError(f"Claude Agent SDK call failed: {e}") from e
        return result_text

    text = asyncio.run(_run())
    if not text:
        raise RuntimeError("Claude Agent SDK returned empty response")
    logger.debug("Agent SDK response: %d chars", len(text))
    return text


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL)


_schema_cache: dict | None = None


def _load_schema() -> dict:
    """Load and cache the consolidation response JSON schema."""
    global _schema_cache
    if _schema_cache is None:
        schema_path = Path(__file__).parent / "schemas" / "consolidation_response.json"
        with open(schema_path) as f:
            _schema_cache = json.load(f)
    return _schema_cache


def _call_backend(
    api_key: str, model: str, system_prompt: str | list[dict], user_message: str,
) -> str:
    """Call the AI backend (SDK or CLI) and return the raw text response."""
    if api_key:
        logger.debug("Using anthropic SDK backend (API key present)")
        return _call_via_sdk(api_key, model, system_prompt, user_message)
    logger.debug("No ANTHROPIC_API_KEY — falling back to Claude Agent SDK")
    return _call_via_sdk_agent(model, system_prompt, user_message)


def _parse_and_validate(text: str, schema: dict) -> list[RepoContent]:
    """Extract JSON from AI text, validate against schema, build RepoContent.

    Raises RuntimeError if extraction or validation fails.
    """
    logger.debug("Raw AI response (%d chars): %.500s", len(text), text)
    stripped = _extract_json(text)
    logger.debug("Extracted JSON (%d chars): %.500s", len(stripped), stripped)

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse Claude response as JSON: {e}") from e

    if not isinstance(parsed, dict):
        raise RuntimeError(
            f"Claude response is not a JSON object (got {type(parsed).__name__})"
        )

    logger.debug("Parsed JSON: %d top-level keys: %s", len(parsed), list(parsed.keys()))

    # Validate against schema if jsonschema is available
    try:
        import jsonschema
        jsonschema.validate(instance=parsed, schema=schema)
        logger.debug("Schema validation passed")
    except ImportError:
        logger.debug("jsonschema not installed — skipping validation")
    except jsonschema.ValidationError as e:
        raise RuntimeError(f"Response failed schema validation: {e.message}") from e

    # Build RepoContent list from nested {group: {subgroup: [items]}}
    result: list[RepoContent] = []
    for group_name in sorted(parsed):
        subgroups = parsed[group_name]
        if not isinstance(subgroups, dict):
            logger.debug("Skipping group %r: value is %s, not dict", group_name, type(subgroups).__name__)
            continue
        blocks: list[ContentBlock] = []
        for subgroup_name in subgroups:
            items_data = subgroups[subgroup_name]
            if not isinstance(items_data, list):
                logger.debug("Skipping subgroup %r/%r: value is %s, not list", group_name, subgroup_name, type(items_data).__name__)
                continue
            items: list[ContentItem] = []
            for item in items_data:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", ""))[:500]
                numbers = item.get("numbers", [])
                if not isinstance(numbers, list):
                    numbers = []
                numbers = [n for n in numbers if isinstance(n, int)]
                if title:
                    items.append(ContentItem(title=title, numbers=numbers))
            if items:
                blocks.append(ContentBlock(heading=subgroup_name, items=items))
        if blocks:
            result.append(RepoContent(repo_name=group_name, blocks=blocks))

    total_items = sum(len(block.items) for rc in result for block in rc.blocks)
    logger.debug("Built %d RepoContent groups with %d total items", len(result), total_items)
    return result


def _call_with_retry(
    api_key: str,
    model: str,
    system_prompt: str | list[dict],
    user_message: str,
    schema: dict,
) -> list[RepoContent]:
    """Call the AI backend and parse/validate the response.

    On failure, retries once with the error message and expected schema
    to give the AI a chance to correct its output.
    """
    logger.debug("Calling AI backend (attempt 1)")
    text = _call_backend(api_key, model, system_prompt, user_message)
    try:
        result = _parse_and_validate(text, schema)
        logger.debug("Parse/validate succeeded on first attempt")
        return result
    except RuntimeError as first_error:
        logger.debug("First attempt failed: %s — retrying with correction prompt", first_error)
        # Single retry with correction prompt
        correction = (
            "Your previous response could not be parsed. Error:\n"
            f"{first_error}\n\n"
            f"Your response (first 2000 chars):\n{text[:2000]}\n\n"
            f"Expected JSON schema:\n{json.dumps(schema, indent=2)}\n\n"
            "Return ONLY the corrected JSON — no markdown fences, "
            "no explanation, no preamble."
        )
        logger.debug("Calling AI backend (attempt 2 — correction)")
        retry_text = _call_backend(api_key, model, system_prompt, correction)
        result = _parse_and_validate(retry_text, schema)
        logger.debug("Parse/validate succeeded on retry")
        return result


def _extract_json(text: str) -> str:
    """Extract a JSON object from an AI response.

    Tries in order:
    1. Direct parse of the full text (clean JSON response).
    2. Extract content from markdown code fences (```json ... ```).
    3. Find the first ``{`` and last ``}`` and try to parse that substring.
    """
    stripped = text.strip()

    # 1. Try direct parse
    try:
        json.loads(stripped)
        return stripped
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Try extracting from markdown fences
    match = _FENCED_JSON_RE.search(stripped)
    if match:
        return match.group(1).strip()

    # 3. Find outermost braces
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start:end + 1]

    # Give up — return original text so caller raises a clear error
    return stripped


def _parse_response(text: str) -> list[RepoContent]:
    """Parse Claude's JSON response into RepoContent objects."""
    stripped = _extract_json(text)

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse Claude response as JSON: {e}") from e

    if not isinstance(parsed, dict):
        raise RuntimeError("Claude response is not a JSON object")

    result: list[RepoContent] = []
    for group_name in sorted(parsed):
        subgroups = parsed[group_name]
        if not isinstance(subgroups, dict):
            continue
        blocks: list[ContentBlock] = []
        for subgroup_name in subgroups:
            items_data = subgroups[subgroup_name]
            if not isinstance(items_data, list):
                continue
            items: list[ContentItem] = []
            for item in items_data:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", ""))[:500]
                numbers = item.get("numbers", [])
                if not isinstance(numbers, list):
                    numbers = []
                numbers = [n for n in numbers if isinstance(n, int)]
                if title:
                    items.append(ContentItem(title=title, numbers=numbers))
            if items:
                blocks.append(ContentBlock(heading=subgroup_name, items=items))
        if blocks:
            result.append(RepoContent(repo_name=group_name, blocks=blocks))

    return result


def _build_repos_data(report: ReportData) -> dict[str, dict[str, list[dict]]]:
    """Build a dict of repo -> categorized PR summaries for the AI prompt.

    Returns:
        ``{repo: {authored: [...], contributed: [...], reviewed: [...], waiting_for_review: [...]}}``
        Only non-empty categories are included per repo.
    """
    authored_prs, reviewed_prs, waiting_prs = _dedup_pr_lists(report)
    repos: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))

    for pr in authored_prs:
        category = "contributed" if pr.contributed else "authored"
        entry: dict = {
            "number": pr.number,
            "title": pr.title,
            "status": pr.status,
            "additions": pr.additions,
            "deletions": pr.deletions,
        }
        if pr.body:
            entry["body"] = pr.body
        if pr.changed_files:
            entry["changed_files"] = pr.changed_files
        repos[pr.repo][category].append(entry)

    for pr in reviewed_prs:
        entry = {
            "number": pr.number,
            "title": pr.title,
            "status": pr.status,
        }
        if pr.body:
            entry["body"] = pr.body
        if pr.changed_files:
            entry["changed_files"] = pr.changed_files
        repos[pr.repo]["reviewed"].append(entry)

    for pr in waiting_prs:
        repos[pr.repo]["waiting_for_review"].append({
            "number": pr.number,
            "title": pr.title,
        })

    # Convert nested defaultdicts to plain dicts for clean JSON serialization
    return {repo: dict(categories) for repo, categories in repos.items()}
