import { useState } from "react";
import { ArrowUpRight, CircleCheck, CircleDashed, CircleMinus, CircleX, ExternalLink, FileText, ListChecks, Send } from "lucide-react";
import { Badge } from "../../components/ui";
import { FAILURE_POINT, ago, money, when, type Tone } from "../../format";
import { CopyId } from "../LedgerGrid";
import type { CheckId, CheckState, DeskEvent, DeskRow, Stage } from "./disputesApi";

// ---------- vocabulary: PayPal's and Doorstep's codes in a seller's words ----------

const CLAIM: Record<string, string> = {
  not_received: "Not received", damaged: "Damaged", wrong_item: "Wrong item", missing_item: "Item missing", empty_box: "Empty box",
};
/** PayPal's dispute reason codes (Disputes API v1), shown only when PayPal sent one. */
const PAYPAL_REASON: Record<string, string> = {
  MERCHANDISE_OR_SERVICE_NOT_RECEIVED: "Not received",
  MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED: "Not as described",
  UNAUTHORISED: "Unauthorised",
  CREDIT_NOT_PROCESSED: "Credit not processed",
  DUPLICATE_TRANSACTION: "Duplicate payment",
  INCORRECT_AMOUNT: "Incorrect amount",
  PAYMENT_BY_OTHER_MEANS: "Paid another way",
  CANCELED_RECURRING_BILLING: "Cancelled billing",
  PROBLEM_WITH_REMITTANCE: "Remittance problem",
  OTHER: "Other",
};
const SCAN: Record<string, string> = { consistent: "Consistent", contradicted: "Contradicted", unverified: "Unverified", no_scan: "No delivered scan" };
export const CHECK_LABEL: Record<CheckId, string> = {
  verdict: "PayPal verdict", address: "Shipped to PayPal address", delivered: "Delivered scan", tracking: "Tracking on PayPal", replied: "Replied in dispute",
};
const CHECK_TIP: Record<CheckId, string> = {
  verdict: "PayPal's own Seller Protection status on this payment. Information only.",
  address: "The label went to the address on the PayPal order (fields from paypal_sp_v1).",
  delivered: "The carrier's delivered scan is on file.",
  tracking: "Tracking was added to the PayPal payment through the outbox.",
  replied: "Evidence or a refund offer reached the PayPal dispute.",
};
export const SEGMENTS: { key: Stage | "all"; label: string; tip: string }[] = [
  { key: "all", label: "All", tip: "Every dispute" },
  { key: "needs_reply", label: "Needs reply", tip: "Open, nothing sent to PayPal yet" },
  { key: "evidence", label: "Evidence sent", tip: "Evidence queued or sent to PayPal" },
  { key: "offer", label: "Offer made", tip: "A refund offer inside the dispute" },
  { key: "resolved", label: "Resolved", tip: "PayPal decided" },
];

const words = (code: string | null | undefined) => (code ? code.replaceAll("_", " ").toLowerCase() : "");
const cap = (t: string) => (t ? t.charAt(0).toUpperCase() + t.slice(1) : t);

export function reasonText(r: DeskRow): string {
  if (r.paypal.reason) return PAYPAL_REASON[r.paypal.reason] ?? cap(words(r.paypal.reason));
  return r.claim ? CLAIM[r.claim] ?? cap(words(r.claim)) : "—";
}
export const causeText = (r: DeskRow) => (r.failure_point ? FAILURE_POINT[r.failure_point] ?? cap(words(r.failure_point)) : "");

export function stageOf(r: DeskRow): { label: string; tone: Tone } {
  if (r.stage === "resolved") {
    const res = r.outcome?.result;
    return res === "won" ? { label: "Won", tone: "good" } : res === "lost" ? { label: "Lost", tone: "bad" } : { label: "Resolved", tone: "neutral" };
  }
  if (r.stage === "offer") return r.offer?.status === "queued" ? { label: "Offer queued", tone: "neutral" } : { label: "Offer made", tone: "neutral" };
  if (r.stage === "evidence") return r.evidence?.status === "queued" ? { label: "Evidence queued", tone: "neutral" } : { label: "Evidence sent", tone: "neutral" };
  return { label: "Needs reply", tone: "accent" };
}

