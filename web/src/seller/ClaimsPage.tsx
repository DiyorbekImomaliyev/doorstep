import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GridApi } from "ag-grid-community";
import { CalendarClock, Hourglass, Landmark, Layers, PackageCheck, RefreshCw } from "lucide-react";
import type { ClaimRow, RecoveryView } from "../api";
import { ErrorBox, Toast, useAsync, usePoll } from "../components/ui";
import { money } from "../format";
import { useMedia } from "../motion";
import { afterMark, claimsApi, type DeskRow, type Mark } from "./claims/claimsApi";
import ClaimCards from "./claims/ClaimCards";
import ClaimClock from "./claims/ClaimClock";
import ClaimsGrid, { type Row } from "./claims/ClaimsGrid";
import { DeskContext, Hint, type Desk } from "./claims/desk";
import { DUE_DAYS, SEGS, carrierOf, isOpen, joinMoney, rateText, shortDate, statusOf, summarise, type Seg } from "./claims/model";
import "./claims/claims.css";

const LIVE_MS = 5000;

const TOAST: Record<Mark, (r: DeskRow, minor?: number) => string> = {
  submitted: (r) => `${r.item}: marked submitted`,
  paid: (r, minor) => `${r.item}: ${money(minor ?? 0, r.currency)} recovered`,
  refused: (r) => `${r.item}: marked refused`,
  withdraw: (r) => `${r.item}: claim withdrawn`,
};

type Props = {
  token: string;
  /** The ledger rows Seller.tsx keeps; a new list (after any change elsewhere) refreshes the desk. */
  rows?: ClaimRow[] | null;
  error?: string | null;
  onOpenOrder: (orderId: string) => void;
  onChanged: () => void;
};

