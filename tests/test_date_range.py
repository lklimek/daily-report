"""Functional tests for daily_report date range mode.

Each test runs the real daily_report package against live GitHub data
(org=dashpay, user=lklimek) and verifies expected PRs appear or
don't appear in the output. Test scenarios are documented in
tests/scenarios/ directory.

Requirements: gh CLI authenticated, network access.
Run with: python3 -m pytest tests/test_date_range.py -v
"""

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ORG = "dashpay"
USER = "lklimek"


def run_report(*extra_args: str) -> str:
    """Run daily_report and return combined stdout+stderr."""
    cmd = [sys.executable, "-m", "daily_report", "--org", ORG, "--user", USER, *extra_args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(PROJECT_ROOT))
    return result.stdout + result.stderr, result.returncode


def run_report_ok(*extra_args: str) -> str:
    """Run daily_report, assert exit 0, return output."""
    output, rc = run_report(*extra_args)
    assert rc == 0, f"daily_report exited with {rc}:\n{output}"
    return output


# ---------------------------------------------------------------------------
# TC00: CLI argument validation
# ---------------------------------------------------------------------------
class TestCLIValidation:
    """Verify error handling for invalid argument combinations."""

    def test_from_without_to(self):
        output, rc = run_report("--from", "2026-02-06")
        assert rc != 0
        assert "must be used together" in output

    def test_to_without_from(self):
        output, rc = run_report("--to", "2026-02-09")
        assert rc != 0
        assert "must be used together" in output

    def test_from_greater_than_to(self):
        output, rc = run_report("--from", "2026-02-07", "--to", "2026-02-01")
        assert rc != 0
        assert "must be <=" in output

    def test_date_combined_with_from_to(self):
        output, rc = run_report(
            "--date", "2026-02-01", "--from", "2026-02-01", "--to", "2026-02-07"
        )
        assert rc != 0
        assert "cannot be combined" in output

    def test_invalid_date_format(self):
        output, rc = run_report("--from", "bad-date", "--to", "2026-02-07")
        assert rc != 0
        assert "Invalid date" in output

    def test_model_without_consolidate_or_summary(self):
        output, rc = run_report("--model", "claude-haiku-4-5-20251001")
        assert rc != 0
        assert "--model requires --consolidate or --summary" in output


# ---------------------------------------------------------------------------
# TC01: Single day with known activity (2026-02-09)
# See tc01_single_day_2026_02_09.md
# ---------------------------------------------------------------------------
class TestSingleDay20260209:
    """Single day 2026-02-09: verify authored and reviewed PRs."""

    @pytest.fixture(autouse=True, scope="class")
    def _output(self, request):
        request.cls.output = run_report_ok("--date", "2026-02-09")

    def test_header_single_date(self):
        assert "2026-02-09" in self.output

    # Authored PRs (commits on 2026-02-09)
    def test_authored_platform_3072(self):
        assert "#3072" in self.output

    def test_authored_tenderdash_1250(self):
        assert "#1250" in self.output

    def test_authored_platform_3068(self):
        assert "#3068" in self.output

    # Reviewed PRs (reviews on 2026-02-09)
    # NOTE: tenderdash#1252 (by Copilot) is not returned by gh search
    # --reviewed-by despite lklimek having reviews on it. This is a known
    # GitHub search API limitation for bot-authored PRs.

    def test_reviewed_evo_tool_534(self):
        assert "#534" in self.output

    def test_reviewed_evo_tool_535(self):
        assert "#535" in self.output

    def test_reviewed_platform_3073(self):
        assert "#3073" in self.output

    # Should NOT appear
    def test_exclude_tenderdash_1248(self):
        """tenderdash#1248 has commits only on 2026-02-06."""
        assert "#1248" not in self.output

    def test_summary_merged_today(self):
        assert "merged today" in self.output


