#!/usr/bin/env python3
"""gh-radar — one command, one honest answer to "what needs me on GitHub today".

    python -m radar                       # print the digest
    python -m radar --user someone        # somebody else's public repos
    python -m radar --languages Python,TypeScript
    python -m radar --json                # machine-readable, for a notifier
    python -m radar --dry-run             # no network; prove the wiring works

The entry point lives inside the package rather than beside it. A top-level
radar.py next to a radar/ package shadow each other depending on how Python
is invoked -- which showed up first as an import error in the test suite,
and would have shown up later as a scheduled job that ran the wrong file.

Read-only against GitHub. It cannot open a pull request, leave a comment, or
push a commit -- the HTTP layer refuses any method other than GET. What it
produces is a short list; a person still decides and acts.

That restriction is the point rather than a limitation. Automated activity
is against GitHub's acceptable use policy and, more to the point, a profile
padded with generated contributions is worth less than an empty one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from radar import github, report, scout, watch


def default_state_path() -> Path:
    """Resolved when asked for, never at import time.

    `Path.home()` raises when the environment has no home variable, which is
    exactly the environment a scheduled task or a service account runs in.
    Evaluating it at module level turns that into an import error, so the
    program dies before it can parse the `--state` argument that would have
    avoided the problem entirely.
    """
    try:
        return Path.home() / ".gh-radar-state.json"
    except RuntimeError:
        return Path.cwd() / ".gh-radar-state.json"

# Issues stay suppressed for this long after being shown once. Long enough
# not to nag, short enough that something genuinely worth doing comes back
# around if it was skipped in a busy week.
SUPPRESS_DAYS = 21


def load_state(path: Path) -> dict:
    """State must never be able to break the run.

    A corrupted file is treated as no file. The alternative -- crashing on a
    half-written JSON from a machine that rebooted mid-write -- means the one
    morning the digest matters is the morning it does not arrive.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as err:
        print(f"warning: could not persist state to {path}: {err}", file=sys.stderr)


def prune_seen(seen: dict, now: dt.datetime) -> dict:
    cutoff = now - dt.timedelta(days=SUPPRESS_DAYS)
    out = {}
    for url, iso in seen.items():
        try:
            when = dt.datetime.fromisoformat(str(iso))
        except ValueError:
            continue
        if when > cutoff:
            out[url] = iso
    return out


def detect_languages(repos: list[dict]) -> list[str]:
    """Read the stack off the repositories instead of asking for it.

    A hand-written list goes stale the moment somebody picks up a new
    language, and nobody remembers to update a config file for a job that
    runs while they sleep.
    """
    counts: dict[str, int] = {}
    for repo in repos:
        lang = repo.get("language")
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    ranked = sorted(counts, key=lambda k: counts[k], reverse=True)
    return ranked[:3] or ["Python"]


def run(args: argparse.Namespace) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    state = load_state(Path(args.state))
    seen = prune_seen(state.get("seen_issues", {}), now)

    repos = watch.own_repos(args.user)
    if args.repo:
        wanted = {r.strip().lower() for r in args.repo.split(",")}
        repos = [r for r in repos if (r.get("name") or "").lower() in wanted]

    if args.languages:
        languages = [lang.strip() for lang in args.languages.split(",") if lang.strip()]
    else:
        # Detected from every repository the token can see, not just the
        # public ones -- see all_repos_for_language_detection() for why that
        # distinction is load-bearing rather than tidy.
        languages = detect_languages(watch.all_repos_for_language_detection(args.user))

    watch_results = [watch.inspect(repo, user=args.user, now=now) for repo in repos]

    previous = state.get("repos", {})
    news = {}
    for result in watch_results:
        changed = watch.diff_against(previous.get(result["repo"], {}), result)
        if changed:
            news[result["repo"]] = changed

    scout_result = (
        {"picks": [], "examined": 0, "rejected": 0, "surviving": 0, "rate_limited": False}
        if args.no_scout
        else scout.find(
            languages,
            exclude_owners={args.user},
            seen_urls=set(seen),
            top_n=args.top,
            now=now,
        )
    )

    for pick in scout_result.get("picks", []):
        seen[pick["url"]] = now.isoformat()

    save_state(
        Path(args.state),
        {
            "last_run": now.isoformat(),
            "repos": {
                r["repo"]: {"stars": r["stars"], "forks": r["forks"], "watchers": r["watchers"]}
                for r in watch_results
            },
            "seen_issues": seen,
        },
    )

    return {
        "generated_at": now.isoformat(),
        "user": args.user,
        "languages": languages,
        "watch": watch_results,
        "scout": scout_result,
        "news": news,
        "text": report.render(watch_results=watch_results, scout_result=scout_result, news=news),
        "headline": report.render_short(watch_results=watch_results, scout_result=scout_result),
    }


def _dry_run(args: argparse.Namespace) -> dict:
    """Exercise the formatting path with fixed data and no network.

    Exists because the expensive failure is not a crash -- it is a digest
    that runs green for weeks and renders something unreadable on the one
    morning it has real news.
    """
    watch_results = [{
        "repo": "Atakan-24/git-secret-scan", "url": "", "stars": 3, "forks": 1, "watchers": 1,
        "traffic": {"views": 40, "unique_views": 9, "clones": 4, "unique_clones": 3},
        "ci": {"conclusion": "success"},
        "findings": [{
            "kind": "external_issue", "urgent": True,
            "title": "False positive on base64 blobs in test fixtures",
            "url": "https://example.invalid/issues/1", "author": "someone",
            "waited_hours": 26.0, "unanswered": True,
        }],
    }]
    scout_result = {
        "picks": [{
            "repo": "example/tool", "title": "Improve error message when config is missing",
            "url": "https://example.invalid/issues/9", "effort": "small",
            "reasons": ["labelled 'good first issue'", "Python — a language you actually ship in"],
            "warnings": [], "points": 88,
        }],
        "examined": 61, "rejected": 47, "surviving": 4, "rate_limited": False,
    }
    news = {"Atakan-24/git-secret-scan": ["+1 star (now 3)"]}
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "user": args.user, "languages": ["Python"], "watch": watch_results,
        "scout": scout_result, "news": news,
        "text": report.render(watch_results=watch_results, scout_result=scout_result, news=news),
        "headline": report.render_short(watch_results=watch_results, scout_result=scout_result),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What needs you on GitHub today.")
    parser.add_argument("--user", default="Atakan-24", help="GitHub login to watch")
    parser.add_argument("--languages", default="", help="comma-separated; default: detected from your repos")
    parser.add_argument("--repo", default="", help="comma-separated repo names to limit the watch to")
    parser.add_argument("--top", type=int, default=3, help="how many opportunities to surface (default 3)")
    parser.add_argument("--no-scout", action="store_true", help="watch your own repos only")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--dry-run", action="store_true", help="no network; render fixed sample data")
    parser.add_argument("--state", default=None, help="where to keep run-to-run state")
    args = parser.parse_args(argv)
    if args.state is None:
        args.state = str(default_state_path())

    try:
        result = _dry_run(args) if args.dry_run else run(args)
    except github.RateLimited as err:
        print(f"gh-radar: {err}", file=sys.stderr)
        return 2
    except github.GitHubError as err:
        print(f"gh-radar: {err}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
