---
title: Earlier documents
slug: history
section: History
order: 10
summary: Older write-ups about this subsystem, what the archive holds, and where the full history is recorded.
---

This reference describes the system as it is. Earlier documents describe it as it was; they are
kept, not rewritten, and several no longer match the code (see
[Drift found while documenting](drift.html)).

## The run log

[[logs/RUN_LOG.md]] is the canonical history: every change, live run, gate and correction,
appended in order and never edited. The entry that documents it lists its major phases.

## Design documents in the repository

| Document | What it is | Current? |
|---|---|---|
| [[docs/SYSTEM_DESIGN.md]] | The system design: components, ADRs, data flow | Partly out of date: active directive, concept sets, concurrency |
| [[docs/DATA_CONTRACT.md]] | The data contract for inputs and records | Out of date in places |
| [[docs/index.html]] | An earlier index page for the documentation | Kept as it was; it predates this site and its styling |
| [[docs/video/archive/*]] | Scripts of two earlier explainer videos | Historical |

## Earlier walkthroughs (local only)

Three HTML write-ups sit in `docs/` on the author's machine. `.gitignore` excludes `docs/*.html`,
so they aren't in the repository and won't exist in a fresh clone. They are linked as they are,
unchanged, and describe the system at the time they were written. On the published site
(GitHub Pages) these three links 404, since the files were never pushed.

- [Three mechanisms against silence](../accountability_layer_week1.html) (week 1)
- [Validation loop walkthrough](../validation_loop_walkthrough_week2.html) (week 2)
- [Inversion Audit Engine](../inversion_audit_engine_week3.html) (week 3)

## The archive

Files replaced by later work are moved to `archive/` with `git mv`, never deleted: the per-provider
adapters that LangChain replaced, and the classic UI that the review app replaced. See
[Archive](files-archive.html).

## Removed from the repository

Files deleted from the repository rather than archived. Their history is still in git.

### The prototype audit (`accountability-layer-audit.md`)


The technical audit of the original prototype, dated 2026-08-13, of commit `fe45eb4`.

> **Removed from the repository** on 2026-09-27, in the commit "add static documentation
> website and update complete documentation" (`7de4db6` in `nikbearbrown/mycroft`, `56ea8ba` in
> `divij-pawar/mycroft-accountability`). It is still in history: run
> `git show 7de4db6^:verification-layer/accountability-layer-audit.md` in the monorepo, or
> `git show 56ea8ba^:accountability-layer-audit.md` in the standalone repo. A copy is in the
> author's gitignored working folder, and `README.md` restates its critical findings. An earlier
> RUN_LOG entry (2026-09-27, "Committing the work since c53746a...") had kept the deletion out of
> those commits because the file was meant to stay.

The audit came in with the prototype on 2026-08-14 as a `*-audit.md` file, per the
Snickerdoodle convention (`logs/RUN_LOG.md`, "Integrate accountability-layer into Mycroft").
It is layer 2 of the verification stack: a report for human judgment, not a verdict. Its
method: every source file read, docstring claims traced to code paths, the suite run (108
tests passing at that commit).

Contents:

- **§0 Executive summary and §0.1 re-orientation.** The core engine is well built; problems
  cluster in guarantees "stated in documentation and enforced at one call site, but not
  structurally".
- **§1 What works correctly:** the parser, the retry-then-halt loop (ADR-07), schema
  validation, key-omitting investor redaction, bound SQL parameters, append-only triggers,
  consistent adapters, honest determinism caveats, and a fully wired UI.
- **§2 Critical findings (five):** 2.1 investor redaction bypassed at storage and read time;
  2.2 read routes unauthenticated; 2.3 `DELETE /api/runs` destroys the audit log without
  auth; 2.4 anyone can mint an `auditor` token; 2.5 a hardcoded fallback signing secret and an
  undocumented environment variable.
- **§3 High:** ADR-04's computed confidence unreachable for real providers; audit records lost
  on infrastructure failure; replay drops `context`; a near-tautological consistency probe;
  number normalisation copied three times.
- **§4 Medium and §5 Low** findings, grouped by area.
- **§6 Test coverage:** which modules had no tests at the time.
- **§7 Prioritized fix list:** eleven items, from closing the tiered-access bypass to
  housekeeping.
- **§8** The project's thesis applied to its own claims.

#### Limits and open issues
- The code has moved since the audit. Some findings are recorded as resolved (the three
  number-regex copies became `core/numeric.py`; `tests/test_claims.py` now covers claim
  extraction). Others still hold in the current code: `DELETE /api/runs` in `web/server.py`
  still calls `clear_all()`, which drops every table, with no auth dependency; replay still
  passes an empty context (`web/server.py`, the comment "context not stored separately —
  replay on subject only").
- Since the removal, three references point at a file that isn't in the repository: `docs/DATA_CONTRACT.md` cites
  its finding 2.3, `web/self_report.py`'s `audit-criticals` entry names it as its source, and
  the README's §7 condition refers to it.

Related: [[README.md]], [[docs/DATA_CONTRACT.md]], [[web/self_report.py]]
