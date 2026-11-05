import { useEffect, useState } from "react";
import { api, type AgentRun, type CaseView, type OrderRow, type PackingResult, type PolicyAnswer, type RecoveryView, type ScanAudit, type SealCard } from "../api";
import { ErrorBox, Loading, Meter, Pill, Timeline, useAsync } from "../components/ui";
import { FAILURE_POINT, METHOD, ROUTE, money, riskTone, stateTone, when } from "../format";

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

  async function act(action: "approve" | "decline" | "send-evidence") {
    setBusy(true); setErr(null);
    try {
      await api.act(token, caseId, action);
      onChanged(action === "approve" ? "Approved. The refund is on its way through PayPal."
        : action === "send-evidence" ? "Evidence is on its way to the PayPal dispute."
        : "Declined. The buyer will be told.");
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
            {c.counterfactual && (
              <div className="notice info" aria-label="What would have changed this">
                <div className="col" style={{ gap: 2 }}>
                  <strong>{c.counterfactual.possession_proven ? "Possession proof" : "What would have changed this"}</strong>
                  <span>{c.counterfactual.text}</span>
                </div>
              </div>
            )}
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

        {c.scan_audit && <ScanAuditPanel audit={c.scan_audit} />}
        {c.recovery && <RecoveryPanel token={token} recovery={c.recovery} onChanged={(m) => { onChanged(m); reload(); }} />}
        {c.state === "DISPUTE_EVIDENCE" && <DisputeNote c={c} />}

        {c.money.length > 0 && (
          <section className="col">
            <h3>Money</h3>
            {c.money.map((m, i) => (
              <div key={i} className="spread card tight">
                <span>{m.kind === "evidence" ? "Evidence to the PayPal dispute" : `${m.kind.replace("_", " ")} ${money(m.amount_minor, m.currency)}`}</span>
                <span className="row">
                  {m.refund_id && <span className="mono faint">{m.refund_id}</span>}
                  <Pill tone={m.status === "completed" ? "good" : m.status === "simulated" ? "neutral" : m.status === "queued" ? "info" : "warn"}>{m.status}</Pill>
                </span>
              </div>
            ))}
          </section>
        )}

        <PolicyPanel token={token} caseId={c.case_id} />
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
            {c.actions.includes("send-evidence") && (
              <button className="btn primary lg" disabled={busy} onClick={() => act("send-evidence")}>Send evidence to PayPal</button>
            )}
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
          {row.delivery_risk != null && (
            <div className="col" style={{ gap: 4 }}>
              <div className="spread"><span className="muted">Delivery risk</span><Pill tone={riskTone(row.delivery_risk)} plain>{`${(row.delivery_risk * 100).toFixed(1)}%`}</Pill></div>
              {row.risk_reasons.map((r) => <span key={r} className="faint">{r}</span>)}
            </div>
          )}
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
        {row.state !== "CREATED" && <SealPrint token={token} orderId={row.order_id} />}
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
function PolicyPanel({ token, caseId }: { token: string; caseId: string }) {
  const [ans, setAns] = useState<PolicyAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setBusy(true); setErr(null);
    try { setAns(await api.policyAnswer(token, caseId)); }
    catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }
  return (
    <section className="card col" aria-label="What your policy says">
      <h3>What your policy says</h3>
      {!ans && <button className="btn" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={go}>{busy ? "Reading…" : "Read the policy for this case"}</button>}
      {err && <ErrorBox message={err} />}
      {ans && (
        <>
          {ans.mode !== "quoted passages" && <p style={{ margin: 0 }}>{ans.answer}</p>}
          {ans.citations.length > 0 && (
            <ul className="col" style={{ gap: 4, margin: 0, paddingLeft: 18 }}>
              {ans.citations.map((c) => <li key={c.id} className="faint">“{c.text}” <span className="muted">({c.source})</span></li>)}
            </ul>
          )}
          <span className="faint">{ans.mode === "rule file" ? "No matching passage in your policy, so the rule file is quoted." : ans.mode === "model" ? "Answer drawn only from the passages shown." : "Passages quoted as written."} {ans.disclaimer}</span>
        </>
      )}
    </section>
  );
}

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

const AUDIT_VERDICT: Record<ScanAudit["verdict"], { tone: "good" | "warn" | "bad" | "neutral"; label: string; text: string }> = {
  contradicted: { tone: "bad", label: "Contradicted", text: "The carrier's own data contradicts its \"Delivered\" scan." },
  consistent: { tone: "good", label: "Consistent", text: "Possession is proven, and the scan agrees." },
  unverified: { tone: "warn", label: "Unverified", text: "A \"Delivered\" scan, but nothing proves the buyer has the parcel." },
  no_scan: { tone: "neutral", label: "No delivered scan", text: "The carrier never marked it delivered." },
};

const CHECK_NAME: Record<string, string> = { location: "Location", sequence: "Sequence", batch: "Batch scan", reported_soon: "Reported" };
const CHECK_TONE: Record<string, "good" | "warn" | "bad" | "neutral" | "info"> = { contradicts: "bad", supports: "good", unknown: "neutral", context: "info" };

/** SA-01..05: the carrier's "Delivered" scan, checked against the carrier's own data. */
function ScanAuditPanel({ audit }: { audit: ScanAudit }) {
  const v = AUDIT_VERDICT[audit.verdict];
  return (
    <section className="card col" aria-label="Delivered-scan audit">
      <div className="spread">
        <h3>Delivered-scan audit</h3>
        <Pill tone={v.tone}>{v.label}</Pill>
      </div>
      <p className="muted" style={{ margin: 0 }}>{v.text}</p>
      <ul className="checks">
        {audit.checks.map((ch) => (
          <li key={ch.id}>
            <Pill tone={CHECK_TONE[ch.result] ?? "neutral"} plain>{CHECK_NAME[ch.id] ?? ch.id}</Pill>
            <span className="faint">{ch.finding}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

const RECOVERY_STATUS: Record<RecoveryView["status"], { tone: "good" | "warn" | "bad" | "info" | "neutral"; label: string }> = {
  PREPARED: { tone: "info", label: "Ready to submit" },
  SUBMITTED: { tone: "warn", label: "Submitted" },
  PAID: { tone: "good", label: "Carrier paid" },
  DENIED: { tone: "bad", label: "Carrier refused" },
  WITHDRAWN: { tone: "neutral", label: "Withdrawn" },
  EXPIRED: { tone: "neutral", label: "Deadline passed" },
};

function daysLeft(iso: string): number {
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);
}

/** RC-01..05: claim the loss back from the carrier. AI drafts the sentences, code checks every
 * one against the facts; the seller submits on the carrier's own site. */
function RecoveryPanel({ token, recovery: r, onChanged }: { token: string; recovery: RecoveryView; onChanged: (m: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [paying, setPaying] = useState(false);
  const [amount, setAmount] = useState(() => (r.claim_minor / 100).toFixed(2));
  const status = RECOVERY_STATUS[r.status];
  const facts = new Map(r.facts.map((f) => [f.id, f]));
  const left = daysLeft(r.deadline_at);
  const claimText = r.sentences.map((x) => x.text).join(" ");

  async function mark(m: RecoveryView["actions"][number], minor?: number) {
    setBusy(true); setErr(null);
    try {
      await api.recoveryMark(token, r.case_id, m, minor);
      onChanged(m === "submitted" ? "Marked as submitted to the carrier." : m === "paid" ? "Recorded: the carrier paid." : m === "refused" ? "Recorded: the carrier refused." : "Claim withdrawn.");
    } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }

  return (
    <section className="card col" aria-label="Recover from the carrier">
      <div className="spread">
        <h3>Recover from {r.carrier_name}</h3>
        <Pill tone={status.tone}>{status.label}</Pill>
      </div>
      <div className="spread"><span className="muted">Claim</span><strong>{money(r.claim_minor, r.currency)}</strong></div>
      {r.cover_minor != null && <span className="faint">Capped at the carrier's standard cover of {money(r.cover_minor, r.currency)}.</span>}
      {(r.status === "PREPARED" || r.status === "SUBMITTED") && (
        <div className="spread"><span className="muted">Deadline</span><span>{when(r.deadline_at)} · <strong>{left > 0 ? `${left} day${left === 1 ? "" : "s"} left` : "today"}</strong></span></div>
      )}
      {r.recovered_minor != null && <div className="spread"><span className="muted">Recovered</span><strong>{money(r.recovered_minor, r.currency)}</strong></div>}
      {!r.carrier_known && <div className="notice warn">{r.terms_note || "Check your carrier's claim terms; Doorstep has no verified terms for this carrier."}</div>}
      <details>
        <summary>Claim pack ({r.sentences.length} checked sentence{r.sentences.length === 1 ? "" : "s"})</summary>
        <ol className="claim-sentences">
          {r.sentences.map((x, i) => (
            <li key={i}>
              {x.text}
              {x.fact_ids.map((id) => <span key={id} className="fact-chip" title={facts.get(id)?.text ?? id}>{id}</span>)}
            </li>
          ))}
        </ol>
        <p className="faint" style={{ marginTop: 8 }}>
          {r.pack_source === "model" ? "Drafted by AI; every sentence cites facts and was checked in code." : "Built from the facts only."} Terms: {r.terms_rule}.
        </p>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn" onClick={() => navigator.clipboard?.writeText(claimText)}>Copy claim</button>
          {r.terms_url && <a className="btn ghost" href={r.terms_url} target="_blank" rel="noreferrer">Carrier's terms ↗</a>}
        </div>
      </details>
      {err && <ErrorBox message={err} />}
      {paying ? (
        <div className="row">
          <label className="field grow">Amount the carrier paid ({r.currency})
            <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </label>
          <button className="btn primary" disabled={busy} onClick={() => mark("paid", Math.round(Number(amount) * 100))}>Save</button>
          <button className="btn ghost" disabled={busy} onClick={() => setPaying(false)}>Cancel</button>
        </div>
      ) : r.actions.length > 0 && (
        <div className="row wrap">
          {r.actions.includes("withdraw") && <button className="btn ghost" disabled={busy} onClick={() => mark("withdraw")}>Withdraw</button>}
          {r.actions.includes("refused") && <button className="btn" disabled={busy} onClick={() => mark("refused")}>Carrier refused</button>}
          <div className="grow" />
          {r.actions.includes("submitted") && <button className="btn primary" disabled={busy} onClick={() => mark("submitted")}>I submitted it</button>}
          {r.actions.includes("paid") && <button className="btn primary" disabled={busy} onClick={() => setPaying(true)}>Carrier paid</button>}
        </div>
      )}
    </section>
  );
}

/** DE-01: a PayPal dispute is open, so PayPal decides; Doorstep sends the evidence. */
function DisputeNote({ c }: { c: CaseView }) {
  const sent = c.money.find((m) => m.kind === "evidence");
  const done = sent && (sent.status === "completed" || sent.status === "simulated");
  const stuck = sent && (sent.status === "failed" || sent.status === "blocked");
  const tone = !sent ? "info" : done ? "good" : stuck ? "warn" : "info";
  const title = !sent ? "The buyer opened a PayPal dispute"
    : done ? "Evidence sent to PayPal"
    : stuck ? "Evidence was not sent"
    : "Evidence queued for PayPal";
  const text = !sent
    ? "PayPal decides this one. Send the facts: tracking, the seal scan, the scan audit and the verify link. Nothing accuses the buyer."
    : stuck
      ? "PayPal refused it, or the dispute closed first. Nothing was sent twice; check the timeline."
      : "PayPal now decides. The evidence holds facts only: tracking, the seal scan, the scan audit and the receipt link.";
  return (
    <div className={`notice ${tone}`} role="status">
      <div className="col" style={{ gap: 2 }}>
        <strong>{title}</strong>
        <span>{text}{sent?.status === "simulated" ? " (Seeded dispute: simulated.)" : ""}</span>
      </div>
    </div>
  );
}

/** SL-01: the seal to print on the packing slip (the same URL works on an NFC sticker). */
function SealPrint({ token, orderId }: { token: string; orderId: string }) {
  const [card, setCard] = useState<SealCard | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function get() {
    try { setCard(await api.sealCard(token, orderId)); } catch (e) { setErr(String((e as Error).message)); }
  }
  return (
    <section className="card col" aria-label="Possession seal">
      <h3>Possession seal</h3>
      <p className="muted" style={{ margin: 0 }}>
        Put this QR inside the parcel. When the buyer opens it and scans, that proves they hold it,
        which is what the law turns on. No app needed.
      </p>
      {!card && <button className="btn" style={{ alignSelf: "flex-start" }} onClick={get}>Show seal</button>}
      {err && <ErrorBox message={err} />}
      {card && (
        <>
          <div className="seal-print">
            <div dangerouslySetInnerHTML={{ __html: card.svg }} />
            <strong>Opened it? Scan to confirm</strong>
            <span className="faint">Order {card.order_ref}</span>
          </div>
          <div className="row">
            <button className="btn" onClick={() => window.print()}>Print</button>
            <a className="btn ghost" href={card.url} target="_blank" rel="noreferrer">Open seal page ↗</a>
          </div>
        </>
      )}
    </section>
  );
}