# ---------------------------------------------------------------------------
# TC02: Date range spanning weekend (2026-02-06 to 2026-02-09)
# See tc02_range_feb06_to_feb09.md
# ---------------------------------------------------------------------------
class TestRangeFeb06ToFeb09:
    """Range 2026-02-06..2026-02-09: verify PRs across multiple days."""

    @pytest.fixture(autouse=True, scope="class")
    def _output(self, request):
        request.cls.output = run_report_ok("--from", "2026-02-06", "--to", "2026-02-09")

    def test_header_range_format(self):
        assert "2026-02-06 .. 2026-02-09" in self.output

    # Authored PRs
    def test_authored_tenderdash_1248(self):
        assert "#1248" in self.output

    def test_authored_platform_3068(self):
        assert "#3068" in self.output

    def test_authored_platform_3072(self):
        assert "#3072" in self.output

    def test_authored_tenderdash_1250(self):
        assert "#1250" in self.output

    def test_authored_platform_3067(self):
        assert "#3067" in self.output

    def test_authored_platform_3065(self):
        assert "#3065" in self.output

    # Reviewed PRs
    def test_reviewed_platform_3062(self):
        assert "#3062" in self.output

    def test_reviewed_evo_tool_534(self):
        assert "#534" in self.output

    def test_reviewed_evo_tool_535(self):
        assert "#535" in self.output

    # NOTE: tenderdash#1252 not tested - GitHub search API limitation for bot-authored PRs

    def test_reviewed_platform_3073(self):
        assert "#3073" in self.output

    # Should NOT appear
    def test_exclude_evo_tool_552(self):
        """dash-evo-tool#552: commits only on 2026-02-10/11."""
        assert "#552" not in self.output

    def test_exclude_evo_tool_556(self):
        """dash-evo-tool#556: commits only on 2026-02-11."""
        assert "#556" not in self.output

    def test_exclude_platform_3059(self):
        """platform#3059: reviewed on 2026-02-05."""
        assert "#3059" not in self.output

    def test_summary_merged_not_today(self):
        assert "merged today" not in self.output
        assert " merged," in self.output


# ---------------------------------------------------------------------------
# TC03: Quiet range (2026-02-07 to 2026-02-08) - no known activity
# See tc03_quiet_range_feb07_to_feb08.md
# ---------------------------------------------------------------------------
class TestQuietRangeFeb07ToFeb08:
    """Range 2026-02-07..2026-02-08: minimal/no activity expected."""

    @pytest.fixture(autouse=True, scope="class")
    def _output(self, request):
        request.cls.output = run_report_ok("--from", "2026-02-07", "--to", "2026-02-08")

    def test_header_range_format(self):
        assert "2026-02-07 .. 2026-02-08" in self.output

    def test_exclude_tenderdash_1248(self):
        """Commits only on 2026-02-06."""
        assert "#1248" not in self.output

    def test_exclude_platform_3072(self):
        """Commits only on 2026-02-09."""
        assert "#3072" not in self.output

    def test_exclude_evo_tool_534(self):
        """lklimek review on 2026-02-09."""
        assert "#534" not in self.output

    def test_exclude_evo_tool_535(self):
        """lklimek review on 2026-02-09."""
        assert "#535" not in self.output

    def test_exclude_platform_3068(self):
        """Commits on 2026-02-06 and 2026-02-09, none in 07-08."""
        assert "#3068" not in self.output

    def test_summary_merged_not_today(self):
        assert "merged today" not in self.output


# ---------------------------------------------------------------------------
# TC04: Same-day range (--from X --to X == --date X)
# See tc04_same_day_range_2026_02_06.md
# ---------------------------------------------------------------------------
class TestSameDayRange20260206:
    """--from 2026-02-06 --to 2026-02-06 must equal --date 2026-02-06."""

    @pytest.fixture(autouse=True, scope="class")
    def _outputs(self, request):
        request.cls.output_date = run_report_ok("--date", "2026-02-06")
        request.cls.output_range = run_report_ok(
            "--from", "2026-02-06", "--to", "2026-02-06"
        )

    def test_identical_output(self):
        assert self.output_date == self.output_range

    def test_header_single_date(self):
        assert "2026-02-06" in self.output_date
        assert ".." not in self.output_date

    def test_authored_tenderdash_1248(self):
        assert "#1248" in self.output_date

    def test_authored_platform_3068(self):
        assert "#3068" in self.output_date

    def test_authored_platform_3067(self):
        assert "#3067" in self.output_date

    def test_authored_platform_3065(self):
        assert "#3065" in self.output_date

    def test_reviewed_platform_3062(self):
        assert "#3062" in self.output_date

    def test_summary_merged_today(self):
        assert "merged today" in self.output_date


