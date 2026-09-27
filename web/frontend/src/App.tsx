import { useCallback, useEffect, useState } from "react";
import type { Run, Scope } from "./api/types";
import { HistoryPanel, type Tab } from "./views/HistoryPanel";
import { RunDetail } from "./views/RunDetail";
import { CompareView } from "./views/CompareView";
import { ChatView, DirectiveView, LedgerView, SettingsView } from "./views/Pages";
import { api } from "./api/client";
import { useLiveRun } from "./state/useLiveRun";

// Workspace shell. The route lives in the URL hash so a view is linkable and the
// back button works: #/runs/{id} a stored record, #/new a compare run, #/chat a
// chat run, and #/settings, #/directive, #/ledger the pages that used to be the
// classic UI's panels and modals (U9: this app now serves "/").

type Page = "new" | "chat" | "settings" | "directive" | "ledger";
type Route = { view: "run"; id: string } | { view: Page } | { view: "empty" };
const PAGES: Page[] = ["new", "chat", "settings", "directive", "ledger"];

function readRoute(): Route {
  const run = window.location.hash.match(/^#\/runs\/([\w-]+)/);
  if (run) return { view: "run", id: run[1] };
  const page = PAGES.find((p) => window.location.hash.startsWith(`#/${p}`));
  return page ? { view: page } : { view: "empty" };
}

function useRoute(): [Route, (hash: string) => void] {
  const [route, setRoute] = useState<Route>(readRoute);
  useEffect(() => {
    const onHash = () => setRoute(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return [route, (hash: string) => { window.location.hash = hash; }];
}

export function App() {
  const [scope, setScope] = useState<Scope>("auditor");
  const [route, go] = useRoute();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [awaiting, setAwaiting] = useState(0);
  const [requestedTab, setRequestedTab] = useState<{ tab: Tab; at: number } | undefined>();
  // One polite live region for the whole app. It lives here, not in the live view,
  // because the live view unmounts the moment a run is handed over to its record —
  // found live: "Comparison ready" was removed before a screen reader could say it.
  const [announcement, setAnnouncement] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [ledgerOpen, setLedgerOpen] = useState<number | null>(null);
  const [directiveVersion, setDirectiveVersion] = useState<string | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  // The classic UI's status dot and badges: is the server up, which directive, how many open issues.
  useEffect(() => {
    api.directive().then((d) => { setDirectiveVersion(d.version); setOnline(true); }).catch(() => setOnline(false));
    api.selfReport().then((r) => setLedgerOpen(r.counts.issues_open)).catch(() => {});
  }, []);

  // A live run that reaches the store opens as a record — read back at the viewer's
  // scope, with its gate — and History picks it up.
  const onStored = useCallback((run: Run) => {
    // Found live: a chat run was announced as "Comparison ready".
    const compare = "cross_agent_comparison" in run;
    setAnnouncement(run.halted
      ? `Run finished without ${compare ? "a comparison" : "an answer"}; showing its record`
      : `${compare ? "Comparison" : "Answer"} ready; showing its record`);
    go(`/runs/${run.run_id}`);
    setRefreshKey((k) => k + 1);
    live.reset();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const live = useLiveRun(onStored);
  const lastLive = live.state.announcements[live.state.announcements.length - 1];
  // A finished run is announced by onStored ("…showing its record"), not by the generic line.
  useEffect(() => {
    if (lastLive && live.state.status !== "done") setAnnouncement(lastLive);
  }, [lastLive, live.state.announcements.length, live.state.status]);

  const select = (id: string) => {
    go(`/runs/${id}`);
    setHistoryOpen(false);
  };
  const runId = route.view === "run" ? route.id : null;

  return (
    <div className="app">
      <div className="sr-only" aria-live="polite" aria-atomic="true" data-testid="announcer">{announcement}</div>
      <header className="topbar">
        <div className="topbar-left">
          <span className="logo" aria-hidden="true">⬡</span>
          <span className="topbar-title">Verification Layer</span>
          <span className="prototype-pill">Prototype</span>
        </div>
        <div className="topbar-right">
          <div className="scope-toggle" role="group" aria-label="View scope">
            {(["auditor", "investor"] as const).map((s) => (
              <button key={s} type="button" aria-pressed={scope === s}
                      className={`scope-btn ${scope === s ? "active" : ""}`} onClick={() => setScope(s)}>
                {s === "auditor" ? "Auditor" : "Investor"}
              </button>
            ))}
          </div>
          {live.running && route.view !== "new" && (
            <button type="button" className="chip chip-running" onClick={() => go("/new")}>
              <span className="spinner" aria-hidden="true" /> 1 running
            </button>
          )}
          {awaiting > 0 && (
            <button type="button" className="chip chip-attention"
                    onClick={() => { setRequestedTab({ tab: "decide", at: Date.now() }); setHistoryOpen(true); }}>
              <span aria-hidden="true">⚑</span> {awaiting} {awaiting === 1 ? "needs" : "need"} a decision
            </button>
          )}
          {online === false && <span className="badge badge-danger" role="status">Server unreachable</span>}
          <button type="button" className="btn" onClick={() => go("/new")}
                  aria-current={route.view === "new" ? "page" : undefined}>
            New compare
          </button>
          <nav className={`topnav ${menuOpen ? "open" : ""}`} aria-label="Pages" id="topnav">
            {([["chat", "Chat"], ["ledger", `Honest Ledger${ledgerOpen !== null ? ` (${ledgerOpen} open)` : ""}`],
               ["directive", `Directive${directiveVersion ? ` ${directiveVersion}` : ""}`], ["settings", "Settings"]] as const).map(([p, label]) => (
              <a key={p} href={`#/${p}`} className="btn-ghost" aria-current={route.view === p ? "page" : undefined}
                 onClick={() => setMenuOpen(false)}>{label}</a>
            ))}
          </nav>
          <button type="button" className="btn-ghost menu-toggle" aria-expanded={menuOpen} aria-controls="topnav"
                  onClick={() => setMenuOpen((o) => !o)}>Menu</button>
          <button type="button" className="btn-ghost history-toggle" aria-expanded={historyOpen}
                  aria-controls="history-drawer" onClick={() => setHistoryOpen((o) => !o)}>
            History
          </button>
        </div>
      </header>

      <div className="workspace">
        <main className="main" id="main">
          {route.view === "run" && (
            <RunDetail runId={route.id} scope={scope} onChanged={() => setRefreshKey((k) => k + 1)} />
          )}
          {route.view === "new" && <CompareView live={live} scope={scope} />}
          {route.view === "chat" && <ChatView live={live} scope={scope} />}
          {route.view === "settings" && <SettingsView scope={scope} />}
          {route.view === "directive" && <DirectiveView />}
          {route.view === "ledger" && <LedgerView onCount={setLedgerOpen} />}
          {route.view === "empty" && (
            <div className="empty-state">
              <h1>Pick a run to review</h1>
              <p className="muted">
                Open a run from History to see its answer, whether its sources check out, and everything the agents
                did — in that order. Or start a run.
              </p>
              <div className="gate-actions" style={{ justifyContent: "center" }}>
                <button type="button" className="btn" onClick={() => go("/new")}>New compare</button>
                <button type="button" className="btn-ghost" onClick={() => go("/chat")}>Ask one agent</button>
              </div>
            </div>
          )}
        </main>
        <aside id="history-drawer" className={`drawer ${historyOpen ? "open" : ""}`} aria-label="History">
          <HistoryPanel selectedId={runId} onSelect={select} scope={scope} refreshKey={refreshKey}
                        requestedTab={requestedTab} onNeedsDecision={setAwaiting} />
        </aside>
        {historyOpen && <div className="scrim" aria-hidden="true" onClick={() => setHistoryOpen(false)} />}
      </div>
    </div>
  );
}
