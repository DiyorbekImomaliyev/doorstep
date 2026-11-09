import { useEffect, useState, type ReactNode, type SyntheticEvent } from "react";
import {
  Bot, Camera, CircleCheck, CircleHelp, CircleX, Copy, ExternalLink, FileText, Info, Landmark, Link2,
  ListChecks, MessageSquare, QrCode, RefreshCw, Scale, ScanLine, Send, Truck, Wallet, X, Zap,
} from "lucide-react";
import { api, type AgentRun, type CaseAction, type CaseView, type OrderRow, type PackingResult, type PolicyAnswer, type RecoveryView, type ScanAudit, type SealCard } from "../api";
import { Badge, Bar, ErrorBox, Loading, SectionHead, Timeline, useAsync, usePoll } from "../components/ui";
import { FAILURE_POINT, METHOD, ROUTE, money, pct, riskTone, stateTone, when, type Tone } from "../format";

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
        {row.case_id ? <CaseBody token={token} caseId={row.case_id} protection={row.seller_protection} onClose={onClose} onChanged={onChanged} /> : <OrderBody token={token} row={row} onClose={onClose} onChanged={onChanged} />}
      </aside>
    </>
  );
}

function Header({ title, over, onClose, right }: { title: string; over: string; onClose: () => void; right?: ReactNode }) {
  return (
    <header>
      <div className="col" style={{ gap: 4, minWidth: 0 }}>
        <span className="faint">{over}</span>
        <h2>{title}</h2>
      </div>
      <div className="row" style={{ gap: 8 }}>{right}<button className="btn ghost icon-only" onClick={onClose} aria-label="Close"><X size={18} /></button></div>
    </header>
  );
}

const REASON_TEXT: Record<string, string> = {
  not_received: "Not received", damaged: "Damaged", wrong_item: "Wrong item", missing_item: "Something missing", empty_box: "Empty box",
};

