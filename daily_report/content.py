"""Content preparation layer for daily-report.

Transforms raw PR lists (authored_prs, reviewed_prs, waiting_prs) into
renderer-agnostic RepoContent structures. Supports two modes:

- prepare_default_content(): groups PRs by repo with semantic ContentItems
- prepare_consolidated_content(): AI consolidation via Claude API (markdown-in/out)
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
import shlex
import subprocess
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
    "Return ONLY the consolidated Markdown report. "
    "Keep the same structure: # title, ## sections, - bullet items. "
    "No preamble, no explanation outside the Markdown."
)

_TOOL_OUTPUT_MAX = 8000


_prompt_cache: dict[str, str] = {}


def _load_prompt(name: str) -> str:
    """Load and cache a behavioral prompt from ``prompts/{name}.md``."""
    if name not in _prompt_cache:
        prompt_path = Path(__file__).parent / "prompts" / f"{name}.md"
        with open(prompt_path) as f:
            _prompt_cache[name] = f.read().strip()
    return _prompt_cache[name]


# ---------------------------------------------------------------------------
# Tool definitions for consolidation
# ---------------------------------------------------------------------------

CONSOLIDATION_TOOLS: list[dict] = [
    {
        "name": "gh_pr_view",
        "description": (
            "View GitHub PR details (title, body, state, reviews, changed files). "
            "Repo format: owner/name (e.g. 'octocat/hello-world')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "number": {"type": "integer", "description": "PR number"},
            },
            "required": ["repo", "number"],
        },
    },
    {
        "name": "gh_pr_diff",
        "description": (
            "View the actual code diff of a GitHub PR. "
            "Repo format: owner/name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "number": {"type": "integer", "description": "PR number"},
            },
            "required": ["repo", "number"],
        },
    },
    {
        "name": "git_log",
        "description": (
            "View git commit history in a local repository. "
            "Provide the repo name (owner/name) and optional git log arguments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "args": {
                    "type": "string",
                    "description": "Additional git log arguments (e.g. '--oneline -20')",
                    "default": "--oneline -20",
                },
            },
            "required": ["repo"],
        },
    },
    {
        "name": "git_diff",
        "description": (
            "View diffs in a local repository. "
            "Provide the repo name (owner/name) and optional git diff arguments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "args": {
                    "type": "string",
                    "description": "Git diff arguments (e.g. 'HEAD~5..HEAD --stat')",
                    "default": "HEAD~1",
                },
            },
            "required": ["repo"],
        },
    },
]


def _truncate(text: str, max_len: int = _TOOL_OUTPUT_MAX) -> str:
    """Truncate text with a note if it exceeds max_len."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n\n... (truncated, {len(text)} total chars)"


