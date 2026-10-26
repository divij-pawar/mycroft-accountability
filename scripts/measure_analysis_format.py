"""
How often does a real model follow the analysis reply format, and how often does it quote its data correctly?

    python scripts/measure_analysis_format.py --models llama3.2 --runs 10
    python scripts/measure_analysis_format.py --models llama3.2,qwen2.5:7b --runs 10 --tickers AAPL

This is phase 2's "done when" (financial_agent/PLAN.md section 10): the format pass rate is
measured on live runs per model before the UI leans on it. It runs the real agent
(financial_agent/agent.py) through the same retry-then-halt loop the app uses, on one frozen
bundle per ticker, so ten runs cost one price fetch. Prices come from today's cache when there is
one and otherwise from Alpha Vantage (two requests per ticker, 25 a day on the free key); nothing
else is fetched. Runs are not written to the app's database.

Each run varies the question (five phrasings) and the seed, at temperature 0.7, so the runs are
samples, not one answer repeated. Per run it records whether the first reply passed, whether the
retry rescued it, whether it halted and why, the tool calls made, the time taken, and the Tier 1
result (validation/analysis_checks.py) for the figures it cited.

Outputs (written after every run, so an interrupted measurement keeps what it has):
    logs/analysis-format-measurements.json   every run
    logs/analysis-format-measurements.md     the summary, for reading
    financial_agent/model_measurements.json  the compact per-model rates the app reads

What this measures: format compliance and figure quoting by one model on one prompt version, on
the tickers and dates named in the report. It is not a measure of whether any signal is right.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from financial_agent import agent as A  # noqa: E402
from financial_agent import prompts  # noqa: E402
from validation.analysis_checks import check_analysis  # noqa: E402

LOGS = ROOT / "logs"
OUT_JSON = LOGS / "analysis-format-measurements.json"
OUT_MD = LOGS / "analysis-format-measurements.md"
OUT_MODELS = ROOT / "financial_agent" / "model_measurements.json"

QUESTIONS = (
    "Should I buy {t}?",
    "What is your view on {t} over the next 6 months?",
    "Is {t} a good long-term hold?",
    "Give me a short-term signal for {t}.",
    "Would you sell {t} here?",
)
TEMPERATURE = 0.7
SEED_BASE = 1000


def failure_kind(message: str | None) -> str:
    """A reply's failure reduced to a class, so reasons can be counted: the text up to the first colon or parenthesis."""
    if not message:
        return "unknown"
    text = message.split("(")[0].split(":")[0].split(",")[0].strip().rstrip(".")
    return text[:90] or "unknown"


def one_run(record: dict | None, *, error: str | None, seconds: float, model: str, ticker: str, question: str, seed: int) -> dict:
    """The per-run row from an analysis record (or from the error that stopped it)."""
    row: dict[str, Any] = {"model": model, "ticker": ticker, "question": question, "seed": seed,
                           "seconds": round(seconds, 1), "error": error}
    if record is None:
        return {**row, "outcome": "error", "first_attempt_passed": False, "attempts": 0, "tool_calls": 0,
                "failures": [], "tier1": None}
    attempts = record["attempts"]
    first = attempts[0]["parse_status"] == "SUCCESS" if attempts else False
    failures = [failure_kind(s.get("detail")) for s in record["steps"]
                if s.get("kind") == "llm" and s.get("status") == "parse_failure"]
    outcome = "passed_first" if first else ("recovered" if record["status"] == "ok" else "halted")
    tier1 = record.get("tier1")
    return {**row, "outcome": outcome, "first_attempt_passed": first, "attempts": len(attempts),
            "tool_calls": len(record["tool_calls"]), "failures": failures, "halt_reason": record.get("halt_reason"),
            "figures_cited": len((record.get("output") or {}).get("cited_figures") or []),
            "tier1": ({k: tier1[k] for k in ("grounded", "total", "mismatched", "unreferenced", "by_value_only")}
                      if tier1 else None),
            "raw_attempts": [a.get("raw_text") for a in attempts]}