export function StagePill({ r }: { r: DeskRow }) {
  const s = stageOf(r);
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

/** The row's one-click action: everything goes through the case drawer's policy-gated flow. */
export function actionOf(r: DeskRow): { label: string; primary: boolean } {
  if (r.can_send_evidence && r.recommended !== "offer") return { label: "Send evidence", primary: true };
  if (r.recommended === "offer") return { label: "Review offer", primary: true };
  return { label: r.case_id ? "Open case" : "Open order", primary: false };
}

/** Long ids keep their head and tail: "SIM-DISPUTE-…822a". The full id is in the tooltip and copy. */
export const shortId = (id: string) => (id.length > 18 ? `${id.slice(0, 12)}…${id.slice(-4)}` : id);

export function daysLeft(iso: string): number {
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);
}

const dueTone = (iso: string) => (daysLeft(iso) <= 3 ? "bad" : "neutral");

export function ReplyBy({ iso }: { iso: string | null }) {
  if (!iso) return <span className="faint">—</span>;
  const n = daysLeft(iso);
  return (
    <span className={`dd-due ${dueTone(iso)}`} title={`PayPal's reply-by date: ${new Date(iso).toLocaleString()}`}>
      <span className="num">{new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
      <span className="dd-due-left num">{n > 0 ? `${n} d left` : n === 0 ? "today" : "passed"}</span>
    </span>
  );
}

/** Five checks as five small segments, with the count. */
export function ProtectionMeter({ r }: { r: DeskRow }) {
  const p = r.protection;
  const tip = p.checks.map((c) => `${CHECK_LABEL[c.id]}: ${c.state === "pass" ? "yes" : c.detail}`).join("\n");
  return (
    <span className="dd-meter" role="img" aria-label={`Seller Protection: ${p.met} of ${p.total} checks met`} title={tip}>
      <span className="dd-segs" aria-hidden>{p.checks.map((c) => <i key={c.id} className={`s-${c.state}`} />)}</span>
      <span className="num dd-met">{p.met}/{p.total}</span>
    </span>
  );
}

function CheckIcon({ state }: { state: CheckState }) {
  if (state === "pass") return <CircleCheck className="c-pass" aria-label="Met" />;
  if (state === "fail") return <CircleX className="c-fail" aria-label="Failed" />;
  if (state === "partial") return <CircleMinus className="c-partial" aria-label="Partly" />;
  return <CircleDashed className="c-missing" aria-label="Missing" />;
}

const MONEY_STATUS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: "On its way", tone: "neutral" }, completed: { label: "Sent", tone: "good" }, simulated: { label: "Simulated", tone: "good" },
  failed: { label: "Not sent", tone: "warn" }, blocked: { label: "Not sent", tone: "warn" }, unconfirmed: { label: "Not confirmed", tone: "warn" },
};
const moneyStatus = (s: string) => MONEY_STATUS[s] ?? { label: cap(words(s)), tone: "neutral" as Tone };

const MAX_EVENTS = 6;

function DeskTimeline({ items }: { items: DeskEvent[] }) {
  const [all, setAll] = useState(false);
  const newest = [...items].reverse();
  const shown = all ? newest : newest.slice(0, MAX_EVENTS);
  return (
    <section className="detail-timeline dd-tl" aria-label="Dispute timeline">
      <h4>Timeline</h4>
      {items.length === 0 ? <p className="faint">Nothing yet.</p> : (
        <ol className="dd-events">
          {shown.map((e, i) => (
            <li key={`${e.at}-${i}`} className={`k-${e.kind}`}>
              <span className="dd-dot" aria-hidden />
              <span className="dd-ev">
                <span className="dd-ev-title">{e.title}</span>
                {e.detail && <span className="dd-ev-detail" title={e.detail}>{e.detail}</span>}
              </span>
              <span className="dd-ev-when num" title={new Date(e.at).toLocaleString()}>{ago(e.at)}</span>
            </li>
          ))}
        </ol>
      )}
      {items.length > MAX_EVENTS && (
        <button type="button" className="btn ghost dd-more" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? "Show less" : `Show all ${items.length}`}
        </button>
      )}
    </section>
  );
}

