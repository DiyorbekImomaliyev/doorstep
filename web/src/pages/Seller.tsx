import { useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry, themeQuartz, type ColDef, type ICellRendererParams } from "ag-grid-community";
import { BookOpen, FolderOpen, Inbox, Landmark, Lightbulb, List, RefreshCw, Search, Truck, Wallet } from "lucide-react";
import { api, type OrderRow, type PreventionTip, type RecoverySummary } from "../api";
import { Badge, Bar, ErrorBox, Loading, Toast, Wordmark, useAsync } from "../components/ui";
import { DeliveryCell, ItemCell, JourneyCell, RiskCell, StakeCell } from "../components/gridCells";
import { FAILURE_POINT, METHOD, ROUTE, ago, money } from "../format";
import CaseDrawer from "./CaseDrawer";

ModuleRegistry.registerModules([AllCommunityModule]);

const theme = themeQuartz.withParams({
  fontFamily: "inherit",
  fontSize: 13.5,
  foregroundColor: "#1f1a18",
  backgroundColor: "#ffffff",
  headerFontWeight: 600,
  headerFontSize: 12.5,
  headerBackgroundColor: "#faf8f6",
  headerTextColor: "#5a524e",
  headerColumnResizeHandleColor: "#e8e2dc",
  headerColumnBorder: false,
  borderColor: "#ede8e3",
  rowBorder: { color: "#f1ece7" },
  rowHoverColor: "#fbf6f7",
  selectedRowBackgroundColor: "#f6ecee",
  accentColor: "#7b2d3d",
  iconColor: "#8b827d",
  wrapperBorder: false,
  wrapperBorderRadius: 0,
  rowHeight: 62,
  headerHeight: 44,
  spacing: 7,
  cellHorizontalPadding: 18,
});

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

function sumStake(rows: OrderRow[]): string {
  const by: Record<string, number> = {};
  rows.forEach((r) => (by[r.currency] = (by[r.currency] ?? 0) + r.money_at_stake_minor));
  return sumBy(by);
}

const METHOD_TEXT: Record<string, string> = { home: "home delivery", locker: "locker pickup", signature: "signature" };

/** CF-02, in one line: computed in code from the seller's own seller-liable cases. */
function Tip({ t }: { t: PreventionTip }) {
  return (
    <div className="tip">
      <span className="tip-icon" aria-hidden><Lightbulb size={16} /></span>
      <div className="col grow" style={{ gap: 2 }}>
        <span style={{ fontWeight: 600 }}>Offer {METHOD_TEXT[t.suggested_method] ?? t.suggested_method} on orders {t.band}</span>
        <span className="muted" style={{ fontSize: 13 }}>
          {t.cases} of {t.orders} {METHOD_TEXT[t.method] ?? t.method} orders in this range became cases you paid for ({money(t.money_minor, t.currency)}).
          {t.suggested_method === "locker" ? " A locker pickup code proves the buyer has it." : " A signature proves who received it."}
        </span>
      </div>
      <Badge>Suggestion</Badge>
    </div>
  );
}

const JOURNEY_RANK: Record<string, number> = {
  CREATED: 0, PAID: 1, PACKED: 2, SHIPPED: 3, IN_TRANSIT: 4, EXCEPTION: 5,
  DELIVERED: 6, PICKED_UP: 6, CONFIRMED: 7, CASE_OPEN: 8, RESOLVED: 9,
};
function journeyRank(r: OrderRow | undefined): number {
  return r ? JOURNEY_RANK[r.state] ?? 0 : 0;
}

