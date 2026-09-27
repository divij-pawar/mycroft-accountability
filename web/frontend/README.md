# web/frontend — React + Vite UI

The React UI for the verification layer, served by FastAPI at **`/app`** once built — and at
**`/`**, which redirects here, since the U9 cutover on 2026-09-27 (`logs/RUN_LOG.md`). The
classic UI it replaced is archived in `../../archive/web-static-legacy/`, whose README says how
to restore it. It covers:

- starting compare runs (`#/new`) and chat runs (`#/chat`) and watching them live;
- History (Runs / Sessions / Flagged / Decide);
- run detail: the review summary, figure matrix, checks, sources, grades, and the decision gate;
- the review export (Markdown, with the JSON audit record under Technical details);
- Settings, the active directive and the Honest Ledger.

## Run it

```bash
npm ci            # install exactly what package-lock.json pins
npm run build     # typecheck + build to dist/ (served at /app by web/server.py)
npm run dev       # :5173 with /api proxied to uvicorn on :8000
npm run verify    # tsc --noEmit + vitest run + vite build — "done" means this passes
```

`scripts/start-server.sh` builds `dist/` automatically if it's missing and npm is available.
Without a build, `/` explains how to make one; the API itself runs either way.

## Dependencies are deliberate exceptions

`verification-layer/CLAUDE.md` says no non-stdlib dependency without documenting it as a
deliberate exception. The backend is stdlib + the documented Python packages. This UI is the
one place npm packages enter, by the human's explicit choice of "full React + Vite". Versions
are pinned by the committed `package-lock.json`.

| Package | Version | Why it's here |
|---|---|---|
| react, react-dom | 18.3.1 | The chosen UI framework |
| react-markdown | 9.1.0 | Renders model text as Markdown **without** raw HTML. It replaces the legacy `renderMd()` innerHTML path, closing an XSS surface in text that comes from an LLM |
| vite, @vitejs/plugin-react | 6.4.3, 4.7.0 | Build + dev server (build-time only) |
| typescript | 5.9.3 | Typecheck (build-time only) |
| vitest, jsdom | 3.2.7, 25.0.1 | Unit/component tests (dev only). Vitest 3 because Vitest 2 pins its own Vite 5, which conflicts with Vite 6 |
| @testing-library/react, @testing-library/jest-dom | 16.3.3, 6.9.1 | Component tests (dev only) |
| @types/react, @types/react-dom | 18.3.x | Types (dev only) |

Nothing here is loaded from a CDN at runtime, and there is no analytics or telemetry.

## Layout

```
src/api/        types.ts (hand-mirrored Python payloads), client.ts (fetch + Bearer scope token, SEC-02),
                stream.ts (fetch + ReadableStream event-stream reader; EventSource can't POST or send auth)
src/state/      liveRun.ts (one live run = one reducer over stream events), useLiveRun.ts (app-level
                owner: per-frame batching with a timer fallback, lost-connection polling, hand-over)
src/lib/        format.ts (figures, periods, clock), glossary.ts (plain term → technical term)
src/components/ primitives (StatusBadge, RolePill, Info, Markdown, FactValue, Section),
                TraceLog (terminal-style run trace), ComparisonMatrix (figure-by-figure:
                Figure · Period · A · Δ · B), DecisionGate (the inline human decision on
                disputed figures and failed checks, validation/gate.py), RecordSections
                (verification, claims, context window, attempts, and the comparison
                summary: review summary with per-agent evidence, issues count and the
                constraint checklist first), SourcePopover (U6: a figure's row in its
                SEC filing, or what the agent read at a cited page, in place), Assessment
                (U7: an agent's structured grade/direction/assumptions, the raw-output
                empty state, and the phone Compare mode), Grades (U8: the kind of
                disagreement, grade candidates with counted evidence, consensus, and a
                reviewer-set grade — the only grade investors see)
src/views/      RunDetail, HistoryPanel, CompareView (start form + live lanes)
tests/          Vitest; fixtures/ are real stored runs exported from web/data
```

## UI rules these components follow

- **Answer first:** summary, then evidence, then mechanics. The trace and technical details are
  collapsed by default.
- **One status vocabulary** (`StatusBadge`). Every state has a text label and a glyph, never
  color alone.
- **Who wrote what:** `RolePill` marks User / Agent / Verification Layer on every section.
- **SEC-01:** investor-scope omissions are announced ("withheld at investor scope"), never
  rendered as empty boxes. `tests/views.test.tsx` checks this against a real redacted run.
- **"Couldn't check" is never shown as "checked and wrong"** (`verified: null` vs `false`).
- **Honest liveness (U4):** a lane says "Thinking… 12s" only because a real step_started arrived
  without its step_finished, timed from the server's own `started_at`; nothing simulates progress.
  If agent B started only after agent A finished, the view says the agents ran one at a time. A
  dropped connection says the run continues on the server — and, after 10 minutes, that it may
  not be coming back. Announcements go to one app-level `aria-live` region that outlives the view.
- **Sources in place (U6):** a Source button opens an overlay anchored to the figure (a bottom
  sheet below 768px), never a page-covering modal; Enter opens it, Escape closes it and returns
  focus. It isn't offered where nothing can be looked up (no CIK recorded) or where the gate is
  withholding the figure. Web snippets are shown as recorded when the agent searched.
- **Humans decide (P1, P4):** the UI never records a decision on its own. The gate form only
  mirrors the server's rules (`validation/gate.py`), and a draft survives navigation
  (`sessionStorage`). Reads send the viewer's scope token, so an investor gets the
  server's withholding, not a client-side imitation of it.
- **Responsive:**
  - ≥1280px: History side panel.
  - Below 1280px: History becomes a drawer. A closed drawer is `visibility: hidden`, so it is out
    of the tab order.
  - Below 768px: single column.
  - Touch targets are 44px on coarse pointers, and `prefers-reduced-motion` is respected.
- **Visual system:** the legacy palette, verbatim. `brutalist/DESIGN.md` does **not** apply to
  the web app (human decision, 2026-09-24).
