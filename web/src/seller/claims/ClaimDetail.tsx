import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Check, Copy, ExternalLink, Sparkles, TriangleAlert } from "lucide-react";
import { Badge, ErrorBox } from "../../components/ui";
import { ROUTE, money } from "../../format";
import { CopyId } from "../LedgerGrid";
import { claimsApi, type DeskEvent, type DeskRow } from "./claimsApi";
import { Hint, symbolOf, useDesk } from "./desk";
import { FAULT, carrierOf, daysLeft, plural, shortDate, statusOf, urgency } from "./model";

/** Claims whose fact-only pack was already sent to the model this session (one try per claim). */
const asked = new Set<string>();

const BY: Record<DeskEvent["by"], string> = { you: "You", doorstep: "Doorstep", carrier: "Carrier scan", demo: "Demo" };

type Step = { key: string; label: string; at: string | null; by?: DeskEvent["by"]; note?: string | null; tone: "done" | "todo" | "good" | "bad" | "muted" };

/** Prepared, submitted, then paid or refused; or prepared then withdrawn or expired. */
function steps(r: DeskRow): Step[] {
  const ev = (s: string) => r.history.filter((e) => e.status === s).at(-1);
  const prepared = ev("PREPARED");
  const out: Step[] = [{ key: "PREPARED", label: "Prepared", at: prepared?.at ?? r.created_at, by: prepared?.by ?? "doorstep", tone: "done" }];
  if (r.status === "WITHDRAWN" || r.status === "EXPIRED") {
    const e = ev(r.status);
    out.push({ key: r.status, label: statusOf(r.status).label, at: e?.at ?? r.resolved_at, by: e?.by, note: e?.note, tone: "muted" });
    return out;
  }
  const sub = ev("SUBMITTED");
  out.push({ key: "SUBMITTED", label: "Submitted", at: sub?.at ?? r.submitted_at, by: sub?.by, tone: r.submitted_at ? "done" : "todo" });
  if (r.status === "DENIED") {
    const e = ev("DENIED");
    out.push({ key: "DENIED", label: "Refused", at: e?.at ?? r.resolved_at, by: e?.by, tone: "bad" });
  } else {
    const e = ev("PAID");
    const paid = r.status === "PAID";
    out.push({ key: "PAID", label: paid ? `Paid ${money(r.recovered_minor, r.currency)}` : "Paid", at: paid ? e?.at ?? r.resolved_at : null, by: e?.by, tone: paid ? "good" : "todo" });
  }
  return out;
}

function ClaimPack({ r }: { r: DeskRow }) {
  const desk = useDesk();
  const [drafting, setDrafting] = useState(false);
  const [copied, setCopied] = useState(false);
  // RC-08: a fact-only pack is drafted by the model the first time it is opened; code checks each sentence.
  useEffect(() => {
    if (r.status !== "PREPARED" || r.pack_source === "model" || asked.has(r.recovery_id)) return;
    asked.add(r.recovery_id);
    let live = true;
    setDrafting(true);
    claimsApi.draft(desk.token, r.case_id)
      .then((v) => desk.drafted(r.recovery_id, v))
      .catch(() => { /* the fact-only pack stays */ })
      .finally(() => { if (live) setDrafting(false); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [r.recovery_id]);
  const facts = new Map(r.facts.map((f) => [f.id, f.text]));
  const text = r.sentences.map((x) => x.text).join(" ");
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 1600); } catch { /* clipboard blocked */ }
  };
  return (
    <section className="cd-pack" aria-label="Claim pack" aria-busy={drafting || undefined}>
      <div className="cd-sec-head">
        <h4>Claim pack</h4>
        {drafting ? <Badge tone="accent" icon={<Sparkles aria-hidden />}>Drafting</Badge>
          : r.pack_source === "model" ? <Badge tone="accent" icon={<Sparkles aria-hidden />}>AI · checked</Badge>
          : <Badge>From facts</Badge>}
        <Hint text={r.pack_source === "model" ? "AI wrote it from the facts; code checked every sentence against them." : "Built from the stored facts only."} />
      </div>
      {drafting && <div className="ds-sweep cd-sweep" aria-hidden />}
      <ol className="claim-sentences cd-sentences">
        {r.sentences.map((x, i) => (
          <li key={i}>
            {x.text}
            {x.fact_ids.map((id) => <span key={id} className="fact-chip" title={facts.get(id) ?? id}>{id}</span>)}
          </li>
        ))}
      </ol>
      <div className="cd-row">
        <button type="button" className="btn" onClick={copy} disabled={!text}>
          {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}{copied ? "Copied" : "Copy claim"}
        </button>
        {r.terms_url && <a className="btn ghost" href={r.terms_url} target="_blank" rel="noreferrer">Carrier terms<ExternalLink size={13} aria-hidden /></a>}
      </div>
    </section>
  );
}