# ---------------------------------------------------------------------------
# TC05: Full week (2026-02-03 to 2026-02-09)
# See tc05_full_week_feb03_to_feb09.md
# ---------------------------------------------------------------------------
class TestFullWeekFeb03ToFeb09:
    """Wide range covering full work week."""

    @pytest.fixture(autouse=True, scope="class")
    def _output(self, request):
        request.cls.output = run_report_ok("--from", "2026-02-03", "--to", "2026-02-09")

    def test_header_range_format(self):
        assert "2026-02-03 .. 2026-02-09" in self.output

    # Authored PRs
    def test_authored_evo_tool_523(self):
        assert "#523" in self.output

    def test_authored_grovestark(self):
        assert "grovestark" in self.output

    def test_authored_evo_tool_527(self):
        assert "#527" in self.output

    def test_authored_evo_tool_532(self):
        assert "#532" in self.output

    def test_authored_evo_tool_531(self):
        assert "#531" in self.output

    def test_authored_platform_3056(self):
        assert "#3056" in self.output

    def test_authored_tenderdash_1248(self):
        assert "#1248" in self.output

    def test_authored_platform_3068(self):
        assert "#3068" in self.output

    def test_authored_platform_3067(self):
        assert "#3067" in self.output

    def test_authored_platform_3065(self):
        assert "#3065" in self.output

    def test_authored_platform_3072(self):
        assert "#3072" in self.output

    def test_authored_tenderdash_1250(self):
        assert "#1250" in self.output

    # Reviewed PRs
    def test_reviewed_platform_3059(self):
        assert "#3059" in self.output

    def test_reviewed_platform_3062(self):
        assert "#3062" in self.output

    def test_reviewed_evo_tool_534(self):
        assert "#534" in self.output

    def test_reviewed_evo_tool_535(self):
        assert "#535" in self.output

    # NOTE: tenderdash#1252 not tested - GitHub search API limitation for bot-authored PRs

    def test_reviewed_platform_3073(self):
        assert "#3073" in self.output

    # Should NOT appear
    def test_exclude_evo_tool_552(self):
        """Commits only on 2026-02-10/11."""
        assert "#552" not in self.output

    def test_exclude_evo_tool_554(self):
        """Reviewed only on 2026-02-11."""
        assert "#554" not in self.output

    def test_exclude_tenderdash_1244(self):
        """Commits only on 2026-01-28."""
        assert "#1244" not in self.output

    def test_summary_merged_not_today(self):
        assert "merged today" not in self.output

    def test_multiple_repos(self):
        assert "platform" in self.output
        assert "tenderdash" in self.output
        assert "dash-evo-tool" in self.output


# ---------------------------------------------------------------------------
# TC06: PR updated in range but no user commit in range
# See tc06_pr_updated_in_range_no_user_commit.md
# ---------------------------------------------------------------------------
class TestPRUpdatedNoCommitInRange:
    """PR updated within range but user's commits are all outside range."""

    @pytest.fixture(autouse=True, scope="class")
    def _output(self, request):
        request.cls.output = run_report_ok("--from", "2026-02-08", "--to", "2026-02-10")

    def test_header_range_format(self):
        assert "2026-02-08 .. 2026-02-10" in self.output

    def test_exclude_evo_tool_521_from_authored(self):
        """dash-evo-tool#521: commits on 2026-02-02/03, updatedAt=2026-02-10.
        Should NOT appear as authored since no commits in 2026-02-08..2026-02-10."""
        # Split output into sections
        lines = self.output
        authored_section = lines.split("**Reviewed")[0] if "**Reviewed" in lines else lines
        assert "#521" not in authored_section

    def test_exclude_evo_tool_514_from_authored(self):
        """dash-evo-tool#514: commits on 2026-02-02, updatedAt=2026-02-10.
        Should NOT appear as authored since no commits in 2026-02-08..2026-02-10."""
        lines = self.output
        authored_section = lines.split("**Reviewed")[0] if "**Reviewed" in lines else lines
        assert "#514" not in authored_section


# ---------------------------------------------------------------------------
# TC07: PR updated after range but has commit within range
# See tc07_pr_updated_after_range_commit_in_range.md
# ---------------------------------------------------------------------------
class TestPRCommitInRangeUpdatedAfter:
    """PR with commits in range should appear even if updatedAt is later."""

    @pytest.fixture(autouse=True, scope="class")
    def _output(self, request):
        request.cls.output = run_report_ok("--date", "2026-02-03")

    def test_header_single_date(self):
        assert "2026-02-03" in self.output

    def test_authored_evo_tool_523(self):
        """dash-evo-tool#523: commits on 2026-02-03, updatedAt=2026-02-04.
        Should appear because it has commits on 2026-02-03."""
        assert "#523" in self.output

    def test_summary_merged_today(self):
        assert "merged today" in self.output


# ---------------------------------------------------------------------------
# TC08: Consolidation — single day (2026-02-09) with --consolidate
# Uses claude-haiku-4-5-20251001 (cheapest model)
# ---------------------------------------------------------------------------
_CONSOLIDATE_MODEL = "claude-haiku-4-5-20251001"