def summarize(rows: list[dict]) -> dict[str, dict]:
    """Per model: how many runs, the rates, why replies failed, time, and how figures were quoted."""
    out: dict[str, dict] = {}
    for model in sorted({r["model"] for r in rows}):
        mine = [r for r in rows if r["model"] == model]
        done = [r for r in mine if r["outcome"] != "error"]
        n = len(done)
        count = lambda o: sum(1 for r in done if r["outcome"] == o)  # noqa: E731
        t1 = [r["tier1"] for r in done if r["tier1"]]
        quoted = sum(t["total"] for t in t1)
        out[model] = {
            "runs": len(mine), "completed": n, "errors": len(mine) - n,
            "passed_first_attempt": count("passed_first"), "recovered_on_retry": count("recovered"), "halted": count("halted"),
            "first_attempt_rate": round(count("passed_first") / n, 3) if n else None,
            "final_rate": round((count("passed_first") + count("recovered")) / n, 3) if n else None,
            "failure_reasons": dict(Counter(f for r in done for f in r["failures"]).most_common()),
            "median_seconds": round(statistics.median(r["seconds"] for r in mine), 1) if mine else None,
            "median_tool_calls": statistics.median(r["tool_calls"] for r in done) if done else None,
            "runs_with_a_signal_that_cited_figures": sum(1 for t in t1 if t["total"] > 0),
            "figures_checked": quoted,
            "figures_grounded": sum(t["grounded"] for t in t1),
            "figures_mismatched": sum(t["mismatched"] for t in t1),
            "figures_unreferenced": sum(t["unreferenced"] for t in t1),
            "runs_with_a_mismatch": sum(1 for t in t1 if t["mismatched"] > 0),
            "tickers": sorted({r["ticker"] for r in mine}),
        }
    return out


