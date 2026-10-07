"""Heuristic issue ranking.

Each weighted signal records its reason. Weights are hand-selected, not
measured effort estimates; exclusions remove unsuitable candidates first."""

from __future__ import annotations

import datetime as dt
import re

# A repository nobody has pushed to in this long will probably not review a
# pull request either. Refuted if a maintainer merges something after a
# longer gap -- it happens, which is why this reduces the score instead of
# excluding outright.
STALE_REPO_DAYS = 90

# An issue older than this in an *active* repository is a warning, not a
# find: if it were easy and wanted, somebody in that project would have done
# it. Refuted if such issues turn out to merge as readily as fresh ones.
STALE_ISSUE_DAYS = 180

# Past this, the pull-request queue is long enough that a first-time
# contributor waits months. Refuted by a merged PR from a stranger inside a
# few weeks on a repo above the line.
CROWDED_STARS = 40_000

# Below this a project is usually one person's side project. The code may be
# fine and the maintainer may answer in an hour -- but a merged PR there is
# not something a third party will ever see, and being seen is the entire
# reason for contributing outward rather than inward.
#
# Measured 2026-08-29: without a hard floor the top suggestions were repos
# with 28 and 2 stars. Technically valid tickets, strategically worthless.
# Refuted if a contribution to a sub-threshold project produces a real
# professional contact -- which does happen, and is why this is a floor on
# the *suggestion*, not a ban on the contribution.
THIN_STARS = 150
FLOOR_STARS = 60

# More than a handful of comments usually means somebody already claimed it
# or the scope is being argued. Either way it is not a clean first move.
BUSY_COMMENTS = 6

# An issue body this short cannot be acted on without a conversation first,
# and that conversation is the expensive part.
THIN_BODY_CHARS = 180

# Labels that mean "we want outside help". Ordered by how strongly.
WELCOMING_LABELS = {
    "good first issue": 26,
    "good-first-issue": 26,
    "help wanted": 20,
    "help-wanted": 20,
    "up-for-grabs": 18,
    "beginner friendly": 16,
    "first-timers-only": 24,
    "documentation": 8,
    "bug": 10,
}

# Labels that mean "do not start here".
BLOCKING_LABELS = {
    "wontfix", "invalid", "duplicate", "question", "discussion",
    "needs design", "blocked", "on hold", "stale", "rfc",
}

# Engagement farming, and why it needs its own filter.
#
# Measured 2026-08-29: with only star and label filters in place, two of the
# three top suggestions came from a token-bounty repository -- including an
# issue titled "Share Why You Starred RustChain -- 3 RTC + Community
# Shoutout" with 166 comments. These farms use exactly the labels this tool
# searches for, carry real star counts, and are active daily, so every
# quality signal points the wrong way.
#
# They cannot be scored down, only excluded. Contributing there is not a
# weak move, it is a harmful one: the profile it produces reads as somebody
# farming rewards, which is the opposite of the credibility this whole
# exercise is for.
#
# Plurals matter here and are easy to miss: `\bbounty\b` does not match
# "bounties", and the repository that prompted this filter is literally
# named `rustchain-bounties`. That gap was found by a test, not by reading
# the regex -- on the live run only the issue title happened to match, so a
# neutrally-titled ticket in the same farm would have sailed through.
FARM_PATTERNS = (
    r"\bbount(?:y|ies)\b", r"\bairdrops?\b", r"\bgiveaways?\b", r"\bshoutouts?\b",
    r"\breward pool\b", r"\bpool:\s*\d", r"\bwhy you starred\b",
    r"\bstar (?:this|the) repo\b", r"\bfollow (?:me|us) (?:on|and)\b",
    r"\b\d+\s*(?:RTC|USDT|USDC|SOL|ETH|BNB)\b",
    r"\b\d+\s*tokens\b",
)


def _parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_days(value: str | None, now: dt.datetime) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return (now - parsed).total_seconds() / 86400.0


