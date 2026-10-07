# gh-radar

[![CI](https://github.com/Atakan-24/gh-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/Atakan-24/gh-radar/actions/workflows/ci.yml)

A Python CLI that builds a digest of activity on your repositories and ranks open issues in other projects. It reads GitHub; it does not create contributions or send messages. Status: command-line tool with heuristic ranking.

## Architecture

`__main__` orchestrates `watch` and `scout`. `score` applies exclusions and weighted signals; `report` formats their results. All HTTP requests go through `github.py`, which accepts GET only and restricts requests and redirects to `https://api.github.com` before adding a bearer token.

Python 3.10+, standard library only. Optional private-repository language detection and traffic access depend on token permissions; the public-repository digest does not need write access.

## Run

```sh
python -m radar --dry-run   # synthetic digest; no token or network
python -m radar --user Atakan-24
python -m radar --user Atakan-24 --no-scout
python -m radar --json
```

Set `GH_RADAR_TOKEN` (or `GITHUB_TOKEN` / `GH_TOKEN`) in the environment using your secret manager or interactive shell. Use the smallest permissions needed. Do not place credentials in arguments or committed files.

## Decisions and verification

- Search calls are paced separately from the core API limit; pagination is bounded.
- Ranking weights are named heuristics, not a trained model or evidence that an issue is easy.
- Local JSON state suppresses repeated suggestions. Malformed nested state and naive timestamps are discarded; temporary files are unique per save.
- Search errors and rate limits mark the digest incomplete. Optional metrics may be unavailable.

```sh
python -m pip install pytest ruff
python -m pytest -q
ruff check .
```

Tests cover scoring, reporting, local state, the HTTP destination boundary and incomplete-search behavior. They do not make authenticated live API calls. The scheduled CI job runs the same offline tests; it does not detect live GitHub response changes by itself.

## Limits

One bounded digest can omit activity. Issue descriptions, labels and weights are imperfect effort signals. Concurrent runs cannot merge state changes: saves are atomic, but the final writer wins. Keep one scheduled writer per state file.

[Detailed design and earlier observations](docs/DEVELOPMENT.md)
