import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GridApi } from "ag-grid-community";
import { ArrowDownLeft, ArrowUpRight, Download, HandCoins, KeyRound, Landmark, Radio, Scale, ShieldCheck, ShieldOff } from "lucide-react";
import type { MoneyRow } from "../api";
import { ErrorBox, Toast, usePoll } from "../components/ui";
import { money } from "../format";
import { useMedia } from "../motion";
import { PageHead } from "./LedgerGrid";
import FlowChart from "./money/FlowChart";
import MoneyGrid, { type Row } from "./money/MoneyGrid";
import { exportLedger } from "./money/exportLedger";
import { STATUS_ORDER, countBy, currencies, figures, inFlight, statusOf, weekly } from "./money/ledger";
import { moneyApi, type DeskRow, type MoneyDesk } from "./money/moneyApi";
import "./money/money.css";

const LIVE_MS = 10_000;

type Props = { rows: MoneyRow[] | null; error: string | null; onOpenOrder: (orderId: string) => void; token: string; onChanged: () => void };

/** The list rows Seller.tsx already holds, read as desk rows until the desk itself answers. */
const asDesk = (r: MoneyRow): DeskRow => ({ ...r, completed_at: null, simulated: r.status === "simulated", receipts: 0 });

/** MN-02..MN-08: the money ledger. Every PayPal write Doorstep made, exactly once and provable. */
export default function MoneyPage({ rows: listRows, error, onOpenOrder, token, onChanged }: Props) {
  const phone = useMedia("(max-width: 600px)");
  const [desk, setDesk] = useState<MoneyDesk | null>(null);
  const [deskError, setDeskError] = useState(false);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [changed, setChanged] = useState<Set<string>>(new Set());
  const seen = useRef<Map<string, string> | null>(null);
  const changedCb = useRef(onChanged);
  changedCb.current = onChanged;

  useEffect(() => {
    let live = true;
    moneyApi.desk(token).then((d) => {
      if (!live) return;
      setDesk(d); setDeskError(false); setUpdated(new Date());
      // A write that moved on (queued -> simulated, a new refund) flashes, and the app's counts refresh.
      const next = new Map(d.rows.map((r) => [r.action_id, `${r.status}|${r.receipts}`]));
      const prev = seen.current;
      seen.current = next;
      if (!prev) return;
      const moved = d.rows.filter((r) => prev.get(r.action_id) !== next.get(r.action_id)).map((r) => r.action_id);
      if (moved.length) { setChanged(new Set(moved)); changedCb.current(); }
    }).catch(() => { if (live) setDeskError(true); });
    return () => { live = false; };
  }, [token, tick]);
  // Seller.tsx reloads the list after any action anywhere: follow it.
  useEffect(() => { if (listRows) reload(); }, [listRows, reload]);

  const rows: DeskRow[] | null = desk?.rows ?? (listRows ? listRows.map(asDesk) : null);
  const recoveries = useMemo(() => desk?.recoveries ?? [], [desk]);
  const flying = (rows ?? []).filter(inFlight).length;
  // MN-08: live. Fast while PayPal has a write, calm otherwise (new writes still appear).
  usePoll(reload, flying > 0, 2000);
  usePoll(reload, flying === 0, LIVE_MS);

  const curs = useMemo(() => currencies(rows ?? [], recoveries), [rows, recoveries]);
  const [cur, setCur] = useState<string | null>(null);
  const currency = cur && curs.includes(cur) ? cur : curs[0] ?? "GBP";
  const f = useMemo(() => figures(rows ?? [], recoveries, currency), [rows, recoveries, currency]);
  const weeks = useMemo(() => weekly(rows ?? [], recoveries, currency, new Date(), phone ? 6 : 8), [rows, recoveries, currency, phone]);

  const counts = useMemo(() => countBy(rows ?? []), [rows]);
  const [status, setStatus] = useState("all");
  const chips = STATUS_ORDER.filter((s) => s !== "unconfirmed" || counts[s]);
  useEffect(() => { if (status !== "all" && !counts[status] && rows) setStatus("all"); }, [counts, status, rows]);

  const integrity = useMemo(() => {
    const r = rows ?? [];
    const keyed = r.filter((x) => x.request_id);
    return {
      writes: keyed.length,
      unique: new Set(keyed.map((x) => x.request_id)).size,
      signed: r.filter((x) => x.receipts > 0).length,
      stopped: r.filter((x) => x.status === "blocked").length,
    };
  }, [rows]);

  const [toast, setToast] = useState<string | null>(null);
  const grid = useRef<GridApi<Row> | null>(null);
  const onApi = useCallback((a: GridApi<Row> | null) => { grid.current = a; }, []);
  const onRedecided = useCallback((msg: string) => { setToast(msg); reload(); changedCb.current(); }, [reload]);
  const doExport = () => {
    if (!grid.current || !rows) return;
    exportLedger(grid.current, rows, recoveries);
    setToast("Ledger exported");
  };

  const empty = rows !== null && rows.length === 0 && recoveries.length === 0;
  const sym = (c: string) => money(0, c).replace(/[\d.,\s]/g, "");

  return (
    <>
      <PageHead
        title="Money"
        line="Every PayPal write, exactly once"
        right={
          <div className="mn-head-right">
            {curs.length > 1 && (
              <div className="mn-seg" role="group" aria-label="Currency">
                {curs.map((c) => (
                  <button key={c} type="button" aria-pressed={c === currency} onClick={() => setCur(c)} title={`Show ${c} figures`}>{sym(c)} {c}</button>
                ))}
              </div>
            )}
            <span className={`mn-live${flying ? " on" : ""}`} title={flying ? "Following PayPal every 2 s" : "Refreshes every 10 s"} aria-live="polite">
              <Radio aria-hidden />{flying ? `Following ${flying}` : "Live"}{updated && !phone ? ` · ${updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : ""}
            </span>
            {!phone && (
              <button type="button" className="btn" onClick={doExport} disabled={!rows?.length} title="Excel: every money action, totals per currency">
                <Download aria-hidden />Export
              </button>
            )}
          </div>
        }
      />
      <div className="mn-body">
      {(error && deskError) && <ErrorBox message="Money did not load. Refresh to try again." />}

      <section className="kpis mn-kpis" aria-label="Summary">
        {!rows ? [0, 1, 2, 3].map((i) => <div key={i} className="kpi"><span className="skeleton" style={{ height: 14, width: 90 }} /><span className="skeleton" style={{ height: 28, width: 120 }} /><span className="skeleton" style={{ height: 12, width: 70 }} /></div>) : (
          <>
            <div className="kpi">
              <span className="label"><ArrowUpRight aria-hidden />Refunded</span>
              <span className="value num">{money(f.refunded, currency)}</span>
              <span className="sub">{f.refunds} refund{f.refunds === 1 ? "" : "s"}{f.simulated ? ` · ${money(f.simulated, currency)} simulated` : ""}</span>
            </div>
            <div className="kpi">
              <span className="label"><ArrowDownLeft aria-hidden />Recovered</span>
              <span className="value num">{money(f.recovered, currency)}</span>
              <span className="sub">{f.claims ? `${f.claims} carrier claim${f.claims === 1 ? "" : "s"} paid` : "From carriers"}</span>
            </div>
            <div className="kpi">
              <span className="label"><Scale aria-hidden />Net cost</span>
              <span className="value num">{money(f.net, currency)}</span>
              <span className="sub">Refunded − recovered</span>
            </div>
            <div className={`kpi${f.inFlight ? " attention" : ""}`}>
              <span className="label"><Landmark aria-hidden />At PayPal</span>
              <span className="value num">{money(f.atPayPal, currency)}</span>
              <span className="sub">{f.inFlight ? <><span className="mn-pulse" aria-hidden />{f.inFlight} in flight</> : "Nothing in flight"}</span>
            </div>
          </>
        )}
      </section>

      {empty ? (
        <section className="table-card mn-empty" aria-label="Money actions">
          <span className="mn-empty-icon" aria-hidden><HandCoins /></span>
          <p className="mn-empty-title">Nothing moved yet</p>
          <p>Refunds and offers land here, each signed.</p>
        </section>
      ) : (
        <>
          <section className="table-card mn-flow" aria-label="Money flow">
            <div className="mn-flow-chart">
              <div className="mn-card-head"><p className="mn-card-title">Money flow</p><span className="faint">Weekly · {currency}</span></div>
              {rows ? <FlowChart weeks={weeks} currency={currency} /> : <span className="skeleton" style={{ display: "block", height: 216 }} />}
            </div>
            <aside className="mn-proof" aria-label="Proof">
              <div className="mn-card-head"><p className="mn-card-title">Proof</p></div>
              <ul>
                <li title="Each PayPal write carries its own PayPal-Request-Id; a retry reuses it, so PayPal pays once">
                  <KeyRound aria-hidden /><span>One key per write</span><b className="num">{integrity.unique}/{integrity.writes}</b>
                </li>
                <li title="Ed25519-signed, hash-chained receipts">
                  <ShieldCheck aria-hidden /><span>Signed</span><b className="num">{integrity.signed}</b>
                </li>
                <li title="Refunds the live re-check stopped before PayPal: no money moved">
                  <ShieldOff aria-hidden /><span>Stopped by re-check</span><b className="num">{integrity.stopped}</b>
                </li>
              </ul>
            </aside>
          </section>

          <div className="mn-filter" role="group" aria-label="Filter by status">
            <button type="button" aria-pressed={status === "all"} onClick={() => setStatus("all")}>All<span className="count num">{rows?.length ?? ""}</span></button>
            {chips.map((s) => (
              <button key={s} type="button" aria-pressed={status === s} disabled={!counts[s]} onClick={() => setStatus(s)} title={statusOf(s).hint}>
                <span className={`mn-chip-dot t-${statusOf(s).tone}`} aria-hidden />{statusOf(s).label}<span className="count num">{counts[s] ?? 0}</span>
              </button>
            ))}
          </div>

          <MoneyGrid rows={rows} token={token} status={status} onOpenOrder={onOpenOrder} onRedecided={onRedecided} onApi={onApi} changed={changed} />
        </>
      )}
      </div>
      <Toast message={toast} onDone={() => setToast(null)} />
    </>
  );
}