function Deadline({ r }: { r: DeskRow }) {
  const { now } = useDesk();
  const d = daysLeft(r, now);
  const u = urgency(r, now);
  const used = r.window_days ? Math.min(100, Math.max(0, ((r.window_days - d) / r.window_days) * 100)) : 0;
  const open = r.status === "PREPARED";
  return (
    <section aria-label="Deadline">
      <h4>Deadline</h4>
      <div className="cd-big">
        <strong>{shortDate(r.deadline_at)}</strong>
        {open ? <span className={`cd-left u-${u}`}>{d === 0 ? "Due today" : `${plural(d, "day")} left`}</span>
          : <span className="faint">{r.status === "SUBMITTED" ? `Filed ${shortDate(r.submitted_at ?? r.deadline_at)}` : "Closed"}</span>}
      </div>
      {r.window_days > 0 && (
        <div className="cd-window" title={`${r.window_days}-day window from ${r.window_starts || "posting"}`}>
          <span className={`cd-window-fill u-${open ? u : "filed"}`} style={{ width: `${open ? used : 100}%` }} />
        </div>
      )}
      <span className="faint">{r.window_days}-day window · from {r.window_starts || "posting"}</span>
      <h4 className="cd-h4-gap">Carrier</h4>
      <div className="cd-kv">
        <span className="k">Name</span><span>{carrierOf(r)}</span>
        <span className="k">Cover</span>
        <span className="num">{r.cover_minor != null ? money(r.cover_minor, r.currency) : <span className="cd-warn"><TriangleAlert aria-hidden />Unconfirmed</span>}</span>
      </div>
      {r.terms_rule && (
        <span className="cd-rule" title={`${r.service_basis}. Versioned carrier terms file.`}>
          <span className="rule-id">{r.terms_rule.split(" ")[0]}</span><span className="faint num">{r.terms_rule.split(" ")[1] ?? ""}</span>
        </span>
      )}
      {!r.carrier_known && r.terms_note && <p className="faint cd-note">{r.terms_note}</p>}
    </section>
  );
}

