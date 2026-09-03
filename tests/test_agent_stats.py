"""Tests for scripts/agent_stats.py."""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Make the scripts directory importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import agent_stats as stats


class TestParseTs(unittest.TestCase):
    def test_iso_z(self):
        dt = stats.parse_ts("2026-03-10T17:16:39.119Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_iso_offset(self):
        dt = stats.parse_ts("2026-03-10T17:16:39+00:00")
        self.assertIsNotNone(dt)

    def test_none_input(self):
        self.assertIsNone(stats.parse_ts(None))

    def test_empty_string(self):
        self.assertIsNone(stats.parse_ts(""))

    def test_garbage(self):
        self.assertIsNone(stats.parse_ts("not-a-date"))


class TestInRange(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 3, 6, tzinfo=timezone.utc)
        self.end = datetime(2026, 3, 17, 23, 59, 59, tzinfo=timezone.utc)

    def test_in_range(self):
        self.assertTrue(stats.in_range("2026-03-10T12:00:00Z", self.start, self.end))

    def test_before_range(self):
        self.assertFalse(stats.in_range("2026-03-05T23:59:59Z", self.start, self.end))

    def test_after_range(self):
        self.assertFalse(stats.in_range("2026-03-18T00:00:01Z", self.start, self.end))

    def test_start_boundary(self):
        self.assertTrue(stats.in_range("2026-03-06T00:00:00Z", self.start, self.end))

    def test_end_boundary(self):
        self.assertTrue(stats.in_range("2026-03-17T23:59:59Z", self.start, self.end))

    def test_none_ts(self):
        self.assertFalse(stats.in_range(None, self.start, self.end))


class TestFmtTokens(unittest.TestCase):
    def test_millions(self):
        self.assertEqual(stats.fmt_tokens(45_200_000), "45.2M")

    def test_thousands(self):
        self.assertEqual(stats.fmt_tokens(134_246), "134K")

    def test_small(self):
        self.assertEqual(stats.fmt_tokens(500), "500")

    def test_exactly_one_million(self):
        self.assertEqual(stats.fmt_tokens(1_000_000), "1.0M")


class TestProcessJsonl(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 3, 6, tzinfo=timezone.utc)
        self.end = datetime(2026, 3, 17, 23, 59, 59, tzinfo=timezone.utc)

    def _write_jsonl(self, tmp_path: Path, records: list) -> Path:
        p = tmp_path / "test.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in records))
        return p

    def test_counts_in_range_assistant(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "session.jsonl"
            record = {
                "type": "assistant",
                "timestamp": "2026-03-10T12:00:00Z",
                "message": {
                    "model": "claude-opus-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_creation_input_tokens": 200,
                        "cache_read_input_tokens": 1000,
                    },
                },
            }
            p.write_text(json.dumps(record))
            model_tokens = {}
            agent_stats_map = {}
            n = stats.process_jsonl(p, self.start, self.end, "test-agent", model_tokens, agent_stats_map)
            self.assertEqual(n, 1)
            self.assertIn("claude-opus-4-6", model_tokens)
            self.assertEqual(model_tokens["claude-opus-4-6"]["input"], 100)
            self.assertEqual(model_tokens["claude-opus-4-6"]["output"], 50)
            self.assertEqual(model_tokens["claude-opus-4-6"]["cache_creation"], 200)
            self.assertEqual(model_tokens["claude-opus-4-6"]["cache_read"], 1000)
            self.assertIn("test-agent", agent_stats_map)
            self.assertEqual(agent_stats_map["test-agent"]["input"], 100)
            self.assertEqual(agent_stats_map["test-agent"]["output"], 50)
            self.assertEqual(agent_stats_map["test-agent"]["messages"], 1)

    def test_skips_out_of_range(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "session.jsonl"
            record = {
                "type": "assistant",
                "timestamp": "2026-03-01T12:00:00Z",
                "message": {"model": "claude-opus-4-6", "usage": {"input_tokens": 100}},
            }
            p.write_text(json.dumps(record))
            model_tokens = {}
            agent_stats_map = {}
            n = stats.process_jsonl(p, self.start, self.end, "test-agent", model_tokens, agent_stats_map)
            self.assertEqual(n, 0)
            self.assertEqual(len(model_tokens), 0)

    def test_skips_non_assistant_records(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "session.jsonl"
            records = [
                {"type": "user", "timestamp": "2026-03-10T12:00:00Z", "message": {"content": "hello"}},
                {"type": "progress", "timestamp": "2026-03-10T12:00:01Z"},
            ]
            p.write_text("\n".join(json.dumps(r) for r in records))
            model_tokens = {}
            agent_stats_map = {}
            n = stats.process_jsonl(p, self.start, self.end, "test-agent", model_tokens, agent_stats_map)
            self.assertEqual(n, 0)

    def test_handles_corrupt_lines(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "session.jsonl"
            p.write_text("not valid json\n{\"type\": \"user\"}\n")
            model_tokens = {}
            agent_stats_map = {}
            # Should not raise
            n = stats.process_jsonl(p, self.start, self.end, "test-agent", model_tokens, agent_stats_map)
            self.assertEqual(n, 0)

    def test_handles_missing_usage_fields(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "session.jsonl"
            record = {
                "type": "assistant",
                "timestamp": "2026-03-10T12:00:00Z",
                "message": {"model": "claude-opus-4-6", "usage": {}},
            }
            p.write_text(json.dumps(record))
            model_tokens = {}
            agent_stats_map = {}
            n = stats.process_jsonl(p, self.start, self.end, "test-agent", model_tokens, agent_stats_map)
            self.assertEqual(n, 1)
            self.assertEqual(model_tokens["claude-opus-4-6"]["input"], 0)

    def test_accumulates_multiple_records(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "session.jsonl"
            records = [
                {
                    "type": "assistant",
                    "timestamp": "2026-03-10T12:00:00Z",
                    "message": {"model": "claude-opus-4-6", "usage": {"input_tokens": 100, "output_tokens": 50}},
                },
                {
                    "type": "assistant",
                    "timestamp": "2026-03-11T12:00:00Z",
                    "message": {"model": "claude-opus-4-6", "usage": {"input_tokens": 200, "output_tokens": 75}},
                },
            ]
            p.write_text("\n".join(json.dumps(r) for r in records))
            model_tokens = {}
            agent_stats_map = {}
            n = stats.process_jsonl(p, self.start, self.end, "my-agent", model_tokens, agent_stats_map)
            self.assertEqual(n, 2)
            self.assertEqual(model_tokens["claude-opus-4-6"]["input"], 300)
            self.assertEqual(model_tokens["claude-opus-4-6"]["output"], 125)
            self.assertEqual(agent_stats_map["my-agent"]["messages"], 2)


class TestFormatHuman(unittest.TestCase):
    def _make_data(self):
        return {
            "date_from": "2026-03-06",
            "date_to": "2026-03-17",
            "totals": {
                "sessions": 299,
                "messages": 134246,
                "tool_calls": 19996,
                "input_tokens": 3200000,
                "output_tokens": 21000000,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
            },
            "by_model": {
                "claude-opus-4-6": {"input": 2300000, "output": 18400000, "cache_creation": 0, "cache_read": 0},
            },
            "by_agent": {
                "claudius:developer-bilby": {"input": 1000000, "output": 500000, "messages": 100, "sessions": 42},
                stats.ORCHESTRATOR_LABEL: {"input": 2200000, "output": 20500000, "messages": 9000, "sessions": 234},
            },
        }

    def test_contains_header(self):
        out = stats.format_human(self._make_data())
        self.assertIn("Claude Code Agent Stats", out)
        self.assertIn("2026-03-06", out)
        self.assertIn("2026-03-17", out)

    def test_contains_totals(self):
        out = stats.format_human(self._make_data())
        self.assertIn("299", out)
        self.assertIn("Sessions:", out)
        self.assertIn("Tool calls:", out)

    def test_contains_model(self):
        out = stats.format_human(self._make_data())
        self.assertIn("claude-opus-4-6", out)

    def test_contains_agent_types(self):
        out = stats.format_human(self._make_data())
        self.assertIn("claudius:developer-bilby", out)
        self.assertIn(stats.ORCHESTRATOR_LABEL, out)


class TestFormatPresentation(unittest.TestCase):
    def _make_data(self):
        return {
            "date_from": "2026-03-06",
            "date_to": "2026-03-17",
            "totals": {
                "sessions": 299,
                "messages": 134246,
                "tool_calls": 19996,
                "input_tokens": 3200000,
                "output_tokens": 21000000,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
            },
            "by_model": {},
            "by_agent": {
                "claudius:developer-bilby": {"input": 1000000, "output": 500000, "messages": 100, "sessions": 42},
                "claudius:qa-engineer": {"input": 500000, "output": 200000, "messages": 50, "sessions": 15},
                stats.ORCHESTRATOR_LABEL: {"input": 2200000, "output": 20500000, "messages": 9000, "sessions": 234},
                stats.SUBAGENT_LABEL: {"input": 100000, "output": 50000, "messages": 200, "sessions": 50},
            },
        }

    def test_single_stats_line(self):
        out = stats.format_presentation(self._make_data())
        first_line = out.split("\n")[0]
        self.assertIn("299 sessions", first_line)
        self.assertIn("tokens", first_line)

    def test_top_agents_line_excludes_placeholders(self):
        out = stats.format_presentation(self._make_data())
        self.assertIn("developer-bilby", out)
        self.assertNotIn(stats.ORCHESTRATOR_LABEL, out)
        self.assertNotIn(stats.SUBAGENT_LABEL, out)

    def test_agent_count(self):
        out = stats.format_presentation(self._make_data())
        # 4 agent types total
        self.assertIn("4 agent types", out)


class TestLoadAgentTypeMap(unittest.TestCase):
    def test_finds_agent_types(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            proj = base / "-home-ubuntu-git-myproject"
            sess = proj / "abc123"
            subagents = sess / "subagents"
            subagents.mkdir(parents=True)
            meta = subagents / "agent-deadbeef.meta.json"
            meta.write_text(json.dumps({"agentType": "claudius:developer-bilby"}))
            # Also create the JSONL file
            (subagents / "agent-deadbeef.jsonl").write_text("")

            mapping = stats.load_agent_type_map(base)
            self.assertIn("abc123:deadbeef", mapping)
            self.assertEqual(mapping["abc123:deadbeef"], "claudius:developer-bilby")

    def test_handles_missing_agent_type_in_meta(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            proj = base / "proj"
            sess = proj / "sess1"
            subagents = sess / "subagents"
            subagents.mkdir(parents=True)
            (subagents / "agent-id1.meta.json").write_text(json.dumps({}))
            mapping = stats.load_agent_type_map(base)
            # No agentType => not added to map
            self.assertEqual(len(mapping), 0)

    def test_handles_corrupt_meta(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            proj = base / "proj"
            sess = proj / "sess1"
            subagents = sess / "subagents"
            subagents.mkdir(parents=True)
            (subagents / "agent-id1.meta.json").write_text("not json")
            # Should not raise
            mapping = stats.load_agent_type_map(base)
            self.assertEqual(len(mapping), 0)


if __name__ == "__main__":
    unittest.main()
