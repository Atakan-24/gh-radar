"""The digest itself, and the state that keeps it from repeating.

These protect the two failure modes that would quietly kill the habit of
reading it: saying the same thing every morning, and making a bad day look
like a quiet one.
"""

import datetime as dt
import json

from radar import __main__ as cli
from radar import report, watch

NOW = dt.datetime(2026, 8, 29, tzinfo=dt.timezone.utc)


def empty_scout(**over):
    base = {"picks": [], "examined": 0, "rejected": 0, "surviving": 0, "rate_limited": False}
    base.update(over)
    return base


def pick(**over):
    base = {
        "repo": "acme/tool", "title": "Fix the retry backoff",
        "url": "https://github.com/acme/tool/issues/1", "effort": "small",
        "reasons": ["labelled 'good first issue'"], "warnings": [], "points": 88,
    }
    base.update(over)
    return base


# --- the quiet day ---------------------------------------------------------


def test_a_day_with_nothing_says_so_plainly():
    text = report.render(watch_results=[], scout_result=empty_scout(examined=61), news={})
    assert "Nothing needs you today" in text
    assert "normal outcome" in text, "a quiet day must not read like a failure"


def test_a_throttled_run_never_looks_like_a_quiet_one():
    """The most dangerous silent failure this tool has: an empty digest
    because GitHub refused, indistinguishable from an empty digest because
    there was no news."""
    text = report.render(
        watch_results=[], scout_result=empty_scout(rate_limited=True), news={}
    )
    assert "rate limit" in text.lower()
    assert "incomplete" in text.lower()


# --- urgency ---------------------------------------------------------------


def watch_result(**over):
    base = {
        "repo": "you/thing", "url": "", "stars": 3, "forks": 0, "watchers": 1,
        "traffic": None, "ci": None, "findings": [],
    }
    base.update(over)
    return base


def test_a_stranger_waiting_is_the_first_thing_you_read():
    text = report.render(
        watch_results=[watch_result(findings=[{
            "kind": "external_issue", "urgent": True, "title": "Crash on empty input",
            "url": "u", "author": "someone", "waited_hours": 26.0, "unanswered": True,
        }])],
        scout_result=empty_scout(picks=[pick()]),
        news={},
    )
    assert text.index("SOMEONE IS WAITING ON YOU") < text.index("WORTH A LOOK")


def test_an_external_pull_request_is_always_urgent():
    """Somebody wrote code for your project. Nothing outranks that."""
    finding = {
        "kind": "external_pr", "urgent": True, "title": "Add Windows support",
        "url": "u", "author": "someone", "waited_hours": 2.0, "unanswered": True,
    }
    text = report.render(
        watch_results=[watch_result(findings=[finding])], scout_result=empty_scout(), news={}
    )
    assert "PULL REQUEST" in text


def test_suggestions_carry_their_reasons_into_the_digest():
    text = report.render(
        watch_results=[], news={},
        scout_result=empty_scout(picks=[pick(reasons=["labelled 'good first issue'", "Python"])]),
    )
    assert "good first issue" in text
    assert "~30 min" in text, "effort must be visible; the reader has two hours a week"


def test_warnings_are_shown_not_hidden():
    text = report.render(
        watch_results=[], news={},
        scout_result=empty_scout(picks=[pick(warnings=["1,671 open issues — yours may not be seen"])]),
    )
    assert "1,671 open issues" in text


def test_headline_summarises_without_the_body():
    line = report.render_short(
        watch_results=[watch_result(findings=[{"kind": "ci_failing", "urgent": True}])],
        scout_result=empty_scout(picks=[pick()]),
    )
    assert "waiting on you" in line and "worth a look" in line


def test_headline_on_a_quiet_day():
    assert report.render_short(watch_results=[], scout_result=empty_scout()) == "nothing needs you today"


# --- what changed ----------------------------------------------------------


