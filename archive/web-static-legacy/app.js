/* ── app.js — Accountability Layer Test Interface ──────────────────────────── */
'use strict';

(function () {

  // ── Markdown renderer ─────────────────────────────────────────────────────────
  if (typeof marked !== 'undefined') {
    marked.setOptions({ breaks: true, gfm: true });
  }
  function renderMd(text) {
    if (!text) return '';
    return typeof marked !== 'undefined'
      ? marked.parse(text)
      : esc(text).replace(/\n/g, '<br>');
  }

  // ── State ────────────────────────────────────────────────────────────────────
  let currentScope = 'auditor';
  let isRunning    = false;
  let activeTab    = 'runs';

  // SEC-02: one JWT per scope, cached in memory (8h TTL on server)
  const _tokens = { auditor: null, investor: null };

  // ── DOM refs ─────────────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);

  const statusDot      = $('statusDot');
  const statusLabel    = $('statusLabel');
  const btnDirective   = $('btnDirective');
  const btnClear       = $('btnClear');

  const scopeAuditor  = $('scopeAuditor');
  const scopeInvestor = $('scopeInvestor');
  const scopeHint     = $('scopeHint');

  // No Provider/Model DOM refs — LangChain is the one framework, configured
  // outside this UI (see index.html's Configuration panel comment). Seed and
  // Temperature stay as standalone verification-layer/audit controls.
  const cfgSeed        = $('cfgSeed');
  const cfgSeedLabel   = $('cfgSeedLabel');
  const cfgTemp        = $('cfgTemp');
  const cfgTempLabel   = $('cfgTempLabel');
  const cfgAgentId     = $('cfgAgentId');
  const cfgConf        = $('cfgConf');
  const cfgConfLabel   = $('cfgConfLabel');
  const confClassHint  = $('confClassHint');
  const cfgConsistencyProbe = $('cfgConsistencyProbe');
  const directiveBadge      = $('directiveBadge');

  const chatMessages = $('chatMessages');
  const btnToggleCtx = $('btnToggleContext');
  const contextInput = $('contextInput');
  const messageInput = $('messageInput');
  const btnSend      = $('btnSend');
  const sendLabel    = $('sendLabel');
  const sendSpinner  = $('sendSpinner');

  const tabRuns        = $('tabRuns');
  const tabSessions    = $('tabSessions');
  const runCountBadge  = $('runCount');
  const sessCountBadge = $('sessionCount');
  const auditRuns      = $('auditRuns');
  const auditSessions  = $('auditSessions');

  const directiveModal    = $('directiveModal');
  const mdlDirVersion     = $('modalDirectiveVersion');
  const mdlDirText        = $('modalDirectiveText');
  const btnCloseDirective = $('btnCloseDirective');

  const detailModal      = $('detailModal');
  const detailModalTitle = $('detailModalTitle');
  const detailModalId    = $('detailModalId');
  const detailModalJson  = $('detailModalJson');
  const btnCloseDetail   = $('btnCloseDetail');

  // Cross-Agent Validation
  const modeChat       = $('modeChat');
  const modeCompare    = $('modeCompare');
  const compareView    = $('compareView');
  const chatInputArea  = $('chatInputArea');
  const compareCaveat  = $('compareCaveat');
  const compareTicker  = $('compareTicker');
  const btnCompare     = $('btnCompare');
  const compareLabel   = $('compareLabel');
  const compareSpinner = $('compareSpinner');
  const compareResults = $('compareResults');
  const cmpModelA      = $('cmpModelA');
  const cmpModelB      = $('cmpModelB');
  const cmpModeTicker         = $('cmpModeTicker');
  const cmpModeSubject        = $('cmpModeSubject');
  const cmpTickerRow          = $('cmpTickerRow');
  const cmpSubjectRow         = $('cmpSubjectRow');
  const compareSubject        = $('compareSubject');
  const compareGenericContext = $('compareGenericContext');
  const btnCompareGeneric     = $('btnCompareGeneric');
  const compareGenericLabel   = $('compareGenericLabel');
  const compareGenericSpinner = $('compareGenericSpinner');

  const tabFlagged     = $('tabFlagged');
  const auditFlagged   = $('auditFlagged');
  const flaggedCount   = $('flaggedCount');

  const prototypePill  = $('prototypePill');
  const btnLedger      = $('btnLedger');
  const ledgerCount    = $('ledgerCount');
  const ledgerModal    = $('ledgerModal');
  const ledgerBody     = $('ledgerBody');
  const btnCloseLedger = $('btnCloseLedger');

  // ── Helpers ───────────────────────────────────────────────────────────────────

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function confLabel(score) {
    return score < 0.4 ? 'HIGH_UNCERTAINTY' : 'STANDARD';
  }

  function confHint(score) {
    return score < 0.4
      ? 'HIGH_UNCERTAINTY — score < 0.4 (speculative)'
      : 'STANDARD — score ≥ 0.4';
  }

  function srcClass(status) {
    return { simulated: 'simulated', cached: 'cached', live: 'live', failed: 'failed' }[status] || 'cached';
  }

  // ── SEC-02: JWT token management ──────────────────────────────────────────────

  /**
   * Ensure a valid JWT exists for the given scope.
   * Issues one from the server if not cached (tokens live 8 h).
   */
  async function ensureToken(scope) {
    if (_tokens[scope]) return _tokens[scope];
    const res  = await fetch('/api/auth/token', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ scope }),
    });
    if (!res.ok) throw new Error(`Failed to obtain ${scope} token: ${res.statusText}`);
    const { access_token } = await res.json();
    _tokens[scope] = access_token;
    return access_token;
  }

  async function authHeader(scope) {
    const token = await ensureToken(scope);
    return { Authorization: `Bearer ${token}` };
  }

  // ── Status dot ────────────────────────────────────────────────────────────────

  function setStatus(state) {
    statusDot.className = 'status-dot ' + state;
    statusLabel.textContent = { ok: 'Online', err: 'Error', connecting: 'Connecting…' }[state] || state;
  }

  // ── Config ────────────────────────────────────────────────────────────────────

  async function loadConfig() {
    const cfg = await fetch('/api/config').then(r => r.json());
    cfgTemp.value            = cfg.temperature    ?? 0.0;
    cfgTempLabel.textContent = cfg.temperature    ?? 0.0;
    if (cfgSeed) { cfgSeed.value = cfg.seed ?? 42; cfgSeedLabel.textContent = cfg.seed ?? 42; }
    cfgAgentId.value         = cfg.agent_id       || 'external';
    cfgConf.value            = cfg.confidence_score ?? 0.75;
    cfgConfLabel.textContent = cfg.confidence_score ?? 0.75;
    confClassHint.textContent = confHint(cfg.confidence_score ?? 0.75);
    cfgConsistencyProbe.checked = cfg.consistency_probe ?? false;
  }

  async function pushConfig(patch) {
    try {
      await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
    } catch (e) {
      console.warn('Config push failed:', e);
    }
  }

  // ── Directive ─────────────────────────────────────────────────────────────────

  async function loadDirective() {
    const d = await fetch('/api/directive').then(r => r.json());
    directiveBadge.textContent = d.version;
    mdlDirVersion.textContent  = d.version;
    mdlDirText.textContent     = d.text;
  }

  // ── Config event listeners ────────────────────────────────────────────────────

  if (cfgSeed) cfgSeed.addEventListener('input', () => {
    const v = parseInt(cfgSeed.value, 10);
    cfgSeedLabel.textContent = v;
    pushConfig({ seed: v });
  });
  cfgTemp.addEventListener('input', () => {
    const v = parseFloat(cfgTemp.value);
    cfgTempLabel.textContent = v;
    pushConfig({ temperature: v });
  });
  cfgAgentId.addEventListener('change', () => pushConfig({ agent_id: cfgAgentId.value }));
  cfgConf.addEventListener('input', () => {
    const v = parseFloat(cfgConf.value);
    cfgConfLabel.textContent  = v;
    confClassHint.textContent = confHint(v);
    pushConfig({ confidence_score: v });
  });
  cfgConsistencyProbe.addEventListener('change', () => {
    pushConfig({ consistency_probe: cfgConsistencyProbe.checked });
  });

  // ── Scope toggle ──────────────────────────────────────────────────────────────

  function setScope(scope) {
    currentScope = scope;
    scopeAuditor.classList.toggle('active',  scope === 'auditor');
    scopeInvestor.classList.toggle('active', scope === 'investor');
    scopeHint.textContent = scope === 'auditor'
      ? 'Full record — thought_log visible.'
      : 'Investor view — thought_log structurally excluded (SEC-01).';
    // Pre-warm token for the new scope
    ensureToken(scope).catch(() => {});
  }

  scopeAuditor.addEventListener('click',  () => setScope('auditor'));
  scopeInvestor.addEventListener('click', () => setScope('investor'));

  // ── Context drawer ────────────────────────────────────────────────────────────

  btnToggleCtx.addEventListener('click', () => {
    const hidden = contextInput.classList.toggle('hidden');
    btnToggleCtx.textContent = hidden ? '+ Add context' : '− Hide context';
  });

  // ── Chat rendering ────────────────────────────────────────────────────────────

  function clearEmpty(container) {
    const el = container.querySelector('.chat-empty');
    if (el) el.remove();
  }

  function scrollChat() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function showThinking() {
    const el = document.createElement('div');
    el.id = 'thinkingIndicator';
    el.className = 'msg-thinking';
    el.innerHTML = '<div class="msg-thinking-dot"></div>'
                 + '<div class="msg-thinking-dot"></div>'
                 + '<div class="msg-thinking-dot"></div>';
    chatMessages.appendChild(el);
    scrollChat();
  }

  function hideThinking() {
    const el = document.getElementById('thinkingIndicator');
    if (el) el.remove();
  }

  function appendUserBubble(text) {
    clearEmpty(chatMessages);
    const div = document.createElement('div');
    div.className   = 'msg-user';
    div.textContent = text;
    chatMessages.appendChild(div);
    scrollChat();
  }

  function sourcesHtml(sources) {
    if (!sources || sources.length === 0) return '';
    const rows = sources.map(s => {
      const cls  = srcClass(s.status);
      const note = s.provenance_note
        ? `<span class="msg-source-note">${esc(s.provenance_note)}</span>` : '';
      return `<div class="msg-source-row">
        <span class="source-status ${cls}">${esc(s.status)}</span>
        <span class="msg-source-name">${esc(s.source)}</span>${note}
      </div>`;
    }).join('');
    return `<div class="msg-sources">
      <div class="msg-sources-title">${rolePill('verifier')}Data Sources</div>
      ${rows}
    </div>`;
  }

  // ── Role pills — who authored a given piece of the record: the human user,
  // the agent under test, or the verification layer's own computed judgment.
  // Exists because a run mixes all three inline (a user's subject line next to
  // an agent's thought_log next to a confidence score the harness computed) and
  // that was reported as genuinely hard to tell apart at a glance.
  const ROLE_LABEL = { user: 'User', agent: 'Agent', verifier: 'Verification Layer' };
  function rolePill(role) {
    return `<span class="role-pill role-${role}">${ROLE_LABEL[role] || role}</span>`;
  }

  // A step's phase tells us WHO did it: PHASE_COMPARE is the verification
  // layer's own work (the one shared EDGAR fetch, the comparator); every other
  // phase's "LLM attempt" and "Tool call" steps are the agent under test acting
  // (the tool call is the agent's own choice to search, not the harness's).
  function stepRole(step) {
    if (step.phase === 'compare') return 'verifier';
    return 'agent';
  }

  // ── Chronological step trace — every LLM attempt and tool call, in order ──
  // Reuses the same .spine-* classes the compare view's "shared work" band
  // already defines, so this reads as one visual language rather than a
  // second one-off timeline component.
  function stepsHtml(steps) {
    if (!steps || !steps.length) return '';
    const rows = steps.map(s => `
      <div class="spine-step">
        <span class="spine-seq">${s.seq}</span>
        <span class="spine-label">${rolePill(stepRole(s))}${esc(s.label)}</span>
        <span class="${STEP_STATUS_BADGE[s.status] || 'badge-neutral'}">${esc(s.status)}</span>
        <span class="spine-dur">${fmtMs(s.duration_ms)}</span>
        <div class="spine-detail">
          ${s.url ? `<span class="spine-url">${esc(s.url)}</span><br>` : ''}
          ${s.detail ? esc(s.detail) : ''}
          ${s.error ? `<span class="detail-error-inline">${esc(s.error)}</span>` : ''}
        </div>
      </div>`).join('');
    return `<details class="msg-context-window">
      <summary>Run Timeline <span class="badge-neutral">${steps.length} step${steps.length > 1 ? 's' : ''}</span></summary>
      <div class="ctxwin-body">${rows}</div>
    </details>`;
  }

  // ── Context window — what was actually sent to the agent, per attempt ────────
  // directive_text/context_window are auditor-only (SEC-01, same tier as
  // thought_log): 'directive_text' in ro is how we tell "withheld" (investor
  // scope) apart from "genuinely absent" — the key itself is omitted, not nulled.
  function contextWindowHtml(reasoningObjects) {
    if (!reasoningObjects || !reasoningObjects.length) return '';
    const withheld = !reasoningObjects.some(ro => 'directive_text' in ro);
    if (withheld) {
      return `<div class="msg-scope-notice">context window excluded — Investor scope (SEC-01)</div>`;
    }
    const attempts = reasoningObjects.map(ro => {
      const retried = ro.attempt_number > 1
        ? ` <span class="badge-neutral">directive: ${esc(ro.directive_version || '—')}</span>` : '';
      return `<div class="ctxwin-attempt">
        <div class="ctxwin-attempt-header">Attempt ${ro.attempt_number}${retried}</div>
        <div class="ctxwin-block">
          <div class="ctxwin-label">${rolePill('verifier')}System prompt (directive ${esc(ro.directive_version || '—')})</div>
          <pre class="ctxwin-text">${esc(ro.directive_text || '')}</pre>
        </div>
        <div class="ctxwin-block">
          <div class="ctxwin-label">${rolePill('user')}User prompt</div>
          <pre class="ctxwin-text">Subject: ${esc((ro.context_window || {}).subject || '')}

Context:
${esc((ro.context_window || {}).context || '(none provided)')}</pre>
        </div>
      </div>`;
    }).join('');
    return `<details class="msg-context-window">
      <summary>Context Window <span class="badge-neutral">${reasoningObjects.length} attempt${reasoningObjects.length > 1 ? 's' : ''}</span></summary>
      <div class="ctxwin-body">${attempts}</div>
    </details>`;
  }

  function confLineHtml(data) {
    const score    = data.confidence_score;
    const label    = data.confidence_classification || confLabel(score || 0);
    const badgeCls = data.high_uncertainty ? 'badge badge-uncertainty' : 'badge-neutral';
    const degraded = data.confidence_degraded
      ? ` <span class="badge-degraded" title="ADR-04: degraded by simulated/failed data sources">↓ degraded</span>`
      : '';
    return `<div class="msg-confidence">
      ${rolePill('verifier')}Confidence <strong>${esc(String(score))}</strong>
      <span class="${badgeCls}">${esc(label)}</span>${degraded}
    </div>`;
  }

  // ── Consistency badge ─────────────────────────────────────────────────────────

  function consistencyHtml(c) {
    if (!c) return '';
    if (c.agreement === 'UNKNOWN') {
      return `<div class="msg-consistency">
        ${rolePill('verifier')}<span class="badge badge-neutral">Consistency</span>
        <span class="badge-neutral">UNKNOWN</span>
        ${c.probe_error ? `<span class="consistency-note">${esc(c.probe_error)}</span>` : ''}
      </div>`;
    }
    const cls = { HIGH: 'badge-success', MEDIUM: 'badge-warning', LOW: 'badge-halt' }[c.agreement] || 'badge-neutral';
    const diverged = c.divergent_numbers && c.divergent_numbers.length
      ? ` <span class="consistency-note">divergent: ${c.divergent_numbers.map(esc).join(', ')}</span>` : '';
    return `<div class="msg-consistency">
      ${rolePill('verifier')}<span class="badge-neutral">Consistency</span>
      <span class="badge ${cls}">${esc(c.agreement)}</span>
      <span class="consistency-score">${esc(String(c.score))}</span>${diverged}
    </div>`;
  }

  // ── Claims summary ────────────────────────────────────────────────────────────

  const CLAIM_ICONS = { citation: '🔗', quantitative: '📊', hedge: '⚠', causal: '→' };
  const CLAIM_CLS   = { citation: 'claim-citation', quantitative: 'claim-quant', hedge: 'claim-hedge', causal: 'claim-causal' };

  // "X% verified" reads the same whether 0 of 5 citations failed verification or
  // there were 0 citations to begin with — verify_claims() only ever runs on
  // claim_type === 'citation' claims found via the [SOURCE: label, url] bracket
  // format (validation/claims.py), so a response that only ever writes its
  // sourcing as prose extracts zero citation claims and is never fetched or
  // checked at all. Distinguishing that from "checked, found unverified" here
  // rather than folding both into one ambiguous percentage.
  function verificationRateLabel(claims, verificationRate) {
    const citations = (claims || []).filter(c => c.claim_type === 'citation');
    if (citations.length === 0) {
      return ` <span class="badge-neutral" title="No [SOURCE: label, url] citation was extracted from this response — nothing was fetched or checked">no citations extracted</span>`;
    }
    const verifiedCount = citations.filter(c => c.verified === true).length;
    const pct = verificationRate != null ? Math.round(verificationRate * 100) : 0;
    return ` <span class="badge-neutral" title="Citation verification rate">${verifiedCount}/${citations.length} citations verified (${pct}%)</span>`;
  }

  function verifiedBadge(v) {
    if (v === true)  return '<span class="badge badge-success" title="Source confirmed">✓</span>';
    if (v === false) return '<span class="badge badge-halt"    title="Source checked, not found">✗</span>';
    return '';
  }

  // ── Verification banner — the direct answer to "was the agent's claim
  // actually checked?" ────────────────────────────────────────────────────────
  // Sits immediately under the Conclusion (not buried inside the collapsed
  // Claims accordion below it), because that's the one thing a reader wants
  // to know before trusting the conclusion at all: was this citation actually
  // fetched and confirmed, checked and NOT found, or never checked in the
  // first place? verified is a three-way value (validation/verification.py):
  // true = source fetched and a claimed number matched it; false = source
  // fetched, nothing matched; null = never resolved either way (no network,
  // non-200, unparseable — NOT the same as "checked and failed").
  function verificationBannerHtml(claims) {
    const citations = (claims || []).filter(c => c.claim_type === 'citation');
    if (citations.length === 0) {
      return `<div class="msg-verify-banner verify-banner-none">
        ${rolePill('verifier')}<span class="badge-warning">⚠ NOT VERIFIED</span>
        <span class="verify-note">No [SOURCE: label, url] citation was extracted from this
          response, so nothing was fetched or checked against the agent's claim.</span>
      </div>`;
    }
    const rows = citations.map(c => {
      let verdict, cls, note;
      if (c.verified === true) {
        verdict = '✓ VERIFIED'; cls = 'badge-success';
        note = 'Source was fetched and a number from the claim matched it.';
      } else if (c.verified === false) {
        verdict = '✗ NOT FOUND'; cls = 'badge-halt';
        note = 'Source was fetched, but no matching number was found in it.';
      } else {
        verdict = '⚠ UNCHECKED'; cls = 'badge-warning';
        note = 'Source could not be resolved (no URL, fetch failed, or unparseable) — never actually checked either way.';
      }
      const urlHtml = c.source_url && c.source_url !== 'N/A'
        ? `<a class="claim-url" href="${esc(c.source_url)}" target="_blank" rel="noopener">${esc(c.source_url.slice(0, 60))}${c.source_url.length > 60 ? '…' : ''}</a>`
        : '<span class="detail-note">no URL given</span>';
      return `<div class="msg-verify-row">
        <span class="badge ${cls}" title="${esc(note)}">${verdict}</span>
        ${urlHtml}
      </div>`;
    }).join('');
    return `<div class="msg-verify-banner">
      ${rolePill('verifier')}<span class="verify-banner-title">Source Verification</span>
      ${rows}
    </div>`;
  }

  function claimsHtml(claims, verificationRate) {
    if (!claims || claims.length === 0) return '';
    const pills = claims.map(c => {
      const icon = CLAIM_ICONS[c.claim_type] || '·';
      const cls  = CLAIM_CLS[c.claim_type]   || '';
      const tip  = esc(c.context || c.text);
      const vb   = (c.claim_type === 'citation') ? verifiedBadge(c.verified) : '';
      return `<span class="claim-pill ${cls}" title="${tip}">${icon} ${esc(c.text.slice(0, 40))}${c.text.length > 40 ? '…' : ''}${vb}</span>`;
    }).join('');
    const vRateHtml = verificationRateLabel(claims, verificationRate);
    return `<details class="msg-claims">
      <summary>${rolePill('verifier')}Claims <span class="badge-neutral">${claims.length}</span>${vRateHtml} <span class="claims-hint">ADR-06</span></summary>
      <div class="claims-pills">${pills}</div>
    </details>`;
  }

  function appendAgentBubble(data) {
    clearEmpty(chatMessages);
    const outer = document.createElement('div');
    outer.className = 'msg-agent';

    let header = '';
    let body   = '';

    if (data.high_uncertainty) {
      const banner = document.createElement('div');
      banner.className = 'msg-uncertainty-banner';
      banner.innerHTML = `⚠ HIGH_UNCERTAINTY — confidence ${esc(String(data.confidence_score))} &lt; 0.4. `
        + `Conclusions are speculative and require human review before acting.`;
      chatMessages.appendChild(banner);
    }

    if (data.tool_capability_warning) {
      const banner = document.createElement('div');
      banner.className = 'msg-uncertainty-banner';
      banner.innerHTML = `🔧 ${esc(data.tool_capability_warning)}`;
      chatMessages.appendChild(banner);
    }

    if (data.halted) {
      header = `<span class="badge badge-halt">⛔ HALTED</span>
                <span class="badge-neutral">ADR-07</span>`;
    } else {
      const attempts = (data.reasoning_objects || []).length;
      const retried  = attempts > 1;
      header = retried
        ? `<span class="badge badge-warning">⚠ Retried</span><span class="badge-neutral">Attempt ${attempts}</span>`
        : `<span class="badge badge-success">✓ OK</span>`;
      if (data.scope) header += ` <span class="badge-neutral">${esc(data.scope)}</span>`;
    }

    if (data.halted) {
      body = `<div class="msg-halt-title">Pipeline Halted</div>
              <div class="msg-halt-body">${esc(data.error || 'Double structural parse failure.')}</div>`;
    } else {
      if (data.conclusion) {
        body += `<div class="msg-conclusion-label">${rolePill('agent')}Conclusion</div>
                  <div class="msg-conclusion md">${renderMd(data.conclusion)}</div>`;
        body += verificationBannerHtml(data.claims);
      }
      if (data.thought_log) {
        body += `<details class="msg-thought-log">
          <summary>${rolePill('agent')}Thought Log <span class="badge-neutral">ADR-06 — unverified</span></summary>
          <div class="msg-thought-body md">${renderMd(data.thought_log)}</div>
        </details>`;
      } else if (currentScope === 'investor' && !data.halted) {
        body += `<div class="msg-scope-notice">thought_log excluded — Investor scope (SEC-01)</div>`;
      }
    }

    body += contextWindowHtml(data.reasoning_objects);
    body += stepsHtml(data.steps);

    body += confLineHtml(data);
    body += consistencyHtml(data.consistency);
    body += claimsHtml(data.claims, data.verification_rate);
    body += sourcesHtml(data.data_sources);

    if (data.run_id) {
      body += `<div class="msg-meta">
        Run <a class="run-link" data-id="${esc(data.run_id)}" href="#">${esc(data.run_id.slice(0, 8))}…</a>
      </div>`;
    }

    outer.innerHTML = `
      <div class="msg-agent-header">${header}</div>
      <div class="msg-agent-body">${body}</div>`;

    outer.querySelectorAll('.run-link').forEach(a => {
      a.addEventListener('click', e => { e.preventDefault(); openRunDetail(a.dataset.id); });
    });

    chatMessages.appendChild(outer);
    scrollChat();
  }

  function appendNetworkError(msg) {
    clearEmpty(chatMessages);
    const div = document.createElement('div');
    div.className = 'msg-agent';
    div.innerHTML = `
      <div class="msg-agent-header"><span class="badge badge-danger">Error</span></div>
      <div class="msg-agent-body">
        <div class="msg-halt-body">${esc(msg)}</div>
      </div>`;
    chatMessages.appendChild(div);
    scrollChat();
  }

  // ── Send (SEC-02: Bearer token instead of ?scope= query param) ────────────────

  async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text || isRunning) return;

    isRunning = true;
    btnSend.disabled = true;
    sendLabel.textContent = 'Running…';
    sendSpinner.classList.remove('hidden');

    appendUserBubble(text);
    messageInput.value = '';
    showThinking();

    try {
      const headers = {
        'Content-Type': 'application/json',
        ...(await authHeader(currentScope)),
      };

      const res = await fetch('/api/chat', {
        method:  'POST',
        headers,
        body:    JSON.stringify({ message: text, context: contextInput.value.trim() }),
      });

      hideThinking();
      if (res.status === 401) {
        // Token expired — clear cache and retry once
        _tokens[currentScope] = null;
        const retryHeaders = {
          'Content-Type': 'application/json',
          ...(await authHeader(currentScope)),
        };
        const retry = await fetch('/api/chat', {
          method:  'POST',
          headers: retryHeaders,
          body:    JSON.stringify({ message: text, context: contextInput.value.trim() }),
        });
        if (!retry.ok) {
          const err = await retry.json().catch(() => ({ detail: retry.statusText }));
          appendNetworkError(err.detail || `HTTP ${retry.status}`);
        } else {
          appendAgentBubble(await retry.json());
          refreshAuditBoth();
        }
      } else if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        appendNetworkError(err.detail || `HTTP ${res.status}`);
      } else {
        appendAgentBubble(await res.json());
        refreshAuditBoth();
      }
    } catch (err) {
      hideThinking();
      appendNetworkError('Network error: ' + err.message);
    } finally {
      isRunning = false;
      btnSend.disabled = false;
      sendLabel.textContent = 'Send';
      sendSpinner.classList.add('hidden');
    }
  }

  btnSend.addEventListener('click', sendMessage);
  messageInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });

  // ── Audit panel ───────────────────────────────────────────────────────────────

  function switchTab(tab) {
    activeTab = tab;
    tabRuns.classList.toggle('active',       tab === 'runs');
    tabSessions.classList.toggle('active',   tab === 'sessions');
    tabFlagged.classList.toggle('active',    tab === 'flagged');
    auditRuns.classList.toggle('hidden',     tab !== 'runs');
    auditSessions.classList.toggle('hidden', tab !== 'sessions');
    auditFlagged.classList.toggle('hidden',  tab !== 'flagged');
    if (tab === 'sessions')      refreshSessions();
    else if (tab === 'flagged')  refreshFlagged();
    else                         refreshRuns();
  }

  tabRuns.addEventListener('click',     () => switchTab('runs'));
  tabSessions.addEventListener('click', () => switchTab('sessions'));
  tabFlagged.addEventListener('click',  () => switchTab('flagged'));

  function sourcePills(sources) {
    if (!sources || sources.length === 0) return '';
    return sources.map(s =>
      `<span class="audit-source-pill pill-${srcClass(s.status)}">${esc(s.source)}</span>`
    ).join('');
  }

  function renderRunCard(run) {
    const card = document.createElement('div');
    card.className = 'audit-card' + (run.halted ? ' audit-card-halted' : '');

    const label    = run.confidence_classification || confLabel(run.confidence_score || 0);
    const badgeCls = run.high_uncertainty ? 'badge badge-uncertainty' : 'badge-neutral';
    const degraded = run.confidence_degraded
      ? `<span class="badge-neutral" title="ADR-04 degraded">↓</span> ` : '';
    const statusBadge = run.halted
      ? `<span class="badge badge-halt">HALTED</span>`
      : `<span class="badge badge-success">DONE</span>`;

    card.innerHTML = `
      <div class="audit-card-header">
        ${statusBadge}
        <span class="audit-run-id">${esc((run.run_id || '').slice(0, 8))}…</span>
      </div>
      <div class="audit-subject" title="${esc(run.subject || '')}">${esc(run.subject || '—')}</div>
      <div class="audit-meta">
        ${degraded}<span class="${badgeCls}">${esc(label)}</span>
        <span class="badge-neutral">${esc(run.scope || 'auditor')}</span>
        ${sourcePills(run.data_sources)}
      </div>`;

    card.addEventListener('click', () => openRunDetail(run.run_id));
    return card;
  }

  function renderSessionCard(session) {
    const card = document.createElement('div');
    card.className = 'audit-card' + (session.status === 'HALTED' ? ' audit-card-halted' : '');

    const ver = session.directive_version || '—';
    const cls = session.confidence_classification;
    const statusBadge = session.status === 'HALTED'
      ? `<span class="badge badge-halt">HALTED</span>`
      : `<span class="badge badge-success">DONE</span>`;

    card.innerHTML = `
      <div class="audit-card-header">
        ${statusBadge}
        <span class="audit-run-id">${esc(session.ticker || 'RUN')}</span>
      </div>
      <div class="audit-subject">${esc((session.run_id || '').slice(0, 8))}…</div>
      <div class="audit-meta">
        <span class="badge-neutral">directive ${esc(ver)}</span>
        ${cls ? `<span class="badge-neutral">${esc(cls)}</span>` : ''}
      </div>`;

    card.addEventListener('click', () => openSessionDetail(session.run_id));
    return card;
  }

  async function refreshRuns() {
    try {
      const runs = await fetch('/api/runs').then(r => r.json());
      runCountBadge.textContent = runs.length;
      auditRuns.innerHTML = '';
      if (runs.length === 0) {
        auditRuns.innerHTML = '<div class="audit-empty">No runs yet.</div>';
      } else {
        runs.forEach(r => auditRuns.appendChild(renderRunCard(r)));
      }
    } catch { /* silent */ }
  }

  async function refreshSessions() {
    try {
      const sessions = await fetch('/api/sessions').then(r => r.json());
      sessCountBadge.textContent = sessions.length;
      auditSessions.innerHTML = '';
      if (sessions.length === 0) {
        auditSessions.innerHTML = '<div class="audit-empty">No sessions yet.</div>';
      } else {
        sessions.forEach(s => auditSessions.appendChild(renderSessionCard(s)));
      }
    } catch { /* silent */ }
  }

  function refreshAuditBoth() {
    fetch('/api/runs').then(r => r.json()).then(d => {
      runCountBadge.textContent = d.length;
      if (activeTab === 'runs') {
        auditRuns.innerHTML = '';
        if (d.length === 0) {
          auditRuns.innerHTML = '<div class="audit-empty">No runs yet.</div>';
        } else {
          d.forEach(r => auditRuns.appendChild(renderRunCard(r)));
        }
      }
    }).catch(() => {});

    fetch('/api/sessions').then(r => r.json()).then(d => {
      sessCountBadge.textContent = d.length;
      if (activeTab === 'sessions') {
        auditSessions.innerHTML = '';
        if (d.length === 0) {
          auditSessions.innerHTML = '<div class="audit-empty">No sessions yet.</div>';
        } else {
          d.forEach(s => auditSessions.appendChild(renderSessionCard(s)));
        }
      }
    }).catch(() => {});
  }

  // ── Detail modal rendering ────────────────────────────────────────────────────

  function detailSection(title, contentHtml, titleSuffixHtml = '') {
    // titleSuffixHtml is raw markup we constructed ourselves (e.g. a badge) — appended
    // after the escaped title rather than folded into `title` itself, which would get
    // HTML-escaped along with it and render as literal tag text instead of a badge.
    return `<div class="detail-section">
      <div class="detail-section-title">${esc(title)}${titleSuffixHtml}</div>
      <div class="detail-section-body">${contentHtml}</div>
    </div>`;
  }

  function detailRow(label, valueHtml, role) {
    const pill = role ? rolePill(role) : '';
    return `<div class="detail-row">
      <span class="detail-label">${pill}${esc(label)}</span>
      <span class="detail-value">${valueHtml}</span>
    </div>`;
  }

  function renderRunDetailHtml(run, flags) {
    const statusBadge = run.halted
      ? `<span class="badge badge-halt">HALTED</span>`
      : `<span class="badge badge-success">COMPLETE</span>`;
    const cl       = run.confidence_classification || confLabel(run.confidence_score || 0);
    const confCls  = run.high_uncertainty ? 'badge badge-uncertainty' : 'badge-neutral';
    const degraded = run.confidence_degraded
      ? ` <span class="badge-degraded">↓ degraded</span>` : '';

    let html = '';

    // ── Meta ──
    html += detailSection('Run', [
      detailRow('ID',      `<code>${esc(run.run_id)}</code>`, 'verifier'),
      detailRow('Subject', esc(run.subject || '—'), 'user'),
      detailRow('Status',  statusBadge, 'verifier'),
      detailRow('Scope',  `<span class="badge-neutral">${esc(run.scope || '—')}</span>`, 'verifier'),
      detailRow('Agent',  `<span class="badge-neutral">${esc(run.config_snapshot?.agent_id || '—')}</span>`, 'verifier'),
      detailRow('Model',  `<span class="badge-neutral">${esc(run.config_snapshot?.model || '—')}</span>`, 'verifier'),
      detailRow('Confidence',
        `<span class="${confCls}">${esc(String(run.confidence_score))}</span> ` +
        `<span class="${confCls}">${esc(cl)}</span>${degraded}`, 'verifier'),
    ].join(''));

    // ── Error ──
    if (run.error) {
      html += detailSection('Error', `<div class="detail-error">${esc(run.error)}</div>`, ' ' + rolePill('verifier'));
    }

    // ── Conclusion ──
    if (run.conclusion) {
      html += detailSection('Conclusion',
        `<div class="detail-md md">${renderMd(run.conclusion)}</div>${verificationBannerHtml(run.claims)}`,
        ' ' + rolePill('agent'));
    }

    // ── Thought log ──
    if (run.thought_log) {
      html += detailSection('Thought Log',
        `<div class="detail-adrnote badge-neutral" style="margin-bottom:8px">ADR-06 — structure ≠ truth</div>
         <div class="detail-md md">${renderMd(run.thought_log)}</div>`, ' ' + rolePill('agent'));
    } else if (run.scope === 'investor') {
      html += detailSection('Thought Log',
        `<div class="detail-adrnote">Excluded — Investor scope (SEC-01)</div>`, ' ' + rolePill('agent'));
    }

    // ── Context window — the actual directive + subject/context sent per attempt ──
    // contextWindowHtml() renders its own titled <details> block (same one the chat
    // view uses), so it is inserted directly rather than wrapped in another
    // detailSection() title, which would duplicate the heading.
    html += contextWindowHtml(run.reasoning_objects || []);
    html += stepsHtml(run.steps);

    // ── Data sources ──
    if (run.data_sources && run.data_sources.length) {
      const rows = run.data_sources.map(s =>
        detailRow(s.source,
          `<span class="source-status ${srcClass(s.status)}">${esc(s.status)}</span>` +
          (s.provenance_note ? ` <span class="detail-note">${esc(s.provenance_note)}</span>` : ''))
      ).join('');
      html += detailSection('Data Sources', rows, ' ' + rolePill('verifier'));
    }

    // ── Reasoning objects ──
    if (run.reasoning_objects && run.reasoning_objects.length) {
      const objs = run.reasoning_objects.map(ro => {
        const statusCls = ro.parse_status === 'SUCCESS' ? 'badge-success'
                        : ro.parse_status === 'HALT'    ? 'badge-halt'
                        : 'badge-warning';
        return `<div class="detail-attempt">
          <div class="detail-attempt-header">
            ${rolePill('agent')}<span class="badge ${statusCls}">Attempt ${ro.attempt_number}</span>
            <span class="badge-neutral">${esc(ro.parse_status)}</span>
            <code class="detail-ro-id">${esc((ro.reasoning_id || '').slice(0, 8))}…</code>
          </div>
          ${ro.raw_output?.text ? `
            <details class="detail-raw">
              <summary>Raw output</summary>
              <pre>${esc(ro.raw_output.text)}</pre>
            </details>` : ''}
        </div>`;
      }).join('');
      html += detailSection('Reasoning Objects', objs);
    }

    // ── Directive ──
    if (run.session?.directive_version) {
      html += detailSection('Directive',
        detailRow('Version', `<span class="badge-neutral">${esc(run.session.directive_version)}</span>`, 'verifier')
      );
    }

    // ── Consistency probe (ADR-06) ──
    if (run.consistency) {
      const c   = run.consistency;
      const cls = { HIGH: 'badge-success', MEDIUM: 'badge-warning', LOW: 'badge-halt' }[c.agreement] || 'badge-neutral';
      const flagRow = c.number_divergence_flag
        ? detailRow('Number divergence', `<span class="badge badge-halt">FLAGGED — value appears in one run only</span>`)
        : detailRow('Number divergence', `<span class="badge-neutral">none</span>`);
      let cHtml = [
        detailRow('Agreement', `<span class="badge ${cls}">${esc(c.agreement)}</span>`),
        detailRow('Score',     `<span class="badge-neutral">${esc(String(c.score))}</span>`),
        flagRow,
        detailRow('Word overlap',   `<span class="badge-neutral">${esc(String(c.word_overlap))}</span>`),
        detailRow('Number overlap', `<span class="badge-neutral">${esc(String(c.number_overlap))}</span>`),
      ].join('');
      if (c.divergent_numbers && c.divergent_numbers.length) {
        cHtml += detailRow('Divergent values', `<span class="detail-error-inline">${c.divergent_numbers.map(esc).join(', ')}</span>`);
      }
      if (c.probe_conclusion) {
        cHtml += `<div class="detail-probe-conclusion">
          <div class="detail-label" style="margin-bottom:4px">Probe conclusion</div>
          <div class="detail-md md">${renderMd(c.probe_conclusion)}</div>
        </div>`;
      }
      if (c.probe_error) {
        cHtml += detailRow('Error', `<span class="detail-error-inline">${esc(c.probe_error)}</span>`);
      }
      html += detailSection('Consistency Probe — ADR-06', cHtml, ' ' + rolePill('verifier'));
    }

    // ── Claims (ADR-06) ──
    if (run.claims && run.claims.length) {
      const vRateLabel = ` —${verificationRateLabel(run.claims, run.verification_rate)}`;
      const claimRows = run.claims.map(c => {
        const icon = { citation: '🔗', quantitative: '📊', hedge: '⚠', causal: '→' }[c.claim_type] || '·';
        const cls  = { citation: 'claim-citation', quantitative: 'claim-quant', hedge: 'claim-hedge', causal: 'claim-causal' }[c.claim_type] || '';
        let vBadge = '';
        if (c.claim_type === 'citation') {
          if (c.verified === true)       vBadge = `<span class="badge badge-success">verified</span>`;
          else if (c.verified === false) vBadge = `<span class="badge badge-halt">not found</span>`;
          else                           vBadge = `<span class="badge-neutral">unattainable</span>`;
        }
        const urlLink = c.source_url && c.source_url !== 'N/A'
          ? ` <a class="claim-url" href="${esc(c.source_url)}" target="_blank" rel="noopener">${esc(c.source_url.slice(0, 50))}…</a>` : '';
        return `<div class="detail-claim">
          <span class="claim-pill ${cls}">${icon} ${esc(c.claim_type)}</span>
          <span class="detail-claim-text">${esc(c.text)}</span>
          ${vBadge}${urlLink}
          <div class="detail-claim-context">${esc(c.context)}</div>
        </div>`;
      }).join('');
      html += detailSection(`Claims — ${run.claims.length} extracted`, claimRows, ' ' + rolePill('verifier') + vRateLabel);
    }

    // ── Reviewer Flags (UN-05) ──
    const existingFlags = (flags || []).map(f =>
      `<div class="detail-flag">
        <span class="badge badge-warning">${esc(f.flag_type)}</span>
        <span class="detail-flag-ts">${esc(f.flagged_at)}</span>
        ${f.reviewer_note ? `<div class="detail-flag-note">${esc(f.reviewer_note)}</div>` : ''}
      </div>`
    ).join('') || '<div class="detail-note">No flags.</div>';

    // Flag form — only shown for auditor scope
    const flagForm = currentScope === 'auditor'
      ? `<form class="flag-form" id="flagForm" data-run-id="${esc(run.run_id)}">
          <select class="field-select flag-type-select" name="flag_type">
            <option value="Hallucinated">Hallucinated</option>
            <option value="Incorrect">Incorrect</option>
            <option value="Other">Other</option>
          </select>
          <textarea class="flag-note-input" name="reviewer_note"
                    placeholder="Optional reviewer note…" rows="2"></textarea>
          <button type="submit" class="btn-ghost small">Add Flag</button>
          <span class="flag-form-msg" id="flagFormMsg"></span>
        </form>`
      : `<div class="detail-note">Switch to Auditor scope to add flags.</div>`;

    html += detailSection('Reviewer Flags',
      existingFlags + '<div class="detail-flag-divider"></div>' + flagForm
    );

    return html;
  }

  function renderSessionDetailHtml(s) {
    const statusBadge = s.status === 'HALTED'
      ? `<span class="badge badge-halt">HALTED</span>`
      : `<span class="badge badge-success">COMPLETE</span>`;

    let html = '';
    html += detailSection('Session', [
      detailRow('Run ID',    `<code>${esc(s.run_id)}</code>`),
      detailRow('Ticker',    `<span class="badge-neutral">${esc(s.ticker || '—')}</span>`),
      detailRow('Status',    statusBadge),
      detailRow('Directive', `<span class="badge-neutral">${esc(s.directive_version || '—')}</span>`),
      detailRow('Confidence', s.run_confidence_score != null
        ? `<span class="badge-neutral">${esc(String(s.run_confidence_score))}</span> <span class="badge-neutral">${esc(s.confidence_classification || '—')}</span>`
        : '—'),
      detailRow('Initiated',  `<span class="detail-note">${esc(s.initiated_at || '—')}</span>`),
      detailRow('Completed',  `<span class="detail-note">${esc(s.completed_at || '—')}</span>`),
    ].join(''));

    if (s.directive_text) {
      html += detailSection('Directive Text',
        `<pre class="detail-directive">${esc(s.directive_text)}</pre>`);
    }

    return html;
  }

  // ── Detail modals ─────────────────────────────────────────────────────────────

  function showDetailModal(title, idText, bodyHtml) {
    detailModalTitle.textContent = title;
    detailModalId.textContent    = idText;
    detailModalJson.className    = 'modal-body detail-body';
    detailModalJson.innerHTML    = bodyHtml;
    detailModal.classList.remove('hidden');

    // Attach flag form handler after DOM is ready
    const form = detailModalJson.querySelector('#flagForm');
    if (form) {
      form.addEventListener('submit', async e => {
        e.preventDefault();
        const fd        = new FormData(form);
        const run_id    = form.dataset.runId;
        const flag_type = fd.get('flag_type');
        const note      = fd.get('reviewer_note') || null;
        const msgEl     = document.getElementById('flagFormMsg');

        try {
          const headers = {
            'Content-Type': 'application/json',
            ...(await authHeader('auditor')),
          };
          const res = await fetch(`/api/runs/${run_id}/flags`, {
            method:  'POST',
            headers,
            body:    JSON.stringify({ flag_type, reviewer_note: note }),
          });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            msgEl.textContent = '✗ ' + (err.detail || `HTTP ${res.status}`);
            msgEl.className   = 'flag-form-msg flag-form-err';
          } else {
            msgEl.textContent = '✓ Flag added';
            msgEl.className   = 'flag-form-msg flag-form-ok';
            form.querySelector('textarea').value = '';
            // Refresh the flags section without closing the modal
            const flags = await fetch(`/api/runs/${run_id}/flags`).then(r => r.json());
            const flagsContainer = form.closest('.detail-section-body');
            const existing = flagsContainer.querySelector('.detail-flag, .detail-note');
            // Re-render existing flags list
            const newFlagsHtml = flags.map(f =>
              `<div class="detail-flag">
                <span class="badge badge-warning">${esc(f.flag_type)}</span>
                <span class="detail-flag-ts">${esc(f.flagged_at)}</span>
                ${f.reviewer_note ? `<div class="detail-flag-note">${esc(f.reviewer_note)}</div>` : ''}
              </div>`
            ).join('') || '<div class="detail-note">No flags.</div>';

            // Replace everything before the divider
            const divider = flagsContainer.querySelector('.detail-flag-divider');
            if (divider) {
              let node = flagsContainer.firstChild;
              while (node && node !== divider) {
                const next = node.nextSibling;
                node.remove();
                node = next;
              }
              divider.insertAdjacentHTML('beforebegin', newFlagsHtml);
            }
          }
        } catch (err) {
          const msgEl2 = document.getElementById('flagFormMsg');
          if (msgEl2) { msgEl2.textContent = '✗ ' + err.message; msgEl2.className = 'flag-form-msg flag-form-err'; }
        }
      });
    }
  }

  async function openRunDetail(id) {
    showDetailModal('Run Record', (id || '').slice(0, 8) + '…', '<div class="detail-loading">Loading…</div>');
    try {
      const [run, flags] = await Promise.all([
        fetch(`/api/runs/${id}`).then(r => r.json()),
        fetch(`/api/runs/${id}/flags`).then(r => r.json()).catch(() => []),
      ]);
      showDetailModal('Run Record', (run.run_id || id).slice(0, 8) + '…', renderRunDetailHtml(run, flags));
    } catch (e) {
      detailModalJson.innerHTML = `<div class="detail-error">Failed to load: ${esc(e.message)}</div>`;
    }
  }

  async function openSessionDetail(id) {
    showDetailModal('Session Record', (id || '').slice(0, 8) + '…', '<div class="detail-loading">Loading…</div>');
    try {
      const s = await fetch(`/api/sessions/${id}`).then(r => r.json());
      showDetailModal('Session Record', s.ticker || (id || '').slice(0, 8) + '…', renderSessionDetailHtml(s));
    } catch (e) {
      detailModalJson.innerHTML = `<div class="detail-error">Failed to load: ${esc(e.message)}</div>`;
    }
  }

  function closeDetailModal()    { detailModal.classList.add('hidden'); }
  function closeDirectiveModal() { directiveModal.classList.add('hidden'); }

  btnCloseDetail.addEventListener('click',    closeDetailModal);
  btnCloseDirective.addEventListener('click', closeDirectiveModal);
  detailModal.addEventListener('click',    e => { if (e.target === detailModal)    closeDetailModal(); });
  directiveModal.addEventListener('click', e => { if (e.target === directiveModal) closeDirectiveModal(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeDetailModal(); closeDirectiveModal(); }
  });

  btnDirective.addEventListener('click', () => directiveModal.classList.remove('hidden'));

  // ── Clear runs ────────────────────────────────────────────────────────────────

  btnClear.addEventListener('click', async () => {
    try {
      await fetch('/api/runs', { method: 'DELETE' });
      auditRuns.innerHTML     = '<div class="audit-empty">No runs yet.</div>';
      auditSessions.innerHTML = '<div class="audit-empty">No sessions yet.</div>';
      runCountBadge.textContent  = '0';
      sessCountBadge.textContent = '0';
    } catch { /* silent */ }
  });

  // ══ Cross-Agent Validation ═══════════════════════════════════════════════════


  let selfReport = null;   // cached /api/self-report payload
  let isComparing = false;

  // ── Mode switching ────────────────────────────────────────────────────────────

  function switchMode(mode) {
    const compare = mode === 'compare';
    modeChat.classList.toggle('active',    !compare);
    modeCompare.classList.toggle('active',  compare);
    compareView.classList.toggle('hidden', !compare);
    chatMessages.classList.toggle('hidden', compare);
    chatInputArea.classList.toggle('hidden', compare);
  }

  modeChat.addEventListener('click',    () => switchMode('chat'));
  modeCompare.addEventListener('click', () => switchMode('compare'));

  // ── Self-report (tests + known issues) ────────────────────────────────────────

  async function loadSelfReport() {
    const res = await fetch('/api/self-report');
    if (!res.ok) throw new Error(`self-report: HTTP ${res.status}`);
    selfReport = await res.json();

    const c = selfReport.counts || {};
    ledgerCount.textContent = c.issues_open != null ? String(c.issues_open) : '–';
    btnLedger.title = `${c.issues_open} open issues, ${c.issues_critical} critical — click for the full ledger`;

    const dep = selfReport.deployment_status || {};
    if (dep.state) {
      prototypePill.textContent = dep.state;
      prototypePill.title = dep.detail || '';
    }

    renderCompareCaveat();
  }

  /**
   * The caveat shown above the compare runner. Not decoration: without it a
   * green "no contradiction" or a red "flagged" reads as more authoritative
   * than this comparator can support.
   */
  function renderCompareCaveat() {
    if (!selfReport) return;
    const issues  = selfReport.known_issues || [];
    const byId    = id => issues.find(i => i.id === id);
    const overlap = byId('disjoint-concepts');
    const noModel = byId('http-no-model-override');

    const bits = [];
    if (overlap && overlap.status === 'OPEN') {
      bits.push(`<strong>This comparator over-flags.</strong> ${esc(overlap.detail)}`);
    }
    if (noModel && noModel.status === 'OPEN') {
      bits.push(`<strong>Both producers run on the same model here.</strong> ${esc(noModel.detail)}`);
    }
    bits.push('A flag means the two conclusions cite different numbers. It does not mean either is wrong, '
      + 'and nothing here decides which agent to believe.');

    compareCaveat.innerHTML = bits.join('<br><br>');
  }

  // ── Numeric normalisation for input-provenance ────────────────────────────────

  const _MULT = {
    trillion: 1e12, t: 1e12,
    billion:  1e9,  bn: 1e9, b: 1e9,
    million:  1e6,  mm: 1e6, m: 1e6,
    thousand: 1e3,  k: 1e3,
  };

  /**
   * Parse a cited figure or an input value to a plain number.
   * Handles "$383.266 billion", "$383,266,000,000", "38.1%", "6.08", "1.5x", "12 bps".
   * Returns null when there is nothing comparable to parse.
   *
   * Percentages and ratios parse to their face value (38.1 -> 38.1), which means
   * they will essentially never match a raw EDGAR input. That is correct: a margin
   * or a ratio is DERIVED, not handed to the agent. See the provenance caveat.
   */
  function parseNumeric(raw) {
    if (raw == null) return null;
    let s = String(raw).trim().toLowerCase();
    s = s.replace(/[$,]/g, '').replace(/\s+/g, ' ').trim();
    const m = s.match(/^(-?\d+(?:\.\d+)?)\s*([a-z]*)/);
    if (!m) return null;
    const n = parseFloat(m[1]);
    if (!isFinite(n)) return null;
    const suffix = (m[2] || '').replace(/[^a-z]/g, '');
    if (!suffix || suffix === '%' ) return n;
    if (suffix === 'x' || suffix === 'bps') return n;
    return _MULT[suffix] ? n * _MULT[suffix] : n;
  }

  /** Parse a producer's context string into its `Concept: value` rows. */
  function parseContext(text) {
    if (!text) return [];
    return text.split('\n').map(line => {
      const i = line.indexOf(':');
      if (i < 0) return null;
      const concept = line.slice(0, i).trim();
      const value   = line.slice(i + 1).trim();
      if (!concept || !value) return null;   // section headers carry no value
      return { concept, value, numeric: parseNumeric(value) };
    }).filter(Boolean);
  }

  const _REL_TOL = 1e-3;   // 0.1% — absorbs restatement rounding, not real differences

  /**
   * Does a cited number reconcile to something this producer was actually handed?
   *
   * NOT claim verification. A legitimately derived figure (a ratio built from two
   * input values) will not match and is reported as 'nomatch' while being sound.
   * This is a pointer for a human — it is how the AAPL 0.34 fabrication was found.
   */
  function inputProvenance(citedRaw, rows) {
    const cited = parseNumeric(citedRaw);
    if (cited == null) return { state: 'unknown', concept: null };
    for (const row of rows) {
      if (row.numeric == null) continue;
      const denom = Math.max(Math.abs(row.numeric), Math.abs(cited), 1e-9);
      if (Math.abs(row.numeric - cited) / denom <= _REL_TOL) {
        return { state: 'match', concept: row.concept };
      }
    }
    return { state: 'nomatch', concept: null };
  }

  // ── Compare rendering — four bands ────────────────────────────────────────────

  function fmtMs(ms) {
    if (ms == null) return '';
    return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`;
  }

  const STEP_STATUS_BADGE = {
    ok:            'badge badge-success',
    parse_failure: 'badge badge-warning',
    error:         'badge badge-halt',
  };

  /** Band 1 — setup: who is running, on what, and what differs. */
  function bandSetup(data) {
    const p = data.producers;
    if (!p) return '';
    const side = (x, cls) => `
      <div class="setup-side ${cls}">
        <div class="producer-role">${esc(x.role)}</div>
        <div class="producer-agent">AgentID.${esc((x.agent_id || '').toUpperCase())}</div>
        <div class="setup-model">${esc(x.model)}${x.overridden
          ? ' <span class="badge-neutral">override</span>' : ''}</div>
        <div class="setup-concepts">${(x.concepts || []).map(c =>
          `<span class="num-pill">${esc(c)}</span>`).join('')}</div>
      </div>`;

    const modelLine = p.same_model
      ? `<strong>Both producers ran on the same model</strong> (${esc(p.a.model)}), so the only
         real asymmetry is the data slice. Set different models above to make the two agents
         genuinely independent.`
      : `<strong>The producers ran on different models</strong> — ${esc(p.a.model)} vs
         ${esc(p.b.model)} — so this comparison has both model and data asymmetry.`;

    return `
      <div class="band band-setup">
        <div class="band-title">1 · Setup <span class="band-sub">who is being compared</span></div>
        <div class="setup-grid">
          ${side(p.a, 'side-a')}
          <div class="setup-vs">vs</div>
          ${side(p.b, 'side-b')}
        </div>
        <div class="band-note">
          <strong>Same EDGAR payload, disjoint concept sets.</strong> The concept lists above come
          from the grader modules themselves, not the UI. That difference is deliberate — it is the
          information asymmetry that makes a disagreement meaningful — and it is also why numbers
          differing between the two columns is <em>not</em> by itself evidence of conflict.
          <br>${modelLine}
        </div>
      </div>`;
  }

  /** Band 2 — the shared spine: what happened once, for both producers. */
  function bandSpine(steps) {
    const shared = (steps || []).filter(s => s.phase === 'shared');
    if (!shared.length) return '';
    const rows = shared.map(s => `
      <div class="spine-step">
        <span class="spine-seq">${s.seq}</span>
        <span class="spine-label">${esc(s.label)}</span>
        <span class="${STEP_STATUS_BADGE[s.status] || 'badge-neutral'}">${esc(s.status)}</span>
        <span class="spine-dur">${fmtMs(s.duration_ms)}</span>
        <div class="spine-detail">
          ${s.url ? `<span class="spine-url">${esc(s.url)}</span><br>` : ''}
          ${s.detail ? esc(s.detail) : ''}
          ${s.error ? `<span class="detail-error-inline">${esc(s.error)}</span>` : ''}
        </div>
      </div>`).join('');

    return `
      <div class="band band-spine">
        <div class="band-title">2 · Shared work
          <span class="band-sub">ran once — both producers reuse it</span>
        </div>
        ${rows}
        <div class="spine-fork">
          <span>↓ Producer A</span><span class="spine-fork-note">one payload, two lenses</span><span>Producer B ↓</span>
        </div>
      </div>`;
  }

  /** One agent column in band 3. */
  function agentColumn(which, data, cmp) {
    const p   = (data.producers || {})[which];
    const ctx = (data.contexts  || {})[which];
    if (!p) return '';

    const agentId  = which === 'a' ? cmp.agent_a_id : cmp.agent_b_id;
    const conclusion = which === 'a' ? cmp.agent_a_conclusion : cmp.agent_b_conclusion;
    const nums     = (which === 'a' ? cmp.agent_a_numbers : cmp.agent_b_numbers) || [];
    const phase    = which === 'a' ? 'agent_a' : 'agent_b';
    const divergent = new Set(cmp.divergent_numbers || []);
    const rows     = parseContext(ctx);

    const objects = (data.reasoning_objects || []).filter(o => o.agent_id === agentId);
    const halted  = objects.some(o => o.parse_status === 'HALT');

    // 1. input it saw
    const inputRows = rows.length
      ? rows.map(r => `
          <div class="io-row">
            <span class="io-concept">${esc(r.concept)}</span>
            <span class="io-value">${esc(r.value)}</span>
          </div>`).join('')
      : '<div class="io-row"><span class="io-concept">—</span><span class="io-value">context not returned</span></div>';

    // 2. llm calls, from the real step trace
    const calls = (data.steps || []).filter(s => s.phase === phase);
    const callRows = calls.length ? calls.map(s => `
      <div class="call-row">
        <span class="spine-seq">${s.seq}</span>
        <span class="call-label">${esc(s.label)}</span>
        <span class="${STEP_STATUS_BADGE[s.status] || 'badge-neutral'}">${esc(s.status)}</span>
        <span class="spine-dur">${fmtMs(s.duration_ms)}</span>
        <div class="call-detail">${esc(s.detail || '')}${s.detail && /directive=corrective/.test(s.detail)
          ? ' <span class="badge badge-warning">ADR-07 retry</span>' : ''}</div>
      </div>`).join('')
      : '<div class="call-detail">no attempts recorded</div>';

    // 3. reasoning — scope-aware, never a silently empty box
    const withheld = objects.length && !objects.some(o => 'thought_log' in o);
    const logs = objects
      .filter(o => o.thought_log)
      .map(o => `<div class="ro-attempt">attempt ${o.attempt_number}</div>${renderMd(o.thought_log)}`)
      .join('');
    const reasoning = withheld
      ? `<div class="reasoning-withheld">thought_log withheld at investor scope (SEC-01).
           The reasoning exists and is stored; it is structurally omitted from this response.</div>`
      : (logs
          ? `<details class="reasoning"><summary>Reasoning (thought_log)</summary>
               <div class="reasoning-body">${logs}</div></details>`
          : '<div class="call-detail">no thought_log recorded</div>');

    // 5. cited numbers, each traced back to this agent's own input
    const numPills = nums.length ? nums.map(n => {
      const prov = inputProvenance(n, rows);
      const mark = { match: '✓ in input', nomatch: '⚠ not in input', unknown: '· not comparable' }[prov.state];
      const cls  = { match: 'prov-match', nomatch: 'prov-nomatch', unknown: 'prov-unknown' }[prov.state];
      const tip  = prov.state === 'match'
        ? `reconciles to ${prov.concept}`
        : prov.state === 'nomatch'
          ? 'no value this agent was given matches — may be derived, may be unsupported'
          : 'could not parse for comparison';
      return `<span class="num-pill ${divergent.has(n) ? 'diverged' : ''} ${cls}" title="${esc(tip)}">
                ${esc(n)} <span class="prov-mark">${mark}</span>
              </span>`;
    }).join('') : '<span class="num-pill">no numbers cited</span>';

    return `
      <div class="agent-col side-${which}${halted ? ' producer-halted' : ''}">
        <div class="agent-col-head">
          <span class="producer-role">${esc(p.role)}</span>
          <span class="producer-agent">${esc(agentId || p.agent_id)}</span>
          <span class="badge-neutral">${esc(p.model)}</span>
          ${halted ? '<span class="badge badge-halt">HALTED</span>'
                   : '<span class="badge badge-success">OK</span>'}
        </div>

        <div class="agent-sec-title">Input it saw</div>
        <div class="io-block">${inputRows}</div>

        <div class="agent-sec-title">LLM calls</div>
        ${callRows}

        <div class="agent-sec-title">Reasoning</div>
        ${reasoning}

        <div class="agent-sec-title">Conclusion</div>
        <div class="producer-conclusion">${conclusion ? renderMd(conclusion)
          : '<em>No conclusion — this producer did not pass structural validation.</em>'}</div>

        <div class="agent-sec-title">Numbers it cited</div>
        <div class="producer-nums">${numPills}</div>
      </div>`;
  }

  /** Band 4 — the comparator's verdict. */
  function bandVerdict(cmp, data) {
    const compared = cmp.status === 'COMPARED';
    const flagged  = cmp.contradiction_flag === true;

    let cls, title, note;
    if (!compared) {
      cls = 'verdict-nocompare';
      title = 'No comparison possible';
      note = `Status <b>${esc(cmp.status)}</b> — at least one producer failed structural validation
              twice, so there was no conclusion to compare. <b>contradiction_flag is null, not
              false</b>: "could not check" is recorded as a different fact from "checked and found
              nothing."`;
    } else if (flagged) {
      cls = 'verdict-flagged';
      title = 'Contradiction flagged';
      note = `At least one number appears in one conclusion and not the other. Because the two
              producers were deliberately given <em>different concept sets</em> (band 1), this is
              frequently a false positive — check the per-column numbers above before treating it
              as a real disagreement.`;
    } else {
      cls = 'verdict-agreed';
      title = 'No contradiction found';
      note = `No number appears in exactly one conclusion. Numeric agreement is not agreement on
              reasoning — this check compares numbers only.`;
    }

    const badge = !compared ? '<span class="badge badge-warning">NULL</span>'
                : flagged   ? '<span class="badge badge-halt">FLAGGED</span>'
                            : '<span class="badge badge-success">CLEAR</span>';

    const metrics = compared ? `
      <div class="verdict-metrics">
        <span>agreement <b>${esc(cmp.agreement)}</b></span>
        <span>score <b>${esc(String(cmp.score))}</b></span>
        <span>word overlap <b>${esc(String(cmp.word_overlap))}</b></span>
        <span>number overlap <b>${esc(String(cmp.number_overlap))}</b></span>
      </div>` : '';

    // Attribute each divergent number to the agent that actually cited it — the
    // flat list alone loses that, which is the whole ambiguity being complained about.
    const aNums = new Set(cmp.agent_a_numbers || []);
    const bNums = new Set(cmp.agent_b_numbers || []);
    const diverged = (cmp.divergent_numbers || []).length ? `
      <div class="divergent-row">
        <span class="producer-role">DIVERGENT</span>
        ${cmp.divergent_numbers.map(n => {
          const owner = aNums.has(n) ? 'A only' : bNums.has(n) ? 'B only' : '?';
          return `<span class="num-pill diverged" title="cited by ${owner}">
                    ${esc(n)} <span class="prov-mark">${owner}</span></span>`;
        }).join('')}
      </div>` : '';

    return `
      <div class="band band-verdict">
        <div class="band-title">4 · Cross-validation
          <span class="band-sub">numeric divergence only</span>
        </div>
        <div class="verdict ${cls}">
          <div class="verdict-head">
            ${badge}
            <span class="verdict-title">${title}</span>
            <span class="badge-neutral">${esc(cmp.subject || '')}</span>
            <span class="audit-run-id">${esc((cmp.run_id || '').slice(0, 8))}…</span>
          </div>
          <div class="verdict-note">${note}</div>
          ${metrics}
          ${diverged}
        </div>
      </div>`;
  }

  function toolWarningBandHtml(data) {
    return data.tool_capability_warning
      ? `<div class="msg-uncertainty-banner">🔧 ${esc(data.tool_capability_warning)}</div>`
      : '';
  }

  function renderCompare(data) {
    if (data.error) {
      // Setup and the partial spine are still shown: knowing which models were
      // configured and how far the run got is exactly what localises the failure.
      compareResults.innerHTML =
        toolWarningBandHtml(data)
        + bandSetup(data)
        + bandSpine(data.steps)
        + `<div class="band band-verdict">
             <div class="verdict verdict-nocompare">
               <div class="verdict-head">
                 <span class="badge badge-halt">ERROR</span>
                 <span class="verdict-title">Comparison did not run</span>
               </div>
               <div class="verdict-note">${esc(data.error)}</div>
             </div>
           </div>`;
      return;
    }

    const cmp = data.cross_agent_comparison;
    if (!cmp) {
      compareResults.innerHTML = '<div class="chat-empty">No comparison returned.</div>';
      return;
    }

    compareResults.innerHTML =
      toolWarningBandHtml(data)
      + bandSetup(data)
      + bandSpine(data.steps)
      + `<div class="band band-agents">
           <div class="band-title">3 · Each agent, independently
             <span class="band-sub">A ran first, then B — neither saw the other</span>
           </div>
           <div class="agents-grid">
             ${agentColumn('a', data, cmp)}
             ${agentColumn('b', data, cmp)}
           </div>
           <div class="band-note">
             <strong>On the ✓/⚠ markers:</strong> they compare each cited number against the values
             that producer was actually handed, with a rounding tolerance. This is
             <em>not</em> claim verification. A ratio or percentage computed from two input values
             is legitimately derived and will still read "not in input" — so treat ⚠ as a place to
             look, not a verdict. Recorded as an open issue in the Honest Ledger.
           </div>
         </div>`
      + bandVerdict(cmp, data)
      + `<p class="field-hint" style="margin-top:10px">
           Both producers' attempts are persisted under one shared run_id — open the run from the
           Runs tab to see the full stored record.
         </p>`
      + (currentScope === 'investor' ? `
         <p class="field-hint" style="margin-top:2px">
           <strong>Investor scope, stated precisely:</strong> thought_log and raw_output are
           structurally absent from the reasoning objects rendered above. They are
           <strong>not</strong> absent from the nested <code>session</code> object in the same
           response, nor from the stored record — that is the unfixed
           <em>session-scope-leak</em> issue in the Honest Ledger, and it is verified reachable,
           not theoretical.
         </p>` : '');
  }

  // ── Compare runner ────────────────────────────────────────────────────────────

  /** Split the "provider|model" select values into the request's override fields. */
  function producerOverrides() {
    // No provider field anymore — a bare model name is enough
    // (langchain_adapter.py infers Ollama- vs. Gemini-family from it).
    return {
      agent_a_model: (cmpModelA && cmpModelA.value) || null,
      agent_b_model: (cmpModelB && cmpModelB.value) || null,
    };
  }

  /** Shared submit logic for both compare modes — only the request body and
   *  which button/label/spinner triple to drive differ between them. */
  async function _submitCompare(body, { btn, label, spinner, loadingHtml }) {
    if (isComparing) return;
    isComparing = true;
    btn.disabled = true;
    label.textContent = 'Running…';
    spinner.classList.remove('hidden');
    compareResults.innerHTML = loadingHtml;

    try {
      const headers = {
        'Content-Type': 'application/json',
        ...(await authHeader(currentScope)),
      };
      const bodyStr = JSON.stringify(body);
      let res = await fetch('/api/compare', { method: 'POST', headers, body: bodyStr });

      if (res.status === 401) {
        _tokens[currentScope] = null;   // expired — reissue once, same as sendMessage()
        res = await fetch('/api/compare', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...(await authHeader(currentScope)) },
          body: bodyStr,
        });
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        renderCompare({ error: err.detail || `HTTP ${res.status}` });
      } else {
        renderCompare(await res.json());
        refreshAuditBoth();
        refreshFlagged();
      }
    } catch (err) {
      renderCompare({ error: 'Network error: ' + err.message });
    } finally {
      isComparing = false;
      btn.disabled = false;
      label.textContent = 'Run Comparison';
      spinner.classList.add('hidden');
    }
  }

  async function runComparison() {
    const ticker = compareTicker.value.trim().toUpperCase();
    if (!ticker) return;
    await _submitCompare(
      { ticker, ...producerOverrides() },
      {
        btn: btnCompare, label: compareLabel, spinner: compareSpinner,
        loadingHtml: `<div class="chat-empty">Fetching EDGAR facts for ${esc(ticker)}, then running both producers…</div>`,
      },
    );
  }

  async function runGenericComparison() {
    const subject = compareSubject.value.trim();
    if (!subject) return;
    await _submitCompare(
      { subject, context: compareGenericContext.value, ...producerOverrides() },
      {
        btn: btnCompareGeneric, label: compareGenericLabel, spinner: compareGenericSpinner,
        loadingHtml: `<div class="chat-empty">Running both agents independently on ${esc(subject)}…</div>`,
      },
    );
  }

  btnCompare.addEventListener('click', runComparison);
  compareTicker.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); runComparison(); }
  });
  btnCompareGeneric.addEventListener('click', runGenericComparison);

  // ── Compare mode toggle (ticker vs. subject) ─────────────────────────────
  function setCompareMode(mode) {
    cmpModeTicker.classList.toggle('active', mode === 'ticker');
    cmpModeSubject.classList.toggle('active', mode === 'subject');
    cmpTickerRow.classList.toggle('hidden', mode !== 'ticker');
    cmpSubjectRow.classList.toggle('hidden', mode !== 'subject');
  }
  cmpModeTicker.addEventListener('click', () => setCompareMode('ticker'));
  cmpModeSubject.addEventListener('click', () => setCompareMode('subject'));

  // ── Flagged contradictions tab ────────────────────────────────────────────────

  async function refreshFlagged() {
    try {
      const rows = await fetch('/api/runs/contradictions').then(r => r.json());
      flaggedCount.textContent = String(rows.length);

      if (!rows.length) {
        auditFlagged.innerHTML =
          '<div class="audit-empty">No flagged contradictions yet.</div>';
        return;
      }

      auditFlagged.innerHTML = '';
      rows.forEach(row => {
        const cmp  = row.cross_agent_comparison || {};
        const card = document.createElement('div');
        card.className = 'audit-card audit-card-halted';
        card.innerHTML = `
          <div class="audit-card-header">
            <span class="badge badge-halt">FLAGGED</span>
            <span class="audit-run-id">${esc((row.run_id || '').slice(0, 8))}…</span>
          </div>
          <div class="audit-subject">${esc(cmp.subject || row.subject || '—')}</div>
          <div class="audit-meta">
            <span class="badge-neutral">${esc(cmp.agent_a_id || 'a')} vs ${esc(cmp.agent_b_id || 'b')}</span>
            <span class="badge-neutral">${esc(cmp.agreement || '—')}</span>
          </div>
          <div class="producer-nums">
            ${(cmp.divergent_numbers || []).map(n =>
              `<span class="num-pill diverged">${esc(n)}</span>`).join('')}
          </div>`;
        card.addEventListener('click', () => openRunDetail(row.run_id));
        auditFlagged.appendChild(card);
      });
    } catch {
      auditFlagged.innerHTML =
        '<div class="audit-empty">Could not load contradictions.</div>';
    }
  }

  // ── Honest ledger modal ───────────────────────────────────────────────────────

  const SEV_BADGE = {
    critical: 'badge badge-halt',
    high:     'badge badge-halt',
    medium:   'badge badge-warning',
    low:      'badge-neutral',
    info:     'badge-neutral',
  };
  const STATUS_BADGE = {
    OPEN:       'badge badge-halt',
    UNVERIFIED: 'badge badge-warning',
    RESOLVED:   'badge badge-success',
    BY_DESIGN:  'badge-neutral',
  };

  function ledgerHtml(r) {
    const at = r.automated_tests || {};
    const lm = r.live_model_tests || {};
    const c  = r.counts || {};
    const dep = r.deployment_status || {};

    const modules = (at.modules || []).map(m =>
      `<span class="ledger-module">${esc(m.module)} <b>${m.tests}</b></span>`).join('');

    const testCount = at.total != null ? at.total
      : `<span title="${esc(at.error || '')}">unavailable</span>`;

    const liveRows = (lm.tests || []).map(t => `
      <div class="ledger-test ${esc(t.outcome)}">
        <div class="ledger-test-head">
          <span class="badge-neutral">${t.n}</span>
          <span class="ledger-test-name">${esc(t.name)}</span>
          <span class="${STATUS_BADGE[t.status] || 'badge-neutral'}">${esc(t.status)}</span>
        </div>
        <div class="ledger-test-row"><span class="lbl">EXPECTED</span>${esc(t.expected)}</div>
        <div class="ledger-test-row"><span class="lbl">ACTUAL</span>${esc(t.actual)}</div>
        <div class="ledger-test-row"><span class="lbl">VERDICT</span>${esc(t.verdict)}</div>
        ${t.resolved_note
          ? `<div class="ledger-issue-source">${esc(t.resolved_note)}</div>` : ''}
      </div>`).join('');

    const issueRows = (r.known_issues || []).map(i => `
      <div class="ledger-issue ${esc(i.severity)}">
        <div class="ledger-issue-head">
          <span class="${SEV_BADGE[i.severity] || 'badge-neutral'}">${esc(i.severity)}</span>
          <span class="${STATUS_BADGE[i.status] || 'badge-neutral'}">${esc(i.status)}</span>
          <span class="badge-neutral">${esc(i.area)}</span>
          <span class="ledger-issue-title">${esc(i.title)}</span>
        </div>
        <div class="ledger-issue-detail">${esc(i.detail)}</div>
        <div class="ledger-issue-source">source: ${esc(i.source)}</div>
      </div>`).join('');

    const caveats = (lm.caveats || []).map(x => `<li>${esc(x)}</li>`).join('');

    return `
      <div class="ledger-summary">
        <div class="ledger-stat">
          <span class="ledger-stat-n">${testCount}</span>
          <span class="ledger-stat-l">automated tests</span>
        </div>
        <div class="ledger-stat bad">
          <span class="ledger-stat-n">${c.issues_open != null ? c.issues_open : '–'}</span>
          <span class="ledger-stat-l">open issues</span>
        </div>
        <div class="ledger-stat bad">
          <span class="ledger-stat-n">${c.issues_critical != null ? c.issues_critical : '–'}</span>
          <span class="ledger-stat-l">critical</span>
        </div>
        <div class="ledger-stat bad">
          <span class="ledger-stat-n">${c.live_tests_gaps_found != null ? c.live_tests_gaps_found : '–'}</span>
          <span class="ledger-stat-l">live tests that found a gap</span>
        </div>
      </div>

      <div class="ledger-section">
        <div class="ledger-section-title">Deployment status — ${esc(dep.state || 'unknown')}</div>
        <div class="ledger-issue-detail">${esc(dep.detail || '')}</div>
      </div>

      <div class="ledger-section">
        <div class="ledger-section-title">Automated suite (counted live, not hard-coded)</div>
        <div class="ledger-modules">${modules || '<em>none discovered</em>'}</div>
        <div class="ledger-issue-source">
          Discovered by unittest at request time from tests/, so this number cannot drift from the
          suite. Offline and model-free by design: passing tells you the logic is consistent, not
          that a real model behaves.
        </div>
        ${at.error ? `<div class="detail-error-inline">discovery failed: ${esc(at.error)}</div>` : ''}
      </div>

      <div class="ledger-section">
        <div class="ledger-section-title">
          Live model tests — ${esc(lm.run_on || '')}, ${esc(lm.model || '')}
        </div>
        ${liveRows || '<em>none recorded</em>'}
        <div class="ledger-section-title" style="margin-top:12px">What these do not establish</div>
        <ul class="ledger-caveats">${caveats}</ul>
      </div>

      <div class="ledger-section">
        <div class="ledger-section-title">Known issues (${(r.known_issues || []).length})</div>
        ${issueRows || '<em>none recorded</em>'}
      </div>`;
  }

  async function openLedger() {
    ledgerModal.classList.remove('hidden');
    ledgerBody.innerHTML = '<div class="detail-loading">Loading…</div>';
    try {
      if (!selfReport) await loadSelfReport();
      ledgerBody.innerHTML = ledgerHtml(selfReport);
    } catch (e) {
      ledgerBody.innerHTML = `<div class="detail-error">Could not load: ${esc(e.message)}</div>`;
    }
  }

  btnLedger.addEventListener('click', openLedger);
  btnCloseLedger.addEventListener('click', () => ledgerModal.classList.add('hidden'));
  ledgerModal.addEventListener('click', e => {
    if (e.target === ledgerModal) ledgerModal.classList.add('hidden');
  });

  // ── Init ──────────────────────────────────────────────────────────────────────

  (async function init() {
    setStatus('connecting');
    try {
      // Pre-warm both tokens so the UI is ready before first message
      await Promise.all([
        loadConfig(),
        loadDirective(),
        loadSelfReport(),
        ensureToken('auditor'),
        ensureToken('investor'),
      ]);
      await refreshRuns();
      await refreshFlagged();
      setStatus('ok');
    } catch (e) {
      console.error('Init failed:', e);
      setStatus('err');
    }
  })();

})();