function NextStep({ r }: { r: OrderRow }) {
  const key = r.case_state && ROUTE[r.case_state] ? r.case_state : r.route;
  if (!key || !ROUTE[key]) {
    if (r.state === "PAID") return r.packing_verdict && r.packing_verdict !== "match"
      ? <Badge tone="accent">Check packing</Badge> : <Badge tone="accent">Pack it</Badge>;
    if (r.state === "PACKED") return <Badge tone="accent">Ship it</Badge>;
    return <span className="faint">—</span>;
  }
  return (
    <div className="cell-two">
      <span><Badge tone={ROUTE[key].tone}>{ROUTE[key].label}</Badge></span>
      {r.failure_point && <span className="sub" style={{ marginTop: 3 }}>{FAILURE_POINT[r.failure_point] ?? r.failure_point}</span>}
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
  const [visible, setVisible] = useState<number | null>(null);

  const counts = useMemo(() => {
    const r = rows ?? [];
    return { action: filterRows(r, "action").length, flight: filterRows(r, "flight").length, cases: filterRows(r, "cases").length, all: r.length };
  }, [rows]);
  const active: Filter = filter ?? (counts.action > 0 ? "action" : "all");
  const shown = useMemo(() => filterRows(rows ?? [], active), [rows, active]);
  const activeMeta = FILTERS.find((f) => f.key === active)!;

  const columns = useMemo<ColDef<OrderRow>[]>(() => [
    {
      headerName: "Order", field: "item", flex: 2.2, minWidth: 250,
      filter: "agTextColumnFilter",
      getQuickFilterText: (p) => `${p.data.item} ${p.data.order_ref} ${p.data.buyer_area} ${p.data.status}`,
      cellRenderer: (p: ICellRendererParams<OrderRow>) => <ItemCell r={p.data!} />,
    },
    {
      headerName: "Journey", field: "status", width: 190,
      comparator: (_a, _b, x, y) => journeyRank(x.data) - journeyRank(y.data),
      cellRenderer: (p: ICellRendererParams<OrderRow>) => <JourneyCell r={p.data!} />,
    },
    {
      headerName: "Next step", field: "route", flex: 1.4, minWidth: 200,
      valueGetter: (p) => p.data?.case_state ?? p.data?.route ?? "",
      cellRenderer: (p: ICellRendererParams<OrderRow>) => <div className="cell-center"><NextStep r={p.data!} /></div>,
    },
    {
      headerName: "Delivery", field: "method", width: 136,
      valueFormatter: (p) => (p.value ? METHOD[String(p.value)] ?? String(p.value) : ""),
      cellRenderer: (p: ICellRendererParams<OrderRow>) => <DeliveryCell r={p.data!} />,
    },
    {
      headerName: "Evidence", width: 140,
      valueGetter: (p) => p.data?.evidence.score ?? 0,
      cellRenderer: (p: ICellRendererParams<OrderRow>) => (
        <div className="cell-center" title={`Missing: ${p.data!.evidence.missing.join(", ") || "nothing"}`}><Bar value={p.data!.evidence.score} /></div>
      ),
    },
    {
      headerName: "Risk", field: "delivery_risk", width: 96, type: "rightAligned",
      cellRenderer: (p: ICellRendererParams<OrderRow>) => <RiskCell r={p.data!} />,
    },
    {
      headerName: "At stake", field: "money_at_stake_minor", width: 118, type: "rightAligned",
      cellRenderer: (p: ICellRendererParams<OrderRow>) => <StakeCell r={p.data!} />,
    },
    {
      headerName: "Updated", field: "last_update", width: 112, sort: "desc", type: "rightAligned",
      valueFormatter: (p) => (p.value ? ago(String(p.value)) : ""),
      cellClass: "faint-cell",
    },
  ], []);

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
              <button key={f.key} aria-pressed={active === f.key} onClick={() => setFilter(f.key)}>
                {f.icon}{f.label}<span className="count">{rows ? counts[f.key] : ""}</span>
              </button>
            ))}
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
          <div className="page-head">
            <div>
              <h1>Delivery Control Room</h1>
              <p>Every order, its proof, and anything that needs you.</p>
            </div>
            <button className="btn" onClick={reload}><RefreshCw size={15} aria-hidden />Refresh</button>
          </div>

          {error && <ErrorBox message="This seller link isn't valid. Open a fresh demo from the home page." />}
          {!rows && !error && <Loading height={320} />}

          {rows && (
            <>
              <section className="kpis" aria-label="Summary">
                <div className={`kpi ${counts.action ? "attention" : ""}`}>
                  <span className="label"><Inbox aria-hidden />Needs you</span>
                  <span className="value">{counts.action}</span>
                  <span className="sub">approvals and reviews</span>
                </div>
                <div className="kpi">
                  <span className="label"><Truck aria-hidden />In transit</span>
                  <span className="value">{counts.flight}</span>
                  <span className="sub">shipped, not yet received</span>
                </div>
                <div className="kpi">
                  <span className="label"><Wallet aria-hidden />Money at stake</span>
                  <span className="value" style={{ fontSize: 22 }}>{sumStake(rows)}</span>
                  <span className="sub">open cases and parcels on the way</span>
                </div>
                <RecoveredKpi s={recovered.data ?? null} />
              </section>

              {tips.data && tips.data.length > 0 && <Tip t={tips.data[0]} />}

              <div className="table-card">
                <div className="head">
                  <div className="row" style={{ gap: 10 }}>
                    <h2 style={{ fontSize: 15 }}>{activeMeta.label}</h2>
                    <span className="count-chip num">{shown.length}</span>
                  </div>
                  <label className="grid-search">
                    <Search aria-hidden />
                    <input type="search" placeholder="Search orders, areas, status" aria-label="Search orders"
                      value={search} onChange={(e) => setSearch(e.target.value)} />
                  </label>
                </div>
                <AgGridReact<OrderRow>
                  theme={theme}
                  rowData={shown}
                  columnDefs={columns}
                  getRowId={(p) => p.data.order_id}
                  onRowClicked={(e) => e.data && setOpen(e.data)}
                  rowStyle={{ cursor: "pointer" }}
                  overlayNoRowsTemplate={`<span style='color:#8b827d'>${search ? "No orders match your search." : activeMeta.empty}</span>`}
                  quickFilterText={search}
                  defaultColDef={{ sortable: true, resizable: true, suppressHeaderMenuButton: true }}
                  onModelUpdated={(e) => setVisible(e.api.getDisplayedRowCount())}
                  animateRows
                  suppressCellFocus
                  domLayout="autoHeight"
                />
                <div className="grid-foot">
                  <span className="faint">{rows.some((r) => r.simulated) ? "Demo orders: refunds on them are simulated" : "Live PayPal sandbox orders"}</span>
                  <span className="foot-stats num">
                    <span>Rows <strong>{visible ?? shown.length}</strong> of {shown.length}</span>
                    <span>At stake <strong>{sumStake(shown)}</strong></span>
                  </span>
                </div>
              </div>
            </>
          )}
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

/** RC-05: what carriers paid back, and what is still claimable. */
function RecoveredKpi({ s }: { s: RecoverySummary | null }) {
  const open = s ? sumBy(s.open_claim_minor) : "";
  return (
    <div className="kpi">
      <span className="label"><Landmark aria-hidden />Recovered from carriers</span>
      <span className="value" style={{ fontSize: 22 }}>{s ? sumBy(s.recovered_minor) : "—"}</span>
      <span className="sub">{s && s.open_claims ? `${s.open_claims} open claim${s.open_claims === 1 ? "" : "s"} · ${open}` : "claims you filed and won"}</span>
    </div>
  );
}