/** CL-02..CL-07: the claims desk, where a seller gets money back from carriers. */
export default function ClaimsPage({ token, rows: ledger, onOpenOrder, onChanged }: Props) {
  const desk = useAsync(() => claimsApi.desk(token), [token]);
  const phone = useMedia("(max-width: 600px)");
  // Seller.tsx reloads its claim rows after a change anywhere (the case drawer marks a claim too).
  const seen = useRef(ledger);
  useEffect(() => {
    if (seen.current !== ledger) desk.reload();
    seen.current = ledger;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ledger]);
  const rows = useMemo(() => desk.data?.rows ?? null, [desk.data]);
  const now = desk.data ? Date.parse(desk.data.now) : Date.now();
  const live = !!rows?.some(isOpen);
  usePoll(desk.reload, live, LIVE_MS);
  const [updated, setUpdated] = useState<Date | null>(null);
  useEffect(() => { if (desk.data) setUpdated(new Date()); }, [desk.data]);

  const [seg, setSeg] = useState<Seg>("all");
  const [byCarrier, setByCarrier] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [paying, setPaying] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<Desk["error"]>(null);
  const [toast, setToast] = useState<string | null>(null);
  const grid = useRef<GridApi<Row> | null>(null);
  const gridBox = useRef<HTMLDivElement>(null);

  const s = useMemo(() => (rows ? summarise(rows, now) : null), [rows, now]);
  const byId = useMemo(() => new Map((rows ?? []).map((r) => [r.recovery_id, r])), [rows]);

  const patch = useCallback((id: string, f: (r: DeskRow) => DeskRow) => {
    desk.setData((d) => (d ? { ...d, rows: d.rows.map((x) => (x.recovery_id === id ? f(x) : x)) } : d));
  }, [desk]);

  const act = useCallback(async (r: DeskRow, m: Mark, minor?: number) => {
    setBusy(r.recovery_id); setError(null);
    try {
      const v = await claimsApi.mark(token, r.case_id, m, minor);
      patch(r.recovery_id, (x) => afterMark(x, v, new Date().toISOString()));
      setPaying(null);
      setToast(TOAST[m](r, minor));
      onChanged(); // sidebar badge and the Control Room's Recovered card
      desk.reload();
    } catch (e) {
      setError({ id: r.recovery_id, message: String((e as Error).message || "That did not save. Try again.") });
    } finally {
      setBusy(null);
    }
  }, [token, patch, onChanged, desk]);

  const pick = useCallback((id: string | null) => {
    setOpen(id);
    setPaying((p) => (p === id ? p : null));
    setError(null);
  }, []);
  /** The clock or a KPI picks a claim: show it even if the filter hides it, then bring it into view. */
  const focusClaim = useCallback((id: string) => {
    const r = byId.get(id);
    if (r && seg !== "all" && seg !== r.status) setSeg("all");
    pick(id);
    // The picked row at the top, its story under it (the phone list scrolls its card itself).
    if (!phone) window.setTimeout(() => gridBox.current?.querySelector(`.ag-row[row-id="${id}"]`)?.scrollIntoView({ block: "start", behavior: "smooth" }), 80);
  }, [byId, seg, phone, pick]);
  const next = useCallback((r: DeskRow) => {
    if (r.actions.includes("submitted")) void act(r, "submitted");
    else if (r.actions.includes("paid")) { setOpen(r.recovery_id); setPaying(r.recovery_id); }
  }, [act]);

  const ctx = useMemo((): Desk => ({
    token, now, phone, rows: byId, busy, paying, error, act, setPaying,
    openCase: (r) => onOpenOrder(r.order_id),
    drafted: (id: string, v: RecoveryView) => patch(id, (x) => (x.status === "PREPARED" ? { ...x, sentences: v.sentences, facts: v.facts, pack_source: v.pack_source } : x)),
  }), [token, now, phone, byId, busy, paying, error, act, onOpenOrder, patch]);

  const rate = s ? rateText(s.rec) : null;

  return (
    <DeskContext.Provider value={ctx}>
      <div className="cd">
        <div className="page-head">
          <div>
            <h1>Claims</h1>
            <p>Money back from carriers</p>
          </div>
          <div className="row" style={{ gap: 10 }}>
            {live && <span className="live" title={`Refreshes every ${LIVE_MS / 1000} seconds while a claim is open`}><span className="live-dot" aria-hidden />Live{updated ? ` · ${updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}</span>}
            <button className="btn icon-btn" onClick={() => { desk.reload(); onChanged(); }} aria-label="Refresh" title="Refresh now"><RefreshCw size={15} aria-hidden /></button>
          </div>
        </div>

        {desk.error && <ErrorBox message="Claims did not load. Refresh to try again." />}

        {!s ? (
          <section className="kpis cd-kpis" aria-hidden>
            {[0, 1, 2].map((i) => <div key={i} className="kpi kpi-skel"><span className="skel w40" /><span className="skel w20 tall" /><span className="skel w60" /></div>)}
          </section>
        ) : (
          <section className="kpis cd-kpis" aria-label="Summary">
            <div className="kpi">
              <span className="label"><Hourglass aria-hidden />Open<Hint text="Claims ready to file or waiting for the carrier." /></span>
              <span className="value num">{s.open}</span>
              <span className="sub num">{s.open ? `${joinMoney(s.openMoney)} · ${s.ready} ready` : "none waiting"}</span>
            </div>
            <button type="button" className="kpi" aria-pressed={seg === "PAID"} onClick={() => setSeg(seg === "PAID" ? "all" : "PAID")}
              title="Paid back by carriers. Rate: paid ÷ claimed, on claims the carrier decided. Click to show paid claims.">
              <span className="label"><Landmark aria-hidden />Recovered<Hint inButton text="Paid back by carriers. Rate: paid ÷ claimed, on claims the carrier decided." /></span>
              <span className="value num">{joinMoney(s.recovered)}</span>
              <span className="sub num">{s.rec.decided ? `${s.rec.won} of ${s.rec.decided} won · ${rate}` : "none decided yet"}</span>
            </button>
            <button type="button" className={`kpi ${s.due ? "attention" : ""}`} disabled={!s.mostUrgent}
              onClick={() => s.mostUrgent && focusClaim(s.mostUrgent.recovery_id)}
              title={`Ready claims whose window closes within ${DUE_DAYS} days.${s.mostUrgent ? ` Click to open the most urgent: ${s.mostUrgent.item}.` : ""}`}>
              <span className="label"><CalendarClock aria-hidden />Due in {DUE_DAYS} days<Hint inButton text={`Ready claims whose window closes within ${DUE_DAYS} days. File them first.`} /></span>
              <span className="value num">{s.due}</span>
              <span className="sub num">{s.mostUrgent ? `${joinMoney(s.dueMoney)} · next ${shortDate(s.mostUrgent.deadline_at)}` : "nothing urgent"}</span>
            </button>
          </section>
        )}

        {!rows ? <div className="skeleton" style={{ height: 132 }} aria-label="Loading" />
          : rows.length > 0 && <ClaimClock rows={rows} now={now} selected={open} onPick={focusClaim} />}

        {rows && rows.length === 0 ? (
          <section className="cd-card cd-empty" aria-label="Carrier claims">
            <span className="cd-empty-icon" aria-hidden><PackageCheck /></span>
            <h2>No claims yet</h2>
            <p className="muted">Lost or damaged parcels land here.</p>
            <a className="btn cd-empty-link" href="#orders">View orders</a>
          </section>
        ) : (
          <section className="table-card cd-table" aria-label="Carrier claims" ref={gridBox}>
            <div className="head cd-head">
              <div className="cd-seg" role="radiogroup" aria-label="Status">
                {SEGS.map((k) => {
                  const n = s?.counts[k] ?? 0;
                  return (
                    <button key={k} type="button" role="radio" aria-checked={seg === k} className={`tone-${k === "all" ? "all" : statusOf(k).tone}${n ? "" : " zero"}`}
                      onClick={() => setSeg(k)} title={k === "all" ? "Every claim" : statusOf(k).hint}>
                      {k !== "all" && <span className="cd-dot" aria-hidden />}{k === "all" ? "All" : statusOf(k).label}<span className="cd-seg-n num">{s ? n : ""}</span>
                    </button>
                  );
                })}
              </div>
              {!phone && (
                <button type="button" className="btn cd-toggle" aria-pressed={byCarrier} onClick={() => setByCarrier((v) => !v)} title="Group by carrier, with won ÷ claimed">
                  <Layers size={14} aria-hidden />By carrier
                </button>
              )}
            </div>
            {!rows ? (
              <div className="cd-skel-rows" aria-label="Loading">{[0, 1, 2, 3].map((i) => <span key={i} className="skel" style={{ width: `${88 - i * 9}%` }} />)}</div>
            ) : phone ? (
              <ClaimCards rows={rows} seg={seg} open={open} onOpen={pick} />
            ) : (
              <ClaimsGrid rows={rows} seg={seg} byCarrier={byCarrier} open={open} onOpen={pick} onNext={next} onReady={(a) => { grid.current = a; }} />
            )}
            {phone && rows && rows.some((r) => r.simulated) && <p className="cd-source faint">Demo orders · carrier history simulated</p>}
          </section>
        )}
      </div>
      <Toast message={toast} onDone={() => setToast(null)} />
      {/* Screen readers hear which claim opened. */}
      <span className="sr-only" aria-live="polite">{open && byId.get(open) ? `${byId.get(open)!.item}, ${carrierOf(byId.get(open)!)}, ${statusOf(byId.get(open)!.status).label}` : ""}</span>
    </DeskContext.Provider>
  );
}
