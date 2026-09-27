import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { Info, Markdown, RolePill, StatusBadge } from "../src/components/primitives";
import { TraceLog, hostOf, stepLine } from "../src/components/TraceLog";
import { VerificationBanner } from "../src/components/RecordSections";
import type { Claim, Step } from "../src/api/types";
import chatRun from "./fixtures/run_chat_auditor.json";

describe("status vocabulary", () => {
  it("every state carries a text label and a glyph, not just a color", () => {
    render(<StatusBadge state="mismatch" />);
    const badge = screen.getByText("Mismatch").closest(".badge")!;
    expect(badge).toHaveTextContent("≠");
    expect(badge).toHaveClass("badge-danger");
  });
  it("role pills name the author", () => {
    render(<><RolePill role="user" /><RolePill role="agent" /><RolePill role="verifier" /></>);
    expect(screen.getByText("User")).toBeInTheDocument();
    expect(screen.getByText("Agent")).toBeInTheDocument();
    expect(screen.getByText("Verification Layer")).toBeInTheDocument();
  });
  it("technical terms are one click away, keyboard-reachable", () => {
    render(<Info term="sec01" />);
    const btn = screen.getByRole("button", { name: /investor view/i });
    expect(btn).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(btn);
    expect(screen.getByRole("note")).toHaveTextContent("SEC-01");
  });
});

describe("Markdown", () => {
  it("does not render raw HTML from model text", () => {
    const { container } = render(<Markdown text={'Hello <img src=x onerror="alert(1)"> **bold**'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("strong")).toHaveTextContent("bold");
  });
});

describe("VerificationBanner", () => {
  it("says so plainly when nothing was checked", () => {
    render(<VerificationBanner claims={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent(/nothing was fetched or checked/i);
  });
  it("keeps couldn't-check distinct from checked-and-wrong", () => {
    const claims: Claim[] = [
      { claim_type: "citation", text: "a", context: "", verified: null, source_label: "A", source_url: "https://a.example" },
      { claim_type: "citation", text: "b", context: "", verified: false, source_label: "B", source_url: "https://b.example" },
      { claim_type: "citation", text: "c", context: "", verified: true, source_label: "C", source_url: "https://c.example" },
    ];
    render(<VerificationBanner claims={claims} />);
    expect(screen.getByText("Couldn't check")).toBeInTheDocument();
    expect(screen.getByText("Not found in source")).toBeInTheDocument();
    expect(screen.getByText("Matches source")).toBeInTheDocument();
  });
});

describe("TraceLog", () => {
  const steps = (chatRun as { steps: Step[] }).steps;

  it("is collapsed by default but always shows the latest step", () => {
    const { container } = render(<TraceLog steps={steps} />);
    const details = container.querySelector("details.trace")!;
    expect(details).not.toHaveAttribute("open");
    const latest = steps.reduce((a, b) => (b.seq > a.seq ? b : a));
    // toHaveTextContent collapses whitespace in the element; do the same to the expectation.
    expect(screen.getByLabelText("Latest step")).toHaveTextContent(stepLine(latest).replace(/\s+/g, " ").trim());
  });

  it("lists steps in seq order and expands one to show its detail", () => {
    const { container } = render(<TraceLog steps={[...steps].reverse()} />);
    const rows = container.querySelectorAll(".trace-line");
    expect(rows.length).toBe(steps.length);
    const firstSeq = Math.min(...steps.map((s) => s.seq));
    const first = steps.find((s) => s.seq === firstSeq)!;
    expect(rows[0]).toHaveTextContent(first.label);
    const btn = within(rows[0] as HTMLElement).getByRole("button");
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
  });

  it("shows a tool call's query and its result URLs", () => {
    const tool: Step = {
      seq: 1, phase: "agent_a", label: "Tool call: tavily_search (finished)", detail: null, url: null,
      status: "ok", error: null, duration_ms: 1420, kind: "tool", tool: "tavily_search", tool_phase: "finished",
      query: "AAPL revenue", urls: ["https://www.apple.com/newsroom/x", "not a url"], retried_query_only: true,
    };
    const { container } = render(<TraceLog steps={[tool]} />);
    expect(container.querySelector(".trace-row")).toHaveTextContent('query="AAPL revenue"');
    expect(screen.getByText("Retried")).toBeInTheDocument();
    fireEvent.click(container.querySelector(".trace-row")!);
    expect(screen.getByText("www.apple.com")).toBeInTheDocument();
    expect(screen.getByText("not a url")).toBeInTheDocument();
  });

  it("filters by lane", () => {
    const mixed: Step[] = [
      { seq: 1, phase: "shared", label: "fetch", detail: null, url: null, status: "ok", error: null, duration_ms: 1 },
      { seq: 2, phase: "agent_a", label: "LLM attempt 1", detail: null, url: null, status: "ok", error: null, duration_ms: 1 },
    ];
    const { container } = render(<TraceLog steps={mixed} />);
    fireEvent.click(screen.getByRole("button", { name: "agent A" }));
    expect(container.querySelectorAll(".trace-line").length).toBe(1);
  });

  it("never throws on a malformed URL", () => {
    expect(hostOf("::::")).toBe("::::");
  });
});
