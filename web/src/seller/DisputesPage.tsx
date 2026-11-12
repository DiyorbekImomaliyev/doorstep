import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact, type CustomCellRendererProps, type CustomDetailCellRendererProps, type CustomStatusPanelProps } from "ag-grid-react";
import { ModuleRegistry, type CellKeyDownEvent, type ColDef, type GridApi, type RowClickedEvent, type StatusPanelDef } from "ag-grid-community";
import { MasterDetailModule, StatusBarModule } from "ag-grid-enterprise";
import { ArrowUpRight, ChevronDown, Gavel, Info, RefreshCw, Scale, Send, ShieldCheck, Trophy, Wallet } from "lucide-react";
import type { DisputeRow } from "../api";
import { ErrorBox, useAsync, usePoll } from "../components/ui";
import { ago, money, type Tone } from "../format";
import { gridTheme } from "../grid/theme";
import { useMedia } from "../motion";
import Chips from "./Chips";
import { CopyId, ItemCell, PageHead } from "./LedgerGrid";
import { disputesDesk, type DeskRow, type Stage } from "./disputes/disputesApi";
import { DisputeDetail, ProtectionMeter, ReplyBy, SEGMENTS, StagePill, actionOf, causeText, reasonText, shortId, stageOf } from "./disputes/parts";
import "./disputes/disputes.css";

ModuleRegistry.registerModules([MasterDetailModule, StatusBarModule]);

type Seg = Stage | "all";
/** The chip dots match the row's stage pill. */
const SEG_TONE: Record<Stage, Tone> = { needs_reply: "accent", evidence: "neutral", offer: "neutral", resolved: "good" };
const SOURCE = "Demo disputes · simulated";

/** Where the disputes come from, on the left of the grid's status bar (as on Claims). */
function SourcePanel(p: CustomStatusPanelProps & { text: string }) {
  return p.text ? <span className="source-panel">{p.text}</span> : null;
}

/** The latest rows and the drawer opener, for detail panels the grid renders in its own tree. */
const Desk = createContext<{ byId: Map<string, DeskRow>; open: (r: DeskRow) => void }>({ byId: new Map(), open: () => {} });

const sig = (r: DeskRow) => [r.status, r.stage, r.evidence?.status, r.offer?.status, r.protection.met, r.timeline.length].join("|");

function perCurrency(rows: DeskRow[]): string[] {
  const by: Record<string, number> = {};
  for (const r of rows) by[r.currency] = (by[r.currency] ?? 0) + r.amount_minor;
  return Object.entries(by).filter(([, v]) => v > 0).map(([c, v]) => money(v, c));
}

function Hint({ text }: { text: string }) {
  return <span className="hint" tabIndex={0} role="note" aria-label={text} data-tip={text}><Info aria-hidden /></span>;
}

function DeskKpis({ rows }: { rows: DeskRow[] | null }) {
  if (!rows) {
    return (
      <section className="dd-kpis" aria-hidden>
        {[0, 1, 2, 3].map((i) => <div key={i} className="kpi kpi-skel"><span className="skel w40" /><span className="skel w20 tall" /><span className="skel w60" /></div>)}
      </section>
    );
  }
  const open = rows.filter((r) => r.status !== "resolved");
  const reply = open.filter((r) => r.stage === "needs_reply").length;
  const resolved = rows.filter((r) => r.status === "resolved");
  const won = resolved.filter((r) => r.outcome?.result === "won").length;
  const lost = resolved.filter((r) => r.outcome?.result === "lost").length;
  const stake = perCurrency(open);
  const meets = open.filter((r) => r.protection.met === r.protection.total).length;
  const checks = open[0]?.protection.total ?? 5;
  return (
    <section className="dd-kpis" aria-label="Summary">
      <div className={`kpi ${reply ? "attention" : ""}`}>
        <span className="label"><Gavel aria-hidden />Open</span>
        <span className="value num">{open.length}</span>
        <span className="sub">{reply ? `${reply} need${reply === 1 ? "s" : ""} a reply` : "none need you"}</span>
      </div>
      <div className="kpi">
        <span className="label"><Wallet aria-hidden />In dispute<Hint text="Open disputes, one total per currency. PayPal's disputed amount when it sent one, else the order amount." /></span>
        <span className="value num dd-money">{stake.length ? stake.map((s) => <span key={s}>{s}</span>) : "—"}</span>
        <span className="sub">{stake.length > 1 ? "per currency" : "open disputes"}</span>
      </div>
      <div className="kpi">
        <span className="label"><Trophy aria-hidden />Outcomes<Hint text="From PayPal's stored outcome codes only: seller favour or buyer cancelled is a win, buyer favour a loss." /></span>
        <span className="value num">{resolved.length ? <>{won} won · {lost} lost</> : "—"}</span>
        <span className="sub">{resolved.length ? `of ${resolved.length} resolved` : "none resolved yet"}</span>
      </div>
      <div className="kpi">
        <span className="label"><ShieldCheck aria-hidden />Seller Protection<Hint text="Open disputes that meet every check: PayPal's verdict, shipped to the PayPal address, delivered scan, tracking on PayPal, reply sent." /></span>
        <span className="value num">{open.length ? `${meets} of ${open.length}` : "—"}</span>
        <span className="sub">{open.length ? `meet all ${checks} checks` : "no open disputes"}</span>
      </div>
    </section>
  );
}