function RouteSection({ r }: { r: DeskRow }) {
  const inDispute = r.case_state === "DISPUTE_EVIDENCE";
  const route = r.stage === "evidence" ? { label: "Evidence to PayPal", tone: "neutral" as Tone }
    : r.stage === "offer" ? { label: "Refund offer", tone: "neutral" as Tone }
    : r.stage === "resolved" ? { label: "PayPal decided", tone: "good" as Tone }
    : r.recommended === "offer" ? { label: "Offer the refund", tone: "accent" as Tone }
    : r.recommended === "evidence" ? { label: "Send evidence", tone: "accent" as Tone }
    : { label: r.case_id ? "Your call" : "No case yet", tone: "neutral" as Tone };
  const why = r.recommended === "offer" ? "Courier scan contradicted" : inDispute || r.stage !== "needs_reply" ? "PayPal decides open disputes" : "";
  return (
    <section aria-label="Route">
      <h4>{r.stage === "needs_reply" ? "Recommended" : "Route"}</h4>
      <Badge tone={route.tone}>{route.label}</Badge>
      <dl className="mini-kv dd-kv">
        {why && <><dt>Why</dt><dd title="Doorstep never refunds beside an open PayPal dispute; it sends PayPal the facts.">{why}</dd></>}
        {r.failure_point && <><dt>Cause</dt><dd>{causeText(r)}{r.confidence ? ` · ${cap(r.confidence)}` : ""}</dd></>}
        {r.scan_verdict && <><dt>Scan audit</dt><dd>{SCAN[r.scan_verdict] ?? cap(words(r.scan_verdict))}</dd></>}
        {r.rule_id && <><dt>Rule</dt><dd><code className="rule-id" title={`Rule file ${r.rule_id}, version ${r.rule_version ?? "?"}`}>{r.rule_id}</code></dd></>}
      </dl>
      {r.reasons.length > 0 && (
        <details className="dd-fold">
          <summary><ListChecks aria-hidden />Evidence used ({r.reasons.length})</summary>
          <ul className="reasons">{r.reasons.map((x) => <li key={x}>{cap(x)}</li>)}</ul>
        </details>
      )}
    </section>
  );
}

function ProtectionSection({ r }: { r: DeskRow }) {
  const p = r.protection;
  return (
    <section aria-label="Seller Protection">
      <h4>Seller Protection</h4>
      <ul className="dd-checks">
        {p.checks.map((c) => (
          <li key={c.id} className={`s-${c.state}`} title={CHECK_TIP[c.id]}>
            <CheckIcon state={c.state} />
            <span className="dd-check-name">{CHECK_LABEL[c.id]}</span>
            {c.state !== "pass" && <span className="dd-check-detail">{c.detail}</span>}
          </li>
        ))}
      </ul>
      <p className="dd-met-line"><strong className="num">{p.met} of {p.total}</strong> met
        {p.categories.length > 0 && <span className="faint" title="Categories PayPal covers on this payment"> · covers {p.categories.map(words).join(", ")}</span>}
      </p>
    </section>
  );
}

