import { useEffect, useState } from "react";
import { api, type AgentRun, type OrderRow, type PackingResult } from "../api";
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

        <AgentPanel token={token} caseId={c.case_id} onDone={reload} />

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
        {(row.state === "PAID" || row.state === "PACKED") && <PackingCheck token={token} row={row} onChanged={onChanged} />}
        {(row.state === "PAID" || row.state === "PACKED") && (
          <section className="card col">
            <h3>Ship this order</h3>
            {row.state === "PAID" && !row.packing_cleared && (
              <p className="faint">Tip: check the packing photo first. It is the proof of what you sent.</p>
            )}
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

const VERDICT: Record<PackingResult["verdict"], { tone: "good" | "warn"; title: string }> = {
  match: { tone: "good", title: "Matches the order" },
  mismatch: { tone: "warn", title: "Check before shipping" },
  check: { tone: "warn", title: "Please check it yourself" },
};

const SENDABLE = ["image/jpeg", "image/png", "image/webp"];

function fileToB64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(new Error("Could not read the photo."));
    reader.readAsDataURL(file);
  });
}

/** A phone photo shrunk to at most 1600 px on its long side, as JPEG (fast upload, fewer
 * tokens). If the browser cannot decode it, the original is sent when its type is supported. */
async function preparePhoto(file: File): Promise<{ b64: string; type: string }> {
  try {
    return { b64: await photoToJpegB64(file), type: "image/jpeg" };
  } catch {
    if (SENDABLE.includes(file.type)) return { b64: await fileToB64(file), type: file.type };
    throw new Error("This photo format isn't supported. Please use JPEG or PNG.");
  }
}

async function photoToJpegB64(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.85).split(",")[1];
}

function PackingCheck({ token, row, onChanged }: { token: string; row: OrderRow; onChanged: (m: string) => void }) {
  const [preview, setPreview] = useState<string | null>(null);
  const [result, setResult] = useState<PackingResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const flagged = result ? result.verdict !== "match" : row.packing_verdict !== null && row.packing_verdict !== "match";

  async function onFile(file: File | undefined) {
    if (!file) return;
    setErr(null); setResult(null); setBusy(true);
    setPreview(URL.createObjectURL(file));
    try {
      const photo = await preparePhoto(file);
      const r = await api.packingCheck(token, row.order_id, photo.b64, photo.type);
      setResult(r);
      onChanged(r.verdict === "match" ? "Packing photo matches. Ready to ship." : "Packing photo needs a look before shipping.");
    } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }
  async function clear() {
    setBusy(true); setErr(null);
    try { await api.packingClear(token, row.order_id); onChanged("Marked as checked. Ready to ship."); }
    catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }

  const shown = result ?? (row.packing_verdict ? { verdict: row.packing_verdict, message: row.packing_message ?? "", reasons: [] as string[] } : null);
  return (
    <section className="card col" aria-label="Packing check">
      <div className="spread">
        <h3>Packing check</h3>
        <span className="faint">Ordered: {row.item}</span>
      </div>
      <p className="muted" style={{ margin: 0 }}>Take one photo of the item before you seal the box. We compare it with the order.</p>
      <label className="btn" style={{ alignSelf: "flex-start" }}>
        {busy ? "Checking…" : preview ? "Retake photo" : "Take or choose a photo"}
        <input type="file" accept="image/*" capture="environment" hidden disabled={busy}
          onChange={(e) => onFile(e.target.files?.[0])} data-testid="packing-photo" />
      </label>
      {preview && <img src={preview} alt="Packing photo" style={{ maxWidth: "100%", maxHeight: 220, objectFit: "contain", borderRadius: 10, background: "var(--surface-2)" }} />}
      {shown && (
        <div className={`notice ${VERDICT[shown.verdict].tone}`} role="status">
          <div className="col" style={{ gap: 4 }}>
            <strong>{VERDICT[shown.verdict].title}</strong>
            <span>{shown.message}</span>
            {shown.reasons.length > 0 && <ul style={{ margin: "2px 0 0", paddingLeft: 18 }}>{shown.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
          </div>
        </div>
      )}
      {err && <ErrorBox message={err} />}
      {flagged && <button className="btn" disabled={busy} onClick={clear}>I checked it, it's right</button>}
    </section>
  );
}

const AGENT_STEP: Record<string, string> = {
  get_order_details: "Read the PayPal order",
  get_shipment_tracking: "Read tracking on the PayPal payment",
  list_disputes: "Checked PayPal disputes",
  get_dispute: "Read the PayPal dispute",
  request_refund: "Asked Doorstep's policy",
  finish: "Finished",
};

const AGENT_OUTCOME: Record<string, { tone: "good" | "warn" | "info" | "neutral"; label: string }> = {
  refund_queued: { tone: "good", label: "Refund queued by policy" },
  escalated_to_seller: { tone: "warn", label: "Escalated to you" },
  held_for_review: { tone: "warn", label: "Held for review" },
  dispute_path: { tone: "info", label: "Dispute path" },
  not_refundable: { tone: "neutral", label: "Not refundable" },
};

/** AT-01..05: the agent reads the PayPal side (Agent Toolkit, read-only) and asks the policy. */
function AgentPanel({ token, caseId, onDone }: { token: string; caseId: string; onDone: () => void }) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setBusy(true); setErr(null);
    try { setRun(await api.runAgent(token, caseId)); onDone(); }
    catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }
  const outcome = run?.outcome ? AGENT_OUTCOME[run.outcome] : null;
  return (
    <section className="card col" aria-label="Doorstep agent">
      <div className="spread">
        <h3>Doorstep agent</h3>
        {outcome && <Pill tone={outcome.tone}>{outcome.label}</Pill>}
      </div>
      <p className="muted" style={{ margin: 0 }}>
        Checks the PayPal order, tracking and disputes with PayPal's Agent Toolkit (read-only), then asks
        Doorstep's policy. It can't refund above your limit or approve anything for you.
      </p>
      {!run && <button className="btn" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={go}>{busy ? "Working…" : "Run the agent"}</button>}
      {err && <ErrorBox message={err} />}
      {run && (
        <ol className="agent-steps">
          {run.steps.map((s, i) => (
            <li key={i}>
              <strong>{AGENT_STEP[s.tool] ?? s.tool}</strong>
              <span className="muted">{s.summary}</span>
            </li>
          ))}
        </ol>
      )}
      {run && <span className="faint">Planner: {run.planner}. Every step is in the timeline below.</span>}
    </section>
  );
}
