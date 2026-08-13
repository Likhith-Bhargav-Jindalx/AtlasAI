# Page-Data-Extraction Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** During a live operations-agent recording session only, inject a floating "Extract this page → JSON" button (ported verbatim from the user's previous Atlas AI application) into the main page and every popup/tab. Clicking it walks the DOM for headings/tables/labeled fields and stores the result as one `PageExtraction` row per click, scoped to the recording. The developer's Authoring page lists every extraction for a recording with a per-entry download action.

**Architecture:** A new `capture/extraction.py` module holds the ported `BUTTON`/`SERIALIZER` browser-side JS plus two small Python helpers: `attach_extraction_button()` (wires the button + an `expose_binding` notification hook onto a context) and `collect_page_data()` (runs `SERIALIZER` across a page's frames and merges the result). `live_capture.py`'s `_LiveCaptureSession` wires these in alongside its existing multi-tab handling, buffers extraction results in memory for the session's lifetime, and hands them to `persist_recording` at the end so they land in the DB atomically with the `Recording` row. A new `GET /recordings/{id}/extractions` endpoint and a small frontend list/download component complete the surface.

**Tech Stack:** Playwright Python sync API, FastAPI, SQLAlchemy (existing `PageExtraction` table from the data-model plan — no new migration), React + react-query.

## Global Constraints

- The button is available **only** during the original operations-agent recording session (`live_capture.py`) — never during `replay`/`brain` phases of an authoring run. Nothing in this plan touches `authoring/`.
- `BUTTON` and `SERIALIZER` are ported **verbatim** from the previous app (`/Users/apple/Desktop/atlasAI/core/src/capture/grabEmit.ts`), de-escaped from their TS template-literal form to real runtime JS. Do not "clean up" or rewrite this JS — copy it exactly as given in Task 1, since it's already correct, tested-in-production browser code.
- **Critical, codebase-specific deadlock hazard** (see the existing comment at the top of `capture/recorder.py`): Playwright's sync API deadlocks if an `expose_binding` callback makes a nested synchronous call back into the page (e.g. `frame.evaluate(...)`) from inside that same callback. The previous app's Python port did exactly this and — per this codebase's own established discipline — must NOT be copied that way here. The binding callback must do nothing but record "page X requested a grab" and return an immediate canned response; the actual `frame.evaluate(SERIALIZER)` work happens later, back on the session's own thread, via the same deferred `on_tick` mechanism `_LiveCaptureSession` already uses for attaching new pages (`_attach_new_pages`). Task 3 below is written around this constraint — follow it exactly, do not "simplify" by calling `collect_page_data` directly inside the binding callback.
- One `PageExtraction` row per button click (per page), not one per frame — multi-frame results are merged into a single `{"fields": {...}, "tables": [...]}` dict before storing, matching the previous app's stored shape.
- `PageExtraction` rows survive regardless of how many authoring runs happen afterward (per the data-model plan, already merged) — this plan only adds producers (the button) and one read path (the list endpoint); nothing here ties extractions to an `AuthoringRun`.

---

### Task 1: Port BUTTON/SERIALIZER JS and the pure data-collection helper

**Files:**
- Create: `engine/src/atlas_engine/capture/extraction.py`
- Create: `engine/tests/fixtures/fixture_portal/extraction_target.html`
- Test: `engine/tests/test_capture_extraction.py`

**Interfaces:**
- Produces: `BUTTON: str`, `SERIALIZER: str` (module-level constants), `collect_page_data(page: playwright.sync_api.Page) -> dict` returning `{"fields": dict[str, Any], "tables": list[dict]}`.

- [ ] **Step 1: Add the fixture page**

Create `engine/tests/fixtures/fixture_portal/extraction_target.html`:

```html
<!doctype html>
<html>
<head><title>Fixture Portal — Extraction Target</title></head>
<body>
  <h1 id="page-title">Patient Summary</h1>
  <div data-lbl="MRN">MRN-4821</div>
  <div aria-label="Ward">West Wing 3</div>
  <label for="dob-field">Date of birth</label>
  <input id="dob-field" name="dob" value="1990-01-15" readonly>
  <table id="visits-table">
    <thead><tr><th>Date</th><th>Reason</th></tr></thead>
    <tbody>
      <tr><td>2026-08-01</td><td>Checkup</td></tr>
      <tr><td>2026-07-15</td><td>Follow-up</td></tr>
    </tbody>
  </table>
</body>
</html>
```

This exercises every `SERIALIZER` path in one page: a table with `thead`/`tbody`, a `[data-lbl]` element, an `[aria-label]` element (on a non-form element, so it's picked up by the labeled-field walk), and a labeled form input.

- [ ] **Step 2: Write the failing test for `collect_page_data`**

```python
# engine/tests/test_capture_extraction.py
from playwright.sync_api import sync_playwright


def test_collect_page_data_extracts_table_and_labeled_fields(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/extraction_target.html")

        from atlas_engine.capture.extraction import collect_page_data
        data = collect_page_data(page)

        browser.close()

    assert data["fields"]["mrn"] == "MRN-4821"
    assert data["fields"]["ward"] == "West Wing 3"
    assert data["fields"]["date_of_birth"] == "1990-01-15"

    assert len(data["tables"]) == 1
    table = data["tables"][0]
    assert table["name"] == "visits_table"
    assert table["headers"] == ["Date", "Reason"]
    assert table["rows"] == [["2026-08-01", "Checkup"], ["2026-07-15", "Follow-up"]]
```

Run: `cd engine && .venv/bin/pytest tests/test_capture_extraction.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atlas_engine.capture.extraction'`

Note: `SERIALIZER` returns two parallel table shapes: `out.tables` (`{name, rowSelector, columns}` — selector-based, meant for a golden script to re-read the same table on a future run) and `out.data.tables` (`{name, headers, rows}` — the human-readable value dump). `collect_page_data` merges from `out.data` (the human-readable dump), since a `PageExtraction` is downloaded and read by a person, not replayed by automation — assert against `headers`/`rows`, not `rowSelector`/`columns`.

Note: `CSS.escape` only backslash-escapes characters that are invalid in a CSS identifier (leading digits, a lone leading `-`, control characters, etc.) — an internal `-` in `visits-table` is already a valid identifier character and is NOT escaped, so `esc(t.id)` here is just `"visits-table"` unchanged. If your local run shows a different value, fix the assertion to match what `CSS.escape` actually produces in the installed Chromium (do not change `SERIALIZER` to work around it).

- [ ] **Step 3: Write `capture/extraction.py` with the ported JS and `collect_page_data`**

```python
from typing import Any

from playwright.sync_api import Page

# Ported verbatim from the previous Atlas AI application
# (/Users/apple/Desktop/atlasAI/core/src/capture/grabEmit.ts, lines 22-82).
# Runs on-demand via frame.evaluate() — never resident via add_init_script.
# Walks the DOM for tables (as header/row pairs with a stable rowSelector)
# and labeled fields (from [data-lbl], [aria-label], and <label>-linked
# form fields), returning both machine-usable selectors (fields/tables)
# and a human-readable value dump (data.fields/data.tables).
SERIALIZER = """
() => {
  var clean = function(s){ return (s||'').replace(/\\s+/g,' ').trim().slice(0,300); };
  var esc = function(s){ try { return CSS.escape(s); } catch(e){ return s; } };
  var vis = function(el){ try { return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length); } catch(e){ return true; } };
  var snake = function(s){ return clean(s).toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'').slice(0,40) || 'field'; };
  var out = { fields: [], tables: [], data: { fields: {}, tables: [] } };
  var seen = {};
  function addField(name, selector, read, value){
    if (!name || !selector || seen[name]) return; seen[name] = 1;
    out.fields.push({ name: name, selector: selector, read: read || 'text' });
    out.data.fields[name] = value;
  }
  document.querySelectorAll('table').forEach(function(t,i){
    if (!vis(t)) return;
    var heads = Array.prototype.map.call(t.querySelectorAll('thead th, thead td'), function(x){return clean(x.innerText);});
    var bodyRows = Array.prototype.slice.call(t.querySelectorAll('tbody tr'));
    if (!bodyRows.length) bodyRows = Array.prototype.slice.call(t.querySelectorAll('tr')).slice(heads.length?1:0);
    var rows = bodyRows.slice(0,300).map(function(tr){ return Array.prototype.map.call(tr.querySelectorAll('td,th'), function(td){return clean(td.innerText);}); });
    rows = rows.filter(function(r){ return r.some(function(c){return c;}); });
    if (!rows.length) return;
    var base = t.id ? 'table#'+esc(t.id) : (t.className && typeof t.className==='string' && t.className.trim() ? 'table.'+esc(t.className.trim().split(/\\s+/)[0]) : null);
    if (!base) return;
    var ncol = Math.max.apply(null, rows.map(function(r){return r.length;}));
    var cols = []; for (var c=0;c<ncol;c++) cols.push({ name: snake(heads[c]||('col'+c)), index: c });
    var name = snake(t.id || t.getAttribute('aria-label') || ('table_'+i));
    out.tables.push({ name: name, rowSelector: base+' tbody tr', columns: cols });
    out.data.tables.push({ name: name, headers: heads, rows: rows });
  });
  document.querySelectorAll('[data-lbl]').forEach(function(el){
    if (!vis(el)) return;
    var lbl = el.getAttribute('data-lbl'); addField(snake(lbl), '[data-lbl="'+esc(lbl)+'"]', 'text', clean(el.innerText));
  });
  document.querySelectorAll('[aria-label]').forEach(function(el){
    if (el.matches('input,select,textarea,button,a') || !vis(el)) return;
    var lbl = el.getAttribute('aria-label'); var v = clean(el.innerText);
    if (v) addField(snake(lbl), '[aria-label="'+esc(lbl)+'"]', 'text', v);
  });
  document.querySelectorAll('[id]').forEach(function(el){
    if (el.matches('input,select,textarea,button,a,form,body,html,table,thead,tbody,tr,td,th')) return;
    if (el.children.length || !vis(el)) return;
    var v = clean(el.innerText);
    if (v && v.length<200) addField(snake(el.id), '#'+esc(el.id), 'text', v);
  });
  document.querySelectorAll('input,select,textarea').forEach(function(el){
    if (el.type==='password' || el.type==='hidden' || !vis(el)) return;
    var lbl = (el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name;
    var val = (el.type==='checkbox'||el.type==='radio') ? el.checked : el.value;
    if (!lbl || val===''||val==null||val===false) return;
    var selector = el.id ? '#'+esc(el.id) : (el.name ? el.tagName.toLowerCase()+'[name="'+esc(el.name)+'"]' : null);
    if (selector) addField(snake(lbl), selector, el.type==='checkbox'||el.type==='radio'?'checked':'value', (typeof val==='boolean'?val:clean(val)));
  });
  return out;
}
"""

# Ported verbatim from the same file, lines 86-108. Injected once per
# context via context.add_init_script — mounts a fixed bottom-right button
# in every top-level frame (never inside iframes) of every current and
# future page in the context. On click it ONLY notifies Python via the
# __atlasGrab binding; it does not run SERIALIZER itself and carries no
# payload — see attach_extraction_button's docstring for why.
BUTTON = """
(() => {
  if (window.top !== window.self) return;
  function mount(){
    if (document.getElementById('__atlasGrabBtn')) return;
    if (!document.body) { setTimeout(mount, 300); return; }
    var b = document.createElement('button');
    b.id = '__atlasGrabBtn';
    b.type = 'button';
    b.textContent = '\\uD83D\\uDCF8 Extract this page \\u2192 JSON';
    b.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:2147483647;background:#5b8cff;color:#fff;border:0;padding:12px 16px;border-radius:10px;font:600 14px system-ui,sans-serif;box-shadow:0 6px 20px rgba(0,0,0,.35);cursor:pointer';
    b.addEventListener('click', function(e){
      e.preventDefault(); e.stopPropagation();
      var o = b.textContent; b.textContent = '\\u23F3 extracting\\u2026';
      Promise.resolve(window.__atlasGrab && window.__atlasGrab()).then(function(r){ b.textContent = '\\u2713 ' + ((r && r.summary) || 'captured'); }).catch(function(err){ b.textContent = '\\u2717 ' + err; });
      setTimeout(function(){ b.textContent = o; }, 2500);
    }, true);
    document.body.appendChild(b);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
  setInterval(mount, 1500);
})();
"""


def collect_page_data(page: Page) -> dict[str, Any]:
    """Runs SERIALIZER on every frame of `page` (including cross-origin
    iframes, which is exactly why this is a Python-side loop over
    page.frames rather than something the button's own JS could do) and
    merges the per-frame results into one dict shaped like the previous
    app's stored extraction: {"fields": {...}, "tables": [...]}.

    Must only ever be called from the thread that owns `page`'s
    Playwright connection, and never from inside an expose_binding
    callback for this same page — see the Global Constraints note on
    the sync-API nested-call deadlock."""
    merged: dict[str, Any] = {"fields": {}, "tables": []}
    for frame in page.frames:
        try:
            result = frame.evaluate(SERIALIZER)
        except Exception:
            continue
        data = result.get("data") if result else None
        if not data:
            continue
        merged["fields"].update(data.get("fields") or {})
        merged["tables"].extend(data.get("tables") or [])
    return merged
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd engine && .venv/bin/pytest tests/test_capture_extraction.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/capture/extraction.py engine/tests/fixtures/fixture_portal/extraction_target.html engine/tests/test_capture_extraction.py
git commit -m "feat: port the previous app's page-extraction serializer verbatim"
```

---

### Task 2: `attach_extraction_button` — wire the button and its notification-only binding

**Files:**
- Modify: `engine/src/atlas_engine/capture/extraction.py`
- Test: `engine/tests/test_capture_extraction.py`

**Interfaces:**
- Consumes: `BUTTON` (Task 1).
- Produces: `attach_extraction_button(context: playwright.sync_api.BrowserContext, on_requested: Callable[[Page], None]) -> None`.

- [ ] **Step 1: Write the failing test**

Append to `engine/tests/test_capture_extraction.py`:

```python
def test_attach_extraction_button_notifies_on_click(fixture_portal_url):
    from atlas_engine.capture.extraction import attach_extraction_button

    requested_pages = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        attach_extraction_button(context, requested_pages.append)
        page = context.new_page()
        page.goto(f"{fixture_portal_url}/extraction_target.html")

        page.click("#__atlasGrabBtn")
        page.wait_for_function("document.getElementById('__atlasGrabBtn').textContent.includes('captured')")

        browser.close()

    assert requested_pages == [page]
```

Run: `cd engine && .venv/bin/pytest tests/test_capture_extraction.py::test_attach_extraction_button_notifies_on_click -v`
Expected: FAIL with `ImportError`

- [ ] **Step 2: Implement `attach_extraction_button`**

Add to `engine/src/atlas_engine/capture/extraction.py`:

```python
from typing import Callable

from playwright.sync_api import BrowserContext


def attach_extraction_button(context: BrowserContext, on_requested: Callable[[Page], None]) -> None:
    """Mounts the floating extraction button in every page (current and
    future) of `context`. The button's click handler only calls the
    exposed binding to notify Python — it carries no payload and Python
    must not run any nested Playwright call (e.g. frame.evaluate) from
    inside `_on_grab` below, since a sync-API binding callback making a
    synchronous call back into the same page deadlocks (see
    capture/recorder.py's _LISTENER_SCRIPT comment for the established
    precedent this follows). on_requested is called with the Page that
    was clicked; the actual data collection (collect_page_data) must
    happen later, back on the thread that owns this context's Playwright
    connection — not from inside on_requested itself unless the caller
    already guarantees that thread-ownership, which live_capture.py's
    wiring in Task 3 is responsible for."""
    context.add_init_script(BUTTON)

    def _on_grab(source: dict) -> dict:
        on_requested(source["page"])
        return {"summary": "captured"}

    context.expose_binding("__atlasGrab", _on_grab)
```

- [ ] **Step 3: Run the test to verify it passes**

Run: `cd engine && .venv/bin/pytest tests/test_capture_extraction.py -v`
Expected: PASS (3 tests)

- [ ] **Step 4: Commit**

```bash
git add engine/src/atlas_engine/capture/extraction.py engine/tests/test_capture_extraction.py
git commit -m "feat: attach_extraction_button wires the floating button as a notify-only binding"
```

---

### Task 3: Persist extractions — extend `persist_recording` and add the list function

**Files:**
- Modify: `engine/src/atlas_engine/recording_service.py`
- Test: `engine/tests/test_recording_service.py`

**Interfaces:**
- Consumes: `PageExtraction` model (`atlas_engine.db.models.PageExtraction`, already exists).
- Produces: `persist_recording(..., page_extractions: list[dict] | None = None)` — new keyword-only parameter, each dict shaped `{"page_index": int, "url": str, "data": dict}`. `list_page_extractions(recording_id: uuid.UUID) -> list[dict]` returning `[{"id": str, "page_index": int, "url": str, "data": dict, "extracted_at": str}, ...]` ordered oldest-first.

- [ ] **Step 1: Write the failing tests**

Add to `engine/tests/test_recording_service.py`. Its existing import block is:

```python
from atlas_engine.db.models import (
    Organization,
    PortalProfile,
    Recording,
    ReferenceTrajectory,
    VideoAsset,
    VideoStep,
)
from atlas_engine.recording_service import persist_recording
```

Add `PageExtraction` to the `db.models` import (alphabetized) and `list_page_extractions` to the `recording_service` import.

```python
def test_persist_recording_stores_page_extractions(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Extraction Org", slug=f"extraction-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    recording_id = persist_recording(
        org_id, start_url, [],
        page_extractions=[
            {"page_index": 0, "url": f"{fixture_portal_url}/extraction_target.html", "data": {"fields": {"mrn": "MRN-4821"}, "tables": []}},
        ],
    )

    extractions = list_page_extractions(recording_id)
    assert len(extractions) == 1
    assert extractions[0]["page_index"] == 0
    assert extractions[0]["url"] == f"{fixture_portal_url}/extraction_target.html"
    assert extractions[0]["data"]["fields"]["mrn"] == "MRN-4821"


def test_list_page_extractions_returns_empty_list_when_none_exist(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="No Extraction Org", slug=f"no-extraction-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    assert list_page_extractions(recording_id) == []
```

Add `list_page_extractions` and `PageExtraction` to this test file's imports.

Run: `cd engine && .venv/bin/pytest tests/test_recording_service.py -k extraction -v`
Expected: FAIL — `persist_recording() got an unexpected keyword argument 'page_extractions'` / `ImportError`

- [ ] **Step 2: Implement in `recording_service.py`**

Add `PageExtraction` to the existing `from atlas_engine.db.models import (...)` block. Add the parameter to `persist_recording`'s signature and body:

```python
def persist_recording(
    org_id: str,
    start_url: str,
    actions: list[RecordedAction],
    *,
    request_id: uuid.UUID | None = None,
    role: str = "demo",
    video_path: Path | None = None,
    video_started_at_ms: int | None = None,
    video_ended_at_ms: int | None = None,
    additional_pages: list[PageCapture] | None = None,
    page_extractions: list[dict] | None = None,
) -> uuid.UUID:
```

Inside the `with session_scope() as session:` block, after the `additional_pages` loop and before `recording_id = recording.id`:

```python
        for extraction in (page_extractions or []):
            session.add(
                PageExtraction(
                    recording_id=recording.id,
                    page_index=extraction["page_index"],
                    url=extraction["url"],
                    data=extraction["data"],
                )
            )
```

Add the new function at module level (near `get_recording_detail`):

```python
def list_page_extractions(recording_id: uuid.UUID) -> list[dict]:
    with session_scope() as session:
        extractions = (
            session.query(PageExtraction)
            .filter_by(recording_id=recording_id)
            .order_by(PageExtraction.extracted_at)
            .all()
        )
        return [
            {
                "id": str(e.id),
                "page_index": e.page_index,
                "url": e.url,
                "data": e.data,
                "extracted_at": e.extracted_at.isoformat(),
            }
            for e in extractions
        ]
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_recording_service.py -v`
Expected: PASS (including all pre-existing tests in this file — unchanged)

- [ ] **Step 4: Commit**

```bash
git add engine/src/atlas_engine/recording_service.py engine/tests/test_recording_service.py
git commit -m "feat: persist_recording stores page extractions alongside the recording"
```

---

### Task 4: Wire the button into `_LiveCaptureSession` (main page and popups)

**Files:**
- Modify: `engine/src/atlas_engine/live_capture.py`
- Test: `engine/tests/test_live_capture.py`

**Interfaces:**
- Consumes: `attach_extraction_button`, `collect_page_data` (Tasks 1-2), `persist_recording(..., page_extractions=...)` (Task 3).

- [ ] **Step 1: Write the failing test for page-index resolution**

This is the one genuinely new piece of logic in this task (everything else is straight wiring already proven correct by Tasks 1-3's real-Playwright tests), so it gets its own focused, no-browser-needed test. Add to `engine/tests/test_live_capture.py`:

```python
def test_resolve_page_index_identifies_main_and_extra_pages():
    from atlas_engine.live_capture import _resolve_page_index

    main_page = object()
    extra_page_1 = object()
    extra_page_2 = object()
    extra_captures = [(extra_page_1, None, None), (extra_page_2, None, None)]

    assert _resolve_page_index(main_page, main_page, extra_captures) == 0
    assert _resolve_page_index(extra_page_1, main_page, extra_captures) == 1
    assert _resolve_page_index(extra_page_2, main_page, extra_captures) == 2
    assert _resolve_page_index(object(), main_page, extra_captures) is None
```

Run: `cd engine && .venv/bin/pytest tests/test_live_capture.py::test_resolve_page_index_identifies_main_and_extra_pages -v`
Expected: FAIL with `ImportError`

- [ ] **Step 2: Implement `_resolve_page_index`**

Add to `engine/src/atlas_engine/live_capture.py`, near the top-level functions:

```python
def _resolve_page_index(page, main_page, extra_captures: list[tuple]) -> int | None:
    """Maps a Page object back to the page_index it has (or will have)
    in the eventual PageCapture list — 0 for the main page, position+1
    for a page already tracked in extra_captures, None if the page isn't
    tracked yet (e.g. a grab request that raced a not-yet-attached
    popup; the caller drops these rather than guessing)."""
    if page is main_page:
        return 0
    for i, (extra_page, _capture, _started_at_ms) in enumerate(extra_captures):
        if extra_page is page:
            return i + 1
    return None
```

- [ ] **Step 3: Run the test to verify it passes**

Run: `cd engine && .venv/bin/pytest tests/test_live_capture.py::test_resolve_page_index_identifies_main_and_extra_pages -v`
Expected: PASS

- [ ] **Step 4: Wire everything into `_LiveCaptureSession._run`**

In `engine/src/atlas_engine/live_capture.py`, add the import:

```python
from atlas_engine.capture.extraction import attach_extraction_button, collect_page_data
```

In `_run`, right after `context, page, video_started_at_ms, video_dir = start_capture_context(browser)` and before `page.goto(self.start_url)`, add:

```python
                pending_grab_pages: list = []
                attach_extraction_button(context, pending_grab_pages.append)
```

Change the `_attach_new_pages` closure to also drain `pending_grab_pages`, renaming its role slightly (keep the name `_attach_new_pages` — it's still passed as `on_tick` — but broaden its body):

```python
                def _attach_new_pages() -> None:
                    while new_pages:
                        new_page = new_pages.pop(0)
                        started_at_ms = int(time.time() * 1000)
                        capture = CaptureSession(new_page)
                        capture.__enter__()
                        extra_captures.append((new_page, capture, started_at_ms))

                    while pending_grab_pages:
                        grabbed_page = pending_grab_pages.pop(0)
                        page_index = _resolve_page_index(grabbed_page, page, extra_captures)
                        if page_index is None:
                            continue
                        collected = collect_page_data(grabbed_page)
                        if not collected["fields"] and not collected["tables"]:
                            continue
                        pending_extractions.append({
                            "page_index": page_index,
                            "url": grabbed_page.url,
                            "data": collected,
                        })
```

Note the ordering inside `_attach_new_pages`: new pages are attached (and thus become resolvable in `extra_captures`) *before* pending grabs are processed, in that same call — this is what lets a grab requested on a popup resolve correctly even if the popup only just opened.

Declare `pending_extractions: list[dict] = []` alongside the existing `extra_captures: list[tuple] = []` declaration (both before the `with CaptureSession(page) as capture:` block). Finally, thread it through to persistence — change the `persist_recording(...)` call at the end of `_run` to pass `page_extractions=pending_extractions`.

- [ ] **Step 5: Run the full live-capture test file**

Run: `cd engine && .venv/bin/pytest tests/test_live_capture.py -v`
Expected: PASS (all tests, including the pre-existing ones — unchanged)

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/live_capture.py engine/tests/test_live_capture.py
git commit -m "feat: live capture sessions collect page extractions from the floating button"
```

---

### Task 5: `GET /recordings/{id}/extractions`

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Test: `engine/tests/test_api_recordings.py`

**Interfaces:**
- Consumes: `list_page_extractions` (Task 3).
- Produces: `GET /recordings/{recording_id}/extractions` → `200 [PageExtractionResponse, ...]` where `PageExtractionResponse = {id: str, page_index: int, url: str, data: dict, extracted_at: str}`. `404` if the recording doesn't exist.

- [ ] **Step 1: Write the failing test**

Add to `engine/tests/test_api_recordings.py`:

```python
def test_list_extractions_endpoint_returns_stored_extractions(tmp_path, fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Extraction API Org", slug=f"extraction-api-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    recording_id = persist_recording(
        org_id, start_url, [],
        page_extractions=[
            {"page_index": 0, "url": f"{fixture_portal_url}/extraction_target.html", "data": {"fields": {"mrn": "MRN-4821"}, "tables": []}},
        ],
    )

    client = TestClient(app)
    response = client.get(f"/recordings/{recording_id}/extractions")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["page_index"] == 0
    assert body[0]["data"]["fields"]["mrn"] == "MRN-4821"


def test_list_extractions_endpoint_404_for_unknown_recording():
    client = TestClient(app)
    response = client.get(f"/recordings/{uuid.uuid4()}/extractions")
    assert response.status_code == 404
```

Run: `cd engine && .venv/bin/pytest tests/test_api_recordings.py -k extractions -v`
Expected: FAIL with `404` for both (route doesn't exist yet, FastAPI's own not-found — check the actual failure mode and confirm it's the missing route, not a false positive)

- [ ] **Step 2: Implement the endpoint**

In `engine/src/atlas_engine/api.py`, add the response model near the other recording response models:

```python
class PageExtractionResponse(BaseModel):
    id: str
    page_index: int
    url: str
    data: dict
    extracted_at: str
```

Add `list_page_extractions` to the existing `from atlas_engine.recording_service import (...)` block (line 19). `Recording` is NOT currently imported in `api.py` — add it to the existing `from atlas_engine.db.models import AuthoringRun, AuthoringRunEvent, CertificationRun, GoldenScript, User, VideoAsset` line (line 16), alphabetized: `... CertificationRun, GoldenScript, Recording, User, VideoAsset`.

Add the endpoint near the other `/recordings/{recording_id}` routes:

```python
@app.get("/recordings/{recording_id}/extractions", response_model=list[PageExtractionResponse])
def list_extractions_endpoint(recording_id: str) -> list[PageExtractionResponse]:
    with session_scope() as session:
        if session.get(Recording, uuid.UUID(recording_id)) is None:
            raise HTTPException(status_code=404, detail=f"No such recording: {recording_id}")
    return [PageExtractionResponse(**e) for e in list_page_extractions(uuid.UUID(recording_id))]
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_api_recordings.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 4: Commit**

```bash
git add engine/src/atlas_engine/api.py engine/tests/test_api_recordings.py
git commit -m "feat: add GET /recordings/{id}/extractions"
```

---

### Task 6: Frontend — list extractions on the Authoring page with a download action

**Files:**
- Create: `frontend/src/lib/extractions.ts`
- Create: `frontend/src/components/PageExtractionsList.tsx`
- Create: `frontend/src/components/PageExtractionsList.test.tsx`
- Modify: `frontend/src/pages/dev/AuthoringPage.tsx`

**Interfaces:**
- Produces: `useRecordingExtractions(recordingId: string | undefined)` (react-query hook, no polling — extractions only change during the original recording, which is over by the time this page is viewed), `PageExtraction` interface, `PageExtractionsList` component taking `{extractions: PageExtraction[]}`.

- [ ] **Step 1: Write `lib/extractions.ts`**

```typescript
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface PageExtraction {
  id: string
  page_index: number
  url: string
  data: { fields: Record<string, unknown>; tables: unknown[] }
  extracted_at: string
}

export function useRecordingExtractions(recordingId: string | undefined) {
  return useQuery({
    queryKey: ['recording-extractions', recordingId],
    queryFn: () => apiFetch<PageExtraction[]>(`/recordings/${recordingId}/extractions`),
    enabled: !!recordingId,
  })
}
```

- [ ] **Step 2: Write the failing component test**

```typescript
// frontend/src/components/PageExtractionsList.test.tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PageExtractionsList } from './PageExtractionsList'
import type { PageExtraction } from '../lib/extractions'

describe('PageExtractionsList', () => {
  it('renders one row per extraction with a download action', () => {
    const extractions: PageExtraction[] = [
      { id: '1', page_index: 0, url: 'https://portal.example.com/summary', data: { fields: { mrn: 'MRN-1' }, tables: [] }, extracted_at: '2026-08-13T10:00:00Z' },
    ]
    render(<PageExtractionsList extractions={extractions} />)
    expect(screen.getByText(/summary/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /download/i })).toBeInTheDocument()
  })

  it('renders an empty state with no extractions', () => {
    render(<PageExtractionsList extractions={[]} />)
    expect(screen.getByText(/no page extractions/i)).toBeInTheDocument()
  })
})
```

Check an existing component test (e.g. `AuthoringEventLog.test.tsx`) for this project's exact render/import conventions before finalizing.

Run: `cd frontend && npm run test -- PageExtractionsList` (or the project's equivalent single-file test invocation)
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement `PageExtractionsList`**

```tsx
// frontend/src/components/PageExtractionsList.tsx
import type { PageExtraction } from '../lib/extractions'

interface Props {
  extractions: PageExtraction[]
}

function downloadExtraction(extraction: PageExtraction) {
  const blob = new Blob([JSON.stringify(extraction.data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `extraction-${extraction.id}.json`
  link.click()
  URL.revokeObjectURL(url)
}

export function PageExtractionsList({ extractions }: Props) {
  if (extractions.length === 0) {
    return <div className="text-[13px] text-atlas-muted">No page extractions for this recording.</div>
  }

  return (
    <div className="bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden">
      <div className="px-[18px] py-3.5 border-b border-atlas-border text-[13.5px] font-semibold">
        Page extractions
      </div>
      {extractions.map((extraction) => (
        <div
          key={extraction.id}
          className="flex items-center justify-between gap-3.5 px-[18px] py-3 border-b border-atlas-rowBorder last:border-b-0"
        >
          <div className="text-[12.5px] text-atlas-body truncate">{extraction.url}</div>
          <button
            onClick={() => downloadExtraction(extraction)}
            className="text-[12.5px] py-1.5 px-3 rounded-full border border-atlas-borderHover text-atlas-primary font-medium hover:bg-atlas-accentSubtle flex-none"
          >
            Download
          </button>
        </div>
      ))}
    </div>
  )
}
```

This component performs a browser `<a download>` click triggered by the app's own code in response to a direct user click on a button the app rendered — a normal client-side file-save affordance, not a fetch of user-supplied/untrusted content.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npm run test -- PageExtractionsList`
Expected: PASS

- [ ] **Step 5: Wire into `AuthoringPage`**

In `frontend/src/pages/dev/AuthoringPage.tsx`, add the import:

```typescript
import { useRecordingExtractions } from '../../lib/extractions'
import { PageExtractionsList } from '../../components/PageExtractionsList'
```

Add the hook call alongside the existing ones:

```typescript
  const extractions = useRecordingExtractions(recordingId)
```

Render the list below the `recording.pages.map(...)` block (still inside the scrollable content `<div>`, after the video players, before the `startRun.isError` block):

```tsx
        {extractions.data && extractions.data.length > 0 && (
          <PageExtractionsList extractions={extractions.data} />
        )}
```

- [ ] **Step 6: Run the frontend test suite and build**

Run: `cd frontend && npm run test && npm run build`
Expected: All tests pass, build succeeds with no type errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/extractions.ts frontend/src/components/PageExtractionsList.tsx frontend/src/components/PageExtractionsList.test.tsx frontend/src/pages/dev/AuthoringPage.tsx
git commit -m "feat: list page extractions on the Authoring page with a download action"
```

---

### Task 7: Manual verification and finish branch

**Files:** None (verification only).

- [ ] **Step 1: Start the backend and frontend dev servers**

From the worktree: backend `.venv/bin/uvicorn atlas_engine.api:app --port 8000`, frontend `npm run dev` inside `frontend/`. Confirm both bind successfully and — critically, per this session's own experience — confirm via `lsof -p <pid> | grep cwd` that the frontend dev server's cwd is the worktree, not any other checkout, before trusting anything the browser shows.

- [ ] **Step 2: Manually verify the button on the main page**

Start a live recording (operations-agent role) against the fixture portal or any real page. Confirm the "📸 Extract this page → JSON" button appears bottom-right. Click it, confirm the button label briefly changes to "⏳ extracting…" then "✓ captured". Stop the recording.

- [ ] **Step 3: Manually verify the button on a popup**

Repeat Step 2 against a page that opens a `target="_blank"` popup (or use the existing `popup_trigger.html` fixture manually), confirming the button also appears and works inside the popup, not just the main window.

- [ ] **Step 4: Manually verify the developer-side list and download**

As the Developer role, open the Authoring page for the recording just captured. Confirm the "Page extractions" list shows one row per click (main page and popup), with the correct URLs. Click Download on one and confirm a `.json` file is saved with the expected `fields`/`tables` content.

- [ ] **Step 5: Run the full backend and frontend test suites**

Run: `cd engine && .venv/bin/pytest` and `cd frontend && npm run test && npm run build`
Expected: both fully green (the backend suite may show the pre-existing, documented flaky `test_capture_recorder.py` failure under full-suite load — rerun that one test in isolation to confirm it's not a real regression before treating the suite as green)

- [ ] **Step 6: Finish the branch**

**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch.
