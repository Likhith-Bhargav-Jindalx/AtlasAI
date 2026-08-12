# Atlas Engine

The capture → trajectory → brain → codegen → certify pipeline behind Atlas AI's
"record once, get a certified golden Playwright script" product loop.
See `docs/superpowers/specs/2026-08-12-atlas-ai-production-architecture-design.md`
for the full architecture this implements.

## Setup

```bash
cd engine
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium

docker compose -f ../infra/docker-compose.dev.yml up -d
alembic upgrade head
```

The dev Postgres runs on **port 5434** (not 5432) — this machine already had
other unrelated projects' Postgres containers bound to 5432/5433, so Atlas's
dev database was deliberately isolated on its own port.

## Run the test suite

```bash
pytest -v
```

Tests connect to a dedicated `atlas_test` Postgres database (create it once
with `docker compose -f ../infra/docker-compose.dev.yml exec postgres createdb -U atlas atlas_test`,
then `alembic upgrade head` with `ATLAS_DATABASE_URL` pointed at it — `tests/conftest.py`
sets this automatically). Every test runs against a local fixture portal
(`tests/fixtures/fixture_portal/`) or a fake LLM client — no Azure OpenAI
credentials or real website are required to develop or verify this package.

## Try it yourself against a real website

1. Create an organization once:
   ```bash
   python -c "
   from atlas_engine.db import session_scope
   from atlas_engine.db.models import Organization
   with session_scope() as s:
       org = Organization(name='My Org', slug='my-org')
       s.add(org); s.flush()
       print(org.id)
   "
   ```
2. Record yourself using any website (a headed Chromium window opens):
   ```bash
   atlas record "https://example.com" --org-id <org-id-from-step-1>
   ```
   Perform your steps in the browser, then press Enter in the terminal to
   finish. This prints a recording ID and writes an `actions.json` file
   under `~/.atlas/runs/recordings/<id>/`.
3. Author a certified golden script from that recording:
   ```bash
   atlas author-from-actions \
     --org-id <org-id> \
     --start-url "https://example.com" \
     --actions-file ~/.atlas/runs/recordings/<id>/actions.json
   ```
   This prints the path to a certified, self-contained `golden.py` — a
   plain Playwright script with no dependency on this package, LLM
   credentials, or network access to anything but the site you recorded.
   Run it yourself with `python <path-to-golden.py>`.

Recording the same domain more than once reuses its existing portal profile
(and adds a new golden script *version* for it) rather than creating
duplicates — this is deliberate: the goal is zero manual config, not a fresh
row per run.

`author-from-actions` replays the recorded trajectory through a real, fresh
browser session — the brain (`atlas_engine.brain.loop.BrainLoop`) — before
emitting anything. Any step that doesn't reproduce live is either repaired
(if you pass an LLM client) or leaves the trajectory as a `draft`, never
`certified` — a golden script only gets certified once every recorded step
has actually been proven to work against the live page, not just replayed
blindly from what was captured.

## What's next

This plan covers Phase 0 only: a real, tested engine and a CLI/local API
that prove the "record → certified golden script" loop end to end,
including the brain's live validation of every step. Not built yet, and
each a separate plan of its own:

- Wiring a real `LlmClient` (the `AzureOpenAiClient` already exists) into
  `atlas author-from-actions` so an off-track step actually gets a bounded
  repair attempt instead of just falling back to `draft`.
- The desktop EXE shell (PyInstaller + pywebview) and packaging.
- The versioned heal-and-approve workflow and the action cache (the
  `heal_events`/`action_cache` tables already exist in the schema).
- The cloud multi-tenant backend, enterprise dashboard, and video/voice
  knowledge base.

See the architecture spec's roadmap (§18) for the full phase breakdown.
