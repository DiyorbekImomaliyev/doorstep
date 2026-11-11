import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight, Check, CircleAlert, CircleCheck, CircleX, ExternalLink, Loader2, RefreshCw, ShieldCheck, ShieldX } from "lucide-react";
import { Badge, usePoll } from "../../components/ui";
import { CopyId } from "../LedgerGrid";
import { moneyApi, verifyReceipts, type DeskRow, type MoneyDetail, type Verdict } from "./moneyApi";
import { inFlight } from "./ledger";

const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const full = (iso: string) => new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const TONE: Record<string, string> = {
  approved: "on", queued: "on", rechecked: "on", pending: "warn", completed: "good", simulated: "accent", external: "good",
  failed: "bad", blocked: "bad", lowered: "warn", signed: "sign",
};

type Props = {
  row: DeskRow;
  token: string;
  onOpenOrder: (orderId: string) => void;
  /** After "Decide again": the page shows the toast and follows the new refund. */
  onRedecided: (message: string) => void;
};

/** MN-05, MN-06: one money action's whole story, under its row. */
export default function MoneyDetailPanel({ row, token, onOpenOrder, onRedecided }: Props) {
  const [d, setD] = useState<MoneyDetail | null>(null);
  const [failed, setFailed] = useState(false);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    let live = true;
    moneyApi.detail(token, row.action_id)
      .then((x) => { if (live) { setD(x); setFailed(false); } })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [token, row.action_id, row.status, row.receipts, tick]);
  // MN-08: while PayPal has it, the story follows the worker without a reload.
  usePoll(reload, inFlight(row) || (!!d && inFlight(d)), 2500);

  const [verdict, setVerdict] = useState<Verdict | "busy" | { error: string } | null>(null);
  useEffect(() => setVerdict(null), [row.action_id, d?.receipts.length]);
  const verify = async () => {
    if (!d?.case_id) return;
    setVerdict("busy");
    try { setVerdict(await verifyReceipts(d.case_id, d.receipts, d.public_key)); }
    catch (e) { setVerdict({ error: e instanceof Error ? e.message : "The check did not run" }); }
  };

  const [busy, setBusy] = useState(false);
  const [actError, setActError] = useState<string | null>(null);
  const redecide = async () => {
    if (!d?.case_id) return;
    setBusy(true); setActError(null);
    try {
      const out = await moneyApi.redecide(token, d.case_id);
      onRedecided(out.money_action_id ? "Decided again: a new refund is queued" : "Decided again: no refund this time");
      reload();
    } catch (e) { setActError(e instanceof Error ? e.message : "Could not decide again"); }
    finally { setBusy(false); }
  };

  if (failed && !d) return <div className="mn-detail-wrap" data-own-click><p className="faint">Details did not load.</p></div>;
  if (!d) {
    return (
      <div className="mn-detail-wrap" data-own-click aria-busy="true" aria-label="Loading details">
        <div className="detail-inner mn-detail">
          {[0, 1, 2].map((i) => <section key={i}><span className="skeleton" style={{ display: "block", height: 12, width: 80, marginBottom: 14 }} />
            {[0, 1, 2, 3].map((j) => <span key={j} className="skeleton" style={{ display: "block", height: 13, width: `${88 - j * 14}%`, marginBottom: 9 }} />)}</section>)}
        </div>
      </div>
    );
  }

  return (
    <div className="mn-detail-wrap" data-own-click>
      <div className="detail-inner mn-detail" role="region" aria-label="Money action details">
        <section aria-label="Lifecycle">
          <h4>Lifecycle</h4>
          <ol className="mn-steps">
            {d.timeline.map((s, i) => ({ s, i, rule: s.rule_id && d.timeline.findIndex((x) => x.rule_id) === i })).map(({ s, i, rule }) => (
              <li key={`${s.step}-${i}`} className={`t-${TONE[s.step] ?? "on"}`}>
                <span className="mn-dot" aria-hidden />
                <span className="mn-step">
                  <span className="mn-step-top"><b>{s.label}</b><time dateTime={s.at} title={full(s.at)} className="num">{clock(s.at)}</time></span>
                  <span className="mn-step-sub">
                    {s.actor}
                    {rule && <> · <code title="Rule id and version (rules/*.yaml)">{s.rule_id}{s.rule_version ? ` v${s.rule_version}` : ""}</code></>}
                    {s.detail && <> · <span className={s.detail.startsWith("SIM-") || /^[A-Z0-9-]{10,}$/.test(s.detail) ? "mono" : ""}>{s.detail}</span></>}
                  </span>
                </span>
              </li>
            ))}
            {inFlight(d) && (
              <li className="t-wait"><span className="mn-dot" aria-hidden /><span className="mn-step"><span className="mn-step-top"><b>{d.status === "queued" ? "Waiting for re-check" : "Waiting for PayPal"}</b><Loader2 className="spin" aria-hidden /></span><span className="mn-step-sub">Live</span></span></li>
            )}
          </ol>
        </section>

        <section aria-label="PayPal">
          <h4>PayPal</h4>
          <dl className="mini-kv mn-kv">
            <dt title="Idempotency key sent with every attempt">Request-Id</dt>
            <dd><CopyId value={d.request_id} /></dd>
            <dt>Refund id</dt>
            <dd>{d.paypal_refund_id ? <CopyId value={d.paypal_refund_id} /> : <span className="faint">—</span>}</dd>
            <dt>Answer</dt>
            <dd>{d.paypal_status ? <code>{d.paypal_status}</code> : <span className="faint">{d.status === "blocked" ? "Not asked" : "—"}</span>}</dd>
            <dt title="Outbox job: retries reuse the same Request-Id">Outbox</dt>
            <dd className="num">{d.outbox ? `${d.outbox.attempts} attempt${d.outbox.attempts === 1 ? "" : "s"} · ${d.outbox.status}` : d.external ? "Made at PayPal" : "—"}</dd>
            {d.approver && <><dt>Approved by</dt><dd>{d.approver}</dd></>}
            {d.rule_id && <><dt>Rule</dt><dd><code>{d.rule_id}</code>{d.rule_version && <span className="faint"> v{d.rule_version}</span>}</dd></>}
          </dl>
          {d.stop ? (
            <div className="notice bad mn-stop" role="note">
              <CircleAlert aria-hidden />
              <span><strong>{d.stop.title}</strong><br />{d.stop.text}{d.stop.check && <span className="mn-failed-check"><CircleX aria-hidden />Failed: {d.stop.check}</span>}</span>
            </div>
          ) : d.checks.length > 0 ? (
            <div className="mn-checks">
              <span className="mn-sub-h" title="Read live from PayPal right before the refund">{d.found_at_paypal ? "Found at PayPal · not resent" : "Live re-check"}</span>
              <ul className="checklist">{d.checks.map((c) => <li key={c} className="ok"><Check aria-hidden />{c}</li>)}</ul>
            </div>
          ) : null}
        </section>

        <section aria-label="Receipt">
          <h4>Receipt</h4>
          {d.receipts.length === 0 ? (
            <p className="faint mn-none">{inFlight(d) ? "Signed when PayPal confirms" : "None signed"}</p>
          ) : (
            <ul className="mn-receipts">
              {d.receipts.map((r) => (
                <li key={r.seq}>
                  <span><b>{r.label}</b> <span className="faint num">#{r.seq}</span></span>
                  <code className="mn-hash" title={r.hash}>{r.hash.slice(0, 16)}…</code>
                </li>
              ))}
            </ul>
          )}
          <div className="mn-verify">
            <button type="button" className="btn" onClick={verify} disabled={!d.case_id || d.receipts.length === 0 || verdict === "busy"}
              title={d.receipts.length ? "Recompute the chain on the server and check each Ed25519 signature in this browser" : "No receipt yet"}>
              {verdict === "busy" ? <Loader2 className="spin" aria-hidden /> : <ShieldCheck aria-hidden />}Verify receipt
            </button>
            {verdict && verdict !== "busy" && ("error" in verdict ? (
              <Badge tone="bad" icon={<ShieldX aria-hidden />}>{verdict.error}</Badge>
            ) : (
              <div className="mn-verdict" role="status">
                <Badge tone={verdict.ok ? "good" : "bad"} icon={verdict.ok ? <CircleCheck aria-hidden /> : <ShieldX aria-hidden />}>{verdict.ok ? "Signature valid" : "Signature invalid"}</Badge>
                <span className="faint">
                  {verdict.ok
                    ? `${verdict.checked.some((c) => c.browser) ? "Ed25519 in this browser · " : ""}chain of ${verdict.chain} intact`
                    : verdict.reason}
                </span>
              </div>
            ))}
          </div>
          <div className="mn-actions">
            {d.can_redecide && (
              <button type="button" className="btn primary" disabled={busy} onClick={redecide} title="Run the decision again on the evidence as it is now">
                {busy ? <Loader2 className="spin" aria-hidden /> : <RefreshCw aria-hidden />}Decide again
              </button>
            )}
            <button type="button" className="btn ghost" onClick={() => onOpenOrder(row.order_id)}><ArrowUpRight aria-hidden />Open case</button>
            {d.case_id && <a className="lg-link" href={`/verify/${d.case_id}`} target="_blank" rel="noreferrer">Public check<ExternalLink aria-hidden /></a>}
          </div>
          {actError && <p className="mn-error" role="alert">{actError}</p>}
        </section>
      </div>
    </div>
  );
}