function History({ r }: { r: DeskRow }) {
  return (
    <section aria-label="History">
      <h4>History</h4>
      <ol className="cd-steps">
        {steps(r).map((s, i, all) => (
          <li key={s.key} className={`t-${s.tone}`} title={s.note ?? undefined}>
            <span className="cd-step-dot" aria-hidden />
            <span className="cd-step-label">{s.label}</span>
            <span className="cd-step-meta">{s.at ? `${shortDate(s.at)}${s.by ? ` · ${BY[s.by]}` : ""}` : all[i - 1]?.tone !== "todo" ? "Next" : ""}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function OrderAndCase({ r }: { r: DeskRow }) {
  const { openCase } = useDesk();
  const route = r.case_state ? ROUTE[r.case_state] ?? { label: "Open", tone: "neutral" as const } : null;
  return (
    <section aria-label="Order and case">
      <h4>Order</h4>
      <div className="cd-kv">
        <span className="k">Ref</span><span><CopyId value={r.order_ref} /></span>
        <span className="k">Item</span><span className="num">{money(r.item_minor, r.currency)} · {FAULT[r.failure_point] ?? r.failure_point}</span>
        <span className="k">Refund</span>
        <span className="num">{r.refunded_minor ? money(r.refunded_minor, r.currency) : "None yet"}{r.simulated && r.refunded_minor > 0 && <Badge>Simulated</Badge>}</span>
        {route && <><span className="k">Case</span><span><Badge tone={route.tone}>{route.label}</Badge></span></>}
      </div>
      <button type="button" className="btn ghost cd-open" onClick={() => openCase(r)}>Open in case<ArrowUpRight size={14} aria-hidden /></button>
    </section>
  );
}

/** The actions this claim allows, each through the existing recovery mark endpoint. */
function Actions({ r }: { r: DeskRow }) {
  const desk = useDesk();
  const busy = desk.busy === r.recovery_id;
  const paying = desk.paying === r.recovery_id;
  const [confirm, setConfirm] = useState<"withdraw" | "refused" | null>(null);
  const [amount, setAmount] = useState(() => (r.claim_minor / 100).toFixed(2));
  const [bad, setBad] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (paying) input.current?.focus(); }, [paying]);
  useEffect(() => { setConfirm(null); }, [r.status]);
  const save = () => {
    const minor = Math.round(Number(amount.replace(/[^0-9.]/g, "")) * 100);
    if (!Number.isFinite(minor) || minor < 1 || minor > r.claim_minor) { setBad(true); return; }
    setBad(false);
    void desk.act(r, "paid", minor);
  };
  const done = r.actions.length === 0;
  return (
    <div className="cd-actions">
      {desk.error?.id === r.recovery_id && <ErrorBox message={desk.error.message} />}
      {paying ? (
        <form className="cd-pay" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <label className="cd-amount">
            <span className="cd-amount-sym" aria-hidden>{symbolOf(r.currency)}</span>
            <input ref={input} inputMode="decimal" aria-label="Amount the carrier paid" aria-invalid={bad || undefined}
              value={amount} onChange={(e) => { setAmount(e.target.value); setBad(false); }} />
          </label>
          <span className={bad ? "cd-bad" : "faint"}>{bad ? `Up to ${money(r.claim_minor, r.currency)}` : `of ${money(r.claim_minor, r.currency)}`}</span>
          <div className="grow" />
          <button type="button" className="btn ghost" disabled={busy} onClick={() => desk.setPaying(null)}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>Save</button>
        </form>
      ) : confirm ? (
        <div className="cd-confirm" role="group" aria-label="Confirm">
          <span>{confirm === "withdraw" ? "Withdraw this claim?" : "Carrier refused?"}</span>
          <div className="grow" />
          <button type="button" className="btn ghost" disabled={busy} onClick={() => setConfirm(null)}>Keep</button>
          <button type="button" className="btn danger" disabled={busy} onClick={() => void desk.act(r, confirm)}>
            {confirm === "withdraw" ? "Yes, withdraw" : "Yes, refused"}
          </button>
        </div>
      ) : (
        <div className="cd-row">
          {r.actions.includes("withdraw") && <button type="button" className="btn ghost" disabled={busy} onClick={() => setConfirm("withdraw")}>Withdraw</button>}
          {r.actions.includes("refused") && <button type="button" className="btn" disabled={busy} onClick={() => setConfirm("refused")}>Carrier refused</button>}
          {done && <span className="faint">{r.status === "PAID" ? `Paid ${money(r.recovered_minor, r.currency)}` : statusOf(r.status).label}{r.resolved_at ? ` · ${shortDate(r.resolved_at)}` : ""}</span>}
          <div className="grow" />
          {r.actions.includes("submitted") && <button type="button" className="btn primary" disabled={busy} onClick={() => void desk.act(r, "submitted")}>I submitted it</button>}
          {r.actions.includes("paid") && <button type="button" className="btn primary" disabled={busy} onClick={() => desk.setPaying(r.recovery_id)}>Carrier paid</button>}
        </div>
      )}
    </div>
  );
}

/** CL-04: one claim's story under its row (or card): pack, deadline and carrier, history, order. */
export default function ClaimDetail({ id }: { id: string }) {
  const desk = useDesk();
  const r = desk.rows.get(id);
  if (!r) return null;
  return (
    <div className="cd-detail" data-own-click aria-label={`Claim for ${r.item}`} role="region">
      <div className="cd-detail-grid">
        <ClaimPack r={r} />
        <Deadline r={r} />
        <History r={r} />
        <OrderAndCase r={r} />
      </div>
      <Actions r={r} />
    </div>
  );
}
