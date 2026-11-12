import { useEffect, useMemo, useRef, useState } from "react";
import { Gauge, HandCoins, Landmark, Power, Zap } from "lucide-react";
import { api, type Autopilot, type AutopilotStatement } from "../api";
import { ErrorBox, Loading, usePoll } from "../components/ui";
import { money } from "../format";
import { PageHead, joinMoney } from "./LedgerGrid";
import { automateApi, type AutopilotDesk } from "./automate/automateApi";
import { RuleCardView, Runs, WhatIf, kindTitle } from "./automate/autopilotParts";
import { Empty, Meter, Sparkbars, UndoToast, useUndo } from "./automate/parts";
import "./pages.css";

const KIND: Record<string, string> = { CARRIER_LOST: "lost parcels", DAMAGED_IN_TRANSIT: "parcels damaged in transit" };
export const kindText = (fp: string) => KIND[fp] ?? fp.toLowerCase().replaceAll("_", " ");

/** Runs an action, reports the result, and keeps one error line. */
function useAct(onChange: (m: string) => void) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true); setErr(null);
    try { await fn(); onChange(done); } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }
  return { busy, err, act };
}

/** AP-06: the seller's own pattern, offered as a rule they turn on. Stays on the Control Room, where
 *  the approvals happen; money still moves only through the policy. Null when nothing is suggested. */
export function AutopilotSuggestion({ token, data, onChange }: { token: string; data: Autopilot; onChange: (m: string) => void }) {
  const { busy, err, act } = useAct(onChange);
  const s = data.suggestions[0];
  if (!s) return null;
  const kind = kindText(s.failure_point);
  const max = money(s.max_amount_minor, s.currency);
  return (
    <section className="autopilot" aria-label="Autopilot suggestion">
      <span className="ap-icon" aria-hidden><Zap /></span>
      <div className="col grow" style={{ gap: 3, minWidth: 0 }}>
        <span className="ap-title">Auto-refund {kind} up to {max}?</span>
        <span className="ap-text">
          You approved {s.approved} of {s.approved} in {s.window_days} days. Disputes, held cases and anything over {max} still come to you.
        </span>
        {err && <ErrorBox message={err} />}
      </div>
      <div className="ap-actions">
        <button className="btn ghost" disabled={busy} onClick={() => act(() => api.autopilotDismiss(token, s.failure_point, s.currency), "Okay. Still yours to approve.")}>Not now</button>
        <button className="btn primary" disabled={busy} onClick={() => act(() => api.autopilotOn(token, s.failure_point, s.currency), `Autopilot on: ${kind} up to ${max}.`)}>Turn on</button>
      </div>
    </section>
  );
}

const DAY = 86_400_000;

/** AP-10..13: what Autopilot may do (rules and their limits), what it did, and what it would have
 *  done with the shop's own past cases. Every switch calls the existing autopilot endpoints. */