function EvidenceSection({ r, onOpen }: { r: DeskRow; onOpen: () => void }) {
  const ev = r.evidence, of = r.offer;
  const act = actionOf(r);
  return (
    <section aria-label="Evidence sent">
      <h4>Sent to PayPal</h4>
      {!ev && !of && (
        <div className="col" style={{ gap: 10 }}>
          <span className="faint">Nothing sent yet</span>
          {r.stage !== "resolved" && (
            <button type="button" className="btn dd-act" onClick={onOpen}>
              {act.primary && act.label === "Send evidence" ? <Send size={14} aria-hidden /> : <ArrowUpRight size={14} aria-hidden />}{act.label}
            </button>
          )}
        </div>
      )}
      {ev && (
        <div className="dd-sent">
          <div className="row" style={{ gap: 8 }}>
            <strong className="dd-sent-what">{ev.evidence_type === "PROOF_OF_FULFILLMENT" ? "Proof of fulfilment" : "Evidence"}</strong>
            <Badge tone={moneyStatus(ev.status).tone}>{moneyStatus(ev.status).label}</Badge>
          </div>
          <dl className="mini-kv dd-kv">
            <dt>{ev.sent_at ? "Sent" : "Queued"}</dt><dd className="num">{when(ev.sent_at ?? ev.queued_at)}</dd>
            {ev.carrier && <><dt>Tracking</dt><dd>{ev.carrier}{ev.tracking_number ? ` · ${ev.tracking_number}` : ""}</dd></>}
            <dt title="PayPal-Request-Id: a retry reuses it, so PayPal never gets the evidence twice">Request ID</dt>
            <dd><CopyId value={ev.request_id} /></dd>
          </dl>
          {ev.notes.length > 0 && (
            <details className="dd-fold">
              <summary><FileText aria-hidden />Notes sent ({ev.notes.length})</summary>
              <ul className="dd-notes">{ev.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
            </details>
          )}
        </div>
      )}
      {of && (
        <div className="dd-sent">
          <div className="row" style={{ gap: 8 }}>
            <strong className="dd-sent-what num">Offer {money(of.amount_minor, of.currency)}</strong>
            <Badge tone={moneyStatus(of.status).tone}>{moneyStatus(of.status).label}</Badge>
          </div>
          <dl className="mini-kv dd-kv">
            <dt>{of.sent_at ? "Made" : "Queued"}</dt><dd className="num">{when(of.sent_at ?? of.queued_at)}</dd>
            <dt>Request ID</dt><dd><CopyId value={of.request_id} /></dd>
          </dl>
        </div>
      )}
      {(ev || of) && r.stage !== "resolved" && (
        <button type="button" className="btn ghost dd-act" onClick={onOpen}><ArrowUpRight size={14} aria-hidden />Open case</button>
      )}
      {r.receipts > 0 && r.case_id && (
        <a className="lg-link" href={`/verify/${r.case_id}`} target="_blank" rel="noreferrer">
          {r.receipts} signed receipt{r.receipts === 1 ? "" : "s"}<ExternalLink aria-hidden />
        </a>
      )}
    </section>
  );
}

function PayPalSection({ r }: { r: DeskRow }) {
  const f = r.paypal;
  return (
    <section aria-label="At PayPal">
      <h4>At PayPal</h4>
      <div className="dd-ppid"><CopyId value={r.paypal_dispute_id} shown={shortId(r.paypal_dispute_id)} /></div>
      <dl className="mini-kv dd-kv">
        {f.status && <><dt>Status</dt><dd>{cap(words(f.status))}</dd></>}
        {f.stage && <><dt>Stage</dt><dd>{cap(words(f.stage))}</dd></>}
        {f.response_due_at && <><dt>Reply by</dt><dd><ReplyBy iso={f.response_due_at} /></dd></>}
        <dt>Amount</dt><dd className="num" title={r.amount_from === "paypal" ? "PayPal's disputed amount" : "The order amount; PayPal sent no disputed amount"}>{money(r.amount_minor, r.currency)}</dd>
        <dt>Opened</dt><dd className="num">{when(r.opened_at)}</dd>
        {r.outcome && <><dt>Outcome</dt><dd>{r.outcome.code ? cap(words(r.outcome.code)) : "Resolved at PayPal"}</dd></>}
      </dl>
      {r.simulated ? <Badge tone="neutral">Demo · simulated</Badge>
        : f.events === 0 && <span className="faint">No PayPal update yet</span>}
    </section>
  );
}

/** DS-05: the dispute's full story under its row (or under its card on a phone). */
export function DisputeDetail({ r, onOpen }: { r: DeskRow; onOpen: () => void }) {
  return (
    <div className="detail-inner dd-detail" data-dispute={r.paypal_dispute_id}>
      <RouteSection r={r} />
      <ProtectionSection r={r} />
      <EvidenceSection r={r} onOpen={onOpen} />
      <PayPalSection r={r} />
      <DeskTimeline items={r.timeline} />
    </div>
  );
}
