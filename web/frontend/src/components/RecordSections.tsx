import { useState, type ReactNode } from "react";
import type { Check, Claim, Comparison, DataSource, Fact, Gate, Producer, ReasoningObject } from "../api/types";
import { Info, Markdown, RolePill, Section, StatusBadge, FactValue, type StatusState } from "./primitives";
import { ComparisonMatrix, matrixCounts } from "./ComparisonMatrix";
import { FilingSource, WebSource } from "./SourcePopover";
import { AssessmentView, CompareModeView, finalAttempt } from "./Assessment";
import { GradesPanel } from "./Grades";

// Each section of a run record, as a component. Ported from the legacy
// renderRunDetailHtml / appendAgentBubble (web/static/app.js) with the same
// honesty rules: SEC-01 omissions are announced, never rendered as empty boxes;
// "couldn't check" is never shown as "checked and failed".

// ── Is this claim's source actually confirmed? ─────────────────────────────────

function citationState(c: Claim): StatusState {
  if (c.verified === true) return "verified";
  if (c.verified === false) return "not_found";
  return "unchecked";
}

export function VerificationBanner({ claims }: { claims: Claim[] | undefined }) {
  const citations = (claims ?? []).filter((c) => c.claim_type === "citation");
  if (!citations.length) {
    return (
      <div className="verify-banner verify-none" role="status">
        <RolePill role="verifier" />
        <StatusBadge state="unchecked">Not verified</StatusBadge>
        <span className="muted">
          No [SOURCE: label, url] citation was extracted, so nothing was fetched or checked against this claim.
        </span>
      </div>
    );
  }
  return (
    <div className="verify-banner" role="status">
      <RolePill role="verifier" />
      <span className="verify-title">Source verification</span>
      <ul className="plain-list">
        {citations.map((c, i) => (
          <li key={`${c.source_url}-${i}`} className="verify-row">
            <StatusBadge state={citationState(c)} />
            {c.source_url && c.source_url !== "N/A" ? (
              <WebSource url={c.source_url} label={c.source_label || c.source_url} slot={null} />
            ) : (
              <span className="muted">no URL given</span>
            )}
            {c.verified === null && <Info term="verified_null" />}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ClaimsTable({ claims }: { claims: Claim[] | undefined }) {
  if (!claims?.length) return null;
  return (
    <Section title={`Claims (${claims.length} extracted)`} role="verifier" defaultOpen={false}>
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Type</th>
            <th scope="col">Claim</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {claims.map((c, i) => (
            <tr key={i}>
              <td>{c.claim_type}</td>
              <td>
                <div>{c.text}</div>
                <div className="muted small">{c.context}</div>
              </td>
              <td>{c.claim_type === "citation" ? <StatusBadge state={citationState(c)} /> : <span className="muted">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

export function DataSourcesTable({ sources }: { sources: DataSource[] | undefined }) {
  if (!sources?.length) return null;
  return (
    <Section title="Data sources" role="verifier">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Source</th>
            <th scope="col">Status</th>
            <th scope="col">Note</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((s, i) => (
            <tr key={i}>
              <td>{s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer">{s.source}</a> : s.source}</td>
              <td><span className="badge-neutral">{s.status}</span></td>
              <td className="muted">{s.provenance_note ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

// ── What the agent was actually sent ───────────────────────────────────────────

export function ContextWindow({ objects }: { objects: ReasoningObject[] }) {
  if (!objects.length) return null;
  // SEC-01: the key is omitted (not nulled) at investor scope — that is how
  // "withheld" is told apart from "never recorded".
  const withheld = !objects.some((o) => "directive_text" in o);
  if (withheld) {
    return (
      <p className="scope-notice">
        What the agent was sent is withheld at investor scope <Info term="sec01" />
      </p>
    );
  }
  // A compare run holds both agents' attempts in one list, so say whose each is —
  // "2 attempts" would otherwise read as one agent trying twice.
  const agents = new Set(objects.map((o) => o.agent_id));
  const title = agents.size > 1
    ? `What each agent was sent (${objects.length} prompts, ${agents.size} agents)`
    : `What the agent was sent (${objects.length} attempt${objects.length > 1 ? "s" : ""})`;
  return (
    <Section title={title} defaultOpen={false}>
      {objects.map((o) => (
        <div key={o.reasoning_id} className="attempt">
          <div className="attempt-head">
            {agents.size > 1 && <span>{o.agent_id} ·</span>} Attempt {o.attempt_number}
            {o.attempt_number > 1 && <span className="badge-neutral">directive: {o.directive_version}</span>}
          </div>
          <div className="prompt-block">
            <div className="prompt-label"><RolePill role="verifier" /> System prompt (directive {o.directive_version ?? "—"})</div>
            <pre className="prompt-text">{o.directive_text}</pre>
          </div>
          <div className="prompt-block">
            <div className="prompt-label"><RolePill role="user" /> User prompt</div>
            <pre className="prompt-text">{`Subject: ${o.context_window?.subject ?? ""}\n\nContext:\n${o.context_window?.context || "(none provided)"}`}</pre>
          </div>
        </div>
      ))}
    </Section>
  );
}

// ── Every attempt, including the ones that failed ──────────────────────────────

const ATTEMPT_STATE: Record<string, StatusState> = {
  SUCCESS: "success",
  PARSE_FAILURE: "parse_failure",
  HALT: "halted",
};

export function Attempts({ objects }: { objects: ReasoningObject[] }) {
  if (!objects.length) return null;
  return (
    <Section title="Attempts" role="agent" badge={<Info term="adr07" />} defaultOpen={false}>
      <ol className="plain-list">
        {objects.map((o) => (
          <li key={o.reasoning_id} className="attempt">
            <div className="attempt-head">
              <span>{o.agent_id} · attempt {o.attempt_number}</span>
              <StatusBadge state={ATTEMPT_STATE[o.parse_status] ?? "error"} />
            </div>
            {o.raw_output?.text ? (
              <details>
                <summary>View raw output</summary>
                <pre className="prompt-text">{o.raw_output.text}</pre>
              </details>
            ) : (
              !("raw_output" in o) && <p className="scope-notice">Raw output withheld at investor scope <Info term="sec01" /></p>
            )}
          </li>
        ))}
      </ol>
    </Section>
  );
}

export function ThoughtLog({ text, withheld }: { text: string | null | undefined; withheld: boolean }) {
  if (withheld) {
    return <p className="scope-notice">Reasoning is withheld at investor scope <Info term="sec01" /></p>;
  }
  if (!text) return null;
  return (
    <Section title="Reasoning" role="agent" badge={<Info term="thought_log" />} defaultOpen={false}>
      <Markdown text={text} />
    </Section>
  );
}

// ── Constraint checklist (B3 results, U5 layout) ───────────────────────────────
// One line per check, in the review summary at the top of the page. What didn't
// pass is open by default with its arithmetic; what passed is folded, counted.
// Hard failures about an agent's figures are what the decision gate asks about;
// heuristics ("unusual, worth a look") and checks on the filing never gate.

function checkState(c: Check): StatusState {
  if (c.outcome === "skipped") return "check_skipped";
  if (c.rule === "matches_source") return c.outcome === "pass" ? "source_match" : "source_mismatch";
  if (c.outcome === "pass") return "check_pass";
  return c.kind === "hard" ? "check_fail" : "check_warn";
}

/** What this result means for the run, in words. */
function consequence(c: Check): string | null {
  if (c.outcome === "skipped") return null; // the reason says it
  if (c.outcome === "pass") return null;
  if (c.kind === "heuristic") return "Unusual, worth a look. Not an error on its own.";
  if (c.gates) return "Held for review: a reviewer must decide.";
  return "The filing's own figures don't line up; check which data was selected.";
}

function CheckLine({ c }: { c: Check }) {
  const open = c.outcome !== "pass";
  const detail = c.withheld ? null : c.math;
  const then = consequence(c);
  return (
    <li className={`check-row check-${c.outcome}`} data-key={c.key}>
      <details open={open}>
        <summary>
          <StatusBadge state={checkState(c)} />
          <span className="check-who">{c.who}</span>
          <span className="check-plain">{c.plain}</span>
          {(detail || c.withheld) && <span className="muted small show-math">Show the math</span>}
        </summary>
        <div className="check-body">
          {c.withheld
            ? <p className="muted small">Figures withheld: pending human review.</p>
            : detail && <code className="check-math">{detail}</code>}
          {then && <p className={`small check-then ${c.gates ? "blocks" : ""}`}>{then}</p>}
          {c.reason && <p className="muted small check-reason">{c.reason}</p>}
        </div>
      </details>
    </li>
  );
}

export function Checklist({ checks }: { checks: Check[] }) {
  if (!checks.length) {
    return <p className="muted small">No accounting checks applied: neither agent stated figures a rule covers.</p>;
  }
  const passed = checks.filter((c) => c.outcome === "pass");
  const rest = checks.filter((c) => c.outcome !== "pass");
  return (
    <div className="checklist">
      {rest.length > 0 && <ul className="plain-list checks">{rest.map((c) => <CheckLine key={c.key} c={c} />)}</ul>}
      {passed.length > 0 && (
        <details className="passed-group">
          <summary><StatusBadge state="check_pass">{plural(passed.length, "check")} passed</StatusBadge></summary>
          <ul className="plain-list checks">{passed.map((c) => <CheckLine key={c.key} c={c} />)}</ul>
        </details>
      )}
    </div>
  );
}

/** "Agent A: 3 of 3 figures it was given match the SEC filing" — counted, per agent. */
function evidenceLines(checks: Check[], facts?: { a: Fact[]; b: Fact[] }): { slot: "a" | "b"; text: string; ok: boolean }[] {
  const out: { slot: "a" | "b"; text: string; ok: boolean }[] = [];
  for (const slot of ["a", "b"] as const) {
    const given = (facts?.[slot] ?? []).filter((f) => !f.missing).length;
    if (!given) continue; // no filing figures were handed to this agent (a generic run)
    const mine = checks.filter((c) => c.rule === "matches_source" && c.scope === `agent_${slot}`);
    const pass = mine.filter((c) => c.outcome === "pass").length;
    const fail = mine.filter((c) => c.outcome === "fail").length;
    const skipped = mine.length - pass - fail;
    const who = `Agent ${slot.toUpperCase()}`;
    if (!pass && !fail) {
      out.push({ slot, ok: true, text: `${who} didn't cite any of the ${plural(given, "filing figure")} it was given${skipped ? ` (${skipped} cited for a different period)` : ""}` });
    } else {
      out.push({ slot, ok: !fail, text: `${who}: ${pass} of ${pass + fail} figures it cited from its inputs match the SEC filing${skipped ? `; ${skipped} not checked (different period)` : ""}` });
    }
  }
  return out;
}

function IssuesIndicator({ checks }: { checks: Check[] }) {
  const hard = checks.filter((c) => c.outcome === "fail" && c.kind === "hard");
  const unusual = checks.filter((c) => c.outcome === "fail" && c.kind === "heuristic").length;
  const blocking = hard.filter((c) => c.gates).length;
  if (!hard.length && !unusual) {
    return <StatusBadge state="check_pass">No accounting issues found</StatusBadge>;
  }
  return (
    <>
      {hard.length > 0 && (
        <StatusBadge state="check_fail">
          {plural(hard.length, "accounting issue")}{blocking ? `: ${blocking} ${blocking === 1 ? "needs" : "need"} a decision` : ""}
        </StatusBadge>
      )}
      {unusual > 0 && <StatusBadge state="check_warn">{plural(unusual, "unusual figure")}, worth a look</StatusBadge>}
    </>
  );
}

// ── Cross-agent comparison (compare runs) ──────────────────────────────────────

const COMPARISON_HEADLINE: Record<Comparison["status"], string> = {
  COMPARED: "",
  AGENT_A_HALTED: "No comparison was possible: agent A's answer failed the format check twice",
  AGENT_B_HALTED: "No comparison was possible: agent B's answer failed the format check twice",
  BOTH_HALTED: "No comparison was possible: neither agent's answer passed the format check",
};

const RULE_LABEL: Record<string, string> = {
  concept_aware: "concept-aware rule",
  canonical_facts: "figure-by-figure rule",
  symmetric_difference: "any-number rule",
};

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** An agent's cited web pages (one pill per address; "N/A" is not an address). */
function citationsOf(claims: Claim[] | undefined): Claim[] {
  const byUrl = new Map<string, Claim>();
  for (const c of claims ?? []) {
    if (c.claim_type === "citation" && c.source_url && /^https?:/.test(c.source_url) && !byUrl.has(c.source_url)) {
      byUrl.set(c.source_url, c);
    }
  }
  return [...byUrl.values()];
}


/** Plain-language headline + detail line, built only from what was actually compared. */
function summarize(cmp: Comparison): { headline: string; details: string[] } {
  if (cmp.status !== "COMPARED") return { headline: COMPARISON_HEADLINE[cmp.status], details: [] };
  const rows = cmp.metric_comparisons;
  if (!rows) {
    return {
      headline: cmp.contradiction_flag
        ? `The agents cite different figures (${cmp.divergent_numbers.length})`
        : "The agents' figures agree",
      details: ["This run was stored before figure-by-figure comparison; only the raw numbers were compared."],
    };
  }
  const c = matrixCounts(rows);
  // Shared figures that were neither matched nor mismatched: periods differ, or
  // only one agent stated one. Never counted as agreement or as conflict.
  const unmatchable = c.shared - c.matched - c.mismatched;
  const uncorroborated = `${plural(c.uncorroborated, "figure")} cited by only one agent, with nothing to back ${c.uncorroborated === 1 ? "it" : "them"} up`;
  let headline: string;
  if (c.mismatched) {
    headline = `The agents disagree on ${c.mismatched} of ${plural(c.shared, "shared figure")}`;
  } else if (c.uncorroborated) {
    // The headline names the reason the run needs review, not only what didn't happen.
    headline = `No conflicting figures, but ${uncorroborated}`;
  } else if (c.shared && c.matched === c.shared) {
    headline = c.shared === 1 ? "The agents agree on the one figure they share"
                              : `The agents agree on all ${c.shared} shared figures`;
  } else if (c.shared) {
    headline = `No conflicting figures among ${plural(c.shared, "shared figure")}`;
  } else {
    headline = "The agents cited no figures in common";
  }
  const details = [
    c.mismatched && c.uncorroborated ? uncorroborated : "",
    unmatchable ? `${plural(unmatchable, "shared figure")} couldn't be compared: the periods differ, or only one agent stated one` : "",
    c.derivedWrong ? `${plural(c.derivedWrong, "figure")} that ${c.derivedWrong === 1 ? "doesn't" : "don't"} add up from the agent's own numbers` : "",
  ].filter(Boolean);
  return { headline, details };
}

/** "Bull" / "Bear" for the bull/bear pairing; the letter otherwise. The lane tints stay either way. */
function laneName(p: Producer | undefined, slot: "a" | "b"): { short: string; long: string } {
  if (p?.role === "BULL") return { short: "Bull", long: "Bull analyst" };
  if (p?.role === "BEAR") return { short: "Bear", long: "Bear analyst" };
  // "PRODUCER A" / "AGENT A" -> "Agent A": cased here, not by CSS, because chips and
  // Compare-mode headers show it too (found live reading "Agent a").
  return { short: slot.toUpperCase(), long: `Agent ${slot.toUpperCase()}` };
}

export function ComparisonSummary({
  cmp, producers, facts, gate, withheld, claims, objects, decidedGrade,
}: {
  cmp: Comparison;
  producers?: { a: Producer; b: Producer; same_model: boolean };
  facts?: { a: Fact[]; b: Fact[] };
  /** The inline decision gate (U3), shown directly under the checklist. */
  gate?: ReactNode;
  /** Investor scope, gate awaiting a decision: why conclusions are absent. */
  withheld?: string;
  /** Each agent's extracted claims (auditor scope), for the web pages it cited. */
  claims?: { a: Claim[]; b: Claim[] };
  /** Every attempt's record, for each agent's structured assessment (U7). */
  objects?: ReasoningObject[];
  /** The grade a reviewer recorded through the gate (U8). */
  decidedGrade?: Gate["decided_grade"];
}) {
  const [mobileView, setMobileView] = useState<"a" | "b" | "compare">("a");
  const compared = cmp.status === "COMPARED";
  const { headline, details } = summarize(cmp);
  const rows = cmp.metric_comparisons;
  const rule = RULE_LABEL[cmp.contradiction_rule ?? "symmetric_difference"] ?? cmp.contradiction_rule;
  const rowsFlag = rows ? matrixCounts(rows).flagged : null;
  const verdictDiffers = compared && rows && rowsFlag !== cmp.contradiction_flag;
  const checks = cmp.structural_flags?.checks;
  const evidence = checks ? evidenceLines(checks, facts) : [];

  return (
    <section className="card" aria-labelledby="cmp-head">
      <div className="review-summary">
      <div className="card-head">
        <RolePill role="verifier" />
        <h2 id="cmp-head" className="headline">{headline}</h2>
      </div>
      {details.length > 0 && (
        <ul className="summary-details">{details.map((d) => <li key={d}>{d}</li>)}</ul>
      )}
      {checks && (
        <div className="summary-indicators" aria-label="Evidence and accounting checks">
          {evidence.map((e) => (
            <span key={e.slot} className={`evidence lane-tag lane-tag-${e.slot}`}>
              <span aria-hidden="true">{e.ok ? "✓" : "≠"}</span> {e.text}
            </span>
          ))}
          <span className="issues"><IssuesIndicator checks={checks} /></span>
        </div>
      )}
      {cmp.synthesis && <GradesPanel synthesis={cmp.synthesis} decided={decidedGrade} />}
      {checks && <Checklist checks={checks} />}
      <p className="verdict-line">
        {compared ? (
          <>
            <span className="muted small">Recorded verdict ({rule}):</span>{" "}
            <StatusBadge state={cmp.contradiction_flag ? "mismatch" : "match"}>
              {cmp.contradiction_flag ? "Flagged for review" : "Not flagged"}
            </StatusBadge>
          </>
        ) : (
          <><StatusBadge state="halted">Not compared</StatusBadge><Info term="contradiction_null" /></>
        )}
      </p>
      {verdictDiffers && (
        <p className="muted small verdict-note" role="note">
          The recorded verdict comes from the {rule}; the figure-by-figure check below
          {rowsFlag ? " does find something to review" : " finds nothing that conflicts"}. Both are shown so neither
          silently wins.
        </p>
      )}
      {producers?.same_model && (
        <p className="muted small">Both agents ran on the same model ({producers.a.model}).</p>
      )}

      {gate}
      </div>

      {compared && rows && (
        <div className="matrix-section">
          <h3 className="subhead">Figure by figure</h3>
          <ComparisonMatrix rows={rows} />
        </div>
      )}


      {/* Below 768px the two lanes don't fit side by side: tabs, or Compare mode. */}
      <div className="mobile-agents" role="group" aria-label="Show">
        {(["a", "b", "compare"] as const).map((v) => (
          <button key={v} type="button" className={`chip ${mobileView === v ? "chip-active" : ""}`} aria-pressed={mobileView === v}
                  onClick={() => setMobileView(v)}>
            {v === "compare" ? "Compare mode" : laneName(producers?.[v], v).long}
          </button>
        ))}
      </div>
      {mobileView === "compare" && (
        <CompareModeView rows={rows} lanes={(["a", "b"] as const).map((slot) => ({
          slot, label: laneName(producers?.[slot], slot).long,
          ro: finalAttempt(objects ?? [], producers?.[slot]?.agent_id ?? (slot === "a" ? cmp.agent_a_id : cmp.agent_b_id)),
          conclusion: slot === "a" ? cmp.agent_a_conclusion : cmp.agent_b_conclusion,
          withheld: Boolean(withheld),
        }))} />
      )}
      <div className={`agents-grid mobile-${mobileView}`}>
        {(["a", "b"] as const).map((slot) => {
          const conclusion = slot === "a" ? cmp.agent_a_conclusion : cmp.agent_b_conclusion;
          const numbers = slot === "a" ? cmp.agent_a_numbers : cmp.agent_b_numbers;
          const p = producers?.[slot];
          const name = laneName(p, slot);
          const ro = finalAttempt(objects ?? [], p?.agent_id ?? (slot === "a" ? cmp.agent_a_id : cmp.agent_b_id));
          return (
            <article key={slot} className={`agent-lane lane-${slot} lane-slot-${slot}`} aria-label={`Agent ${slot.toUpperCase()}`}>
              <header className="lane-head">
                <span className={`lane-letter ${name.short.length > 1 ? "lane-word" : ""}`} aria-hidden="true">{name.short}</span>
                <RolePill role="agent" />
                <span>{name.long}</span>
                {p && <span className="badge-neutral">{p.model}</span>}
              </header>
              <AssessmentView ro={ro} />
              {!!facts?.[slot]?.length && (
                <dl className="facts">
                  {facts[slot].map((f) => (
                    <div key={f.concept} className="fact-row">
                      <dt>{f.concept}</dt>
                      <dd><FactValue fact={f} /> {!f.missing && <FilingSource slot={slot} fact={f} />}</dd>
                    </div>
                  ))}
                </dl>
              )}
              {conclusion ? <Markdown text={conclusion} />
                : withheld ? <p className="scope-notice">Conclusion withheld: pending human review.</p>
                : <p className="muted">No conclusion: this agent did not pass the format check.</p>}
              {!withheld && citationsOf(claims?.[slot]).length > 0 && (
                <div className="cite-pills" aria-label={`Web pages agent ${slot.toUpperCase()} cited`}>
                  {citationsOf(claims?.[slot]).map((c) => (
                    <WebSource key={c.source_url} url={c.source_url!} label={c.source_label || c.source_url!} slot={slot} />
                  ))}
                </div>
              )}
              <div className="num-pills" aria-label="Figures cited">
                {withheld ? null : numbers.length
                  ? numbers.map((n) => (
                      <span key={n} className={`num-pill ${cmp.divergent_numbers.includes(n) ? "diverged" : ""}`}>{n}</span>
                    ))
                  : <span className="muted small">no figures cited</span>}
              </div>
            </article>
          );
        })}
      </div>
      {compared && (
        <details className="tech">
          <summary>Technical details <Info term="score" /></summary>
          <p className="muted small">
            agreement {cmp.agreement} · score {cmp.score} · word overlap {cmp.word_overlap} · number overlap {cmp.number_overlap}
          </p>
        </details>
      )}
    </section>
  );
}
