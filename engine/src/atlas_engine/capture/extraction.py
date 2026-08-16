from typing import Any, Callable

from playwright.sync_api import BrowserContext, Page

# Ported verbatim from the previous Atlas AI application's reportDump.ts
# (REPORT_DUMP_JS). Runs inside a nested content frame — an Epic HSWeb
# "Detail Report", a ReportViewer iframe, any heading+table report layout —
# and returns its COMPLETE content as structured JSON:
# { "<section title>": [ { label: value, ... }, ... ] }. Used by the golden's
# `reportFrame` extraction path: when the operator marked a data-dense nested
# frame rather than page fields, the extractor finds that frame and dumps
# EVERYTHING in it instead of reading individual selectors. Kept as a raw
# string so regex backslashes survive; embedded into the emitted Python via
# base64 (see codegen/emit.py) so no escaping layer can corrupt it.
REPORT_DUMP_JS = r"""() => {
  const collapse = (s) => (s || "").replace(/\n/g, " ").replace(/\s+/g, " ").trim();
  const txt = (el) => collapse(el ? (el.textContent || "") : "");
  const lines = (el) => (el ? (el.textContent || "") : "")
    .split(/\n|[\r\n]+/).map((x) => x.replace(/\s+/g, " ").trim()).filter(Boolean);
  const cellPairs = (cell) => {
    const o = {};
    const labs = Array.from(cell.querySelectorAll('[class*="label" i]'));
    if (!labs.length) return o;
    const vals = Array.from(cell.querySelectorAll('[class*="content" i], [class*="value" i]'));
    if (vals.length === labs.length) {
      labs.forEach((l, i) => { const k = txt(l); if (k) o[k] = txt(vals[i]); });
    } else {
      for (const l of labs) { const k = txt(l); if (!k) continue; const s = l.nextElementSibling; o[k] = s ? txt(s) : ""; }
    }
    return o;
  };
  const isHeading = (el) => {
    const t = el.tagName.toLowerCase();
    if (["h1", "h2", "h3", "h4"].includes(t)) return true;
    const c = (el.className || "") + "";
    return /section.?title|sectiontitle|report.?title|panelheader|group.?title/i.test(c);
  };
  const dumpTable = (tbl) => {
    const allRows = Array.from(tbl.querySelectorAll(":scope > thead > tr, :scope > tbody > tr, :scope > tr"));
    if (!allRows.length) return null;
    const headRow = allRows.find((r) => r.querySelector(":scope > th"));
    const headers = headRow ? Array.from(headRow.querySelectorAll(":scope > th")).map(txt) : [];
    const bodyRows = allRows.filter((r) => r !== headRow && r.querySelector(":scope > td"));
    const rows = [];
    for (const r of bodyRows) {
      const cells = Array.from(r.querySelectorAll(":scope > td"));
      if (!cells.length) continue;
      if (headers.length && headers.length === cells.length) {
        const o = {};
        headers.forEach((h, i) => { const v = txt(cells[i]); if (h || v) o[h || ("col" + i)] = v; });
        if (Object.values(o).some((v) => v !== "")) rows.push(o);
        continue;
      }
      const o = {}; let anyPair = false;
      cells.forEach((c, i) => {
        const pairs = cellPairs(c);
        if (Object.keys(pairs).length) { Object.assign(o, pairs); anyPair = true; return; }
        const ls = lines(c);
        if (ls.length >= 2) { o[ls[0]] = ls.slice(1).join(" "); anyPair = true; }
        else if (ls.length === 1) { o["col" + i] = ls[0]; }
      });
      if (Object.keys(o).length && (anyPair || cells.length === 2)) rows.push(o);
      else { const vals = cells.map(txt).filter((v) => v !== ""); if (vals.length) rows.push(vals); }
    }
    return rows.length ? rows : null;
  };
  const out = {};
  let section = "";
  const walk = (node) => {
    for (const el of Array.from(node.children || [])) {
      if (isHeading(el)) { section = txt(el) || section; continue; }
      if (el.tagName && el.tagName.toLowerCase() === "table") {
        if (el.closest("td, th")) continue;
        const rows = dumpTable(el);
        if (rows) { (out[section || "data"] = out[section || "data"] || []).push(...rows); }
        continue;
      }
      walk(el);
    }
  };
  walk(document.body || document.documentElement);
  for (const k of Object.keys(out)) {
    const seen = new Set(); const uniq = [];
    for (const row of out[k]) { const sig = JSON.stringify(row); if (!seen.has(sig)) { seen.add(sig); uniq.push(row); } }
    out[k] = uniq;
  }
  return out;
}"""

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


def sanitize_collected_text(value: Any) -> Any:
    """Scrubs anything Postgres JSONB will refuse from every string in a
    collected extraction payload: lone UTF-16 surrogates (which JS
    textContent/innerText can legally hold and SERIALIZER's clean() does not
    filter) and embedded NUL bytes.

    This is the point of control for that concern — where JS strings first
    become Python strings. persist_recording inserts PageExtraction rows in
    the same transaction as the Recording itself, so one bad character
    reaching the driver would roll back the ENTIRE recording, not just the
    offending extraction. Everything else in this feature is best-effort
    (per-frame try/except below, whole-loop try/except in live_capture.py);
    this keeps the payload itself from being the one fatal path."""
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace").decode("utf-8").replace("\x00", "")
    if isinstance(value, dict):
        return {sanitize_collected_text(k): sanitize_collected_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_collected_text(v) for v in value]
    return value


def collect_page_data(page: Page) -> dict[str, Any]:
    """Runs SERIALIZER on every frame of `page` (including cross-origin
    iframes, which is exactly why this is a Python-side loop over
    page.frames rather than something the button's own JS could do) and
    merges the per-frame results into one dict shaped like the previous
    app's stored extraction: {"fields": {...}, "tables": [...]}.

    The per-frame SERIALIZER output carries BOTH the discovered spec
    (`fields`/`tables` arrays with selectors + read modes, suitable for
    freezing into a golden's _extract_<name>(scope) functions) and the
    value dump (`data.fields`/`data.tables`). Both are preserved here,
    under `spec` and the value keys, so a recording holds everything
    needed to compile a verified extraction spec later.

    Must only ever be called from the thread that owns `page`'s
    Playwright connection, and never from inside an expose_binding
    callback for this same page — see the Global Constraints note on
    the sync-API nested-call deadlock."""
    merged: dict[str, Any] = {"fields": {}, "tables": [], "spec": {"fields": [], "tables": []}}
    for frame in page.frames:
        try:
            result = frame.evaluate(SERIALIZER)
        except Exception:
            continue
        if not result:
            continue
        data = result.get("data") if isinstance(result, dict) else None
        if data:
            merged["fields"].update(data.get("fields") or {})
            merged["tables"].extend(data.get("tables") or [])
        if isinstance(result, dict):
            merged["spec"]["fields"].extend(result.get("fields") or [])
            merged["spec"]["tables"].extend(result.get("tables") or [])
    return sanitize_collected_text(merged)


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
