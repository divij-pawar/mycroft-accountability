# Archived: the classic UI (`web/static/`)

The vanilla-JS browser UI this subsystem served at `/` from its first prototype until
**2026-09-27**. Archived, not deleted: `index.html`, `app.js` and `style.css` were moved
here with `git mv`, so their history follows them. They include every edit made to them
up to the move, such as the role pills and verification banner added on 2026-09-23.

## Why it was replaced

It was replaced by the React app in `web/frontend/`, which now serves `/` (roadmap phase U9;
`logs/RUN_LOG.md`, 2026-09-27). The cutover was gated on a parity checklist: everything the
classic UI could do, the React app does, with one deliberate exception.

- **Not ported: "Clear all runs"** (`DELETE /api/runs`). That route drops every table,
  including the append-only audit record and the human decisions. That contradicts the
  repository's never-delete rule, so the React app offers no button for it. The route itself
  is unchanged (admin/test use).

## How to bring it back

1. Move the three files back:

   ```bash
   git mv archive/web-static-legacy/index.html web/static/index.html
   git mv archive/web-static-legacy/app.js web/static/app.js
   git mv archive/web-static-legacy/style.css web/static/style.css
   ```

2. In `web/server.py`, restore the two lines the cutover removed:

   ```python
   app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
   ```

   and, in `root()`:

   ```python
   return FileResponse(STATIC_DIR / "index.html")
   ```

The API it calls is unchanged, apart from what later phases added. It still works against
it, though it can't show anything added after 2026-09-24: the figure matrix, checks, the
decision gate, sources or grades.