def to_markdown(rows: list[dict], summary: dict[str, dict], meta: dict) -> str:
    lines = [
        "# Analysis format measurement",
        "",
        f"Measured {meta['finished_at']} with prompt `{meta['prompt_version']}`, temperature {meta['temperature']}, one seed per run, "
        f"on {', '.join(meta['tickers'])} (data as of {', '.join(sorted(set(meta['data_as_of'].values())))}).",
        "",
        "This is format compliance and figure quoting by the models listed, on these tickers and this prompt. "
        "It does not say whether any signal is right, and one ticker is a narrow sample.",
        "",
        "| Model | Runs | Passed first try | Recovered on retry | Halted | Errors | Median time |",
        "|---|---|---|---|---|---|---|",
    ]
    for m, s in summary.items():
        lines.append(f"| `{m}` | {s['runs']} | {s['passed_first_attempt']} | {s['recovered_on_retry']} | {s['halted']} | "
                     f"{s['errors']} | {s['median_seconds']} s |")
    for m, s in summary.items():
        lines += ["", f"## `{m}`", ""]
        if s["completed"]:
            lines.append(f"- First reply passed the format in **{s['passed_first_attempt']} of {s['completed']}** completed runs "
                         f"({s['first_attempt_rate']:.0%}); a signal was produced in **{s['passed_first_attempt'] + s['recovered_on_retry']}** "
                         f"({s['final_rate']:.0%}) after the one retry.")
        if s["failure_reasons"]:
            lines.append("- Why replies failed (per failed attempt): " + "; ".join(f"{k} x{v}" for k, v in s["failure_reasons"].items()) + ".")
        if s["figures_checked"]:
            lines.append(f"- Figures it cited that could be checked: {s['figures_checked']}; {s['figures_grounded']} grounded, "
                         f"{s['figures_mismatched']} mismatched, {s['figures_unreferenced']} unreferenced; "
                         f"{s['runs_with_a_mismatch']} of {s['runs_with_a_signal_that_cited_figures']} runs that cited figures had at least one mismatch.")
        else:
            lines.append("- No run produced a signal that cited a checkable figure.")
        lines.append(f"- Median tool calls per completed run: {s['median_tool_calls']}.")
    lines += ["", "## Runs", "", "| # | Model | Ticker | Question | Outcome | Attempts | Tool calls | Time | Grounded | Why a reply failed |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        t = r["tier1"]
        grounded = "{}/{}".format(t["grounded"], t["total"]) if t else "n/a"
        why = "; ".join(r["failures"]) or (r["error"] or "")[:80]
        lines.append(f"| {i} | `{r['model']}` | {r['ticker']} | {r['question']} | {r['outcome']} | {r['attempts']} | "
                     f"{r['tool_calls']} | {r['seconds']} s | {grounded} | {why} |")
    return "\n".join(lines) + "\n"


def write_outputs(rows: list[dict], meta: dict) -> dict[str, dict]:
    summary = summarize(rows)
    LOGS.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps({"meta": meta, "summary": summary, "runs": rows}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(to_markdown(rows, summary, meta), encoding="utf-8")
    compact = {"measured_at": meta["finished_at"], "prompt_version": meta["prompt_version"], "temperature": meta["temperature"],
               "tickers": meta["tickers"],
               "models": {m: {"runs": s["completed"], "first_attempt_passed": s["passed_first_attempt"],
                              "recovered_on_retry": s["recovered_on_retry"], "halted": s["halted"],
                              "first_attempt_rate": s["first_attempt_rate"], "final_rate": s["final_rate"]}
                          for m, s in summary.items()}}
    OUT_MODELS.write_text(json.dumps(compact, indent=1) + "\n", encoding="utf-8")
    return summary


def measure(models: list[str], tickers: list[str], runs: int, *, step_for: Callable[[str, float, int], A.ModelStep],
            bundle_for: Callable[[str], dict], progress: Callable[[str], None] = print) -> tuple[list[dict], dict]:
    """The runs, one model after another, and the metadata. `step_for(model, temperature, seed)` is the model."""
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bundles = {t: bundle_for(t) for t in tickers}
    rows: list[dict] = []
    meta = {"started_at": started, "finished_at": started, "prompt_version": prompts.ANALYSIS_PROMPT_VERSION,
            "temperature": TEMPERATURE, "runs_per_model": runs, "models": models, "tickers": tickers,
            "data_as_of": {t: b["data"]["as_of"] for t, b in bundles.items()}, "bundle_hashes": {t: b["bundle_hash"] for t, b in bundles.items()}}
    for model in models:
        for i in range(runs):
            ticker = tickers[i % len(tickers)]
            question = QUESTIONS[i % len(QUESTIONS)].format(t=ticker)
            seed = SEED_BASE + i
            plan = A.plan_request(question)
            began = time.monotonic()
            record, error = None, None
            try:
                record = A.run_analysis(plan, bundle=bundles[ticker], model_step=step_for(model, TEMPERATURE, seed),
                                        model=model, temperature=TEMPERATURE, seed=seed)
                record["tier1"] = check_analysis(record)
            except Exception as exc:  # a model that can't be reached is a result, not a crash
                error = f"{type(exc).__name__}: {exc}"
            row = one_run(record, error=error, seconds=time.monotonic() - began, model=model, ticker=ticker,
                          question=question, seed=seed)
            rows.append(row)
            meta["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            write_outputs(rows, meta)
            progress(f"{model} run {i + 1}/{runs}: {row['outcome']} in {row['seconds']} s"
                     + (f" ({', '.join(row['failures'])})" if row["failures"] else "")
                     + (f" [{error[:100]}]" if error else ""))
    return rows, meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models", default="llama3.2", help="comma-separated Ollama (or Gemini) model names")
    ap.add_argument("--runs", type=int, default=10, help="runs per model")
    ap.add_argument("--tickers", default="AAPL", help="comma-separated; each costs two price requests unless today's cache has it")
    args = ap.parse_args()

    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True))

    def bundle_for(ticker: str) -> dict:
        bundle, notes = A.prepare_bundle(ticker)
        for n in notes:
            print(f"  note ({ticker}): {n}")
        print(f"  {ticker}: data as of {bundle['data']['as_of']}, bundle {bundle['bundle_hash'][:19]}")
        return bundle

    def step_for(model: str, temperature: float, seed: int) -> A.ModelStep:
        return A.langchain_model_step(model, temperature, seed)

    rows, meta = measure([m.strip() for m in args.models.split(",") if m.strip()],
                         [t.strip().upper() for t in args.tickers.split(",") if t.strip()], args.runs,
                         step_for=step_for, bundle_for=bundle_for)
    print(f"\nwrote {OUT_JSON.relative_to(ROOT)}, {OUT_MD.relative_to(ROOT)}, {OUT_MODELS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
