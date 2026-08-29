"""Scoring is where this tool is right or wrong, so it gets the most tests.

Every case below is one the live API actually produced on 2026-08-29 while
the tool was being built. None of them are invented: the farm repository,
the nine-year-old ticket and the two-star project were all real top
suggestions before the filter that now excludes them existed.
"""

import datetime as dt

import pytest

from radar import score

NOW = dt.datetime(2026, 8, 29, tzinfo=dt.timezone.utc)
LANGS = {"Python", "TypeScript"}


def iso(days_ago: float) -> str:
    return (NOW - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def issue(**over):
    base = {
        "title": "Fix the retry backoff in the uploader",
        "body": "x" * 400,
        "state": "open",
        "comments": 0,
        "created_at": iso(5),
        "labels": [{"name": "good first issue"}],
        "html_url": "https://github.com/acme/tool/issues/1",
        "repository_url": "https://api.github.com/repos/acme/tool",
    }
    base.update(over)
    return base


def repo(**over):
    base = {
        "full_name": "acme/tool",
        "language": "Python",
        "stargazers_count": 3000,
        "open_issues_count": 40,
        "pushed_at": iso(3),
        "archived": False,
        "fork": False,
        "has_issues": True,
        "description": "A tool",
    }
    base.update(over)
    return base


# --- hard exclusions -------------------------------------------------------


def test_a_healthy_issue_is_not_disqualified():
    assert score.disqualify(issue(), repo(), NOW) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "closed"),
        ("assignee", {"login": "someone"}),
        ("assignees", [{"login": "someone"}]),
        ("pull_request", {"url": "..."}),
    ],
)
def test_issues_somebody_else_already_owns_are_excluded(field, value):
    assert score.disqualify(issue(**{field: value}), repo(), NOW) is not None


@pytest.mark.parametrize("flag", ["archived", "disabled", "fork"])
def test_dead_or_derived_repositories_are_excluded(flag):
    assert score.disqualify(issue(), repo(**{flag: True}), NOW) is not None


def test_repository_with_issues_disabled_is_excluded():
    assert score.disqualify(issue(), repo(has_issues=False), NOW) is not None


def test_tiny_repositories_are_excluded_because_nobody_would_see_it():
    """Real case: a 2-star repo was a top suggestion before this existed."""
    reason = score.disqualify(issue(), repo(stargazers_count=2), NOW)
    assert reason is not None and "stars" in reason


def test_a_crowd_of_comments_means_it_is_not_an_opening():
    reason = score.disqualify(issue(comments=166), repo(), NOW)
    assert reason is not None and "comments" in reason


# --- engagement farming ----------------------------------------------------
# The single most important filter here. These repositories use the exact
# labels this tool searches for and carry real star counts, so every other
# quality signal points the wrong way.


@pytest.mark.parametrize(
    "title",
    [
        "[BOUNTY: 5 RTC] Fix ALL broken README links",
        "[BOUNTY] Share Why You Starred RustChain — 3 RTC + Community Shoutout",
        "Airdrop for contributors!",
        "Star this repo and get 10 USDT",
        "Giveaway: 100 TOKENS for the first PR",
    ],
)
def test_reward_farming_is_refused(title):
    assert score.looks_like_farm(issue(title=title)) is not None


def test_farming_is_caught_from_the_repository_name_too():
    found = score.looks_like_farm(issue(title="Add a docstring"), repo(full_name="x/rustchain-bounties"))
    assert found is not None


def test_farming_is_a_disqualification_not_a_deduction():
    """It must never be possible to out-score this. A high-star, fresh,
    well-labelled farm issue would otherwise reach the top of the digest."""
    farm = issue(title="[BOUNTY: 50 RTC] fix the docs")
    assert score.disqualify(farm, repo(stargazers_count=9000), NOW) is not None


def test_ordinary_engineering_words_are_not_mistaken_for_farming():
    """The filter must not eat real work. 'Token' in a parser context is not
    a cryptocurrency, and 'star' appears in plenty of legitimate titles."""
    for title in (
        "Tokenizer drops the final token on empty input",
        "Fix star-import handling in the linter",
        "Reward function returns NaN for empty batches",
    ):
        assert score.looks_like_farm(issue(title=title)) is None, title


# --- scoring ---------------------------------------------------------------


def test_language_you_ship_in_beats_one_you_do_not():
    mine = score.score(issue(), repo(language="Python"), languages=LANGS, now=NOW)
    theirs = score.score(issue(), repo(language="Haskell"), languages=LANGS, now=NOW)
    assert mine["points"] > theirs["points"]


def test_staleness_penalty_grows_with_age():
    """A flat penalty let a 3,284-day-old ticket score 56 and reach the
    digest. Nine years must cost far more than seven months."""
    fresh = score.score(issue(created_at=iso(5)), repo(), languages=LANGS, now=NOW)
    old = score.score(issue(created_at=iso(200)), repo(), languages=LANGS, now=NOW)
    ancient = score.score(issue(created_at=iso(3284)), repo(), languages=LANGS, now=NOW)
    assert fresh["points"] > old["points"] > ancient["points"]
    assert fresh["points"] - ancient["points"] > 60


def test_an_abandoned_repository_outweighs_a_good_ticket():
    """A perfect issue in a repo nobody has touched in a year is not an
    opportunity — the pull request would sit unread."""
    active = score.score(issue(), repo(pushed_at=iso(3)), languages=LANGS, now=NOW)
    dead = score.score(issue(), repo(pushed_at=iso(400)), languages=LANGS, now=NOW)
    assert active["points"] - dead["points"] >= 40


def test_every_point_awarded_carries_a_stated_reason():
    """The digest prints these. A suggestion that cannot explain itself in
    one line has no business being in a list somebody reads in 30 seconds."""
    result = score.score(issue(), repo(), languages=LANGS, now=NOW)
    assert result["reasons"], "a positively scored issue must say why"
    assert all(isinstance(r, str) and r.strip() for r in result["reasons"])


def test_warnings_are_reported_rather_than_silently_deducted():
    result = score.score(issue(comments=8), repo(stargazers_count=90_000), languages=LANGS, now=NOW)
    assert result["warnings"]


def test_scoring_never_mutates_its_input():
    original = issue()
    snapshot = dict(original)
    score.score(original, repo(), languages=LANGS, now=NOW)
    assert original == snapshot


# --- effort ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Fix typo in README", "small"),
        ("Update the documentation for the CLI", "small"),
        ("Refactor the scheduler into separate modules", "large"),
        ("Migrate the storage layer to Postgres", "large"),
        ("Handle empty response from the upstream API", "medium"),
    ],
)
def test_effort_hint_sorts_by_size(title, expected):
    assert score.effort_hint(issue(title=title)) == expected


def test_effort_reads_labels_as_well_as_the_title():
    assert score.effort_hint(issue(title="Improve this", labels=[{"name": "documentation"}])) == "small"


# --- helpers ---------------------------------------------------------------


def test_repo_name_is_recovered_from_the_api_url():
    assert score.repo_full_name(issue()) == "acme/tool"


def test_missing_repository_url_does_not_raise():
    assert score.repo_full_name({"repository_url": ""}) == ""
    assert score.repo_full_name({}) == ""


def test_scoring_survives_a_missing_repository():
    """Repo lookups fail — rate limits, deleted repos, permissions. A
    failed lookup must degrade the score, not crash the whole run."""
    result = score.score(issue(), None, languages=LANGS, now=NOW)
    assert isinstance(result["points"], int)
