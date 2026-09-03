#!/usr/bin/env python3
"""Mine Claude Code session data from ~/.claude/ to produce agent usage statistics.

Scans JSONL session files and subagent files, filters by date range, and reports
token usage by model and agent type.

Usage:
    python3 scripts/agent_stats.py --from 2026-03-06 --to 2026-03-17
    python3 scripts/agent_stats.py --from 2026-03-06 --to 2026-03-17 --format json
    python3 scripts/agent_stats.py --from 2026-03-06 --to 2026-03-17 --format presentation
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


CLAUDE_DIR = Path.home() / ".claude"

# Label used for top-level orchestrator sessions (no agent type)
ORCHESTRATOR_LABEL = "(orchestrator)"
# Label for subagents whose meta.json is missing
SUBAGENT_LABEL = "(subagent)"


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def parse_ts(ts) -> datetime | None:
    """Parse ISO 8601 timestamp string, return None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def in_range(ts_str, start: datetime, end: datetime) -> bool:
    dt = parse_ts(ts_str)
    if dt is None:
        return False
    return start <= dt <= end


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def load_agent_type_map(projects_dir: Path) -> dict[str, str]:
    """Return mapping of '{session_uuid}:{agent_id}' -> agentType from meta.json files."""
    mapping: dict[str, str] = {}
    for proj in projects_dir.iterdir():
        if not proj.is_dir():
            continue
        for sess_dir in proj.iterdir():
            if not sess_dir.is_dir():
                continue
            sub_dir = sess_dir / "subagents"
            if not sub_dir.exists():
                continue
            for meta_file in sub_dir.glob("agent-*.meta.json"):
                try:
                    meta = json.loads(meta_file.read_text())
                    agent_type = meta.get("agentType", "")
                    if agent_type:
                        # stem is e.g. "agent-a3e06a583e23174c3.meta"
                        raw_stem = meta_file.name[: -len(".meta.json")]  # "agent-a3e..."
                        agent_id = raw_stem[len("agent-"):]
                        mapping[f"{sess_dir.name}:{agent_id}"] = agent_type
                except Exception:
                    pass
    return mapping


def _make_model_entry() -> dict:
    return {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}


def _make_agent_entry() -> dict:
    return {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0, "messages": 0, "sessions": 0}


