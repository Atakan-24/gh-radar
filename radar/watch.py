"""Inspect public repository issues, CI and releases.

Traffic is optional and depends on token permissions. Unavailable API
responses must not establish that a repository has no releases."""

from __future__ import annotations

import datetime as dt

from . import github

# A stranger who opens an issue and hears nothing forms an opinion about the
# project quickly. This is the threshold at which "not yet answered" turns
# into a finding rather than a fact.
UNANSWERED_HOURS = 20

# A repository with commits since its last release has shipped work nobody
# can install by version. Not urgent, but it is what separates a repository
# from a project.
UNRELEASED_COMMITS = 10


def _parse(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_releasable(repo: dict, *, user: str) -> bool:
    """Is this a thing somebody could install a version of?

    Three cases are not: the profile README repository (name equals the
    login), a GitHub Pages site, and anything with no detected language.
    Suggesting a release for those is noise, and noise is what makes a daily
    message stop being read.
    """
    name = (repo.get("name") or "").lower()
    if name == user.lower():
        return False
    if name.endswith(".github.io"):
        return False
    if repo.get("has_pages") and not repo.get("language"):
        return False
    return bool(repo.get("language"))


def own_repos(user: str, *, include_forks: bool = False) -> list[dict]:
    """Public repositories, for watching.

    Only public ones: this half of the tool is about strangers interacting
    with your work, and nobody can open an issue on a private repository.
    """
    repos = github.paged(f"/users/{user}/repos", {"type": "owner", "sort": "pushed"}, limit=100)
    return [r for r in repos if include_forks or not r.get("fork")]


def all_repos_for_language_detection(user: str) -> list[dict]:
    """Every repository the token can see, public and private.

    Deliberately different from own_repos(). Reading the stack off public
    repositories only gets it wrong in exactly the case that matters here:
    the strongest language can live entirely in private work, and then the
    scout spends every morning searching the wrong ecosystem.

    Measured 2026-08-29: public repos alone yielded Python and HTML, missing
    TypeScript entirely -- the language of the largest system in the
    portfolio, which happens to be private.

    Falls back to the public list if the token lacks repo scope.
    """
    try:
        repos = github.paged("/user/repos", {"affiliation": "owner", "sort": "pushed"}, limit=100)
    except github.RateLimited:
        raise
    except github.GitHubError:
        return own_repos(user)
    return [r for r in repos if not r.get("fork")]


def _traffic(full_name: str) -> dict | None:
    """Views and clones over the last 14 days.

    Needs push access on the repository. On a token without it this returns
    None rather than raising -- traffic is a nice-to-have, and a missing
    permission must not take the whole digest down.
    """
    try:
        views, _ = github.request(f"/repos/{full_name}/traffic/views")
        clones, _ = github.request(f"/repos/{full_name}/traffic/clones")
    except github.RateLimited:
        raise
    except github.GitHubError:
        return None
    if not isinstance(views, dict) or not isinstance(clones, dict):
        return None
    return {
        "views": int(views.get("count") or 0),
        "unique_views": int(views.get("uniques") or 0),
        "clones": int(clones.get("count") or 0),
        "unique_clones": int(clones.get("uniques") or 0),
    }


def _latest_ci(full_name: str, default_branch: str) -> dict | None:
    try:
        body, _ = github.request(
            f"/repos/{full_name}/actions/runs",
            {"branch": default_branch, "per_page": 1, "status": "completed"},
        )
    except github.RateLimited:
        raise
    except github.GitHubError:
        return None
    runs = (body or {}).get("workflow_runs") or []
    if not runs:
        return None
    run = runs[0]
    return {
        "conclusion": run.get("conclusion"),
        "name": run.get("name"),
        "url": run.get("html_url"),
        "at": run.get("updated_at"),
    }


def inspect(repo: dict, *, user: str, now: dt.datetime | None = None) -> dict:
    """Everything worth knowing about one of your repositories today."""
    now = now or dt.datetime.now(dt.timezone.utc)
    full_name = repo.get("full_name") or ""
    findings: list[dict] = []

    # --- issues and PRs opened by other people -------------------------------
    items = github.paged(f"/repos/{full_name}/issues", {"state": "open", "sort": "created"}, limit=50)
    for item in items:
        author = ((item.get("user") or {}).get("login") or "").lower()
        if author == user.lower():
            continue  # your own tickets are not someone waiting on you
        is_pr = bool(item.get("pull_request"))
        opened = _parse(item.get("created_at"))
        waited_h = (now - opened).total_seconds() / 3600.0 if opened else 0.0
        unanswered = int(item.get("comments") or 0) == 0
        findings.append({
            "kind": "external_pr" if is_pr else "external_issue",
            "urgent": is_pr or (unanswered and waited_h >= UNANSWERED_HOURS),
            "title": item.get("title", ""),
            "url": item.get("html_url", ""),
            "author": (item.get("user") or {}).get("login", ""),
            "waited_hours": round(waited_h, 1),
            "unanswered": unanswered,
        })

    # --- continuous integration ---------------------------------------------
    ci = _latest_ci(full_name, repo.get("default_branch") or "main")
    if ci and ci.get("conclusion") not in (None, "success", "skipped", "neutral"):
        findings.append({
            "kind": "ci_failing",
            "urgent": True,
            "title": f"{ci.get('name') or 'CI'} is {ci.get('conclusion')}",
            "url": ci.get("url", ""),
        })

    # --- releases ------------------------------------------------------------
    # Only for things a person could install or depend on. A profile README,
    # a Pages site and a notes repository have nothing to version, and
    # telling their owner to cut a release every morning is how a digest
    # teaches its reader to skim past it.
    if is_releasable(repo, user=user):
        try:
            releases = github.paged(f"/repos/{full_name}/releases", limit=1)
        except github.RateLimited:
            raise
        except github.GitHubError:
            releases = None
        if releases == []:
            findings.append({
                "kind": "no_release",
                "urgent": False,
                "title": "no release yet — nobody can pin a version",
                "url": f"https://github.com/{full_name}/releases/new",
            })

    return {
        "repo": full_name,
        "url": repo.get("html_url", ""),
        "stars": int(repo.get("stargazers_count") or 0),
        "forks": int(repo.get("forks_count") or 0),
        "watchers": int(repo.get("subscribers_count") or 0),
        "traffic": _traffic(full_name),
        "ci": ci,
        "findings": findings,
    }


def diff_against(previous: dict, current: dict) -> list[str]:
    """What changed since the last run, in plain sentences.

    State lives in a file rather than being recomputed, because "you have 3
    stars" is not news on the two hundredth morning -- "somebody starred it
    yesterday" is. A digest that repeats yesterday's facts stops being read,
    and then the one line that mattered gets skipped with it.
    """
    news: list[str] = []
    for name in ("stars", "forks", "watchers"):
        before = int(previous.get(name) or 0)
        after = int(current.get(name) or 0)
        if after > before:
            delta = after - before
            noun = name[:-1] if delta == 1 else name
            news.append(f"+{delta} {noun} (now {after})")
    return news
