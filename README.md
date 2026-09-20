---
title: Shift-H
emoji: 🏥
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8080
pinned: false
---

# Shift-H — clinician leave and cover

WA Health hackathon, Challenge 3: *when a clinician asks for leave, how might an AI agent recommend the best approach to cover, or explain exactly why those dates won't work?*

Shift-H answers that question for three people:

| Who | What they get |
|---|---|
| **Clinician** (staff app) | A straight answer on any dates, the one rule that matters, what happens to each rostered shift, the closest dates that work, best windows for a break, and an assistant that remembers the check |
| **Manager** (console) | An inbox of requests with a cover plan already found, colleagues who accept or decline cover, ward coverage and a what-if simulator, fairness of extra shifts |
| **Super user** (console, Policy section) | Every rule, agreement value and cover option. Policy-backed rules change only with an Australian policy document the AI verifies; system rules change with a written reason. Every change is logged |

Everything runs on the synthetic staffing, roster, leave and balance data supplied by the hackathon (cleaned, in `data/clean/`). No data is invented.

## Run it locally

```bash
python -m pip install -r requirements.txt
copy .env.example .env      # then put your Gemini key in .env (optional: without it the built-in assistant answers)
python run.py               # http://127.0.0.1:8000
```

Python 3.12 or newer. First start takes about 6 s while the roster is loaded into memory; the app needs about 1.3 GB of RAM.

Demo accounts: any staff number (for example `SYN000009`) with the staff password; `manager` and `admin` for the console. Passwords default to `password`, `manager` and `admin` and are overridden by `STAFF_PASSWORD`, `MANAGER_PASSWORD` and `ADMIN_PASSWORD`.

## How it works

`check -> rules -> demand -> gaps -> cover plan -> outcome -> alternatives -> explanation`

| Piece | File |
|---|---|
| Rulebook: 41 rules with severities and parameters, agreements, cover options, ranking weights | `config/rules.yaml` |
| Rule implementations and the fatigue checker (rest, consecutive shifts, hours) | `leavecover/rules.py` |
| Data store: cleaned data, coverage cubes, hybrid staffing model for partly published rosters, live overlay of requests | `leavecover/store.py` |
| Requirement per ward/shift/role and the requester's shifts | `leavecover/coverage.py` |
| Cover recommender: eligible colleagues, safe hours, tiers (ordinary, redeploy, casual, overtime, agency), equity | `leavecover/recommend.py` |
| Decision engine, alternatives, explanations | `leavecover/engine.py` |
| Best-window suggestions and split plans | `leavecover/suggestions.py` |
| Assistant: Gemini with tools, built-in fallback, conversation history | `leavecover/chat.py` |
| Policy document verification and discovery | `leavecover/policy_check.py` |
| API, auth, manager and super-user endpoints | `leavecover/api.py` |
| UI (no build step): staff app `ui/index.html`, console `ui/manager.html`, policy pages `ui/policy.html` | `ui/` |
| Policy research behind each rule (awards, agreements, WA Health policies, AI standard) | `data/research/` |

## Tests, calibration, benchmark

```bash
python -m pytest -q          # 51 tests: engine, staffing model, invariants, concurrency, API, access control
python calibrate.py          # known-answer cases (100%) and agreement with leave the organisation actually booked (95.9%)
python benchmark.py          # 10,442 real scenarios, median 0.5 ms per decision
```

Tests use their own scratch database; they never touch the live one.

## Environment variables

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio key for the assistant and policy checks. Without it the deterministic built-in assistant answers |
| `GEMINI_MODELS` | Comma-separated model fallback list |
| `ADMIN_PASSWORD`, `MANAGER_PASSWORD`, `STAFF_PASSWORD` | Demo account passwords |
| `HOST`, `PORT` | Bind address and port (`127.0.0.1:8000` locally; the Dockerfile sets `0.0.0.0:8080`) |
| `LEAVECOVER_DB` | Path of the SQLite request database |

Never commit `.env`; it is git-ignored.

## Deploy

The Dockerfile builds a self-contained image (app plus cleaned data). See `DEPLOY.md` for the step-by-step guide for the chosen host.
