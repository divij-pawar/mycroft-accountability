import { useEffect, useId, useRef, useState } from "react";

// Asked for when the server refuses an auditor token without its admin key (web/auth.py). The key
// is held for this tab only (sessionStorage, src/api/client.ts) and sent in a header, never a URL.
export function AdminKeyDialog({ onSubmit, onCancel }: { onSubmit: (key: string) => void; onCancel: () => void }) {
  const id = useId();
  const [key, setKey] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  return (
    <div className="scrim-modal">
      <form className="card key-dialog" role="dialog" aria-modal="true" aria-labelledby={`${id}-t`}
            onKeyDown={(e) => { if (e.key === "Escape") onCancel(); }}
            onSubmit={(e) => { e.preventDefault(); if (key.trim()) onSubmit(key.trim()); }}>
        <h2 id={`${id}-t`} className="headline">Auditor access needs the admin key</h2>
        <p className="muted small">
          The server only issues auditor access to someone who has its admin key (<code>ACCOUNTABILITY_ADMIN_KEY</code>,
          saved in <code>.env</code> by <code>scripts/start-server.sh</code>). It is kept for this tab only.
          Investor access needs no key.
        </p>
        <label className="gate-field">
          Admin key
          <input ref={input} type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
        </label>
        <div className="gate-actions">
          <button type="submit" className="btn" disabled={!key.trim()}>Continue</button>
          <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        </div>
      </form>
    </div>
  );
}
