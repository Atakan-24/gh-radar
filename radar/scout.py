"""Search and rank contribution opportunities.

Queries are bounded by language, labels and recency. score.py supplies
heuristic exclusions and weights; one suggestion per repository is retained."""

from __future__ import annotations

import datetime as dt

from . import github, score

# Queries are narrow on purpose. `sort=updated` matters more than it looks:
# it surfaces issues somebody touched recently, which correlates with a
# maintainer who is still present.
_LABELS = ['"good first issue"', '"help wanted"', '"first-timers-only"']

_EFFORT_RANK = {"small": 0, "medium": 1, "large": 2}

# Below this, do not suggest it at all.
#
# The temptation is to always show the best three, and it is wrong: on a
# thin day "the best available" and "worth your time" are different things.
# Measured 2026-08-29, a nine-year-old ticket with 18 comments was the only
# survivor of 50 and would have been presented as the day's recommendation.
#
# A clean pick clears this comfortably: a welcoming label (~24) plus a
# language you ship in (22) plus any two of freshness, no replies, a real
# description, or an active maintainer already lands above 70. Anything
# scraping the floor is carrying warnings that cancel its own merits.
MIN_POINTS = 55


def _repo_cache_get(cache: dict, full_name: str) -> dict | None:
    if full_name in cache:
        return cache[full_name]
    try:
        repo, _ = github.request(f"/repos/{full_name}")
    except github.RateLimited:
        raise
    except github.GitHubError:
        repo = None
    cache[full_name] = repo if isinstance(repo, dict) else None
    return cache[full_name]


def build_queries(languages: list[str], *, updated_within_days: int = 45) -> list[str]:
    """One query per (language, label) pair, bounded by recency.

    The date bound is the single most effective filter available: it removes
    the entire long tail of issues nobody has looked at since 2023, before a
    single result is scored.
    """
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=updated_within_days)).date().isoformat()
    queries = []
    for lang in languages:
        for label in _LABELS:
            queries.append(
                f"is:issue is:open no:assignee label:{label} "
                f"language:{lang} updated:>={since} sort:updated-desc"
            )
    return queries


def find(
    languages: list[str],
    *,
    exclude_owners: set[str] | None = None,
    seen_urls: set[str] | None = None,
    per_query: int = 25,
    top_n: int = 3,
    max_effort: str = "medium",
    now: dt.datetime | None = None,
) -> dict:
    """Search, score, and return only the few that survive.

    `seen_urls` is what stops the digest suggesting the same issue every
    morning until it is closed. Without it the list is technically correct
    and practically ignored after a week.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    exclude_owners = {o.lower() for o in (exclude_owners or set())}
    seen_urls = seen_urls or set()
    lang_set = set(languages)

    repo_cache: dict[str, dict | None] = {}
    scored: list[dict] = []
    rejected = 0
    examined = 0
    rate_limited = False
    search_errors = 0

    for query in build_queries(languages):
        try:
            items = github.search_issues(query, per_page=per_query)
        except github.RateLimited:
            # Stop, but report it. An empty digest and a throttled digest
            # must never look the same to the reader.
            rate_limited = True
            break
        except github.GitHubError:
            search_errors += 1
            continue

        for issue in items:
            url = issue.get("html_url", "")
            if not url or url in seen_urls:
                continue
            full_name = score.repo_full_name(issue)
            if not full_name or full_name.split("/")[0].lower() in exclude_owners:
                continue

            examined += 1
            repo = _repo_cache_get(repo_cache, full_name)
            reason = score.disqualify(issue, repo, now)
            if reason:
                rejected += 1
                continue

            effort = score.effort_hint(issue)
            # An honest read of the reader's week. Somebody with one or two
            # hours does not need a refactor recommended to them, however
            # good the ticket is -- it will sit unstarted and make the whole
            # digest feel like homework.
            if _EFFORT_RANK[effort] > _EFFORT_RANK.get(max_effort, 1):
                rejected += 1
                continue

            result = score.score(issue, repo, languages=lang_set, now=now)
            result["effort"] = effort
            result["description"] = (repo or {}).get("description") or ""
            scored.append(result)

    scored.sort(key=lambda r: r["points"], reverse=True)

    # One pick per repository. Three tickets from the same project read as
    # one opportunity, not three, and crowd out the only other candidate
    # that made the cut.
    picks: list[dict] = []
    used_repos: set[str] = set()
    for candidate in scored:
        if candidate["points"] < MIN_POINTS:
            break  # sorted descending, so nothing after this qualifies either
        if candidate["repo"] in used_repos:
            continue
        used_repos.add(candidate["repo"])
        picks.append(candidate)
        if len(picks) >= top_n:
            break

    return {
        "picks": picks,
        "examined": examined,
        "rejected": rejected,
        "surviving": len(scored),
        "rate_limited": rate_limited,
        "search_errors": search_errors,
    }
