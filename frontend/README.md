# Atlas AI Console (frontend)

React + Vite + TypeScript + Tailwind console implementing the design in
`../docs/design/atlas-ai-console-mockup.dc.html`.

## Run it

```bash
npm install
npm run dev
```

Requires the backend running at `http://localhost:8010` (see `../engine/README.md`).
The dev server is pinned to `http://localhost:5183` (see `vite.config.ts`) — the
backend's CORS allowlist is keyed to that exact origin. Set `VITE_API_BASE_URL`
if the backend runs somewhere else.

## Structure

- `src/lib/` — typed API client + TanStack Query hooks, one module per backend resource.
- `src/auth/` — session (`/auth/me`) context and the route guard that redirects to `/login`.
- `src/layout/` — sidebar shell and the client-side "viewing as" role switcher (a stand-in
  for real per-role authorization, which is deferred — see the frontend design spec).
- `src/pages/agent/`, `src/pages/dev/`, `src/pages/ApprovalsPage.tsx` — the six screens.
- `src/components/VideoStepPlayer.tsx` — the video-plus-synced-steps player shared by the
  agent review screen and the developer authoring workspace.

## Fonts

The mockup specifies Halenoir/HalenoirCompactText, both commercial fonts. This build
substitutes an open-license pairing with a similar geometric character: Outfit
(`@fontsource-variable/outfit`) for display headings, Inter (`@fontsource/inter`) for
body/UI text. The color palette and layout are otherwise unchanged from the mockup.

## What's adapted from the mockup, not copied literally

- No editable recording title/description on the review screen — `Recording` has no such
  columns, and inventing an input with nowhere to save would be dishonest UI.
- No manual "Submit to developer" action — `persist_recording` already marks a linked
  request `submitted` the moment capture stops.
- The heal approval detail shows the real mechanical selector diff
  (`heal_events.diff_summary`), not narrative prose or before/after frame images —
  neither exists in the data model.
- The developer library's per-card action is "Author" (a recording, once it exists), not
  "Claim" (which applies to a request, before a recording exists) — a dedicated developer
  request-queue screen is out of scope for this pass.
