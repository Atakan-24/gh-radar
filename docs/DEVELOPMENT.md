> Historical development notes. See the root README for current behavior, verification and limits. Old test counts, status statements and AutoLM bootstrap intervals are not current guarantees.

# gh-radar

[![CI](https://github.com/Atakan-24/gh-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/Atakan-24/gh-radar/actions/workflows/ci.yml)
![dependencies none](https://img.shields.io/badge/dependencies-none-blue)
![python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![read only](https://img.shields.io/badge/GitHub_access-read--only-blue)

A daily read of what actually needs you on GitHub: people waiting on your
repositories, and a short list of issues elsewhere worth your time.

```
$ python -m radar

GH RADAR

SOMEONE IS WAITING ON YOU

ISSUE on you/your-tool, unanswered
  Crash when the config file is empty
  by someone, waiting 26h
  https://github.com/you/your-tool/issues/12

WORTH A LOOK

acme/parser  (~30 min)
  Improve the error message when the schema is missing
  + labelled 'good first issue'; Python — a language you actually ship in; opened in the last two weeks
  ! 1,200 open issues — yours may not be seen
  https://github.com/acme/parser/issues/903
```

<img src="demo.svg" alt="gh-radar's daily digest: an unanswered issue on your own repo, and one scored suggestion elsewhere" width="640">

---

## Why

Two problems, both of them about *noticing* rather than *doing*.

Somebody opens an issue on a small project of yours. GitHub emails you, and
the email lands between forty CI notifications. You see it four days later,
by which time the person has formed an opinion about the project and moved
on. The work was five minutes; the delay was the whole cost.

Meanwhile the standard advice for building an engineering reputation is
"contribute to open source", and the standard starting point —
`label:"good first issue"` — returns **72,000 open issues**. The top of
that list is not a shortlist. It is whatever was indexed most recently.

## Architecture

```
                     __main__.py  (CLI, orchestration, state)
                    /                                        \
              watch.py                                   scout.py
        your own repos:                             issues elsewhere:
        issues waiting on you,                       search -> disqualify
        CI status, releases,                         -> score -> top N
        traffic, diffs                                     |
                    \                                  score.py
                     \                            (hard exclusions +
                      \                             weighted scoring)
                       \                                  /
                        \                                /
                         github.py  (GET-only HTTP client)
                                       |
                                 GitHub REST API

              watch_results + scout_result + news
                                       |
                                 report.py  (renders one digest;
                                 imports nothing from the other
                                 four modules' logic, only their output)
```

Two things this shape is doing on purpose: every module that touches the
network goes through `github.py`, so the read-only constraint below has
exactly one place to hold; and `report.py` never calls the API or the
scorer itself, so a rendering change cannot accidentally change what gets
found or excluded.

## What it does not do

It cannot write to GitHub. Not "does not by default" — it cannot: the HTTP
layer refuses any method other than `GET`, there is a CI job that asserts
this, and a second job that fails the build if a write verb appears anywhere
in the package.

That is a deliberate ceiling, not an unfinished feature. It rules out the
version of this idea that keeps a contribution graph green by itself, which
is against GitHub's acceptable use policy and — more to the point — produces
a profile worth less than an empty one. **This tool finds and explains. A
person decides and acts.**

## The interesting part: throwing results away

The search is trivial. The filtering is the product.

Run against the live API while building this, the naive version's top
suggestions were:

| What came back | Why it is worthless |
|---|---|
| A 2-star repository | Nobody would ever see the contribution |
| An issue open for **3,284 days** | Nine years in a healthy project means it is not waiting for a newcomer |
| `[BOUNTY] Share Why You Starred RustChain — 3 RTC + Community Shoutout`, 166 comments | Token farming |

The third one is the reason this file has a section instead of a sentence.
**Engagement farms use exactly the labels this tool searches for**, carry
real star counts, and are active daily — so every quality signal points the
wrong way. They cannot be scored down, only excluded, because contributing
there is not a weak move but a harmful one.

So the pipeline is: several narrow queries instead of one wide one, then
hard exclusions, then scoring, then a **minimum score** below which nothing
is shown at all. On a normal day almost everything is discarded — a typical
run examines ~50 issues and surfaces one or two.

**Silence is a designed output.** "Nothing needs you today" is the honest
answer most mornings, and a tool that manufactures three suggestions to
avoid saying it teaches you to stop reading it.

## Every suggestion explains itself

```
acme/parser  (~30 min)
  + labelled 'good first issue'; Python — a language you actually ship in
  ! 1,200 open issues — yours may not be seen
```

The reasons and the warnings both come from the scorer. A list of links
with no stated reason gets trusted once.

## Two decisions worth stealing

**Your stack is read off your repositories, including private ones.**
Detecting it from public repos alone got it wrong in the one case that
mattered here: the strongest language lived entirely in private work, so
the scout spent every morning searching the wrong ecosystem. A hand-written
config would have been stale for the same reason nobody updates config for
a job that runs while they sleep.

**A throttled run never looks like a quiet one.** An empty digest because
GitHub refused, and an empty digest because there was no news, are the same
picture and opposite facts. Rate limiting raises rather than returns
nothing, and the digest says so.

## Install and run

```bash
git clone https://github.com/Atakan-24/gh-radar
cd gh-radar
export GH_RADAR_TOKEN=ghp_...     # a token with public read access is enough
python -m radar
```

```
--user LOGIN          whose repositories to watch (default: Atakan-24)
--languages A,B       override detection
--top N               how many opportunities to surface (default 3)
--no-scout            watch your own repositories only
--json                machine-readable, for a notifier
--dry-run             no network at all; renders fixed sample data
--state PATH          where run-to-run state lives
```

The token is read from the environment only — never from a file in the
repository, never from a CLI argument, because an argument ends up in shell
history and in the process list of every other user on the machine.

State (a small JSON file) is what stops the same issue being suggested every
morning for three weeks. Delete it and the tool simply starts over.

## Running it daily

Any scheduler works; it prints to stdout and exits 0. `--json` is there for
piping into whatever sends you messages.

One note from running it: schedule it with something that fails *loudly*.
A daily digest that stops arriving looks exactly like a quiet week, which is
the failure mode this tool exists to prevent in the first place.

## Testing

```bash
pip install pytest ruff
python -m pytest
```

**58 tests.** The ones that matter are not the happy paths:

| Suite | What it protects |
|---|---|
| `test_score.py` | Every hard exclusion, and the farm filter against five real farm titles *plus* three legitimate titles that must not be caught (`Tokenizer drops the final token`, `Fix star-import handling`) |
| `test_report_and_state.py` | That a quiet day reads as quiet, a throttled day reads as throttled, and a corrupted state file is survivable |

CI runs across three operating systems and two Python versions, asserts the
read-only property, imports every module into a bare interpreter to prove
the zero-dependency claim, and scans itself with
[git-secret-scan](https://github.com/Atakan-24/git-secret-scan).

### A bug the tests found

The farm filter used `\bbounty\b`, which does not match **"bounties"** — and
the repository that prompted the whole filter is named `rustchain-bounties`.
On the live run only the issue *title* happened to match, so a neutrally
titled ticket in the same farm would have gone straight through. Found by a
test, not by re-reading the regex.

## Honest limits

- The scoring weights are **heuristics, not measurements**. Nobody has run a
  controlled study of which issues become merged pull requests. What makes
  them defensible is that each one has the observation that would refute it
  written next to it in `score.py`.
- The effort estimate is keyword matching. It separates "typo in the README"
  from "rewrite the scheduler" and does not pretend to more.
- It only searches labelled issues. Plenty of good contributions are
  unlabelled; those need a human who already knows the project.

## License

All rights reserved. Published to be read as a work sample — not licensed
for reuse, redistribution or modification.