def repo_full_name(issue: dict) -> str:
    """Search results carry `repository_url`, not a repo object."""
    url = issue.get("repository_url") or ""
    parts = [p for p in url.split("/") if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else ""


def looks_like_farm(issue: dict, repo: dict | None = None) -> str | None:
    """Detect reward-for-engagement tickets. Returns the trigger, or None.

    Checked against title, body and repository name -- a farm usually
    advertises itself in all three, and matching any one of them is enough
    because the cost of a false negative (recommending it) is far higher
    than the cost of a false positive (skipping one real bounty issue).
    """
    haystack = " ".join([
        issue.get("title", "") or "",
        (issue.get("body") or "")[:1500],
        (repo or {}).get("full_name", "") or "",
        (repo or {}).get("description", "") or "",
    ]).lower()
    for pattern in FARM_PATTERNS:
        match = re.search(pattern, haystack, re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return None


def disqualify(issue: dict, repo: dict | None, now: dt.datetime) -> str | None:
    """Hard exclusions. Returns the reason, or None if the issue survives.

    These are separate from scoring on purpose. A pull request is not
    "slightly worse" because the repository is archived -- it is impossible,
    and a scoring system that lets impossible things through with a low
    number will eventually surface one on a quiet day.
    """
    if issue.get("pull_request"):
        return "is a pull request, not an issue"
    if issue.get("state") != "open":
        return "closed"
    if issue.get("assignee") or issue.get("assignees"):
        return "already assigned"
    if issue.get("draft"):
        return "draft"

    labels = {str(lbl.get("name", "")).lower() for lbl in issue.get("labels", []) if isinstance(lbl, dict)}
    blocking = labels & BLOCKING_LABELS
    if blocking:
        return f"labelled {sorted(blocking)[0]}"

    farm = looks_like_farm(issue, repo)
    if farm:
        return f"engagement farming ('{farm}')"

    # A crowd this size means the ticket is a discussion, a contest, or
    # already somebody else's. None of those is a contribution opportunity.
    if int(issue.get("comments") or 0) >= 25:
        return f"{issue.get('comments')} comments — not an opening"

    if repo:
        if repo.get("archived"):
            return "repository archived"
        if repo.get("disabled"):
            return "repository disabled"
        if repo.get("fork"):
            return "repository is a fork"
        # A repository that does not accept issues from outside is not a
        # place to start; the ticket may exist but the workflow does not.
        if repo.get("has_issues") is False:
            return "issues disabled"
        # Below the floor the contribution is invisible to anyone who is not
        # the maintainer. That is a fine way to spend a Saturday and a poor
        # way to spend the one slot this digest gets to recommend.
        stars = int(repo.get("stargazers_count") or 0)
        if stars < FLOOR_STARS:
            return f"only {stars} stars — nobody would see the contribution"

    return None


def score(issue: dict, repo: dict | None, *, languages: set[str], now: dt.datetime | None = None) -> dict:
    """Score one issue. Returns points plus the reasons that produced them.

    The reasons are not decoration -- they are printed in the digest. If a
    suggestion cannot explain itself in one line, it should not be in the
    list.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    points = 0
    reasons: list[str] = []
    warnings: list[str] = []

    labels = {str(lbl.get("name", "")).lower() for lbl in issue.get("labels", []) if isinstance(lbl, dict)}
    best_label = max((WELCOMING_LABELS.get(name, 0) for name in labels), default=0)
    if best_label:
        matched = next(name for name in labels if WELCOMING_LABELS.get(name, 0) == best_label)
        points += best_label
        reasons.append(f"labelled '{matched}'")

    lang = (repo or {}).get("language")
    if lang and lang.lower() in {value.lower() for value in languages}:
        points += 22
        reasons.append(f"{lang} — a language you actually ship in")
    elif lang:
        points -= 12
        warnings.append(f"{lang} is outside your stack")

    issue_age = _age_days(issue.get("created_at"), now)
    if issue_age is not None:
        if issue_age <= 14:
            points += 14
            reasons.append("opened in the last two weeks")
        elif issue_age <= 60:
            points += 6
        elif issue_age > STALE_ISSUE_DAYS:
            # Progressive, not flat. A flat penalty let a nine-year-old
            # ticket (3,284 days, measured 2026-08-29) score 56 and reach
            # the digest. An issue that has survived that long in a healthy
            # project is not waiting for a newcomer -- it is waiting for a
            # reason it is still open, and that reason is rarely "nobody
            # got around to it".
            years = issue_age / 365.0
            points -= min(60, int(14 + 18 * years))
            label = f"{years:.0f} years" if years >= 1 else f"{int(issue_age)} days"
            warnings.append(f"open for {label} — probably harder than it reads")

    comments = int(issue.get("comments") or 0)
    if comments == 0:
        points += 12
        reasons.append("nobody has replied yet")
    elif comments >= BUSY_COMMENTS:
        points -= 16
        warnings.append(f"{comments} comments — likely already claimed or contested")

    body = (issue.get("body") or "").strip()
    if len(body) >= THIN_BODY_CHARS:
        points += 10
        reasons.append("the ticket is actually specified")
    else:
        points -= 10
        warnings.append("thin description — needs clarification before any code")

    if repo:
        stars = int(repo.get("stargazers_count") or 0)
        if THIN_STARS <= stars <= CROWDED_STARS:
            points += 12
            reasons.append(f"{stars:,} stars — visible, but the PR queue is survivable")
        elif stars > CROWDED_STARS:
            points -= 10
            warnings.append(f"{stars:,} stars — expect a long review queue")
        elif stars < THIN_STARS:
            points -= 18
            warnings.append(f"only {stars} stars — modest visibility")

        pushed_age = _age_days(repo.get("pushed_at"), now)
        if pushed_age is not None:
            if pushed_age <= 14:
                points += 16
                reasons.append("maintainers were active this fortnight")
            elif pushed_age > STALE_REPO_DAYS:
                points -= 26
                warnings.append(f"no push in {int(pushed_age)} days — a PR may never be looked at")

        open_issues = int(repo.get("open_issues_count") or 0)
        if open_issues > 900:
            points -= 8
            warnings.append(f"{open_issues:,} open issues — yours may not be seen")

    return {
        "points": points,
        "reasons": reasons,
        "warnings": warnings,
        "repo": repo_full_name(issue),
        "title": issue.get("title", ""),
        "url": issue.get("html_url", ""),
        "number": issue.get("number"),
        "language": lang,
        "comments": comments,
    }


def effort_hint(issue: dict) -> str:
    """A rough read of how big the job is, from the words people use.

    Deliberately coarse. The point is to protect two hours a week, not to
    estimate precisely: 'typo in the README' and 'rewrite the scheduler' need
    to land in different buckets, and everything in between can say so.
    """
    text = f"{issue.get('title', '')} {issue.get('body', '') or ''}".lower()
    names = (str(lbl.get("name", "")) for lbl in issue.get("labels", []) if isinstance(lbl, dict))
    haystack = f"{text} {' '.join(names).lower()}"

    small = r"\b(typo|docs?|documentation|readme|comment|link|spelling|rename|lint|format)\b"
    large = r"\b(refactor|redesign|architecture|rewrite|migrat\w+|breaking change|rfc|proposal)\b"

    if re.search(large, haystack):
        return "large"
    if re.search(small, haystack):
        return "small"
    return "medium"
