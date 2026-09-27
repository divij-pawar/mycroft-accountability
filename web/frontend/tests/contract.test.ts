import { describe, expect, it } from "vitest";
import { isCompareRun, type Run } from "../src/api/types";
import chatAuditor from "./fixtures/run_chat_auditor.json";
import chatInvestor from "./fixtures/run_chat_investor.json";
import compareStored from "./fixtures/run_compare_auditor.json";
import gatedAuditor from "./fixtures/run_compare_gated_auditor.json";
import gatedInvestor from "./fixtures/run_compare_gated_investor.json";

// src/api/types.ts is hand-mirrored from the Python payloads. These are real stored
// runs; if the backend's shape drifts, this fails before a renderer silently breaks.

const fixtures: [string, Run][] = [
  ["chat, auditor", chatAuditor as unknown as Run],
  ["chat, investor", chatInvestor as unknown as Run],
  ["compare, stored", compareStored as unknown as Run],
  ["compare, gated, auditor", gatedAuditor as unknown as Run],
  ["compare, gated, investor", gatedInvestor as unknown as Run],
];

describe.each(fixtures)("stored run contract: %s", (_name, run) => {
  it("has the keys every renderer relies on", () => {
    expect(typeof run.run_id).toBe("string");
    expect(typeof run.halted).toBe("boolean");
    expect(Array.isArray(run.reasoning_objects)).toBe(true);
    for (const ro of run.reasoning_objects) {
      expect(["SUCCESS", "PARSE_FAILURE", "HALT"]).toContain(ro.parse_status);
      expect(typeof ro.attempt_number).toBe("number");
      expect(typeof ro.agent_id).toBe("string");
    }
  });
});

describe("run kinds", () => {
  it("tells compare runs from chat runs", () => {
    expect(isCompareRun(compareStored as unknown as Run)).toBe(true);
    expect(isCompareRun(chatAuditor as unknown as Run)).toBe(false);
  });
  it("a compare run carries both agents' conclusions", () => {
    const cmp = (compareStored as unknown as Run).cross_agent_comparison!;
    expect(cmp.status).toBe("COMPARED");
    expect(cmp.agent_a_conclusion).toBeTruthy();
    expect(cmp.agent_b_conclusion).toBeTruthy();
  });
  it("SEC-01: investor-scope objects omit the key, they don't null it", () => {
    for (const ro of (chatInvestor as unknown as Run).reasoning_objects) {
      expect("thought_log" in ro).toBe(false);
      expect("raw_output" in ro).toBe(false);
    }
  });
});

describe("decision gate (BG)", () => {
  it("every read carries the gate the server computed", () => {
    for (const run of [gatedAuditor, gatedInvestor] as unknown as Run[]) {
      const g = run.gate!;
      expect(["NOT_GATED", "NO_DECISION_NEEDED", "AWAITING_DECISION", "DECIDED"]).toContain(g.status);
      expect(Array.isArray(g.items) && Array.isArray(g.pending) && Array.isArray(g.decisions)).toBe(true);
      expect(g.identity_note).toMatch(/not authenticated/);
    }
  });
  it("a pre-gate stored run has no gate policy, so it is never gated retroactively", () => {
    expect((compareStored as unknown as Run).gate_policy).toBeUndefined();
  });
});
