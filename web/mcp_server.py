"""
The verification layer as an MCP server: an agent you run yourself (Claude Code, Codex, Cursor
or any MCP client, with your own login) becomes the agent the layer checks, records and gates.

    claude mcp add --transport http mycroft http://localhost:8000/mcp

The model runs in your session; the layer never calls it. What the layer does is unchanged:
the session's answers go through the same output contract, the same one-retry-then-halt loop,
the same figure-by-figure comparison, accounting checks and decision gate as a run the server
drives itself (web/agent_sessions.py). Every call is recorded by the server as it happens, so
the review app can watch the run live (GET /api/runs/{id}/live).

Tools:
    start_compare   create a compare run; this session is agent A. Agent B is the local model
                    (partner="ollama") or a second session that calls join_compare.
    join_compare    join a run as agent B.
    submit          hand in a reply; returns what to do next (a retry, the grade extraction,
                    or the outcome).
    search          the layer's own web search: every query and result is recorded.
    get_run         where a run stands, and its comparison and gate once it's done.

Deliberate exception to the stdlib-leaning rule (CLAUDE.md): this module adds the official MCP
Python SDK (`mcp`), imported only here and only when the server mounts it.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Callable

from mcp.server.fastmcp import Context, FastMCP

from web import agent_sessions
from web.agent_sessions import SessionAgent

APP_URL = os.environ.get("MYCROFT_APP_URL", "http://localhost:8000")
FIRST_PROMPT_WAIT_S = 120.0   # start_compare waits this long for the EDGAR fetch and the first prompt
NEXT_WAIT_S = 110.0           # submit waits this long for the next step before saying "still working"
OUTCOME_WAIT_S = 90.0         # after the last step, how long submit waits for the comparison

INSTRUCTIONS = """\
This server is a verification layer. You act as one agent in a two-agent compare; the layer
checks your answer's format, compares your figures with the other agent's and with the SEC
filing, runs accounting checks, and stops the run for a human reviewer when something
conflicts. It never decides who is right.

How to work:
1. Call start_compare(ticker="AAPL") (or subject="..." for a non-financial question). You get
   the instructions (the directive), the subject and the context to work from.
2. Do the task. For web research, use this server's `search` tool (every query and result is
   recorded and can be checked) or your own web search, whichever gives better results. Cite
   every source as [SOURCE: label, url]; cited pages are fetched and checked.
3. Call submit(run_id, slot, text) with your complete reply, exactly in the format the
   instructions require. The reply is checked by the server, not by you.
4. Follow what submit returns: a retry (your reply broke the format; fix it and submit
   again), an assessment request (return only the JSON object asked for), or the outcome.
5. Use get_run(run_id) any time. A person can watch the whole run live in the review app at
   the `watch` link.