class TestConsolidateSingleDay20260209:
    """--consolidate on 2026-02-09: verify AI-summarised output."""

    @pytest.fixture(autouse=True, scope="class")
    def _output(self, request):
        request.cls.output = run_report_ok(
            "--date", "2026-02-09",
            "--consolidate", "--model", _CONSOLIDATE_MODEL,
        )

    def test_header_single_date(self):
        assert "2026-02-09" in self.output

    def test_has_summary_heading(self):
        """Consolidated output uses 'Summary' block heading."""
        assert "**Summary**" in self.output

    def test_no_authored_or_reviewed_headings(self):
        """Consolidated mode replaces per-type headings with Summary."""
        assert "**Authored" not in self.output
        assert "**Reviewed" not in self.output
        assert "**Waiting" not in self.output

    def test_platform_repo_present(self):
        assert "platform" in self.output

    def test_tenderdash_repo_present(self):
        assert "tenderdash" in self.output

    def test_pr_numbers_referenced(self):
        """AI summaries must reference PR numbers."""
        assert "#3072" in self.output or "#3068" in self.output

    def test_summary_stats_present(self):
        """Footer summary stats should still be present."""
        assert "PRs across" in self.output
        assert "merged today" in self.output


class TestConsolidateRange:
    """--consolidate on a date range: verify AI-summarised output."""

    @pytest.fixture(autouse=True, scope="class")
    def _output(self, request):
        request.cls.output = run_report_ok(
            "--from", "2026-02-06", "--to", "2026-02-09",
            "--consolidate", "--model", _CONSOLIDATE_MODEL,
        )

    def test_header_range(self):
        assert "2026-02-06 .. 2026-02-09" in self.output

    def test_has_summary_heading(self):
        assert "**Summary**" in self.output

    def test_multiple_repos(self):
        assert "platform" in self.output
        assert "tenderdash" in self.output

    def test_pr_numbers_referenced(self):
        assert "#" in self.output

    def test_summary_stats_range(self):
        assert "PRs across" in self.output
        assert "merged today" not in self.output
        assert " merged," in self.output


class TestConsolidateSlides:
    """--consolidate --slides: verify PPTX generation works."""

    @pytest.fixture(autouse=True, scope="class")
    def _output(self, request, tmp_path_factory):
        out_path = str(tmp_path_factory.mktemp("slides") / "consolidated.pptx")
        request.cls.slides_path = out_path
        request.cls.output = run_report_ok(
            "--date", "2026-02-09",
            "--consolidate", "--model", _CONSOLIDATE_MODEL,
            "--slides", "--slides-output", out_path,
        )

    def test_file_created(self):
        from pathlib import Path
        assert Path(self.slides_path).exists()

    def test_output_message(self):
        assert "Slides written to" in self.output


# ---------------------------------------------------------------------------
# TC09: --summary flag (AI-generated summary)
# Uses claude-haiku-4-5-20251001 (cheapest model)
# ---------------------------------------------------------------------------

class TestSummarySingleDay:
    """--summary on single day: AI summary replaces default stats."""

    @pytest.fixture(autouse=True, scope="class")
    def _output(self, request):
        request.cls.output = run_report_ok(
            "--date", "2026-02-09",
            "--summary", "--model", _CONSOLIDATE_MODEL,
        )

    def test_header_present(self):
        assert "2026-02-09" in self.output

    def test_has_summary_label(self):
        assert "**Summary:**" in self.output

    def test_no_default_stats(self):
        """AI summary should replace the default stats breakdown."""
        assert "PRs across" not in self.output
        assert "merged today" not in self.output
        assert "Key themes:" not in self.output

    def test_summary_is_short(self):
        """AI summary should be under 320 chars."""
        for line in self.output.splitlines():
            if line.startswith("**Summary:**"):
                # Strip the "**Summary:** " prefix
                summary_text = line[len("**Summary:** "):]
                assert len(summary_text) <= 320
                assert len(summary_text) > 0
                break
        else:
            pytest.fail("No **Summary:** line found")


class TestSummaryWithConsolidate:
    """--summary combined with --consolidate."""

    @pytest.fixture(autouse=True, scope="class")
    def _output(self, request):
        request.cls.output = run_report_ok(
            "--date", "2026-02-09",
            "--consolidate", "--summary", "--model", _CONSOLIDATE_MODEL,
        )

    def test_has_consolidated_content(self):
        """Per-repo sections should use Summary heading (consolidated)."""
        # Count occurrences — repo content headings + footer summary
        assert "**Summary**" in self.output or "**Summary:**" in self.output

    def test_no_default_stats(self):
        assert "PRs across" not in self.output

    def test_repos_present(self):
        assert "platform" in self.output


class TestSummaryCliValidation:
    """CLI validation for --summary and --model."""

    def test_model_without_consolidate_or_summary_errors(self):
        output, rc = run_report("--model", "claude-haiku-4-5-20251001")
        assert rc != 0
        assert "--model requires --consolidate or --summary" in output
