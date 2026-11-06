import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GridApi } from "ag-grid-community";
import { BarChart3, BookOpen, Columns3, Download, FolderOpen, Info, Inbox, Landmark, Lightbulb, List, RefreshCw, Search, Truck, Wallet } from "lucide-react";
import { api, type OrderRow, type PreventionTip, type RecoverySummary } from "../api";
import { Badge, ErrorBox, Loading, Toast, Wordmark, useAsync } from "../components/ui";
import { money } from "../format";
import CaseDrawer from "./CaseDrawer";
import OrdersGrid, { exportExcel, sumStake, toggleColumnsPanel } from "./OrdersGrid";

// AG Studio is large: it loads only when the seller opens Insights.
const Insights = lazy(() => import("../insights/Insights"));

type Filter = "action" | "flight" | "cases" | "all";

const FILTERS: { key: Filter; label: string; icon: React.ReactNode; empty: string }[] = [
  { key: "action", label: "Needs you", icon: <Inbox />, empty: "Nothing needs you. You're all caught up." },
  { key: "flight", label: "In transit", icon: <Truck />, empty: "No parcels on their way." },
  { key: "cases", label: "Cases", icon: <FolderOpen />, empty: "No delivery problems reported." },
  { key: "all", label: "All orders", icon: <List />, empty: "No orders yet." },
];

const IN_FLIGHT = ["SHIPPED", "IN_TRANSIT", "EXCEPTION", "DELIVERED", "PICKED_UP"];

function filterRows(rows: OrderRow[], f: Filter): OrderRow[] {
  if (f === "action") return rows.filter((r) => r.needs_action);
  if (f === "flight") return rows.filter((r) => IN_FLIGHT.includes(r.state));
  if (f === "cases") return rows.filter((r) => r.case_id);
  return rows;
}

function sumBy(by: Record<string, number>): string {
  const parts = Object.entries(by).filter(([, v]) => v > 0).map(([c, v]) => money(v, c));
  return parts.length ? parts.join(" + ") : money(0, "GBP");
}

const LIVE_MS = 10_000;

const METHOD_TEXT: Record<string, string> = { home: "home delivery", locker: "locker pickup", signature: "signature" };

/** CF-02, in one line: computed in code from the seller's own seller-liable cases. */
function Tip({ t }: { t: PreventionTip }) {
  return (
    <div className="tip">
      <span className="tip-icon" aria-hidden><Lightbulb size={16} /></span>
      <div className="col grow" style={{ gap: 2 }}>
        <span style={{ fontWeight: 600 }}>Offer {METHOD_TEXT[t.suggested_method] ?? t.suggested_method} on {t.band} orders</span>
        <span className="muted" style={{ fontSize: 13 }} title={t.suggested_method === "locker" ? "A locker pickup code proves the buyer has it." : "A signature proves who received it."}>
          {t.cases} of {t.orders} {METHOD_TEXT[t.method] ?? t.method} orders here became cases you paid for ({money(t.money_minor, t.currency)}).
        </span>
      </div>
      <Badge>Suggestion</Badge>
    </div>
  );
}