// ---------- grid cells ----------

function DisputeCell(p: CustomCellRendererProps<DeskRow>) {
  const r = p.data;
  if (!r) return null;
  return <ItemCell item={r.item} sub={`${r.order_ref} · ${ago(r.opened_at)}`} />;
}

function ReasonCell(p: CustomCellRendererProps<DeskRow>) {
  const r = p.data;
  if (!r) return null;
  const cause = causeText(r);
  return (
    <span className="cell-two">
      <span>{reasonText(r)}</span>
      {cause && <span className="sub" title="Doorstep's likely cause">{cause}</span>}
    </span>
  );
}

function ActionCell(p: CustomCellRendererProps<DeskRow>) {
  const { open } = useContext(Desk);
  const r = p.data;
  if (!r) return null;
  const a = actionOf(r);
  return (
    <button type="button" className={`btn ${a.primary ? "primary" : "ghost"} dd-row-act`} onClick={() => open(r)}>
      {a.label === "Send evidence" ? <Send size={14} aria-hidden /> : <ArrowUpRight size={14} aria-hidden />}{a.label}
    </button>
  );
}

function Detail(p: CustomDetailCellRendererProps<DeskRow>) {
  const { byId, open } = useContext(Desk);
  const r = (p.data && byId.get(p.data.dispute_id)) ?? p.data;
  if (!r) return null;
  return <DisputeDetail r={r} onOpen={() => open(r)} />;
}

function DeskGrid({ rows, onReady, openFirst }: { rows: DeskRow[]; onReady: (a: GridApi<DeskRow>) => void; openFirst: string | null }) {
  const hasDue = rows.some((r) => r.paypal.response_due_at);
  const source = rows.some((r) => r.simulated) ? SOURCE : "";
  const statusBar = useMemo((): { statusPanels: StatusPanelDef[] } => ({
    statusPanels: [
      { statusPanel: SourcePanel, statusPanelParams: { text: source }, align: "left" },
      { statusPanel: "agTotalAndFilteredRowCountComponent", align: "right" },
    ],
  }), [source]);
  const columns = useMemo((): ColDef<DeskRow>[] => [
    { headerName: "Dispute", field: "item", flex: 2, minWidth: 220, cellRenderer: "agGroupCellRenderer", cellRendererParams: { innerRenderer: DisputeCell, suppressCount: true } },
    { headerName: "PayPal ID", field: "paypal_dispute_id", width: 198, cellRenderer: (q: CustomCellRendererProps<DeskRow>) => q.data && <CopyId value={q.data.paypal_dispute_id} shown={shortId(q.data.paypal_dispute_id)} /> },
    { headerName: "Reason", colId: "reason", flex: 1, minWidth: 160, valueGetter: (q) => (q.data ? reasonText(q.data) : ""), cellRenderer: ReasonCell },
    { headerName: "Amount", field: "amount_minor", width: 104, headerClass: "ag-right-aligned-header", cellClass: "ag-right-aligned-cell num lg-money",
      valueFormatter: (q) => (q.data ? money(q.data.amount_minor, q.data.currency) : ""),
      tooltipValueGetter: (q) => (q.data?.amount_from === "paypal" ? "PayPal's disputed amount" : "Order amount") },
    { headerName: "Stage", colId: "stage", width: 138, valueGetter: (q) => (q.data ? stageOf(q.data).label : ""), cellRenderer: (q: CustomCellRendererProps<DeskRow>) => q.data && <StagePill r={q.data} /> },
    ...(hasDue ? [{ headerName: "Reply by", colId: "due", width: 118, valueGetter: (q: { data?: DeskRow }) => q.data?.paypal.response_due_at ?? "",
      cellRenderer: (q: CustomCellRendererProps<DeskRow>) => q.data && <ReplyBy iso={q.data.paypal.response_due_at} /> } as ColDef<DeskRow>] : []),
    { headerName: "Protection", colId: "protection", width: 124, headerTooltip: "Seller Protection checks met",
      // The states are in the value, so the cell redraws when any check changes; it sorts by checks met.
      valueGetter: (q) => (q.data ? `${q.data.protection.met}:${q.data.protection.checks.map((c) => c.state).join(",")}` : ""),
      comparator: (a: string, b: string) => parseInt(a, 10) - parseInt(b, 10), cellRenderer: (q: CustomCellRendererProps<DeskRow>) => q.data && <ProtectionMeter r={q.data} /> },
    { headerName: "", colId: "action", width: 156, sortable: false, resizable: false, valueGetter: (q) => (q.data ? actionOf(q.data).label : ""), cellRenderer: ActionCell, cellClass: "dd-act-cell" },
  ], [hasDue]);
  const defaultColDef = useMemo<ColDef<DeskRow>>(() => ({ sortable: true, resizable: true, suppressHeaderMenuButton: true, suppressHeaderFilterButton: true }), []);
  const clicked = (e: RowClickedEvent<DeskRow>) => {
    if ((e.event?.target as HTMLElement | null)?.closest("button, a, summary, .ag-group-contracted, .ag-group-expanded")) return;
    e.node.setExpanded(!e.node.expanded);
  };
  const keyed = (e: CellKeyDownEvent<DeskRow>) => {
    const k = (e.event as KeyboardEvent | null)?.key;
    if (k !== "Enter" && k !== " ") return;
    if ((e.event?.target as HTMLElement | null)?.closest("button, a")) return;
    e.event?.preventDefault();
    e.node.setExpanded(!e.node.expanded);
  };
  return (
    <section className="table-card ledger-grid orders-grid dd-grid" aria-label="PayPal disputes">
      <AgGridReact<DeskRow>
        theme={gridTheme}
        rowData={rows}
        columnDefs={columns}
        defaultColDef={defaultColDef}
        getRowId={(q) => q.data.dispute_id}
        masterDetail
        isRowMaster={() => true}
        detailCellRenderer={Detail}
        detailRowAutoHeight
        onRowClicked={clicked}
        onCellKeyDown={keyed}
        onGridReady={(e) => onReady(e.api)}
        onFirstDataRendered={(e) => { if (openFirst) e.api.getRowNode(openFirst)?.setExpanded(true); }}
        rowHeight={60}
        statusBar={statusBar}
        tooltipShowDelay={250}
        domLayout="autoHeight"
      />
    </section>
  );
}

