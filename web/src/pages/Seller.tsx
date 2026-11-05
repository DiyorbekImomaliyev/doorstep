import { useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry, themeQuartz, type ColDef, type ICellRendererParams } from "ag-grid-community";
import { api, type OrderRow, type PreventionTip, type RecoverySummary } from "../api";
import { Brand, ErrorBox, Loading, Meter, Pill, Toast, useAsync } from "../components/ui";
import { FAILURE_POINT, METHOD, ROUTE, ago, money, riskTone, stateTone } from "../format";
import CaseDrawer from "./CaseDrawer";

ModuleRegistry.registerModules([AllCommunityModule]);

const theme = themeQuartz.withParams({
  fontFamily: "inherit",
  fontSize: 13,
  headerFontWeight: 600,
  headerBackgroundColor: "#f6f7f9",
  headerTextColor: "#4a535e",
  borderColor: "#e3e6ea",
  rowHoverColor: "#f3f8f7",
  selectedRowBackgroundColor: "#e3f1ef",
  accentColor: "#0f5f5c",
  wrapperBorder: false,
  wrapperBorderRadius: 0,
  rowHeight: 56,
  headerHeight: 40,
  spacing: 6,
});

type Filter = "action" | "flight" | "cases" | "all";

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

/** RC-05: what carriers paid back, and what is still claimable. */
function RecoveredKpi({ s }: { s: RecoverySummary | null }) {
  const open = s ? sumBy(s.open_claim_minor) : "";
  return (
    <div className="kpi">
      <div className="label">Recovered from carriers</div>
      <div className="value" style={{ fontSize: 22 }}>{s ? sumBy(s.recovered_minor) : "—"}</div>
      <div className="sub">{s && s.open_claims ? `${s.open_claims} open claim${s.open_claims === 1 ? "" : "s"} · ${open}` : "claims you filed and won"}</div>
    </div>
  );
}