export default function Seller({ token }: { token: string }) {
  const me = useAsync(() => api.me(token), [token]);
  const { data: rows, error, reload: reloadOrders } = useAsync(() => api.orders(token), [token]);
  const recovered = useAsync(() => api.recoveries(token), [token]);
  const tips = useAsync(() => api.prevention(token), [token]);
  const reload = () => { reloadOrders(); recovered.reload(); tips.reload(); };
  const [filter, setFilter] = useState<Filter | null>(null);
  const [open, setOpen] = useState<OrderRow | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const gridApi = useRef<GridApi | null>(null);
  const [inView, setInView] = useState<OrderRow[] | null>(null);
  const [page, setPage] = useState<"orders" | "insights">(() => (location.hash === "#insights" ? "insights" : "orders"));
  const go = (p: "orders" | "insights") => { setPage(p); history.replaceState(null, "", p === "insights" ? "#insights" : location.pathname + location.search); };

  // Live: the worker moves orders on (tracking, refunds); pull the table quietly while the tab is open.
  useEffect(() => {
    const t = window.setInterval(() => { if (document.visibilityState === "visible") reloadOrders(); }, LIVE_MS);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const onOpen = useCallback((r: OrderRow) => setOpen(r), []);
  const onReady = useCallback((a: GridApi) => { gridApi.current = a; }, []);
  // Footer numbers follow the grid's own filters; only update when the set of rows really changed.
  const onShown = useCallback((list: OrderRow[]) => setInView((prev) => {
    const same = prev && prev.length === list.length && prev.every((r, i) => r === list[i]);
    return same ? prev : list;
  }), []);

  const counts = useMemo(() => {
    const r = rows ?? [];
    return { action: filterRows(r, "action").length, flight: filterRows(r, "flight").length, cases: filterRows(r, "cases").length, all: r.length };
  }, [rows]);
  const active: Filter = filter ?? (counts.action > 0 ? "action" : "all");
  const shown = useMemo(() => filterRows(rows ?? [], active), [rows, active]);
  const activeMeta = FILTERS.find((f) => f.key === active)!;

  return (
    <div className="shell">
      <aside className="sidebar">
        <Wordmark />
        <div className="shop-chip">
          <span className="avatar" aria-hidden>{(me.data?.name ?? "M").slice(0, 1)}</span>
          <div className="col" style={{ gap: 0, minWidth: 0 }}>
            <span style={{ fontWeight: 600, fontSize: 13.5 }}>{me.data?.name ?? "Your shop"}</span>
            <span className="faint">{me.data?.region === "US" ? "US" : "UK"} · PayPal sandbox</span>
          </div>
        </div>
        <div>
          <div className="nav-label">Orders</div>
          <div className="nav" role="group" aria-label="Filter orders">
            {FILTERS.map((f) => (
              <button key={f.key} aria-pressed={page === "orders" && active === f.key} onClick={() => { setFilter(f.key); go("orders"); }}>
                {f.icon}{f.label}<span className="count">{rows ? counts[f.key] : ""}</span>
              </button>
            ))}
          </div>
        </div>
        <div>
          <div className="nav-label">Analytics</div>
          <div className="nav">
            <button aria-pressed={page === "insights"} onClick={() => go("insights")}><BarChart3 />Insights</button>
          </div>
        </div>
        <div className="bottom">
          <div className="nav">
            <a href="/#how"><BookOpen />How Doorstep works</a>
          </div>
        </div>
      </aside>

      <main className="main">
        <div className="page stack-lg">
          {page === "insights" ? (
            <Suspense fallback={<Loading height={480} />}>
              <Insights token={token} />
            </Suspense>
          ) : (<>
          <div className="page-head">
            <div>
              <p className="greet">{greeting(me.data?.name)}{rows ? <> · <b>{counts.action ? `${counts.action} need${counts.action === 1 ? "s" : ""} you` : "nothing needs you"}</b></> : null}</p>
              <h1>Delivery Control Room</h1>
            </div>
            <button className="btn" onClick={reload}><RefreshCw size={15} aria-hidden />Refresh</button>
          </div>

          {error && <ErrorBox message="This seller link isn't valid. Open a fresh demo from the home page." />}
          {!rows && !error && <Loading height={320} />}

          {rows && (
            <>
              <section className="kpis" aria-label="Summary">
                <div className={`kpi ${counts.action ? "attention" : ""}`}>
                  <span className="label"><Inbox aria-hidden />Needs you<Hint text="Cases to approve or review, and orders waiting for a packing check." /></span>
                  <span className="value">{counts.action}</span>
                  <span className="sub">to approve or review</span>
                </div>
                <div className="kpi">
                  <span className="label"><Truck aria-hidden />In transit<Hint text="Shipped parcels the buyer has not received yet." /></span>
                  <span className="value">{counts.flight}</span>
                  <span className="sub">on the way</span>
                </div>
                <div className="kpi">
                  <span className="label"><Wallet aria-hidden />Money at stake<Hint text="The value of open cases plus parcels still on the way: what a delivery failure could cost you today." /></span>
                  <span className="value" style={{ fontSize: 22 }}>{sumStake(rows)}</span>
                  <span className="sub">open cases and parcels in transit</span>
                </div>
                <RecoveredKpi s={recovered.data ?? null} />
              </section>

              {tips.data && tips.data.length > 0 && <Tip t={tips.data[0]} />}

              <div className="table-card">
                <div className="head">
                  <div className="row" style={{ gap: 10 }}>
                    <h2 style={{ fontSize: 15 }}>{activeMeta.label}</h2>
                    <span className="count-chip num">{shown.length}</span>
                    <span className="live" title="Refreshes every 10 seconds while this tab is open"><span className="live-dot" aria-hidden />Live</span>
                  </div>
                  <div className="row" style={{ gap: 8 }}>
                    <label className="grid-search">
                      <Search aria-hidden />
                      <input type="search" placeholder="Search orders, areas, status" aria-label="Search orders"
                        value={search} onChange={(e) => setSearch(e.target.value)} />
                    </label>
                    <button className="btn icon-btn" title="Columns and filters" aria-label="Columns and filters"
                      onClick={() => gridApi.current && toggleColumnsPanel(gridApi.current)}>
                      <Columns3 size={15} aria-hidden />
                    </button>
                    <button className="btn icon-btn" title="Export what you see to Excel" aria-label="Export to Excel"
                      onClick={() => gridApi.current && exportExcel(gridApi.current)}>
                      <Download size={15} aria-hidden />
                    </button>
                  </div>
                </div>
                <OrdersGrid rows={shown} search={search} empty={activeMeta.empty} onOpen={onOpen} onReady={onReady} onShown={onShown} />
                <div className="grid-foot">
                  <span className="faint">{rows.some((r) => r.simulated) ? "Demo orders · refunds simulated" : "PayPal sandbox orders"}</span>
                  <span className="foot-stats num">
                    <span>Rows <strong>{inView?.length ?? shown.length}</strong> of {shown.length}</span>
                    <span>At stake <strong>{sumStake(inView ?? shown)}</strong></span>
                  </span>
                </div>
              </div>
            </>
          )}
          </>)}
        </div>
      </main>

      {open && (
        <CaseDrawer
          token={token}
          row={rows?.find((r) => r.order_id === open.order_id) ?? open}
          onClose={() => setOpen(null)}
          onChanged={(msg) => { setToast(msg); reload(); }}
        />
      )}
      <Toast message={toast} onDone={() => setToast(null)} />
    </div>
  );
}

/** "Good afternoon, Maria": the first name from the shop name ("Maria's Vintage"). */
function greeting(shop: string | undefined): string {
  const h = new Date().getHours();
  const part = h < 12 ? "morning" : h < 18 ? "afternoon" : "evening";
  const who = shop?.split(/['’]s\b/)[0]?.trim();
  return who ? `Good ${part}, ${who}` : `Good ${part}`;
}

/** A small (i) with the longer explanation, on hover or keyboard focus. */
function Hint({ text }: { text: string }) {
  return <span className="hint" tabIndex={0} role="note" aria-label={text} data-tip={text}><Info aria-hidden /></span>;
}

/** RC-05: what carriers paid back, and what is still claimable. */
function RecoveredKpi({ s }: { s: RecoverySummary | null }) {
  const open = s ? sumBy(s.open_claim_minor) : "";
  return (
    <div className="kpi">
      <span className="label"><Landmark aria-hidden />Recovered from carriers<Hint text="What carriers paid back on claims Doorstep prepared and you submitted." /></span>
      <span className="value" style={{ fontSize: 22 }}>{s ? sumBy(s.recovered_minor) : "—"}</span>
      <span className="sub">{s && s.open_claims ? `${s.open_claims} open claim${s.open_claims === 1 ? "" : "s"} · ${open}` : "from claims you filed"}</span>
    </div>
  );
}