// ---------- phone ----------

function PhoneCards({ rows, changed }: { rows: DeskRow[]; changed: Set<string> }) {
  const { open } = useContext(Desk);
  const [shown, setShown] = useState<string | null>(() => rows.find((r) => r.stage === "needs_reply")?.dispute_id ?? null);
  return (
    <section className="table-card dd-cards" aria-label="PayPal disputes">
      <ul className="ledger-cards">
        {rows.map((r) => {
          const a = actionOf(r);
          const isOpen = shown === r.dispute_id;
          return (
            <li key={r.dispute_id} className={changed.has(r.dispute_id) ? "dd-flash" : ""}>
              <div className="ledger-card dd-card">
                <span className="lc-top"><span className="lc-item">{r.item}</span><span className="lc-money num">{money(r.amount_minor, r.currency)}</span></span>
                <span className="lc-meta">{r.order_ref} · {reasonText(r)} · {ago(r.opened_at)}</span>
                <span className="lc-bottom"><StagePill r={r} /><ProtectionMeter r={r} />{r.paypal.response_due_at && <ReplyBy iso={r.paypal.response_due_at} />}</span>
                <span className="dd-card-acts">
                  <button type="button" className="btn ghost" aria-expanded={isOpen} onClick={() => setShown(isOpen ? null : r.dispute_id)}>
                    <ChevronDown size={15} aria-hidden className={isOpen ? "dd-rot" : ""} />{isOpen ? "Hide" : "Details"}
                  </button>
                  <button type="button" className={`btn ${a.primary ? "primary" : ""}`} onClick={() => open(r)}>{a.label}</button>
                </span>
              </div>
              {isOpen && <DisputeDetail r={r} onOpen={() => open(r)} />}
            </li>
          );
        })}
      </ul>
      {rows.some((r) => r.simulated) && <p className="desk-source faint">{SOURCE}</p>}
    </section>
  );
}

// ---------- page ----------