/** CF-02: from your own seller-liable cases, computed in code. */
function PreventionCard({ tips }: { tips: PreventionTip[] }) {
  return (
    <section className="card col" aria-label="Prevent the next one">
      <h3>Prevent the next one</h3>
      <ul style={{ margin: 0, paddingLeft: 18 }} className="col">
        {tips.map((t) => <li key={`${t.method}-${t.currency}-${t.band}`}>{t.text}</li>)}
      </ul>
      <span className="faint">From your own cases where you paid. Suggestions only; you decide the delivery options.</span>
    </section>
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

  const counts = useMemo(() => {
    const r = rows ?? [];
    return { action: filterRows(r, "action").length, flight: filterRows(r, "flight").length, cases: filterRows(r, "cases").length, all: r.length };
  }, [rows]);
  const active: Filter = filter ?? (counts.action > 0 ? "action" : "all");
  const shown = useMemo(() => filterRows(rows ?? [], active), [rows, active]);

  const columns = useMemo<ColDef<OrderRow>[]>(() => [
    {
      headerName: "Order", field: "item", flex: 2, minWidth: 220,
      cellRenderer: (p: ICellRendererParams<OrderRow>) => (
        <div className="cell-two"><span>{p.data!.item}</span><span className="sub">{p.data!.order_ref} · {p.data!.buyer_area || p.data!.buyer_country}</span></div>
      ),
    },
    {
      headerName: "Status", field: "status", width: 190,
      cellRenderer: (p: ICellRendererParams<OrderRow>) => <Pill tone={stateTone(p.data!.state)}>{p.data!.status}</Pill>,
    },
    {
      headerName: "Next step", field: "route", flex: 1.3, minWidth: 190,
      valueGetter: (p) => p.data?.case_state ?? p.data?.route ?? "",
      cellRenderer: (p: ICellRendererParams<OrderRow>) => {
        const r = p.data!;
        const key = r.case_state && ROUTE[r.case_state] ? r.case_state : r.route;
        if (!key || !ROUTE[key]) {
          if (r.state === "PAID") return r.packing_verdict && r.packing_verdict !== "match"
            ? <Pill tone="warn">Check packing</Pill> : <Pill tone="warn">Pack it</Pill>;
          if (r.state === "PACKED") return <Pill tone="warn">Ship it</Pill>;
          return <span className="faint">—</span>;
        }
        return (
          <div className="cell-two">
            <span><Pill tone={ROUTE[key].tone}>{ROUTE[key].label}</Pill></span>
            {r.failure_point && <span className="sub">{FAILURE_POINT[r.failure_point] ?? r.failure_point}</span>}
          </div>
        );
      },
    },
    {
      headerName: "Evidence", width: 150,
      valueGetter: (p) => p.data?.evidence.score ?? 0,
      cellRenderer: (p: ICellRendererParams<OrderRow>) => <div style={{ display: "flex", alignItems: "center", height: "100%" }} title={`Missing: ${p.data!.evidence.missing.join(", ") || "nothing"}`}><Meter value={p.data!.evidence.score} /></div>,
    },
    {
      headerName: "Risk", field: "delivery_risk", width: 100,
      cellRenderer: (p: ICellRendererParams<OrderRow>) => {
        const r = p.data!;
        if (r.delivery_risk == null) return <span className="faint">—</span>;
        return <span title={r.risk_reasons.join("\n")}><Pill tone={riskTone(r.delivery_risk)} plain>{`${(r.delivery_risk * 100).toFixed(1)}%`}</Pill></span>;
      },
    },
    {
      headerName: "Delivery", field: "method", width: 120,
      valueFormatter: (p) => (p.value ? METHOD[p.value as string] ?? String(p.value) : "—"),
    },
    {
      headerName: "At stake", field: "money_at_stake_minor", width: 110, type: "rightAligned",
      valueFormatter: (p) => (p.data && p.data.money_at_stake_minor ? money(p.data.money_at_stake_minor, p.data.currency) : "—"),
    },
    {
      headerName: "Updated", field: "last_update", width: 120, sort: "desc",
      valueFormatter: (p) => (p.value ? ago(String(p.value)) : ""),
    },
  ], []);

  const evidenceAvg = rows && rows.length ? rows.reduce((a, r) => a + r.evidence.score, 0) / rows.length : 0;
  const settled = (rows ?? []).filter((r) => r.case_state === "SETTLED").length;

  return (
    <>
      <Brand right={<span className="muted">{me.data?.name ?? ""}</span>} />
      <main className="page stack-lg">
        <div className="spread wrap">
          <div className="col" style={{ gap: 2 }}>
            <h1>Delivery Control Room</h1>
            <p className="muted">Every order, its proof, and anything that needs you.</p>
          </div>
          <button className="btn" onClick={reload}>Refresh</button>
        </div>

        {error && <ErrorBox message="This seller link isn't valid. Open a fresh demo from the home page." />}
        {!rows && !error && <Loading height={300} />}

        {rows && (
          <>
            <section className="kpis" aria-label="Summary">
              <div className={`kpi ${counts.action ? "attention" : ""}`}><div className="label">Needs you</div><div className="value">{counts.action}</div><div className="sub">approvals and reviews</div></div>
              <div className="kpi"><div className="label">In flight</div><div className="value">{counts.flight}</div><div className="sub">shipped, not yet received</div></div>
              <div className="kpi"><div className="label">Money at stake</div><div className="value" style={{ fontSize: 22 }}>{sumStake(rows)}</div><div className="sub">open cases + in flight</div></div>
              <div className="kpi"><div className="label">Evidence coverage</div><div className="value">{Math.round(evidenceAvg * 100)}%</div><div className="sub">proof per order, average</div></div>
              <RecoveredKpi s={recovered.data ?? null} />
              <div className="kpi"><div className="label">Cases settled</div><div className="value">{settled}</div><div className="sub">with signed receipts</div></div>
            </section>

            {tips.data && tips.data.length > 0 && <PreventionCard tips={tips.data} />}

            <div className="spread wrap">
              <div className="seg" role="group" aria-label="Filter orders">
                {([["action", "Needs you"], ["flight", "In flight"], ["cases", "Cases"], ["all", "All"]] as [Filter, string][]).map(([k, label]) => (
                  <button key={k} aria-pressed={active === k} onClick={() => setFilter(k)}>
                    {label}<span className="count">{counts[k]}</span>
                  </button>
                ))}
              </div>
              {rows.some((r) => r.simulated) && <span className="faint">Seeded demo orders: refunds on them are simulated.</span>}
            </div>

            <div className="grid-wrap">
              <AgGridReact<OrderRow>
                theme={theme}
                rowData={shown}
                columnDefs={columns}
                getRowId={(p) => p.data.order_id}
                onRowClicked={(e) => e.data && setOpen(e.data)}
                rowStyle={{ cursor: "pointer" }}
                overlayNoRowsTemplate="<span style='color:#7a838e'>Nothing here. You're all caught up.</span>"
                suppressCellFocus
                domLayout="autoHeight"
              />
            </div>
          </>
        )}
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
    </>
  );
}