def test_only_increases_are_news():
    """Losing a star is not something to be told about at 7am."""
    assert watch.diff_against({"stars": 3}, {"stars": 5}) == ["+2 stars (now 5)"]
    assert watch.diff_against({"stars": 5}, {"stars": 3}) == []
    assert watch.diff_against({"stars": 5}, {"stars": 5}) == []


def test_a_single_star_is_singular():
    assert watch.diff_against({"stars": 0}, {"stars": 1}) == ["+1 star (now 1)"]


def test_first_run_with_no_history_reports_nothing_rather_than_everything():
    """Otherwise day one is a wall of 'news' that is just current state."""
    assert watch.diff_against({}, {"stars": 0, "forks": 0, "watchers": 0}) == []


# --- releases are only suggested where they mean something -----------------


def test_a_profile_readme_is_never_told_to_cut_a_release():
    assert watch.is_releasable({"name": "Atakan-24", "language": "Markdown"}, user="Atakan-24") is False


def test_a_pages_site_is_never_told_to_cut_a_release():
    assert watch.is_releasable({"name": "atakan-24.github.io", "language": "HTML"}, user="Atakan-24") is False


def test_a_real_tool_is():
    assert watch.is_releasable({"name": "git-secret-scan", "language": "Python"}, user="Atakan-24") is True


def test_a_repository_with_no_code_is_not():
    assert watch.is_releasable({"name": "notes", "language": None}, user="Atakan-24") is False


# --- state -----------------------------------------------------------------


def test_a_corrupt_state_file_is_treated_as_no_state(tmp_path):
    """A half-written file from a machine that rebooted mid-write must not
    be the reason the digest fails to arrive."""
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert cli.load_state(path) == {}


def test_a_missing_state_file_is_fine(tmp_path):
    assert cli.load_state(tmp_path / "absent.json") == {}


def test_state_survives_a_round_trip(tmp_path):
    path = tmp_path / "state.json"
    cli.save_state(path, {"seen_issues": {"u": "2026-08-29T00:00:00+00:00"}})
    assert cli.load_state(path)["seen_issues"] == {"u": "2026-08-29T00:00:00+00:00"}


def test_state_is_written_atomically(tmp_path):
    """Written to a temp file and moved, so an interrupted write leaves the
    previous state intact rather than a truncated one."""
    path = tmp_path / "state.json"
    cli.save_state(path, {"a": 1})
    cli.save_state(path, {"b": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"b": 2}
    assert not path.with_suffix(".tmp").exists()


def test_suggestions_stop_repeating_but_come_back_eventually():
    seen = {
        "recent": (NOW - dt.timedelta(days=2)).isoformat(),
        "old": (NOW - dt.timedelta(days=cli.SUPPRESS_DAYS + 1)).isoformat(),
    }
    kept = cli.prune_seen(seen, NOW)
    assert "recent" in kept, "a suggestion from Monday must not reappear on Tuesday"
    assert "old" not in kept, "a skipped one must get a second chance eventually"


def test_unparseable_timestamps_are_dropped_not_fatal():
    assert cli.prune_seen({"u": "not-a-date"}, NOW) == {}


# --- language detection ----------------------------------------------------


def test_language_detection_ranks_by_how_much_you_actually_write():
    repos = [
        {"language": "Python"}, {"language": "Python"}, {"language": "Python"},
        {"language": "TypeScript"}, {"language": "TypeScript"},
        {"language": "HTML"},
    ]
    assert cli.detect_languages(repos)[:2] == ["Python", "TypeScript"]


def test_language_detection_ignores_repositories_without_one():
    assert cli.detect_languages([{"language": None}, {"language": "Go"}]) == ["Go"]


def test_language_detection_falls_back_rather_than_returning_nothing():
    """An empty language list would produce zero queries and a permanently
    silent scout — which looks exactly like 'no opportunities today'."""
    assert cli.detect_languages([]) == ["Python"]