export default function DisputesPage({ rows, error, onOpenOrder, token, onChanged }: { rows: DisputeRow[] | null; error: string | null; onOpenOrder: (orderId: string) => void; token: string; onChanged: () => void }) {
  const desk = useAsync(() => disputesDesk(token), [token]);
  const data = desk.data;
  const phone = useMedia("(max-width: 600px)");
  const [seg, setSeg] = useState<Seg>("all");
  const [updated, setUpdated] = useState<Date | null>(null);
  const gridApi = useRef<GridApi<DeskRow> | null>(null);

  // The drawer's actions reload the page's rows; follow them. The worker then finishes the job:
  // poll while a dispute is open (faster while a reply is on its way).
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    desk.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);
  const anyOpen = !!data?.some((r) => r.status !== "resolved");
  const inFlight = !!data?.some((r) => r.evidence?.status === "queued" || r.offer?.status === "queued");
  usePoll(desk.reload, anyOpen, inFlight ? 2000 : 5000);

  // What changed since the last read: flash those rows, and refresh the sidebar when the open count moved.
  const seen = useRef<Map<string, string> | null>(null);
  const [changed, setChanged] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!data) return;
    setUpdated(new Date());
    const next = new Map(data.map((r) => [r.dispute_id, sig(r)]));
    const prev = seen.current;
    seen.current = next;
    if (!prev) return;
    const ids = data.filter((r) => prev.get(r.dispute_id) !== next.get(r.dispute_id)).map((r) => r.dispute_id);
    if (!ids.length) return;
    setChanged(new Set(ids));
    const api = gridApi.current;
    if (api && !api.isDestroyed()) {
      const nodes = ids.map((id) => api.getRowNode(id)).filter((n) => !!n);
      window.requestAnimationFrame(() => { if (!api.isDestroyed()) api.flashCells({ rowNodes: nodes }); });
    }
    const t = window.setTimeout(() => setChanged(new Set()), 2400);
    const openNow = data.filter((r) => r.status !== "resolved").length;
    if (rows && openNow !== rows.filter((r) => r.status === "open").length) onChanged();
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const counts = useMemo(() => {
    const c: Record<Seg, number> = { all: 0, needs_reply: 0, evidence: 0, offer: 0, resolved: 0 };
    for (const r of data ?? []) { c.all += 1; c[r.stage] += 1; }
    return c;
  }, [data]);
  const shown = useMemo(() => (data ?? []).filter((r) => seg === "all" || r.stage === seg), [data, seg]);
  const ctx = useMemo(() => ({ byId: new Map((data ?? []).map((r) => [r.dispute_id, r])), open: (r: DeskRow) => onOpenOrder(r.order_id) }), [data, onOpenOrder]);

  const onReady = useCallback((api: GridApi<DeskRow>) => { gridApi.current = api; }, []);
  // The first dispute that needs a reply opens with its story showing.
  const openFirst = (data?.find((r) => r.stage === "needs_reply") ?? data?.[0])?.dispute_id ?? null;

  const failed = (desk.error && !data) || error;
  const label = SEGMENTS.find((s) => s.key === seg)!.label;
  return (
    <Desk.Provider value={ctx}>
      <div className="dd-page">
        <PageHead title="Disputes" line="PayPal disputes on your orders" right={
          <div className="row" style={{ gap: 10 }}>
            <span className="live" title={anyOpen ? "Refreshes every few seconds while a dispute is open" : "Up to date"}>
              <span className="live-dot" aria-hidden />Live{updated ? ` · ${updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}
            </span>
            <button className="btn icon-btn" onClick={() => desk.reload()} aria-label="Refresh disputes" title="Refresh now"><RefreshCw size={15} aria-hidden /></button>
          </div>
        } />
        {failed && <ErrorBox message="Disputes did not load. Refresh to try again." />}
        <DeskKpis rows={data} />
        {!data && !failed && (
          <div className="dd-skel" aria-busy="true" aria-label="Loading disputes">
            <span className="skel dd-skel-seg" />
            <div className="table-card">{[0, 1, 2].map((i) => <div key={i} className="dd-skel-row"><span className="skel w40" /><span className="skel w20" /><span className="skel w20" /></div>)}</div>
          </div>
        )}
        {data && data.length === 0 && (
          <section className="table-card dd-empty" aria-label="No disputes">
            <span className="dd-empty-icon" aria-hidden><Scale /></span>
            <h2>No disputes</h2>
            <p className="muted">PayPal disputes on your orders land here.</p>
          </section>
        )}
        {data && data.length > 0 && (
          <>
            <div className="desk-bar">
              <Chips<Seg> label="Filter disputes" value={seg} onChange={setSeg}
                items={SEGMENTS.map((s) => ({ key: s.key, label: s.label, count: counts[s.key], title: s.tip, tone: s.key === "all" ? undefined : SEG_TONE[s.key] }))} />
            </div>
            {shown.length === 0 ? (
              <section className="table-card dd-none" aria-label="PayPal disputes">
                <span>Nothing in {label}</span>
                <button type="button" className="btn ghost" onClick={() => setSeg("all")}>Show all</button>
              </section>
            ) : phone ? <PhoneCards rows={shown} changed={changed} /> : <DeskGrid rows={shown} onReady={onReady} openFirst={openFirst} />}
          </>
        )}
      </div>
    </Desk.Provider>
  );
}
