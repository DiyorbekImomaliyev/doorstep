import { useEffect, useState } from "react";
import { api, type OrderRow } from "../api";
import { ErrorBox, Loading, Meter, Pill, Timeline, useAsync } from "../components/ui";
import { FAILURE_POINT, METHOD, ROUTE, money, stateTone } from "../format";

const TEST_TRACKING = ["SHIPPO_DELIVERED", "SHIPPO_TRANSIT", "SHIPPO_FAILURE", "SHIPPO_PRE_TRANSIT", "SHIPPO_RETURNED"];

export default function CaseDrawer({ token, row, onClose, onChanged }: { token: string; row: OrderRow; onClose: () => void; onChanged: (msg: string) => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={`Order ${row.order_ref}`}>
        {row.case_id ? <CaseBody token={token} caseId={row.case_id} onClose={onClose} onChanged={onChanged} /> : <OrderBody token={token} row={row} onClose={onClose} onChanged={onChanged} />}
      </aside>
    </>
  );
}

function Header({ title, sub, onClose, right }: { title: string; sub: string; onClose: () => void; right?: React.ReactNode }) {
  return (
    <header className="spread">
      <div className="col" style={{ gap: 2 }}>
        <h2>{title}</h2>
        <span className="faint">{sub}</span>
      </div>
      <div className="row">{right}<button className="btn ghost" onClick={onClose} aria-label="Close">✕</button></div>
    </header>
  );
}

