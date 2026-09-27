# Claude Instructions — verification-layer/

Scoped to this subsystem only. For the whole-repo constitution, see the repo root's
`SNICKERDOODLE.md` and `CLAUDE.md`/`AGENTS.md` — this file does not override them, it adds
subsystem-specific rules for anyone (human or AI agent) working inside `verification-layer/`.

## Log every meaningful change

- Before finishing any change to a file in this directory, add an entry to
  [`logs/RUN_LOG.md`](logs/RUN_LOG.md) in the existing format: **Recipe, Inputs, Commands,
  Outputs, Result, Open issues.** This is the canonical, detailed record — read a couple of
  existing entries before writing a new one, to match tone and level of detail.
- If the change was made, in whole or part, by a Claude agent session, also add a
  corresponding entry to [`work.md`](divij/work.md), following its own evidentiary rule: only
  claim AI authorship for a change with actual evidence (the session making the change
  itself, or a verifiable `Co-Authored-By:` git trailer) — never inferred from writing style
  alone.
- Do not backdate, edit, or delete a prior log entry to make history read as if it was
  always accurate (P3/P7: the record is append-only). If something documented earlier turns
  out to be wrong, add a new entry that says so — don't rewrite the old one.

## Stay self-contained

- Do not modify any file outside this directory. Every dependency, script, and doc this
  subsystem needs lives here (see `README.md`'s "Self-contained by design").
- Do not add a new non-stdlib dependency without documenting it as a **deliberate
  exception** in the importing file's docstring, the same way `langfuse` is documented in
  `producers/financial.py` / `pipeline/observability.py`.
- `env/` (the local virtualenv) and `.env` are gitignored — never commit them, never read
  `.env`'s contents into a message, a commit, or a log entry.

## Test before claiming done

- Run `python -m unittest discover -s tests -t .` (from inside this directory) or
  `python -m unittest discover -s verification-layer/tests -t verification-layer` (from the
  repo root) — the suite is network-free and model-free by design. If a change needs a new
  dependency or a live call to pass, the test is wrong, not the environment.
- Run `node scripts/conformance.mjs verification-layer/<changed files>` from the repo root,
  scoped to the files actually touched. Running it against the whole directory walks into a
  local `env/` virtualenv if one exists and can take 10+ minutes (see `README.md`).

## Writing a video script

If asked to write a script for a video about this subsystem (a project update, an explainer, a
deep dive) — follow [`divij/video-script-writing-guide.md`](divij/video-script-writing-guide.md).
It covers choosing update-vs-deep-dive length, what to read before drafting (recent
`logs/RUN_LOG.md` entries, `work.md`, any named proposal/SDD gap being addressed), the required
structure (cold open, chapters with chapter cards, VO/VISUAL pairing, honest-ledger chapter,
close, end card), the non-negotiable style rules, and the two credibility devices every script
needs (a fact-check table, a "deliberately refuses to say" list). Don't improvise the format from
scratch — the guide's own header names the three worked examples it was extracted from (two still
in `divij/`, one moved to `../../accountability_layer/youtube/` once it became a produced video —
see the guide for the exact path).

## Don't overclaim

- A capability existing (code written, tests passing) is not the same as that capability
  having been *observed* (a live run actually happened, an output was actually read). State
  which one is true. `logs/RUN_LOG.md`'s 2026-08-28 entries are the model for this: "wired
  in" and "not yet run against a live model" are recorded as two separate facts, never
  blurred into one.
- Before describing this subsystem's capabilities anywhere — a resume, a README, a status
  update, a video script — check the claim against
  `divij/cross-agent-validation-proposal.md` §9 ("Explicitly not claimed") and `divij/sdd.md`
  §14 ("Explicitly deferred"). Both documents exist specifically so future claims don't have
  to be re-derived from scratch, or worse, assumed.
