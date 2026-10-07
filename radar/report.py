"""Turning findings into something readable in thirty seconds on a phone.

The constraint that shaped this file: the person reading it has one to two
hours a week. Every line has to earn its place, and "nothing happened today"
has to be one line rather than a page of empty sections.
"""

from __future__ import annotations

_EFFORT = {"small": "~30 min", "medium": "~2 h", "large": "a weekend"}


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def render(*, watch_results: list[dict], scout_result: dict, news: dict[str, list[str]]) -> str:
    """The daily digest. Returns plain text, Telegram-safe (no markup)."""
    lines: list[str] = ["GH RADAR"]
    urgent: list[str] = []
    normal: list[str] = []

    # --- things a person is waiting on ---------------------------------------
    for result in watch_results:
        repo = result["repo"]
        for finding in result["findings"]:
            kind = finding["kind"]
            if kind == "external_pr":
                urgent.append(
                    f"PULL REQUEST on {repo}\n"
                    f"  {finding['title']}\n"
                    f"  by {finding['author']}, waiting {finding['waited_hours']:.0f}h\n"
                    f"  {finding['url']}"
                )
            elif kind == "external_issue" and finding["urgent"]:
                urgent.append(
                    f"ISSUE on {repo}, unanswered\n"
                    f"  {finding['title']}\n"
                    f"  by {finding['author']}, waiting {finding['waited_hours']:.0f}h\n"
                    f"  {finding['url']}"
                )
            elif kind == "ci_failing":
                urgent.append(f"CI RED on {repo}\n  {finding['title']}\n  {finding['url']}")
            elif kind == "no_release":
                normal.append(f"{repo}: {finding['title']}")

    # --- movement on your own repositories -----------------------------------
    for repo, items in news.items():
        for item in items:
            normal.append(f"{repo}: {item}")

    for result in watch_results:
        traffic = result.get("traffic")
        if traffic and traffic["unique_clones"] > 0:
            normal.append(
                f"{result['repo']}: {_plural(traffic['unique_clones'], 'person', 'people')} "
                f"cloned it in the last 14 days"
            )

    if urgent:
        lines.append("")
        lines.append("SOMEONE IS WAITING ON YOU")
        lines.extend(f"\n{item}" for item in urgent)

    # --- opportunities --------------------------------------------------------
    picks = scout_result.get("picks") or []
    if picks:
        lines.append("")
        lines.append("WORTH A LOOK")
        for pick in picks:
            effort = _EFFORT.get(pick.get("effort", "medium"), "~2 h")
            lines.append(f"\n{pick['repo']}  ({effort})")
            lines.append(f"  {pick['title']}")
            if pick.get("reasons"):
                lines.append(f"  + {'; '.join(pick['reasons'][:3])}")
            if pick.get("warnings"):
                lines.append(f"  ! {'; '.join(pick['warnings'][:2])}")
            lines.append(f"  {pick['url']}")

    if normal:
        lines.append("")
        lines.append("ALSO")
        lines.extend(f"  {item}" for item in normal)

    # --- honesty about the run itself ----------------------------------------
    if scout_result.get("rate_limited"):
        lines.append("")
        lines.append("NOTE: GitHub rate limit hit — this list is incomplete.")

    if not urgent and not picks and not normal:
        lines.append("")
        lines.append("Nothing needs you today.")
        examined = scout_result.get("examined", 0)
        if examined:
            lines.append(
                f"Checked {examined} issues; none cleared the bar. "
                "That is the normal outcome most days."
            )

    return "\n".join(lines)


def render_short(*, watch_results: list[dict], scout_result: dict) -> str:
    """One line, for a notification title."""
    waiting = sum(
        1 for r in watch_results for f in r["findings"] if f.get("urgent")
    )
    picks = len(scout_result.get("picks") or [])
    if waiting and picks:
        return f"{waiting} waiting on you, {picks} worth a look"
    if waiting:
        return f"{_plural(waiting, 'thing', 'things')} waiting on you"
    if picks:
        return f"{_plural(picks, 'opportunity', 'opportunities')} worth a look"
    return "nothing needs you today"