def _exec_gh_pr_view(repo: str, number: int) -> str:
    """Execute gh pr view and return output."""
    cmd = [
        "gh", "pr", "view", str(number),
        "-R", repo,
        "--json", "title,body,state,isDraft,additions,deletions,files,reviews,comments",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return f"Error: {result.stderr.strip()[:500]}"
        return _truncate(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"Error: {e}"


def _exec_gh_pr_diff(repo: str, number: int) -> str:
    """Execute gh pr diff and return output."""
    cmd = ["gh", "pr", "diff", str(number), "-R", repo]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return f"Error: {result.stderr.strip()[:500]}"
        return _truncate(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"Error: {e}"


def _exec_git_log(repo: str, args: str, repo_paths: dict[str, str]) -> str:
    """Execute git log on a local repo."""
    path = repo_paths.get(repo)
    if not path:
        return f"Error: no local path for repo '{repo}'. Available: {list(repo_paths.keys())}"
    try:
        cmd = ["git", "-C", path, "log"] + shlex.split(args)
    except ValueError as e:
        return f"Error parsing args: {e}"
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return f"Error: {result.stderr.strip()[:500]}"
        return _truncate(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"Error: {e}"


def _exec_git_diff(repo: str, args: str, repo_paths: dict[str, str]) -> str:
    """Execute git diff on a local repo."""
    path = repo_paths.get(repo)
    if not path:
        return f"Error: no local path for repo '{repo}'. Available: {list(repo_paths.keys())}"
    try:
        cmd = ["git", "-C", path, "diff"] + shlex.split(args)
    except ValueError as e:
        return f"Error parsing args: {e}"
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return f"Error: {result.stderr.strip()[:500]}"
        return _truncate(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"Error: {e}"


def _execute_tool(
    name: str, tool_input: dict, repo_paths: dict[str, str],
) -> str:
    """Dispatch a tool call to the appropriate executor."""
    logger.debug("Executing tool %s with input: %s", name, tool_input)
    if name == "gh_pr_view":
        result = _exec_gh_pr_view(tool_input["repo"], tool_input["number"])
    elif name == "gh_pr_diff":
        result = _exec_gh_pr_diff(tool_input["repo"], tool_input["number"])
    elif name == "git_log":
        result = _exec_git_log(
            tool_input["repo"],
            tool_input.get("args", "--oneline -20"),
            repo_paths,
        )
    elif name == "git_diff":
        result = _exec_git_diff(
            tool_input["repo"],
            tool_input.get("args", "HEAD~1"),
            repo_paths,
        )
    else:
        result = f"Error: unknown tool '{name}'"
    logger.debug("Tool %s result: %d chars", name, len(result))
    return result


# ---------------------------------------------------------------------------
# PR deduplication
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Default content preparation
# ---------------------------------------------------------------------------

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

        # Worked on
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
            blocks.append(ContentBlock(heading="Worked on", items=items))

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

    # Worked on
    authored_by_repo: dict[str, list[ContentItem]] = defaultdict(list)
    for pr in authored_prs:
        authored_by_repo[pr.repo].append(_make_authored_item(pr))
    if authored_by_repo:
        blocks = [
            ContentBlock(heading=repo, items=items)
            for repo, items in sorted(authored_by_repo.items())
        ]
        result.append(RepoContent(repo_name="Worked on", blocks=blocks))

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


# ---------------------------------------------------------------------------
# AI consolidation (markdown-in, tool use, markdown-out)
# ---------------------------------------------------------------------------

def prepare_consolidated_content(
    report: ReportData,
    model: str = "claude-sonnet-4-5-20250929",
    prompt: str | None = None,
    group_by: str = "contribution",
    repo_paths: dict[str, str] | None = None,
) -> str:
    """Consolidate the report via Claude API with tool use.

    Generates the default markdown report, sends it to Claude with tools
    for deeper analysis (gh pr view/diff, git log/diff), and returns
    the consolidated markdown.

    Args:
        report: Complete report data with populated PR lists.
        model: Claude model ID for consolidation.
        prompt: Custom system prompt. Uses default if None.
        group_by: Grouping mode for the input markdown.
        repo_paths: Map of "owner/name" → local filesystem path for git tools.

    Returns:
        Consolidated markdown string.

    Raises:
        RuntimeError: If the API call fails or no auth method is available.
    """
    from daily_report.format_markdown import format_markdown

    # Generate default markdown as input
    if not report.content:
        report.content = regroup_content(report, group_by)
    markdown_input = format_markdown(report, group_by=group_by)

    if not markdown_input.strip():
        logger.debug("No content to consolidate — returning empty string")
        return ""

    logger.debug("Consolidation input (%d chars):\n%s", len(markdown_input), markdown_input)

    if prompt:
        system_prompt: str | list[dict] = prompt
        logger.debug("Using custom consolidation prompt (%d chars)", len(prompt))
    else:
        system_prompt = [
            {"type": "text", "text": _load_prompt("consolidation")},
            {"type": "text", "text": _CONSOLIDATION_FORMAT},
        ]
        logger.debug("Using default consolidation prompt")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    effective_repo_paths = repo_paths or {}
    logger.debug(
        "Auth method: %s, model: %s, tools: %d, repo_paths: %d",
        "ANTHROPIC_API_KEY" if api_key else "claude-agent-sdk",
        model,
        len(CONSOLIDATION_TOOLS),
        len(effective_repo_paths),
    )

    text = _call_backend_with_tools(
        api_key, model, system_prompt, markdown_input,
        CONSOLIDATION_TOOLS, effective_repo_paths,
    )
    result = text.strip()
    logger.debug("Consolidation output (%d chars):\n%s", len(result), result)
    return result


# ---------------------------------------------------------------------------
# AI summary
# ---------------------------------------------------------------------------

def prepare_ai_summary(
    report: ReportData,
    model: str = "claude-sonnet-4-5-20250929",
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
        "AI summary input (auth=%s, model=%s, %d chars):\n%s",
        "ANTHROPIC_API_KEY" if api_key else "claude-agent-sdk",
        model,
        len(user_message),
        user_message,
    )
    text = _call_backend(api_key, model, system_prompt, user_message)
    result = text.strip()
    logger.debug("AI summary output (%d chars):\n%s", len(result), result)
    return result


# ---------------------------------------------------------------------------
# Backend callers
# ---------------------------------------------------------------------------

def _call_backend(
    api_key: str, model: str, system_prompt: str | list[dict], user_message: str,
) -> str:
    """Call the AI backend (SDK or agent SDK) without tools."""
    if api_key:
        logger.debug("Using anthropic SDK backend (API key present)")
        return _call_via_sdk(api_key, model, system_prompt, user_message)
    logger.debug("No ANTHROPIC_API_KEY — falling back to Claude Agent SDK")
    return _call_via_sdk_agent(model, system_prompt, user_message)


def _call_backend_with_tools(
    api_key: str,
    model: str,
    system_prompt: str | list[dict],
    user_message: str,
    tools: list[dict],
    repo_paths: dict[str, str],
) -> str:
    """Call the AI backend with tool use support."""
    if api_key:
        logger.debug("Using anthropic SDK backend with tools (API key present)")
        return _call_via_sdk_with_tools(
            api_key, model, system_prompt, user_message, tools, repo_paths,
        )
    logger.debug("No ANTHROPIC_API_KEY — falling back to Claude Agent SDK with Bash")
    return _call_via_sdk_agent_with_tools(model, system_prompt, user_message)


def _call_via_sdk(
    api_key: str, model: str, system_prompt: str | list[dict], user_message: str,
) -> str:
    """Call Claude via the anthropic Python SDK (API key auth), no tools."""
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


def _call_via_sdk_with_tools(
    api_key: str,
    model: str,
    system_prompt: str | list[dict],
    user_message: str,
    tools: list[dict],
    repo_paths: dict[str, str],
    max_turns: int = 10,
) -> str:
    """Call Claude via anthropic SDK with tool use conversation loop.

    Sends the initial message, then loops handling tool_use responses
    until Claude returns a final text response (stop_reason == "end_turn").
    """
    import anthropic  # lazy import

    logger.debug("Calling Claude SDK with tools, model=%s, max_turns=%d", model, max_turns)
    client = anthropic.Anthropic(api_key=api_key)

    messages: list[dict] = [{"role": "user", "content": user_message}]

    for turn in range(max_turns):
        logger.debug("Tool conversation turn %d", turn + 1)
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                timeout=120.0,
                messages=messages,
                system=system_prompt,
                tools=tools,
            )
        except anthropic.APIError as e:
            logger.debug("Claude SDK API error on turn %d: %s (%s)", turn + 1, type(e).__name__, e)
            raise RuntimeError(f"Claude API call failed: {e}") from e

        logger.debug(
            "Turn %d response: stop=%s, blocks=%d, usage=in=%d/out=%d",
            turn + 1,
            response.stop_reason,
            len(response.content),
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        # If no tool use, extract text and return
        if response.stop_reason == "end_turn":
            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text
            return text

        # Handle tool_use blocks
        tool_results = []
        has_tool_use = False
        for block in response.content:
            if block.type == "tool_use":
                has_tool_use = True
                logger.debug("Tool call: %s(%s)", block.name, block.input)
                result = _execute_tool(block.name, block.input, repo_paths)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        if not has_tool_use:
            # No tool use and not end_turn — extract whatever text we have
            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text
            return text

        # Append assistant response and tool results to conversation
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(
        f"Consolidation exceeded {max_turns} tool-use turns without completing"
    )


def _call_via_sdk_agent(
    model: str, system_prompt: str | list[dict], user_message: str,
) -> str:
    """Call Claude via ``claude-agent-sdk`` (subscription / OAuth auth), no tools."""
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


def _call_via_sdk_agent_with_tools(
    model: str, system_prompt: str | list[dict], user_message: str,
) -> str:
    """Call Claude via ``claude-agent-sdk`` with Bash tool for gh/git commands.

    The agent SDK handles tool execution natively — we just allow the Bash
    tool and let Claude run gh/git commands itself.
    """
    logger.debug("Calling Claude Agent SDK with Bash tool, model=%s", model)
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
        max_turns=10,
        allowed_tools=["Bash"],
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


# ---------------------------------------------------------------------------
# Helpers for AI summary
# ---------------------------------------------------------------------------

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
