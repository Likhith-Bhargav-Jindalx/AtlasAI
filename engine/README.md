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

## Healing a golden script

If a portal's UI changes, a certified golden script's steps can drift out
of date. Re-validate it against the live portal:

```bash
atlas heal <golden-script-id>
```

If every step still works, this reports "Already on-track — nothing to
heal." If something's off, it re-resolves the failing step (from the
action cache first, then an LLM if one is configured), prints a diff, and
creates a new, *unapproved* version. Set `ATLAS_AZURE_OPENAI_ENDPOINT` /
`_API_KEY` / `_MODEL` in your environment first if you want an actual LLM
repair attempt rather than just drift detection —
`atlas_engine.llm.factory.create_llm_client` returns `None`, and any step
the cache can't fix on its own is simply left off-track, when they're unset.

Review the diff, then either:

```bash
atlas approve-heal <heal-event-id>   # activates the new version, deprecates the old one
atlas reject-heal <heal-event-id>    # discards the proposed version, nothing changes
```

A healed version is never active until you explicitly approve it — this
holds whether the fix came from the LLM or from a cached selector found
during an earlier heal of the same script.

## What's next

Phase 0 and Phase 1 together cover: record -> certified golden script ->
detect drift -> repair -> approve -> new version live, all provable
without real credentials or a real portal (see
`tests/test_end_to_end_heal.py` for the full loop, including simulated
portal drift). Not built yet, each its own plan:

- The desktop EXE shell (PyInstaller + pywebview) and packaging.
- The cloud multi-tenant backend and the enterprise dashboard — including
  a UI for the approval queue this plan's CLI/API already support.
- The video/voice knowledge base.

See the architecture spec's roadmap (§18) for the full phase breakdown.
