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
