"""Minimal GitHub REST client.

No dependencies on purpose. This runs as a scheduled job on a machine where
adding a package is a maintenance cost nobody pays back, and where a broken
transitive dependency would take the daily run down silently.

Only reads. Nothing in this module can create, edit, or delete anything on
GitHub -- see `request()`, which refuses any method other than GET.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
UA = "gh-radar (+https://github.com/Atakan-24/gh-radar)"

# Search is a different, much smaller bucket than core: 30 requests/minute
# against 5000/hour. Treating them as one budget is how a scan that looks
# fine in testing starts returning empty results in production.
SEARCH_PAUSE_S = 2.2


class GitHubError(RuntimeError):
    pass


class RateLimited(GitHubError):
    """Raised instead of silently returning nothing.

    A digest that is empty because the API refused is indistinguishable from
    a digest that is empty because there was no news -- and the second one is
    a normal day. They must never look the same.
    """

    def __init__(self, reset_epoch: int | None = None) -> None:
        self.reset_epoch = reset_epoch
        wait = ""
        if reset_epoch:
            wait = f", resets in {max(0, int(reset_epoch - time.time()))}s"
        super().__init__(f"GitHub rate limit reached{wait}")


def token() -> str:
    """Token from the environment only.

    Never read from a file inside the repository, and never accept one as a
    CLI argument -- an argument ends up in shell history and in the process
    list of every other user on the machine.
    """
    for name in ("GH_RADAR_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise GitHubError(
        "No token. Set GH_RADAR_TOKEN (or GITHUB_TOKEN). A fine-grained token "
        "with public read access is enough for scouting; add repo scope only "
        "if you want your own private repositories watched."
    )


def request(path: str, params: dict | None = None, *, method: str = "GET") -> tuple[dict | list, dict]:
    """One authenticated GET. Returns (parsed body, response headers).

    Read-only is enforced here rather than by convention, so a future caller
    cannot turn this into a write path by passing a different method.
    """
    if method != "GET":
        raise GitHubError(f"gh-radar is read-only; refused method {method}")

    url = path if path.startswith("http") else f"{API}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    if not url.lower().startswith("https://"):
        raise GitHubError(f"refusing non-https URL: {url}")

    req = urllib.request.Request(url, method="GET")  # noqa: S310 - scheme checked above
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", UA)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - scheme checked above
            headers = dict(resp.headers)
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        if err.code in (403, 429):
            remaining = err.headers.get("X-RateLimit-Remaining")
            if remaining == "0" or err.headers.get("Retry-After"):
                reset = err.headers.get("X-RateLimit-Reset")
                raise RateLimited(int(reset) if reset else None) from err
        # Pulling GitHub's own message out of the body makes the difference
        # between "HTTP 404" and "Not Found - the repository may be private".
        # It must never itself raise: failing to decode an error body would
        # replace a useful message with a traceback from the error handler.
        detail = ""
        with contextlib.suppress(Exception):
            detail = json.loads(err.read().decode("utf-8")).get("message", "")
        raise GitHubError(f"HTTP {err.code} for {url}: {detail}") from err
    except urllib.error.URLError as err:
        raise GitHubError(f"network error for {url}: {err.reason}") from err

    return (json.loads(body) if body else {}), headers


def paged(path: str, params: dict | None = None, *, limit: int = 100) -> list:
    """Follow rel=next until `limit` items or the pages run out.

    `limit` is a hard stop, not a suggestion: an unbounded follow of Link
    headers against a busy repository is how a five-second job becomes a
    five-minute one without anybody noticing.
    """
    out: list = []
    p = dict(params or {})
    p.setdefault("per_page", min(100, limit))
    url: str | None = path
    while url and len(out) < limit:
        body, headers = request(url, p if url == path else None)
        items = body if isinstance(body, list) else body.get("items", [])
        if not items:
            break
        out.extend(items)
        url = _next_link(headers.get("Link", ""))
    return out[:limit]


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        if 'rel="next"' in part:
            start, end = part.find("<"), part.find(">")
            if start != -1 and end > start:
                return part[start + 1 : end]
    return None


def search_issues(query: str, *, per_page: int = 50, pause: float = SEARCH_PAUSE_S) -> list[dict]:
    """Issue search, paced for the search bucket.

    The pause is deliberate and applied *before* returning rather than
    between pages only, so a caller looping over several queries inherits
    the pacing without having to remember it.
    """
    body, _ = request("/search/issues", {"q": query, "per_page": min(100, per_page)})
    time.sleep(pause)
    return body.get("items", []) if isinstance(body, dict) else []


def rate_limit() -> dict:
    body, _ = request("/rate_limit")
    return body.get("resources", {}) if isinstance(body, dict) else {}