function CaseBody({ token, caseId, protection, onClose, onChanged }: { token: string; caseId: string; protection: string | null; onClose: () => void; onChanged: (m: string) => void }) {
  const { data: c, error, reload } = useAsync(() => api.casePage(token, caseId), [caseId]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // UI-01: an approved refund owns the case until it lands; follow it without a reopen.
  const queued = c?.money.find((m) => (m.kind === "refund" || m.kind === "partial_refund") && m.status === "queued");
  usePoll(reload, !!queued, 2000);

  async function act(action: CaseAction) {
    setBusy(true); setErr(null);
    try {
      await api.act(token, caseId, action);
      onChanged(action === "approve"
          ? (c?.order.simulated ? "Approved. This is a demo order, so the refund is simulated." : "Approved. The refund is on its way through PayPal.")
        : action === "send-evidence" ? "Evidence is on its way to the PayPal dispute."
        : action === "make-offer" ? "Your refund offer is on its way to the PayPal dispute."
        : action === "re-check" ? "Decided again with the latest evidence."
        : "Declined. The buyer will be told.");
      reload();
    } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }

  if (error && !c) return <div className="body"><ErrorBox message={error} /></div>;
  if (!c) return <div className="body col" style={{ paddingTop: 24 }}><Loading /><Loading /></div>;
  const d = c.decision;
  const route = ROUTE[c.state];
  const refund = d?.refund_amount_minor != null ? money(d.refund_amount_minor, d.currency) : null;
  const reported = `${c.order.order_ref} · ${REASON_TEXT[c.reason] ?? c.reason.replaceAll("_", " ")}${c.reported_by === "buyer" ? "" : ` · reported by ${c.reported_by}`}`;

  return (
    <>
      <Header title={c.order.item} over={reported} onClose={onClose} right={route && <Badge tone={route.tone}>{route.label}</Badge>} />
      <div className="body">
        {d && (
          <section className="sec">
            <div className="facts">
              <div><span className="k">Likely cause</span><span className="v">{FAILURE_POINT[d.failure_point ?? ""] ?? d.failure_point ?? "—"}</span></div>
              <div><span className="k">Confidence</span><span className="v" style={{ textTransform: "capitalize" }}>{d.confidence ?? "—"}</span></div>
              <div><span className="k">Refund</span><span className="v num">{refund ?? "—"}</span></div>
            </div>
            <div className="row wrap" style={{ gap: 8 }}>
              <span className="rule-line" title="The rule file and version this decision used">
                <Scale aria-hidden /><span className="mono">{d.rule_id ?? "no matching rule set"}{d.rule_version ? ` · ${d.rule_version}` : ""}</span>
              </span>
              {protection && (
                <span className="rule-line" title="PayPal's own verdict on this payment. Information only; it does not change Doorstep's decision.">
                  Seller Protection: {PROTECTION[protection] ?? protection}
                </span>
              )}
            </div>
            {d.reason_codes.includes("seller_rule") && (
              <div className="notice good" aria-label="Refunded by your autopilot rule">
                <Zap aria-hidden />
                <div className="col" style={{ gap: 2 }}>
                  <strong>Refunded by your autopilot rule</strong>
                  <span className="muted">{cap(d.reasons.find((r) => r.includes("autopilot")) ?? "Within the rule you turned on.")}. Turn it off in the Control Room any time.</span>
                </div>
              </div>
            )}
            {c.counterfactual && (
              <div className="notice accent" aria-label="What would have changed this">
                <Info aria-hidden />
                <div className="col" style={{ gap: 2 }}>
                  <strong>{c.counterfactual.possession_proven ? "Possession proof" : "What would have changed this"}</strong>
                  <span className="muted">{c.counterfactual.text}</span>
                </div>
              </div>
            )}
          </section>
        )}

        {c.state === "DISPUTE_EVIDENCE" && <section className="sec"><DisputeNote c={c} /></section>}
        {c.scan_audit && <ScanAuditPanel audit={c.scan_audit} />}
        {c.recovery && <RecoveryPanel token={token} recovery={c.recovery} onChanged={(m) => { onChanged(m); reload(); }} />}

        {c.money.length > 0 && (
          <section className="sec">
            <SectionHead icon={<Wallet aria-hidden />} title="Money" />
            {c.money.some((m) => m.kind === "offer" && m.status === "unconfirmed") && (
              <div className="notice warn" role="status">
                <Info aria-hidden />
                <span>PayPal did not confirm the refund offer. Check the case in PayPal's Resolution Center. Doorstep never sends an offer twice.</span>
              </div>
            )}
            {c.money.map((m, i) => (
              <div key={i} className="money-row">
                <span>{m.kind === "evidence" ? "Evidence to the PayPal dispute"
                  : m.kind === "offer" ? `Refund offered in the PayPal dispute ${money(m.amount_minor, m.currency)}`
                  : `${m.kind === "partial_refund" ? "Partial refund" : "Refund"} ${money(m.amount_minor, m.currency)}${m.status === "simulated" ? " (demo)" : ""}`}</span>
                <span className="row" style={{ gap: 8 }}>
                  {m.refund_id && <span className="mono faint">{m.refund_id}</span>}
                  <Badge tone={m.status === "completed" || m.status === "simulated" ? "good" : m.status === "failed" || m.status === "blocked" || m.status === "unconfirmed" ? "warn" : "neutral"}>{MONEY_STATUS[m.status] ?? m.status}</Badge>
                </span>
              </div>
            ))}
          </section>
        )}

        {d && (
          <section className="sec">
            <details className="fold">
              <summary><Info aria-hidden />Why{d.explainer_source === "model" ? " (AI explanation)" : ""}</summary>
              <div><p className="prose small">{d.explanation}</p></div>
            </details>
            {d.reasons.length > 0 && (
              <details className="fold">
                <summary><ListChecks aria-hidden />Evidence used ({d.reasons.length})</summary>
                <div>
                  <ul style={{ margin: 0, paddingLeft: 18 }} className="muted">
                    {d.reasons.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                  {d.missing_signals.length > 0 && <p className="faint" style={{ marginTop: 8 }}>Missing: {d.missing_signals.join(", ").replaceAll("_", " ")}</p>}
                </div>
              </details>
            )}
            {d.claim_letter && (
              <details className="fold">
                <summary><FileText aria-hidden />Claim letter (draft)</summary>
                <div className="col" style={{ gap: 10 }}>
                  <p className="prose small">{d.claim_letter}</p>
                  <button className="btn" style={{ alignSelf: "flex-start" }} onClick={() => navigator.clipboard?.writeText(d.claim_letter ?? "")}><Copy size={14} />Copy letter</button>
                </div>
              </details>
            )}
            {d.buyer_message && (
              <details className="fold">
                <summary><MessageSquare aria-hidden />Message to the buyer</summary>
                <div><p className="prose small">{d.buyer_message}</p></div>
              </details>
            )}
          </section>
        )}

        <PolicyPanel token={token} caseId={c.case_id} />
        <AgentPanel token={token} caseId={c.case_id} auto={NEEDS_SELLER.includes(c.state)} onDone={reload} />

        <section className="sec">
          <SectionHead title="Timeline" right={
            <a href={`/verify/${c.case_id}`} target="_blank" rel="noreferrer" className="row" style={{ gap: 5, fontSize: 13 }}>
              {c.receipts} signed receipt{c.receipts === 1 ? "" : "s"} · verify <ExternalLink size={13} aria-hidden />
            </a>
          } />
          <Timeline items={c.timeline} />
        </section>
        <p className="faint" style={{ paddingTop: 4 }}>{c.disclaimer}</p>
      </div>
      {queued && c.actions.length === 0 && (
        <footer className="row" role="status" style={{ gap: 8 }}>
          <RefreshCw size={15} className="spin" aria-hidden />
          <span>
            {c.order.simulated ? "Demo refund" : "Refund"} {money(queued.amount_minor, queued.currency)}{" "}
            {c.order.simulated ? "is being simulated. No real money moves." : "is on its way through PayPal."}
          </span>
        </footer>
      )}
      {c.actions.length > 0 && (
        <footer className="col" style={{ gap: 10 }}>
          {err && <ErrorBox message={err} />}
          <div className="row">
            {c.actions.includes("decline") && <button className="btn ghost danger" disabled={busy} onClick={() => act("decline")}>Decline</button>}
            {c.actions.includes("re-check") && (
              <button className="btn ghost" disabled={busy} onClick={() => act("re-check")} title="Run the decision again on the evidence as it is now">
                <RefreshCw size={15} />Decide again
              </button>
            )}
            <div className="grow" />
            {c.offer && (
              <button className="btn ghost" disabled={busy} onClick={() => act("make-offer")} title="PayPal shows the buyer this offer inside the dispute; PayPal refunds only if the buyer accepts">
                <Wallet size={15} />Offer {money(c.offer.amount_minor, c.offer.currency)} refund in the dispute
              </button>
            )}
            {c.actions.includes("send-evidence") && (
              <button className="btn primary lg" disabled={busy} onClick={() => act("send-evidence")}><Send size={16} />Send evidence to PayPal</button>
            )}
            {c.actions.includes("approve") && (
              <button className="btn primary lg" disabled={busy} onClick={() => act("approve")}>
                Approve {refund ? `refund ${refund}` : ""}
              </button>
            )}
          </div>
          {c.actions.includes("approve") && (
            <span className="faint" style={{ textAlign: "right" }}>{c.order.simulated ? "Demo order: the refund is simulated. No real money moves." : "Paid through PayPal. PayPal keeps its fee."}</span>
          )}
        </footer>
      )}
    </>
  );
}

// Plain words for the money row's status (PR-08: a simulated refund never reads as a real one).
const MONEY_STATUS: Record<string, string> = {
  queued: "On its way", completed: "Sent", simulated: "Simulated", failed: "Failed",
  blocked: "Held for review", unconfirmed: "Not confirmed",
};

const PROTECTION: Record<string, string> = {
  ELIGIBLE: "Eligible",
  PARTIALLY_ELIGIBLE: "Partly eligible",
  NOT_ELIGIBLE: "Not eligible",
};

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
  const addressTone: Tone = row.address_check === "overridden" ? "warn" : "neutral";

  return (
    <>
      <Header title={row.item} over={`${row.order_ref} · ${row.buyer_area || row.buyer_country}`} onClose={onClose} right={<Badge tone={stateTone(row.state)}>{row.status}</Badge>} />
      <div className="body">
        <section className="sec">
          <div className="facts">
            <div><span className="k">Amount</span><span className="v num">{money(row.amount_minor, row.currency)}</span></div>
            <div><span className="k">Delivery</span><span className="v">{row.method ? METHOD[row.method] : "—"}</span></div>
            <div><span className="k">Address check</span><span className="v" style={{ textTransform: "capitalize" }}>{row.address_check}</span></div>
          </div>
          <div className="kv">
            {row.recommended_method && row.recommended_method !== row.method && (
              <><span className="k">Suggested delivery</span><span>{METHOD[row.recommended_method]}</span></>
            )}
            {row.delivery_risk != null && (
              <>
                <span className="k">Delivery risk</span>
                <span className="col" style={{ gap: 2 }}>
                  <span className="num" style={{ fontWeight: 600, color: riskTone(row.delivery_risk) === "bad" ? "var(--bad)" : undefined }}>{pct(row.delivery_risk)}</span>
                  {row.risk_reasons.map((r) => <span key={r} className="faint">{r}</span>)}
                </span>
              </>
            )}
            {row.seller_protection && (
              <>
                <span className="k">PayPal Seller Protection</span>
                <span className="col" style={{ gap: 2 }}>
                  <span style={{ fontWeight: 600 }}>{PROTECTION[row.seller_protection] ?? row.seller_protection}</span>
                  <span className="faint">PayPal's own verdict on this payment. Information only.</span>
                </span>
              </>
            )}
            <span className="k">Evidence</span>
            <span className="col" style={{ gap: 4 }}>
              <Bar value={row.evidence.score} />
              {row.evidence.missing.length > 0 && <span className="faint">Missing: {row.evidence.missing.join(", ")}</span>}
            </span>
            {addressTone === "warn" && <><span className="k" /><span className="faint">The buyer kept an address that failed the check.</span></>}
          </div>
        </section>
        {(row.state === "PAID" || row.state === "PACKED") && <PackingCheck token={token} row={row} onChanged={onChanged} />}
        {(row.state === "PAID" || row.state === "PACKED") && (
          <section className="sec">
            <SectionHead icon={<Truck aria-hidden />} title="Ship this order" />
            {row.state === "PAID" && !row.packing_cleared && (
              <p className="faint">Check the packing photo first.</p>
            )}
            <label className="field">Tracking number (Shippo test)
              <select value={tracking} onChange={(e) => setTracking(e.target.value)}>
                {TEST_TRACKING.map((t) => <option key={t}>{t}</option>)}
              </select>
            </label>
            {err && <ErrorBox message={err} />}
            <button className="btn primary" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={ship}>Mark shipped</button>
          </section>
        )}
        {row.state !== "CREATED" && <SealPrint token={token} orderId={row.order_id} />}
        <section className="sec">
          <SectionHead icon={<Link2 aria-hidden />} title="Buyer's tracking page" />
          {!link && <button className="btn" style={{ alignSelf: "flex-start" }} onClick={buyerLink}>Get the buyer's tracking link</button>}
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
    <section className="sec" aria-label="Packing check">
      <SectionHead icon={<Camera aria-hidden />} title="Packing check" right={<span className="faint">Ordered: {row.item}</span>} />
      <p className="muted">One photo before you seal the box.</p>
      <label className="btn" style={{ alignSelf: "flex-start" }}>
        <Camera size={15} aria-hidden />{busy ? "Checking…" : preview ? "Retake photo" : "Take or choose a photo"}
        <input type="file" accept="image/*" capture="environment" hidden disabled={busy}
          onChange={(e) => onFile(e.target.files?.[0])} data-testid="packing-photo" />
      </label>
      {preview && <img src={preview} alt="Packing photo" style={{ maxWidth: "100%", maxHeight: 220, objectFit: "contain", borderRadius: 10, background: "var(--surface-2)" }} />}
      {shown && (
        <div className={`notice ${VERDICT[shown.verdict].tone}`} role="status">
          {VERDICT[shown.verdict].tone === "good" ? <CircleCheck aria-hidden /> : <CircleHelp aria-hidden />}
          <div className="col" style={{ gap: 4 }}>
            <strong>{VERDICT[shown.verdict].title}</strong>
            <span>{shown.message}</span>
            {shown.reasons.length > 0 && <ul style={{ margin: "2px 0 0", paddingLeft: 18 }}>{shown.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
          </div>
        </div>
      )}
      {err && <ErrorBox message={err} />}
      {flagged && <button className="btn" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={clear}>I checked it, it's right</button>}
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

const AGENT_OUTCOME: Record<string, { tone: Tone; label: string }> = {
  refund_queued: { tone: "good", label: "Refund queued by policy" },
  escalated_to_seller: { tone: "accent", label: "Escalated to you" },
  held_for_review: { tone: "accent", label: "Held for review" },
  dispute_path: { tone: "neutral", label: "Dispute path" },
  not_refundable: { tone: "neutral", label: "Not refundable" },
};

/** RG-01: what the seller's own policy says about this case, with the passages it came from. */
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
    <section className="sec" aria-label="What your policy says">
      <SectionHead icon={<FileText aria-hidden />} title="What your policy says" />
      {!ans && <button className="btn" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={go}>{busy ? "Reading…" : "Read the policy for this case"}</button>}
      {err && <ErrorBox message={err} />}
      {ans && (
        <>
          {ans.mode !== "quoted passages" && <p>{ans.answer}</p>}
          {ans.citations.length > 0 && (
            <ul className="col" style={{ gap: 6, margin: 0, paddingLeft: 18, fontSize: 13 }}>
              {ans.citations.map((c) => <li key={c.id} className="muted">“{c.text}” <span className="faint">({c.source})</span></li>)}
            </ul>
          )}
          <span className="faint">{ans.mode === "rule file" ? "No matching passage in your policy, so the rule file is quoted." : ans.mode === "model" ? "Answer drawn only from the passages shown." : "Passages quoted as written."} {ans.disclaimer}</span>
        </>
      )}
    </section>
  );
}

/** AT-01..05: the agent reads the PayPal side (Agent Toolkit, read-only) and asks the policy. */
const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

/** AT-06: cases whose agent pre-check already started in this tab (the server also reuses runs). */
const AUTO_RAN = new Set<string>();
const NEEDS_SELLER = ["AWAITING_SELLER", "HOLD_FOR_REVIEW", "DISPUTE_EVIDENCE"];

function AgentPanel({ token, caseId, auto, onDone }: { token: string; caseId: string; auto: boolean; onDone: () => void }) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setBusy(true); setErr(null);
    try { setRun(await api.runAgent(token, caseId)); onDone(); }
    catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }
  // The agent works before the seller looks: on a case that needs them, it checks PayPal on its own.
  useEffect(() => {
    if (auto && !AUTO_RAN.has(caseId)) { AUTO_RAN.add(caseId); void go(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, caseId]);
  const outcome = run?.outcome ? AGENT_OUTCOME[run.outcome] : null;
  const shown = run?.steps.filter((s) => s.tool !== "finish") ?? [];
  return (
    <section className="sec" aria-label="Doorstep agent">
      <SectionHead icon={<Bot aria-hidden />} title="Doorstep agent" right={outcome && <Badge tone={outcome.tone}>{outcome.label}</Badge>} />
      <p className="muted" style={{ fontSize: 13 }} title="PayPal Agent Toolkit, read-only. Money goes only through the policy service, within your limits.">
        {run ? "Checked PayPal for you, read-only, then asked your policy. It can't approve for you." : "Checks PayPal for you (read-only), then asks your policy. It can't approve for you."}
      </p>
      {busy && !run && <Loading height={96} />}
      {err && <ErrorBox message={err} />}
      {run && (
        <ol className="checks agent-checks">
          {shown.map((s, i) => (
            <li key={i}>
              <CircleCheck className="r-supports" aria-hidden />
              <span className="col" style={{ gap: 1, minWidth: 0 }}>
                <strong style={{ fontWeight: 600 }}>{AGENT_STEP[s.tool] ?? s.tool}</strong>
                <span className="muted">{s.summary}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
      <div className="row wrap" style={{ gap: 10 }}>
        <button className="btn ghost" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={() => go()}>{busy ? "Checking PayPal…" : run ? "Run the agent again" : "Run the agent"}</button>
        {run && (
          <span className="faint" title={run.planner}>
            {run.planner.startsWith("model") ? "Planned by AI, within code checks." : "Standard checklist (the AI planner was unavailable)."} Every step is in the timeline.
          </span>
        )}
      </div>
    </section>
  );
}

const AUDIT_VERDICT: Record<ScanAudit["verdict"], { tone: Tone; label: string; text: string }> = {
  contradicted: { tone: "bad", label: "Contradicted", text: "The courier's own data disagrees." },
  consistent: { tone: "good", label: "Consistent", text: "Possession proven; the scan agrees." },
  unverified: { tone: "neutral", label: "Unverified", text: "Scanned, but no proof the buyer has it." },
  no_scan: { tone: "neutral", label: "No delivered scan", text: "Never marked delivered." },
};

const CHECK_NAME: Record<string, string> = { location: "Location", sequence: "Sequence", batch: "Batch scan", reported_soon: "Reported" };

function CheckIcon({ result }: { result: string }) {
  if (result === "contradicts") return <CircleX className="r-contradicts" aria-hidden />;
  if (result === "supports") return <CircleCheck className="r-supports" aria-hidden />;
  if (result === "context") return <Info className="r-context" aria-hidden />;
  return <CircleHelp className="r-unknown" aria-hidden />;
}

/** SA-01..05: the carrier's "Delivered" scan, checked against the carrier's own data. */
function ScanAuditPanel({ audit }: { audit: ScanAudit }) {
  const v = AUDIT_VERDICT[audit.verdict];
  return (
    <section className="sec" aria-label="Delivered-scan audit">
      <SectionHead icon={<ScanLine aria-hidden />} title="Courier's “Delivered” scan" right={<Badge tone={v.tone}>{v.label}</Badge>} />
      <p className="muted" style={{ fontSize: 13 }}>{v.text}</p>
      <ul className="checks">
        {audit.checks.map((ch) => (
          <li key={ch.id}>
            <span title={ch.result}><CheckIcon result={ch.result} /></span>
            <span className="name">{CHECK_NAME[ch.id] ?? ch.id}</span>
            <span className="finding">{ch.finding}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

const RECOVERY_STATUS: Record<RecoveryView["status"], { tone: Tone; label: string }> = {
  PREPARED: { tone: "accent", label: "Ready to submit" },
  SUBMITTED: { tone: "neutral", label: "Submitted" },
  PAID: { tone: "good", label: "Carrier paid" },
  DENIED: { tone: "warn", label: "Carrier refused" },
  WITHDRAWN: { tone: "neutral", label: "Withdrawn" },
  EXPIRED: { tone: "neutral", label: "Deadline passed" },
};

function daysLeft(iso: string): number {
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);
}

/** RC-01..05: claim the loss back from the carrier. AI drafts the sentences, code checks every
 * one against the facts; the seller submits on the carrier's own site. */
function RecoveryPanel({ token, recovery, onChanged }: { token: string; recovery: RecoveryView; onChanged: (m: string) => void }) {
  // RC-08: a claim pack built from the facts is drafted by AI the first time it is opened.
  const [r, setR] = useState(recovery);
  useEffect(() => setR(recovery), [recovery]);
  const [drafting, setDrafting] = useState(false);
  const [tried, setTried] = useState(false);
  async function draftOnOpen(e: SyntheticEvent<HTMLDetailsElement>) {
    if (!e.currentTarget.open || tried || r.pack_source === "model" || r.status !== "PREPARED") return;
    setTried(true); setDrafting(true);
    try {
      const drafted = await api.recoveryDraft(token, r.case_id);
      setR((cur) => (cur.status === "PREPARED" ? drafted : cur)); // never over a claim already sent
    } catch { /* the fact-only pack stays */ } finally { setDrafting(false); }
  }
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
    <section className="sec" aria-label="Recover from the carrier">
      <SectionHead icon={<Landmark aria-hidden />} title={r.carrier_known ? `Recover from ${r.carrier_name}` : "Recover from the carrier"} right={<Badge tone={status.tone}>{status.label}</Badge>} />
      <div className="kv">
        <span className="k">Claim</span><strong className="num">{money(r.claim_minor, r.currency)}</strong>
        {(r.status === "PREPARED" || r.status === "SUBMITTED") && (
          <><span className="k">Deadline</span><span>{when(r.deadline_at)} · <strong>{left > 0 ? `${left} day${left === 1 ? "" : "s"} left` : "today"}</strong></span></>
        )}
        {r.recovered_minor != null && <><span className="k">Recovered</span><strong className="num">{money(r.recovered_minor, r.currency)}</strong></>}
      </div>
      {r.cover_minor != null && <span className="faint">Capped at the carrier's cover ({money(r.cover_minor, r.currency)}).</span>}
      {!r.carrier_known && <div className="notice warn"><Info aria-hidden /><span>{r.terms_note || "Check your carrier's claim terms; Doorstep has no verified terms for this carrier."}</span></div>}
      <details className="fold" onToggle={draftOnOpen}>
        <summary><FileText aria-hidden />Claim pack ({r.sentences.length} checked sentence{r.sentences.length === 1 ? "" : "s"})</summary>
        <div className="col" style={{ gap: 10 }}>
          {drafting ? (
            <div className="col" style={{ gap: 8 }} aria-live="polite">
              <span className="muted">AI is writing this claim from the facts; code checks every sentence…</span>
              <Loading height={120} />
            </div>
          ) : (
          <ol className="claim-sentences">
            {r.sentences.map((x, i) => (
              <li key={i}>
                {x.text}
                {x.fact_ids.map((id) => <span key={id} className="fact-chip" title={facts.get(id)?.text ?? id}>{id}</span>)}
              </li>
            ))}
          </ol>
          )}
          {!drafting && (
            <p className="faint">
              {r.pack_source === "model" ? "AI draft; every sentence checked against the facts." : tried ? "AI unavailable right now; built from the facts only." : "Built from the facts only."} Terms: {r.terms_rule}.
            </p>
          )}
          <div className="row">
            <button className="btn" disabled={drafting} onClick={() => navigator.clipboard?.writeText(claimText)}><Copy size={14} />Copy claim</button>
            {r.terms_url && <a className="btn ghost" href={r.terms_url} target="_blank" rel="noreferrer">Carrier's terms <ExternalLink size={13} /></a>}
          </div>
        </div>
      </details>
      {err && <ErrorBox message={err} />}
      {paying ? (
        <div className="row" style={{ alignItems: "flex-end" }}>
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
  const tone = !sent ? "accent" : done ? "good" : stuck ? "warn" : "accent";
  const title = !sent ? "The buyer opened a PayPal dispute"
    : done ? "Evidence sent to PayPal"
    : stuck ? "Evidence was not sent"
    : "Evidence queued for PayPal";
  const text = !sent
    ? c.offer
      ? "PayPal decides. Send the facts, or offer the refund inside the dispute. Nothing accuses the buyer."
      : "PayPal decides. Send the facts; nothing accuses the buyer."
    : stuck
      ? "PayPal refused it or the dispute closed. Nothing was sent twice."
      : "PayPal decides now. Facts only: tracking, seal scan, receipt.";
  return (
    <div className={`notice ${tone}`} role="status">
      {done ? <CircleCheck aria-hidden /> : <Scale aria-hidden />}
      <div className="col" style={{ gap: 2 }}>
        <strong>{title}</strong>
        <span className="muted">{text}{sent?.status === "simulated" ? " (Demo dispute: simulated.)" : ""}</span>
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
    <section className="sec" aria-label="Possession seal">
      {/* Only the QR itself is an SVG in this section (SL-02 checks for one). */}
      <SectionHead title="Possession seal" />
      <p className="muted" style={{ fontSize: 13 }} title="No app needed. Scans from your own browser never count as proof.">
        Put it inside the parcel. The buyer's scan proves they hold it.
      </p>
      {!card && <button className="btn" style={{ alignSelf: "flex-start" }} onClick={get}><QrCode size={15} />Show seal</button>}
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
            <a className="btn ghost" href={card.url} target="_blank" rel="noreferrer">Open seal page</a>
          </div>
        </>
      )}
    </section>
  );
}