Don't fabricate figures: every figure is compared with the filing you were given.
"""


def _client_label(ctx: Context | None, given: str) -> str:
    """What the MCP client says it is (or what the caller passed): recorded, never verified."""
    if given.strip():
        return given.strip()[:60]
    try:
        info = ctx.session.client_params.clientInfo  # type: ignore[union-attr]
        return f"{info.name} {info.version}".strip()[:60]
    except Exception:
        return "agent session"


def _watch(run_id: str) -> str:
    return f"{APP_URL}/app/#/live/{run_id}"


def build(run_compare: Callable[..., dict], read_run: Callable[[str], dict | None],
          make_request: Callable[..., Any], run_search: Callable[[str], Any] | None,
          result_urls: Callable[[Any], list], result_snippets: Callable[[Any], list]) -> FastMCP:
    """The MCP server, wired to the web server's compare runner and readers (passed in, so this
    module doesn't import web/server.py)."""
    mcp = FastMCP("mycroft-verification-layer", instructions=INSTRUCTIONS)

    def _reply(run_id: str, slot: str, msg: dict | None) -> dict:
        base = {"run_id": run_id, "slot": slot, "watch": _watch(run_id)}
        if msg is None:
            return {**base, "status": "waiting",
                    "note": "The layer is still working (the filing fetch, or the other agent). "
                            "Call get_run(run_id) or submit again later."}
        kind = msg["kind"]
        if kind == "answer":
            return {**base, "status": "your_turn", "attempt": msg["attempt"],
                    "previous_attempt_failed": msg.get("previous_attempt_failed"),
                    "instructions": msg["instructions"], "subject": msg["subject"], "context": msg["context"],
                    "how_to_respond": "Call submit(run_id, slot, text) with your complete reply in exactly the "
                                      "format the instructions require."}
        if kind == "assess":
            return {**base, "status": "assessment_requested", "instructions": msg["instructions"],
                    "input": msg["input"],
                    "how_to_respond": "Call submit(run_id, slot, text) with only the JSON object the instructions ask for."}
        if kind == "slot_done":
            return {**base, "status": "halted" if msg["halted"] else "accepted",
                    "note": ("Your reply failed the output contract twice, so your side of the run halted."
                             if msg["halted"] else "Your answer was accepted.")}
        return {**base, "status": "run_over", "error": msg.get("error")}

    async def _next(agent: SessionAgent, wait: float) -> dict | None:
        return await asyncio.to_thread(agent.next_message, wait)

    def _agent(run_id: str, slot: str) -> SessionAgent:
        run = agent_sessions.get(run_id)
        if run is None or slot not in run.agents:
            raise ValueError(f"No session slot {slot!r} in run {run_id!r}.")
        return run.agents[slot]

    @mcp.tool()
    async def start_compare(ticker: str = "", subject: str = "", context: str = "", pairing: str = "lenses",
                            partner: str = "ollama", agent_label: str = "", ctx: Context | None = None) -> dict:
        """Start a compare run with this session as agent A. Give a stock `ticker` (the agents
        reason over its SEC filing) or a `subject` (a question, with optional `context`).
        `pairing` is "lenses" (financial vs earnings figures) or "bull_bear". `partner` is
        "ollama" (the layer runs agent B on the local model) or "session" (a second session
        joins with join_compare). Returns the instructions and inputs for your first answer."""
        if bool(ticker.strip()) == bool(subject.strip()):
            return {"error": "Give exactly one of ticker or subject."}
        if partner not in ("ollama", "session"):
            return {"error": 'partner must be "ollama" or "session".'}
        if pairing not in ("lenses", "bull_bear"):
            return {"error": 'pairing must be "lenses" or "bull_bear".'}
        try:
            request = make_request(ticker=ticker.strip() or None, subject=subject.strip() or None,
                                   context=context, pairing=pairing)
        except Exception as exc:
            return {"error": f"Invalid request: {exc}"}
        run_id = str(uuid.uuid4())
        label = _client_label(ctx, agent_label)
        agents = {"a": SessionAgent(run_id, "a", label)}
        if partner == "session":
            agents["b"] = SessionAgent(run_id, "b", "second session")
        agent_sessions.start(run_id, agents, partner, lambda: run_compare(
            request, "auditor", run_id=uuid.UUID(run_id), sessions=agents, started_by=f"session: {label}"))
        reply = _reply(run_id, "a", await _next(agents["a"], FIRST_PROMPT_WAIT_S))
        reply["partner"] = partner
        if partner == "session":
            reply["for_the_second_session"] = f'join_compare(run_id="{run_id}")'
        return reply

    @mcp.tool()
    async def join_compare(run_id: str, agent_label: str = "", ctx: Context | None = None) -> dict:
        """Join a compare run as agent B (the run must have been started with partner="session").
        Returns the instructions and inputs for your first answer."""
        run = agent_sessions.get(run_id)
        if run is None or "b" not in run.agents:
            return {"error": "No run with a session slot for agent B under that id."}
        agent = run.agents["b"]
        if agent.joined:
            return {"error": "Agent B has already joined this run."}
        agent.joined = True
        agent.label = _client_label(ctx, agent_label)
        return _reply(run_id, "b", await _next(agent, FIRST_PROMPT_WAIT_S))

    @mcp.tool()
    async def submit(run_id: str, slot: str, text: str) -> dict:
        """Hand in your reply for this run and slot ("a" or "b"): your answer, or the assessment
        JSON when one was requested. The server checks it; returns what to do next."""
        try:
            agent = _agent(run_id, slot)
        except ValueError as exc:
            return {"error": str(exc)}
        if agent.finished:
            return {**_reply(run_id, slot, {"kind": "slot_done", "halted": False}), "note": "This slot is already done."}
        agent.submit(text)
        msg = await _next(agent, NEXT_WAIT_S)
        reply = _reply(run_id, slot, msg)
        if msg is not None and msg["kind"] in ("slot_done", "run_done"):
            run = agent_sessions.get(run_id)
            if run is not None:
                await asyncio.to_thread(run.done.wait, OUTCOME_WAIT_S)
            reply["outcome"] = _outcome(run_id)
        return reply

    @mcp.tool()
    async def search(run_id: str, slot: str, query: str) -> dict:
        """The layer's web search (Tavily). Every query and result is recorded in the run's trace,
        so sources found this way can be shown and checked. Your own web search works too."""
        if run_search is None or not os.environ.get("TAVILY_API_KEY"):
            return {"error": "The layer's search isn't configured (no TAVILY_API_KEY). Use your own web search, "
                             "and cite every source as [SOURCE: label, url]."}
        try:
            agent = _agent(run_id, slot)
        except ValueError as exc:
            return {"error": str(exc)}
        result = await asyncio.to_thread(agent.record_search, query, run_search, result_urls, result_snippets)
        if isinstance(result, str) and result.startswith("Tool error:"):
            return {"error": result}
        return {"query": query, "results": result_snippets(result)}

    @mcp.tool()
    async def get_run(run_id: str) -> dict:
        """Where a run stands: which agents are still working, or, once it's done, the
        figure-by-figure comparison, the checks that failed and the decision-gate status."""
        run = agent_sessions.get(run_id)
        if run is not None and not run.done.is_set():
            return {"run_id": run_id, "status": "in_progress", "watch": _watch(run_id),
                    "agents": {s: ("done" if a.finished else "working") for s, a in run.agents.items()},
                    "partner": run.partner}
        return {"run_id": run_id, "status": "done", **_outcome(run_id)}

    def _outcome(run_id: str) -> dict:
        run = agent_sessions.get(run_id)
        record = read_run(run_id)
        if record is None:
            return {"stored": False, "error": run.error if run else "No such run.", "watch": _watch(run_id)}
        cmp = record.get("cross_agent_comparison") or {}
        checks = (cmp.get("structural_flags") or {}).get("checks") or []
        return {
            "stored": True,
            "record": f"{APP_URL}/app/#/runs/{run_id}",
            "comparison_status": cmp.get("status"),
            "figures": [{"metric": r.get("metric"), "status": r.get("status"), "a": r.get("raw_a"), "b": r.get("raw_b")}
                        for r in cmp.get("metric_comparisons") or []],
            "failed_checks": [{"who": c.get("who"), "rule": c.get("rule"), "math": c.get("math"), "gates": c.get("gates")}
                              for c in checks if c.get("outcome") == "fail"],
            "gate": (record.get("gate") or {}).get("status"),
            "note": "A person decides anything the gate holds; this tool can't.",
        }

    return mcp