function CaseBody({ token, caseId, onClose, onChanged }: { token: string; caseId: string; onClose: () => void; onChanged: (m: string) => void }) {
  const { data: c, error, reload } = useAsync(() => api.casePage(token, caseId), [caseId]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function act(action: "approve" | "decline") {
    setBusy(true); setErr(null);
    try {
      await api.act(token, caseId, action);
      onChanged(action === "approve" ? "Approved. The refund is on its way through PayPal." : "Declined. The buyer will be told.");
      reload();
    } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }

  if (error) return <div className="body"><ErrorBox message={error} /></div>;
  if (!c) return <div className="body col"><Loading /><Loading /></div>;
  const d = c.decision;
  const route = ROUTE[c.state];
  const refund = d?.refund_amount_minor != null ? money(d.refund_amount_minor, d.currency) : null;

  return (
    <>
      <Header title={c.order.item} sub={`${c.order.order_ref} · reported ${c.reason.replace("_", " ")} by ${c.reported_by}`} onClose={onClose}
        right={route && <Pill tone={route.tone}>{route.label}</Pill>} />
      <div className="body stack-lg">
        {d && (
          <section className="col">
            <div className="spread">
              <h3>Most likely: {FAILURE_POINT[d.failure_point ?? ""] ?? d.failure_point}</h3>
              <Pill tone={d.confidence === "high" ? "good" : d.confidence === "medium" ? "warn" : "neutral"} plain>{d.confidence} confidence</Pill>
            </div>
            <div className="card tight col" style={{ gap: 6, background: "var(--surface-2)", boxShadow: "none" }}>
              <span className="faint">Rule applied</span>
              <span className="mono">{d.rule_id ?? "no matching rule set"}{d.rule_version ? ` · ${d.rule_version}` : ""}</span>
            </div>
            <p className="prose" style={{ color: "var(--text)" }}>{d.explanation}</p>
            {d.reasons.length > 0 && (
              <details>
                <summary>Evidence used ({d.reasons.length})</summary>
                <ul style={{ margin: 0, paddingLeft: 18 }} className="muted">
                  {d.reasons.map((r, i) => <li key={i}>{r}</li>)}
                </ul>
                {d.missing_signals.length > 0 && <p className="faint" style={{ marginTop: 8 }}>Missing: {d.missing_signals.join(", ").replaceAll("_", " ")}</p>}
              </details>
            )}
            {d.claim_letter && (
              <details>
                <summary>Carrier claim letter (draft)</summary>
                <p className="prose">{d.claim_letter}</p>
                <button className="btn" style={{ marginTop: 8 }} onClick={() => navigator.clipboard?.writeText(d.claim_letter ?? "")}>Copy letter</button>
              </details>
            )}
            {d.buyer_message && (
              <details>
                <summary>Message to the buyer</summary>
                <p className="prose">{d.buyer_message}</p>
              </details>
            )}
          </section>
        )}

        {c.money.length > 0 && (
          <section className="col">
            <h3>Money</h3>
            {c.money.map((m, i) => (
              <div key={i} className="spread card tight">
                <span>{m.kind.replace("_", " ")} {money(m.amount_minor, m.currency)}</span>
                <span className="row">
                  {m.refund_id && <span className="mono faint">{m.refund_id}</span>}
                  <Pill tone={m.status === "completed" ? "good" : m.status === "simulated" ? "neutral" : m.status === "queued" ? "info" : "warn"}>{m.status}</Pill>
                </span>
              </div>
            ))}
          </section>
        )}

        <section className="col">
          <div className="spread">
            <h3>Timeline</h3>
            <a href={`/verify/${c.case_id}`} target="_blank" rel="noreferrer">{c.receipts} signed receipt{c.receipts === 1 ? "" : "s"} · verify ↗</a>
          </div>
          <Timeline items={c.timeline} />
        </section>
        <p className="faint">{c.disclaimer}</p>
      </div>
      {c.actions.length > 0 && (
        <footer className="col">
          {err && <ErrorBox message={err} />}
          <div className="row">
            {c.actions.includes("decline") && <button className="btn danger" disabled={busy} onClick={() => act("decline")}>Decline</button>}
            <div className="grow" />
            {c.actions.includes("approve") && (
              <button className="btn primary lg" disabled={busy} onClick={() => act("approve")}>
                Approve {refund ? `refund ${refund}` : ""}
              </button>
            )}
          </div>
        </footer>
      )}
    </>
  );
}

function OrderBody({ token, row, onClose, onChanged }: { token: string; row: OrderRow; onClose: () => void; onChanged: (m: string) => void }) {
  const [tracking, setTracking] = useState(TEST_TRACKING[0]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);

  async function ship() {
    setBusy(true); setErr(null);
    try { await api.ship(token, row.order_id, tracking, 900); onChanged("Shipped. Tracking is being added to the PayPal payment."); onClose(); }
    catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }
  async function buyerLink() {
    const r = await api.buyerLink(token, row.order_id);
    setLink(r.buyer_url);
  }

  return (
    <>
      <Header title={row.item} sub={`${row.order_ref} · ${row.buyer_area || row.buyer_country}`} onClose={onClose} right={<Pill tone={stateTone(row.state)}>{row.status}</Pill>} />
      <div className="body stack-lg">
        <section className="col">
          <div className="spread"><span className="muted">Amount</span><span>{money(row.amount_minor, row.currency)}</span></div>
          <div className="spread"><span className="muted">Delivery</span><span>{row.method ? METHOD[row.method] : "—"}{row.recommended_method && row.recommended_method !== row.method ? <span className="faint"> (suggested: {METHOD[row.recommended_method]})</span> : null}</span></div>
          <div className="spread"><span className="muted">Address check</span><Pill tone={row.address_check === "valid" ? "good" : row.address_check === "overridden" ? "warn" : "neutral"} plain>{row.address_check}</Pill></div>
          <div className="col" style={{ gap: 6 }}>
            <span className="muted">Evidence</span>
            <Meter value={row.evidence.score} />
            {row.evidence.missing.length > 0 && <span className="faint">Missing: {row.evidence.missing.join(", ")}</span>}
          </div>
        </section>
        {row.state === "PAID" && (
          <section className="card col">
            <h3>Ship this order</h3>
            <label className="field">Tracking number (Shippo test)
              <select value={tracking} onChange={(e) => setTracking(e.target.value)}>
                {TEST_TRACKING.map((t) => <option key={t}>{t}</option>)}
              </select>
            </label>
            {err && <ErrorBox message={err} />}
            <button className="btn primary" disabled={busy} onClick={ship}>Mark shipped</button>
          </section>
        )}
        <section className="col">
          <button className="btn" onClick={buyerLink}>Get the buyer's tracking link</button>
          {link && <a href={link} target="_blank" rel="noreferrer" className="mono" style={{ wordBreak: "break-all" }}>{link}</a>}
        </section>
      </div>
    </>
  );
}