def process_jsonl(
    path: Path,
    start: datetime,
    end: datetime,
    agent_type: str,
    model_tokens: dict,
    agent_stats: dict,
) -> int:
    """Process one JSONL file; return count of in-range assistant messages."""
    count = 0
    try:
        for raw in path.read_text(errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            if rec.get("type") != "assistant":
                continue
            if not in_range(rec.get("timestamp"), start, end):
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage") or {}
            model = msg.get("model") or "unknown"
            inp = usage.get("input_tokens", 0) or 0
            out = usage.get("output_tokens", 0) or 0
            cc = usage.get("cache_creation_input_tokens", 0) or 0
            cr = usage.get("cache_read_input_tokens", 0) or 0

            if model not in model_tokens:
                model_tokens[model] = _make_model_entry()
            mt = model_tokens[model]
            mt["input"] += inp
            mt["output"] += out
            mt["cache_creation"] += cc
            mt["cache_read"] += cr

            if agent_type not in agent_stats:
                agent_stats[agent_type] = _make_agent_entry()
            ae = agent_stats[agent_type]
            ae["input"] += inp
            ae["output"] += out
            ae["cache_creation"] += cc
            ae["cache_read"] += cr
            ae["messages"] += 1
            count += 1
    except Exception:
        pass
    return count


def collect(
    start: datetime,
    end: datetime,
    project_filter: str | None,
) -> dict:
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.exists():
        return {}

    agent_type_map = load_agent_type_map(projects_dir)

    model_tokens: dict = {}
    agent_stats: dict = {}
    sessions_seen: set[str] = set()

    for proj in sorted(projects_dir.iterdir()):
        if not proj.is_dir():
            continue
        if project_filter and not any(
            term.strip().lower() in proj.name.lower()
            for term in project_filter.split("|")
        ):
            continue

        for item in proj.iterdir():
            if item.is_dir():
                # Session directory — contains subagent JSONL files
                sess_id = item.name
                sub_dir = item / "subagents"
                if not sub_dir.exists():
                    continue
                for jsonl_file in sub_dir.glob("agent-*.jsonl"):
                    agent_id = jsonl_file.stem[len("agent-"):]
                    key = f"{sess_id}:{agent_id}"
                    agent_type = agent_type_map.get(key, SUBAGENT_LABEL)
                    n = process_jsonl(
                        jsonl_file, start, end, agent_type, model_tokens, agent_stats
                    )
                    if n > 0 and key not in sessions_seen:
                        sessions_seen.add(key)
                        if agent_type not in agent_stats:
                            agent_stats[agent_type] = _make_agent_entry()
                        agent_stats[agent_type]["sessions"] += 1

            elif item.suffix == ".jsonl":
                # Top-level session file — the orchestrator
                n = process_jsonl(
                    item, start, end, ORCHESTRATOR_LABEL, model_tokens, agent_stats
                )
                sess_key = f"{proj.name}:{item.stem}"
                if n > 0 and sess_key not in sessions_seen:
                    sessions_seen.add(sess_key)
                    if ORCHESTRATOR_LABEL not in agent_stats:
                        agent_stats[ORCHESTRATOR_LABEL] = _make_agent_entry()
                    agent_stats[ORCHESTRATOR_LABEL]["sessions"] += 1

    # Load daily aggregates from stats-cache.json for session/message/tool_call totals
    cache_file = CLAUDE_DIR / "stats-cache.json"
    totals = {"sessions": 0, "messages": 0, "tool_calls": 0}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
            for day in cache.get("dailyActivity", []):
                d = day.get("date", "")
                if not d:
                    continue
                try:
                    day_dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if start.date() <= day_dt.date() <= end.date():
                    totals["sessions"] += day.get("sessionCount", 0)
                    totals["messages"] += day.get("messageCount", 0)
                    totals["tool_calls"] += day.get("toolCallCount", 0)
        except Exception:
            pass

    total_input = sum(v["input"] for v in model_tokens.values())
    total_output = sum(v["output"] for v in model_tokens.values())
    total_cc = sum(v["cache_creation"] for v in model_tokens.values())
    total_cr = sum(v["cache_read"] for v in model_tokens.values())

    # Sort models by input+output descending
    sorted_models = dict(
        sorted(
            model_tokens.items(),
            key=lambda kv: kv[1]["input"] + kv[1]["output"],
            reverse=True,
        )
    )
    # Sort agents by input+output descending
    sorted_agents = dict(
        sorted(
            agent_stats.items(),
            key=lambda kv: kv[1]["input"] + kv[1]["output"],
            reverse=True,
        )
    )

    return {
        "date_from": start.strftime("%Y-%m-%d"),
        "date_to": end.strftime("%Y-%m-%d"),
        "totals": {
            "sessions": totals["sessions"],
            "messages": totals["messages"],
            "tool_calls": totals["tool_calls"],
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_creation_tokens": total_cc,
            "cache_read_tokens": total_cr,
        },
        "by_model": sorted_models,
        "by_agent": sorted_agents,
    }


def format_human(data: dict) -> str:
    t = data["totals"]
    total_tokens = t["input_tokens"] + t["output_tokens"] + t["cache_creation_tokens"] + t["cache_read_tokens"]
    lines = [
        f"Claude Code Agent Stats -- {data['date_from']} to {data['date_to']}",
        "=" * 51,
        "",
        "Totals",
        f"  Sessions:      {t['sessions']:,}",
        f"  Messages:      {t['messages']:,}",
        f"  Tool calls:    {t['tool_calls']:,}",
        f"  Input tokens:  {fmt_tokens(t['input_tokens'])}",
        f"  Output tokens: {fmt_tokens(t['output_tokens'])}",
        f"  Cache create:  {fmt_tokens(t['cache_creation_tokens'])}",
        f"  Cache read:    {fmt_tokens(t['cache_read_tokens'])}",
        f"  Total tokens:  {fmt_tokens(total_tokens)}",
        "",
        "By Model",
    ]
    for model, vals in data["by_model"].items():
        model_total = vals["input"] + vals["output"] + vals["cache_creation"] + vals["cache_read"]
        lines.append(
            f"  {model:<40} {fmt_tokens(model_total):>8} total"
            f"  ({fmt_tokens(vals['input'])} in + {fmt_tokens(vals['output'])} out"
            f" + {fmt_tokens(vals['cache_creation'])} cc + {fmt_tokens(vals['cache_read'])} cr)"
        )
    lines += ["", "By Agent Type"]
    for agent, vals in data["by_agent"].items():
        agent_total = vals["input"] + vals["output"] + vals.get("cache_creation", 0) + vals.get("cache_read", 0)
        lines.append(
            f"  {agent:<38} {vals['sessions']:>4} sessions"
            f"  {fmt_tokens(agent_total):>8} tokens"
        )
    return "\n".join(lines)


def format_presentation(data: dict) -> str:
    t = data["totals"]
    total_tokens = t["input_tokens"] + t["output_tokens"] + t["cache_creation_tokens"] + t["cache_read_tokens"]
    agent_count = len(data["by_agent"])

    # Top named agents (exclude orchestrator/subagent placeholders)
    top = [
        (a, v)
        for a, v in data["by_agent"].items()
        if a not in (ORCHESTRATOR_LABEL, SUBAGENT_LABEL)
    ][:3]

    top_str = ", ".join(
        f"{a.split(':')[-1]} ({v['sessions']} sessions)" for a, v in top
    )
    line1 = (
        f"{t['sessions']} sessions · {fmt_tokens(t['messages'])} messages · "
        f"{fmt_tokens(total_tokens)} tokens · {agent_count} agent types"
    )
    line2 = f"Top: {top_str}" if top_str else ""
    return line1 + ("\n" + line2 if line2 else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude Code agent usage statistics")
    parser.add_argument("--from", dest="date_from", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--projects", help="Filter project dirs by substring")
    parser.add_argument(
        "--format",
        choices=["human", "json", "presentation"],
        default="human",
        help="Output format (default: human)",
    )
    parser.add_argument("--json", action="store_true", help="Shorthand for --format json")
    args = parser.parse_args()

    fmt = "json" if args.json else args.format

    try:
        start = parse_date(args.date_from)
        end = parse_date(args.date_to).replace(hour=23, minute=59, second=59)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    data = collect(start, end, args.projects)

    if fmt == "json":
        print(json.dumps(data, indent=2))
    elif fmt == "presentation":
        print(format_presentation(data))
    else:
        print(format_human(data))


if __name__ == "__main__":
    main()