export default function AutopilotPage({ token, data, statement, onChange, onOpen }: {
  token: string; data: Autopilot | null; statement: AutopilotStatement | null; onChange: (m: string) => void; onOpen: (orderId: string) => void;
}) {
  const [desk, setDesk] = useState<AutopilotDesk | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [actErr, setActErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState<Set<string>>(new Set());
  const [pick, setPick] = useState<number | null>(null);
  const undo = useUndo();
  const whatIf = useRef<HTMLDivElement>(null);

  const reload = () => {
    automateApi.autopilot(token).then((d) => { setDesk(d); setLoadErr(null); }).catch((e) => setLoadErr(String(e.message ?? e)));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [token, data, statement]); // the Control Room reloaded: follow it
  // Live: rules refund as buyers report, and the worker completes those refunds at PayPal. Quick
  // while one is on its way, calm otherwise (the Control Room's 10 s).
  usePoll(reload, !!desk, desk?.runs.some((r) => r.status === "queued" || r.status === "pending") ? 3000 : 10_000);

  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true); setActErr(null);
    try { await fn(); onChange(done); reload(); } catch (e) { setActErr(String((e as Error).message)); } finally { setBusy(false); }
  }
  /** Turning a rule off is final, so it waits five seconds for Undo before the endpoint is called. */
  function turnOff(ids: string[], label: string, done: string) {
    setStopping(new Set(ids));
    undo.schedule(label, async () => {
      setActErr(null);
      try {
        for (const id of ids) await api.autopilotOff(token, id);
        onChange(done);
      } catch (e) { setActErr(String((e as Error).message)); }
      setStopping(new Set());
      reload();
    });
  }
  const cancel = () => { undo.undo(); setStopping(new Set()); };

  const replayable = useMemo(() => (desk?.cards ?? []).filter((c) => c.replay), [desk]);
  const defaultPick = useMemo(() => {
    const order = ["on", "offered", "learning", "off", "dismissed"];
    for (const s of order) { const i = replayable.findIndex((c) => c.status === s); if (i >= 0) return i; }
    return 0;
  }, [replayable]);
  const selected = pick != null && pick < replayable.length ? pick : defaultPick;

  const onRules = (desk?.cards ?? []).filter((c) => c.status === "on" && c.rule_id);
  const home = desk?.limits[0];
  const days = useMemo(() => {
    const out = Array<number>(7).fill(0);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    for (const r of desk?.runs ?? []) {
      const d = new Date(r.at); d.setHours(0, 0, 0, 0);
      const ago = Math.round((today.getTime() - d.getTime()) / DAY);
      if (ago >= 0 && ago < 7) out[6 - ago] += 1;
    }
    return out;
  }, [desk]);

  const st = desk?.statement;
  const refunded = st ? joinMoney(Object.fromEntries(st.totals.map((t) => [t.currency, t.refunded_minor]))) : "";
  const claimable = st ? joinMoney(Object.fromEntries(st.totals.map((t) => [t.currency, t.claimable_minor]))) : "";
  const recovered = st ? joinMoney(Object.fromEntries(st.totals.map((t) => [t.currency, t.recovered_minor]))) : "";
  const nRefunds = st ? st.totals.reduce((s, t) => s + t.refunds, 0) : 0;
  const ready = st ? st.totals.reduce((s, t) => s + t.ready_to_send, 0) : 0;
  const stopAll = onRules.filter((c) => !stopping.has(c.rule_id!));

  return (
    <div className="ax-page stack-lg">
      <PageHead title="Autopilot" line="Refunds Doorstep may approve for you" right={stopAll.length > 0 ? (
        <button type="button" className="btn" disabled={busy || !!undo.pending}
          title="Stops every rule now. Doorstep won't offer them again."
          onClick={() => turnOff(stopAll.map((c) => c.rule_id!), `Turning off ${stopAll.length} rule${stopAll.length === 1 ? "" : "s"}`,
            `Autopilot off: ${stopAll.map((c) => kindText(c.failure_point)).join(" and ")} come to you again.`)}>
          <Power size={15} aria-hidden />Turn all off
        </button>
      ) : undefined} />
      {loadErr && !desk && <ErrorBox message="Autopilot did not load. Refresh to try again." />}
      {actErr && <ErrorBox message={actErr} />}

      {!desk ? (
        <>
          <section className="kpis" aria-hidden>
            {[0, 1, 2, 3].map((i) => <div key={i} className="kpi kpi-skel"><span className="skel w40" /><span className="skel w20 tall" /><span className="skel w60" /></div>)}
          </section>
          {!loadErr && <div className="ax-rules"><Loading height={250} /><Loading height={250} /></div>}
        </>
      ) : !desk.enabled ? (
        <section className="table-card" aria-label="Autopilot rules"><Empty icon={<Zap />} title="Autopilot is off" /></section>
      ) : (
        <>
          <section className="kpis" aria-label="Summary">
            <div className="kpi">
              <span className="label"><Zap aria-hidden />Rules on</span>
              <span className="value num">{onRules.length}<span className="ax-of"> of {desk.cards.length}</span></span>
              <span className="sub">{onRules.length ? onRules.map((c) => kindTitle(c.failure_point)).join(", ") : "all yours to approve"}</span>
            </div>
            <div className="kpi">
              <span className="label"><HandCoins aria-hidden />Refunded · {st?.days ?? 7} days</span>
              <span className="ax-kpi-row"><span className="value num" style={{ fontSize: 22 }}>{refunded}</span><Sparkbars values={days} label="Automatic refunds per day, last 7 days" /></span>
              <span className="sub">{nRefunds} automatic refund{nRefunds === 1 ? "" : "s"}</span>
            </div>
            <div className="kpi" title={`All refunds today (UTC), rules included. Cap from ${desk.money_limits.id} (${desk.money_limits.version}) or your own setting.`}>
              <span className="label"><Gauge aria-hidden />Daily cap</span>
              <span className="value num" style={{ fontSize: 22 }}>{home ? money(home.today_minor, home.currency) : "—"}<span className="ax-of"> of {home ? money(home.daily_cap_minor, home.currency) : "—"}</span></span>
              {home ? <Meter label="Refunds today against the daily cap" total={home.daily_cap_minor} parts={[{ value: home.today_minor, tone: "accent" }]} /> : <span className="sub"> </span>}
            </div>
            <div className="kpi" title="Carrier claims on the refunds Autopilot made in the last 7 days. Every claim is on the Claims page.">
              <span className="label"><Landmark aria-hidden />To claim back</span>
              <span className="value num" style={{ fontSize: 22 }}>{claimable}</span>
              <span className="sub">{ready ? `${ready} claim${ready === 1 ? "" : "s"} ready` : recovered !== money(0, "GBP") ? `${recovered} recovered` : "from its refunds"}</span>
            </div>
          </section>

          <section aria-label="Autopilot rules" className="ax-section">
            <div className="ax-section-head">
              <h2>Rules</h2>
              <span className={`ax-state ${stopAll.length ? "on" : ""}`}>
                {stopAll.length ? `Autopilot on: ${stopAll.map((c) => `${kindText(c.failure_point)} up to ${money(c.max_amount_minor, c.currency)}`).join(", ")}` : "Autopilot is off"}
              </span>
            </div>
            <div className="ax-rules">
              {desk.cards.map((c) => {
                const i = replayable.indexOf(c);
                return (
                  <RuleCardView key={`${c.failure_point}-${c.currency}`} c={c} busy={busy}
                    limits={desk.limits.find((l) => l.currency === c.currency)}
                    stopping={!!c.rule_id && stopping.has(c.rule_id)}
                    onOn={() => act(() => api.autopilotOn(token, c.failure_point, c.currency), `Autopilot on: ${kindText(c.failure_point)} up to ${money(c.max_amount_minor, c.currency)}.`)}
                    onDismiss={() => act(() => api.autopilotDismiss(token, c.failure_point, c.currency), "Okay. Still yours to approve.")}
                    onOff={() => turnOff([c.rule_id!], `${kindTitle(c.failure_point)}: turning off`, `Autopilot off: ${kindText(c.failure_point)} come to you again.`)}
                    onReplay={i >= 0 ? () => { setPick(i); whatIf.current?.scrollIntoView({ behavior: "smooth", block: "start" }); } : undefined}
                  />
                );
              })}
            </div>
          </section>

          <Runs runs={desk.runs} statement={desk.statement} onOpen={onOpen} />
          <div ref={whatIf} style={{ scrollMarginTop: 16 }}>
            <WhatIf cards={replayable} selected={selected} onSelect={setPick} limits={desk.limits} onOpen={onOpen} />
          </div>
          <p className="faint ax-foot">
            Limits from {desk.money_limits.id} ({desk.money_limits.version}) · disputes and held cases always come to you
          </p>
        </>
      )}
      <UndoToast pending={undo.pending} onUndo={cancel} ms={undo.ms} />
    </div>
  );
}
