import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GridApi } from "ag-grid-community";
import { BarChart3, BookOpen, Check, ReceiptText, Columns3, Copy, Download, FileText, FileWarning, FolderOpen, Info, Inbox, Landmark, Lightbulb, List, RefreshCw, Search, Truck, Wallet, Zap } from "lucide-react";
import { api, type Autopilot, type AutopilotStatement, type Economics, type GridAnswer, type GridPlan, type OrderRow, type PolicyCheck, type PreventionTip, type RecoverySummary } from "../api";
import { OPEN_ORDER_EVENT, type OpenOrderDetail } from "../events";
import { Badge, ErrorBox, Loading, Toast, Wordmark, useAsync } from "../components/ui";
import CommandBar, { type Command } from "../components/CommandBar";
import { itemKind } from "../components/gridCells";
import { money } from "../format";
import CaseDrawer from "./CaseDrawer";
import OrdersGrid, { exportExcel, exportHandoffPdf, revealRows, sumStake, toggleColumnsPanel } from "./OrdersGrid";
import Reading from "../grid/Reading";
import { applyPlan, menuFromGrid, menuSource, restore, snapshot, type Snapshot } from "../grid/ask";

// AG Studio is large: it loads only when the seller opens Insights.
const Insights = lazy(() => import("../insights/Insights"));

type Filter = "action" | "flight" | "cases" | "all";
const VIEW_OF: Record<GridPlan["view"], Filter> = { needs_you: "action", in_transit: "flight", cases: "cases", all: "all" };

const FILTERS: { key: Filter; label: string; icon: React.ReactNode; empty: string }[] = [
  { key: "action", label: "Needs you", icon: <Inbox />, empty: "All caught up." },
  { key: "flight", label: "In transit", icon: <Truck />, empty: "Nothing in transit." },
  { key: "cases", label: "Cases", icon: <FolderOpen />, empty: "No cases." },
  { key: "all", label: "All orders", icon: <List />, empty: "No orders." },
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

/** What the seller would notice changing on a row: where it is, its case, its proof and money. */
const rowSig = (r: OrderRow) =>
  [r.state, r.case_state, r.status, r.evidence.score, r.money_at_stake_minor, r.needs_action, r.delivery_risk].join("|");

const METHOD_TEXT: Record<string, string> = { home: "home delivery", locker: "locker pickup", signature: "signature" };

/** CF-02, in one line: computed in code from the seller's own seller-liable cases. */
/** EC-01/EC-02: the seller's plan this month, what Doorstep measurably did, and the published
 * costs beside it. No "saved" total, since not every problem would have become a failed delivery
 * or a lost claim: the break-even restates the price as failed deliveries. */
function PlanStrip({ e }: { e: Economics }) {
  const month = new Date(`${e.month}-01T12:00:00`).toLocaleString("en-GB", { month: "long" });
  const price = e.price_minor === 0 ? "Free" : `${money(e.price_minor, e.currency)} so far`;
  const fee = money(e.dispute_fee_minor, e.currency);
  const failed = money(e.failed_delivery_cost_minor, e.currency);
  return (
    <section className="plan-strip" aria-label="Your Doorstep plan">
      <span className="tip-icon" aria-hidden><ReceiptText size={16} /></span>
      <div className="col grow" style={{ gap: 2 }}>
        <span><strong>{month} plan:</strong> {e.orders_this_month} order{e.orders_this_month === 1 ? "" : "s"} · {price}</span>
        <span className="muted" style={{ fontSize: 13 }}>
          {e.free_orders_per_month} free, then {money(e.per_order_minor, e.currency)} each · max {money(e.monthly_cap_minor, e.currency)}
        </span>
      </div>
      <div className="col plan-side" style={{ gap: 2 }}>
        <span>
          <strong className="num">{e.addresses_fixed}</strong> address{e.addresses_fixed === 1 ? "" : "es"} fixed ·{" "}
          <strong className="num">{e.settled_before_dispute}</strong> settled, no dispute
        </span>
        <span className="muted" style={{ fontSize: 13 }}
          title={`${e.break_even_failures > 0 ? `This month's price equals ${e.break_even_failures} failed ${e.break_even_failures === 1 ? "delivery" : "deliveries"}. ` : ""}A lost PayPal claim costs ${fee}: none under ${money(e.fee_waived_below_minor, e.currency)} or with Seller Protection.`}>
          Failed delivery {failed} (<a href={e.failed_delivery_source} target="_blank" rel="noreferrer">Loqate</a>) · lost claim {fee} (<a href={e.fee_source} target="_blank" rel="noreferrer">fees</a>, <a href={e.fee_terms_source} target="_blank" rel="noreferrer">terms</a>)
        </span>
      </div>
    </section>
  );
}

function Tip({ t }: { t: PreventionTip }) {
  return (
    <div className="tip">
      <span className="tip-icon" aria-hidden><Lightbulb size={16} /></span>
      <div className="col grow" style={{ gap: 2 }}>
        <span style={{ fontWeight: 600 }}>Offer {METHOD_TEXT[t.suggested_method] ?? t.suggested_method} on {t.band} orders</span>
        <span className="muted" style={{ fontSize: 13 }} title={t.suggested_method === "locker" ? "A locker pickup code proves the buyer has it." : "A signature proves who received it."}>
          {t.cases} of {t.orders} {METHOD_TEXT[t.method] ?? t.method} orders became cases ({money(t.money_minor, t.currency)}).
        </span>
      </div>
      <Badge>Suggestion</Badge>
    </div>
  );
}

const KIND: Record<string, string> = { CARRIER_LOST: "lost parcels", DAMAGED_IN_TRANSIT: "parcels damaged in transit" };
const kindText = (fp: string) => KIND[fp] ?? fp.toLowerCase().replaceAll("_", " ");

/** AP-06: the seller's own pattern, offered as a rule they turn on; active rules with an off switch.
 *  Suggestions are computed in code from the seller's approvals; money still moves only through the policy. */
function AutopilotCard({ token, data, statement, onChange, onOpen }: {
  token: string; data: Autopilot; statement: AutopilotStatement | null; onChange: (m: string) => void; onOpen: (orderId: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true); setErr(null);
    try { await fn(); onChange(done); } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }
  const s = data.suggestions[0];
  if (s) {
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
  if (!data.rules.length && !statement?.cases.length) return null;
  return (
    <section className="autopilot on" aria-label="Autopilot rules">
      <span className="ap-icon" aria-hidden><Zap /></span>
      <div className="col grow" style={{ gap: 6, minWidth: 0 }}>
        {data.rules.map((r) => {
          const kind = kindText(r.failure_point);
          const max = money(r.max_amount_minor, r.currency);
          return (
            <div key={r.id} className="ap-rule">
              <span className="col" style={{ gap: 2, minWidth: 0 }}>
                <span className="ap-title">Autopilot on: {kind} up to {max}</span>
                <span className="ap-text">Within your daily cap · learned from {r.precedents} approvals</span>
              </span>
              <button className="btn ghost" disabled={busy} onClick={() => act(() => api.autopilotOff(token, r.id), `Autopilot off: ${kind} come to you again.`)}>Turn off</button>
            </div>
          );
        })}
        {!data.rules.length && <span className="ap-title">Autopilot is off</span>}
        {statement && <StatementLine s={statement} onOpen={onOpen} />}
        {err && <ErrorBox message={err} />}
      </div>
    </section>
  );
}

/** AP-07: automatic spending, accounted for. Figures come from stored records, never a model. */
function StatementLine({ s, onOpen }: { s: AutopilotStatement; onOpen: (orderId: string) => void }) {
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
  if (!s.cases.length) return <span className="ap-statement muted">No automatic refunds in {s.days} days.</span>;
  return (
    <div className="ap-statement" aria-label="Autopilot statement">
      {s.totals.map((t) => (
        <span key={t.currency}>
          <strong>Last {s.days} days: {plural(t.refunds, "automatic refund")}, {money(t.refunded_minor, t.currency)}</strong>
          {t.claimable_minor > 0 && <> · {money(t.claimable_minor, t.currency)} to claim back{t.ready_to_send > 0 && ` (${plural(t.ready_to_send, "claim")} ready to send)`}</>}
          {t.recovered_minor > 0 && <> · {money(t.recovered_minor, t.currency)} recovered</>}
        </span>
      ))}
      <span className="ap-cases">
        {s.cases.map((c) => (
          <button key={c.case_id} className="chip" onClick={() => onOpen(c.order_id)} title={`Open ${c.order_ref}`}>
            {c.item} · {money(c.amount_minor, c.currency)}
          </button>
        ))}
      </span>
    </div>
  );
}

/** PC-05: sentences in the seller's own policy that the rules contradict. The model reads; code
 *  checked every quote word for word against the policy before it reached this card. */
function PolicyCard({ check }: { check: PolicyCheck }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);
  const n = check.issues.length;
  const law = check.source.split(" — ")[0];
  async function copy(i: number, text: string) {
    try { await navigator.clipboard.writeText(text); setCopied(i); window.setTimeout(() => setCopied(null), 1600); } catch { /* clipboard blocked: the text stays selectable */ }
  }
  return (
    <section className="autopilot policy" aria-label="Policy check">
      <span className="ap-icon" aria-hidden><FileWarning /></span>
      <div className="col grow" style={{ gap: 6, minWidth: 0 }}>
        <div className="ap-rule">
          <span className="col" style={{ gap: 2, minWidth: 0 }}>
            <span className="ap-title">{n === 1 ? "One sentence" : `${n} sentences`} in your delivery policy {n === 1 ? "conflicts" : "conflict"} with {law}</span>
            {!open && <span className="ap-text">{check.issues[0].problem}</span>}
          </span>
          <button className="btn ghost" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide" : "Review"}</button>
        </div>
        {open && (
          <ol className="pc-issues">
            {check.issues.map((it, i) => (
              <li key={i}>
                <blockquote>“{it.quote}”</blockquote>
                <p>{it.problem}</p>
                <div className="pc-fix">
                  <span className="faint">Suggested wording</span>
                  <p>{it.suggestion}</p>
                  <button className="btn ghost" onClick={() => copy(i, it.suggestion)}>{copied === i ? <><Check aria-hidden />Copied</> : <><Copy aria-hidden />Copy</>}</button>
                </div>
                <span className="faint">Based on: {it.facts.map((f) => (f.source === "doorstep_settings" ? "your Doorstep settings" : `${f.source} (${check.rule_version})`)).filter((v, j, a) => a.indexOf(v) === j).join(", ")}</span>
              </li>
            ))}
          </ol>
        )}
        {open && <span className="faint">Read by AI; every quote was matched word for word to your policy before it was shown. Nothing changes until you edit your policy. {check.disclaimer}</span>}
      </div>
    </section>
  );
}

export default function Seller({ token }: { token: string }) {
  const me = useAsync(() => api.me(token), [token]);
  const { data: rows, error, reload: reloadOrders } = useAsync(() => api.orders(token), [token]);
  const recovered = useAsync(() => api.recoveries(token), [token]);
  const costs = useAsync(() => api.economics(token), [token]);
  const tips = useAsync(() => api.prevention(token), [token]);
  const auto = useAsync(() => api.autopilot(token), [token]);
  const spent = useAsync(() => api.autopilotStatement(token), [token]);
  const policy = useAsync(() => api.policyCheck(token), [token]);
  const reload = () => { reloadOrders(); recovered.reload(); tips.reload(); auto.reload(); spent.reload(); policy.reload(); costs.reload(); };
  const [filter, setFilter] = useState<Filter | null>(null);
  const [open, setOpen] = useState<OrderRow | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  // Live diff: orders whose journey, case, proof or money changed since the last poll. Compared
  // across all orders (not just the visible list), so switching views never counts as a change.
  const seen = useRef<Map<string, string> | null>(null);
  const [changed, setChanged] = useState<{ ids: Set<string>; at: Date } | null>(null);
  useEffect(() => {
    if (!rows) return;
    const next = new Map(rows.map((r) => [r.order_id, rowSig(r)]));
    const prev = seen.current;
    seen.current = next;
    if (!prev) return;
    const ids = rows.filter((r) => prev.get(r.order_id) !== next.get(r.order_id)).map((r) => r.order_id);
    if (ids.length) setChanged({ ids: new Set(ids), at: new Date() });
  }, [rows]);
  const [search, setSearch] = useState("");
  const searchBox = useRef<HTMLInputElement>(null);
  const [cmd, setCmd] = useState(false);
  // ⌘K / Ctrl+K opens the command bar anywhere; "/" jumps to the search box.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmd((v) => !v); return; }
      const typing = (e.target as HTMLElement | null)?.closest("input, textarea, select, [contenteditable]");
      if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); searchBox.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const gridApi = useRef<GridApi | null>(null);
  const [page, setPage] = useState<"orders" | "insights">(() => (location.hash === "#insights" ? "insights" : "orders"));
  const go = (p: "orders" | "insights") => { setPage(p); history.replaceState(null, "", p === "insights" ? "#insights" : location.pathname + location.search); };

  // Live: the worker moves orders on (tracking, refunds); pull the table quietly while the tab is open.
  useEffect(() => {
    const t = window.setInterval(() => { if (document.visibilityState === "visible") reloadOrders(); }, LIVE_MS);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const onOpen = useCallback((r: OrderRow) => setOpen(r), []);
  // S1: Insights' brief opens a case here, over whichever page is showing. An order the list has not
  // loaded yet is fetched once; if it is still missing, nothing opens.
  const [wanted, setWanted] = useState<{ id: string; before?: OrderRow[] } | null>(null);
  useEffect(() => {
    const on = (e: Event) => { const id = (e as CustomEvent<OpenOrderDetail>).detail?.orderId; if (id) setWanted({ id }); };
    window.addEventListener(OPEN_ORDER_EVENT, on);
    return () => window.removeEventListener(OPEN_ORDER_EVENT, on);
  }, []);
  useEffect(() => {
    if (!wanted || !rows) return;
    const r = rows.find((x) => x.order_id === wanted.id);
    if (r) { setOpen(r); setWanted(null); }
    else if (!wanted.before) { setWanted({ ...wanted, before: rows }); reloadOrders(); }
    else if (wanted.before !== rows) setWanted(null); // fetched again, still not there
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, rows]);
  const onReady = useCallback((a: GridApi) => { gridApi.current = a; }, []);

  const counts = useMemo(() => {
    const r = rows ?? [];
    return { action: filterRows(r, "action").length, flight: filterRows(r, "flight").length, cases: filterRows(r, "cases").length, all: r.length };
  }, [rows]);
  const active: Filter = filter ?? (counts.action > 0 ? "action" : "all");
  const [updated, setUpdated] = useState<Date | null>(null);
  useEffect(() => { if (rows) setUpdated(new Date()); }, [rows]);
  // Insights (AG Studio) is the largest download: fetch it in idle time once the grid is up, so the
  // tab opens at once. Skipped when the browser asks to save data.
  const gridReady = !!rows;
  useEffect(() => {
    const saveData = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData;
    if (!gridReady || saveData) return;
    const load = () => void import("../insights/Insights");
    if (typeof window.requestIdleCallback === "function") {
      const id = window.requestIdleCallback(load, { timeout: 5000 });
      return () => window.cancelIdleCallback(id);
    }
    const t = window.setTimeout(load, 2500); // Safari has no requestIdleCallback
    return () => window.clearTimeout(t);
  }, [gridReady]);
  // G3 Ask the grid: Enter in the search box asks; the answer comes back as grid filters and sort.
  const [asking, setAsking] = useState(false);
  const [reading, setReading] = useState<GridAnswer | null>(null);
  const undoStack = useRef<Snapshot[]>([]);
  const answered = useRef<GridAnswer | null>(null);
  /** Run once the grid has taken the rows of a newly picked view (or now, if the view is unchanged). */
  const inView = (view: Filter, fn: () => void) => {
    const a = gridApi.current;
    if (!a || view === active) { fn(); return; }
    const once = () => { a.removeEventListener("rowDataUpdated", once); window.requestAnimationFrame(fn); };
    a.addEventListener("rowDataUpdated", once);
    setFilter(view);
  };
  const askGrid = async (question: string) => {
    const a = gridApi.current;
    const q = question.trim();
    if (!a || asking || q.length < 2) return;
    setAsking(true);
    try {
      const menu = menuFromGrid(a);
      if (import.meta.env.DEV) console.debug("grid menu from", menuSource, menu);
      const res = await api.gridAsk(token, q, menu);
      setSearch("");
      if (res.plan.answerable) {
        undoStack.current = [...undoStack.current, snapshot(a, active)].slice(-10);
        answered.current = res;
        inView(VIEW_OF[res.plan.view], () => { const g = gridApi.current; if (g && !g.isDestroyed()) applyPlan(g, res.plan); });
      }
      setReading(res);
    } catch {
      setToast("Doorstep couldn't read that question just now. Try again in a moment.");
    } finally {
      setAsking(false);
    }
  };
  const undoReading = () => {
    const prev = undoStack.current.pop();
    if (!prev) { setReading(null); return; }
    inView(prev.view as Filter, () => { const g = gridApi.current; if (g && !g.isDestroyed()) restore(g, prev); });
    if (!undoStack.current.length) setReading(null);
  };
  /** The seller picked a view by hand: the grid goes back to its usual order first. */
  const dropReading = useCallback(() => {
    const a = gridApi.current, first = undoStack.current[0];
    if (a && first) restore(a, first);
    undoStack.current = [];
    setReading(null);
  }, []);
  // G7: the hand-off report of what the grid shows.
  const handoff = () => {
    const a = gridApi.current;
    if (!a) return;
    exportHandoffPdf(a, {
      shop: me.data?.name ?? "Your shop", region: me.data?.region ?? "UK", view: FILTERS.find((f) => f.key === active)!.label,
      source: rows?.some((r) => r.simulated) ? "Demo orders · refunds simulated" : "PayPal sandbox orders",
    });
  };
  const handoffNow = useRef(handoff);
  handoffNow.current = handoff;
  const choose = (f: Filter) => { if (reading) dropReading(); setFilter(f); go("orders"); };
  const pick = choose;
  const shown = useMemo(() => filterRows(rows ?? [], active), [rows, active]);
  const commands = useMemo((): Command[] => {
    const views: Command[] = FILTERS.map((f) => ({
      id: `view-${f.key}`, group: "Go to", label: f.label, hint: rows ? `${counts[f.key]}` : undefined, icon: f.icon,
      keywords: "view list filter", run: () => choose(f.key),
    }));
    views.push({ id: "view-insights", group: "Go to", label: "Insights", hint: "AG Studio reports", icon: <BarChart3 />, keywords: "analytics dashboard reports charts", run: () => go("insights") });
    const actions: Command[] = [
      { id: "act-excel", group: "Actions", label: "Export to Excel", hint: "What you see", icon: <Download />, keywords: "xlsx download", run: () => { go("orders"); window.setTimeout(() => gridApi.current && exportExcel(gridApi.current), 50); } },
      { id: "act-handoff", group: "Actions", label: "Download hand-off report", hint: "PDF", icon: <FileText />, keywords: "pdf print report share helper", run: () => { go("orders"); window.setTimeout(() => handoffNow.current(), 50); } },
      { id: "act-columns", group: "Actions", label: "Columns and filters", icon: <Columns3 />, keywords: "side bar panel hide show", run: () => { go("orders"); window.setTimeout(() => gridApi.current && toggleColumnsPanel(gridApi.current), 50); } },
      { id: "act-refresh", group: "Actions", label: "Refresh now", icon: <RefreshCw />, keywords: "reload update", run: () => reload() },
      { id: "act-search", group: "Actions", label: "Search this list", hint: "/", icon: <Search />, keywords: "find filter", run: () => { go("orders"); window.setTimeout(() => searchBox.current?.focus(), 50); } },
    ];
    const orders: Command[] = (rows ?? []).map((r) => ({
      id: `order-${r.order_id}`, group: "Orders", label: r.item, hint: `${r.order_ref} · ${r.status}`,
      icon: itemKind(r.item).icon, keywords: `${r.buyer_area} ${r.method ?? ""} ${r.case_state ?? ""} ${money(r.amount_minor, r.currency)}`,
      run: () => setOpen(r),
    }));
    return [...views, ...actions, ...orders];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, counts]);
  const changedHere = useMemo(() => (changed ? shown.filter((r) => changed.ids.has(r.order_id)) : []), [changed, shown]);
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
              <button key={f.key} aria-pressed={page === "orders" && !!rows && active === f.key} onClick={() => choose(f.key)}>
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
              <p className="greet">
                {greeting(me.data?.name)}
                {rows ? <> · <b>{counts.action ? `${counts.action} need${counts.action === 1 ? "s" : ""} you` : "nothing needs you"}</b></> : null}
              </p>
              <h1>Delivery Control Room</h1>
            </div>
            <div className="row" style={{ gap: 10 }}>
              <span className="live" title="Refreshes every 10 seconds while this tab is open"><span className="live-dot" aria-hidden />Live{updated ? ` · ${updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}</span>
              <button className="btn icon-btn" onClick={reload} aria-label="Refresh" title="Refresh now"><RefreshCw size={15} aria-hidden /></button>
            </div>
          </div>

          {error && <ErrorBox message="This seller link isn't valid. Open a fresh demo from the home page." />}
          {!rows && !error && (
            <section className="kpis" aria-hidden>
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="kpi kpi-skel"><span className="skel w40" /><span className="skel w20 tall" /><span className="skel w60" /></div>
              ))}
            </section>
          )}

          {!error && (
            <>
              {rows && <section className="kpis" aria-label="Summary">
                <button className={`kpi ${counts.action ? "attention" : ""}`} aria-pressed={page === "orders" && active === "action"} onClick={() => pick("action")}>
                  <span className="label"><Inbox aria-hidden />Needs you<Hint text="Cases to review and parcels to pack." /></span>
                  <span className="value">{counts.action}</span>
                  <span className="sub">approve or review</span>
                </button>
                <button className="kpi" aria-pressed={page === "orders" && active === "flight"} onClick={() => pick("flight")}>
                  <span className="label"><Truck aria-hidden />In transit<Hint text="Shipped, not yet received." /></span>
                  <span className="value">{counts.flight}</span>
                  <span className="sub">on the way</span>
                </button>
                <button className="kpi" aria-pressed={page === "orders" && active === "all"} onClick={() => pick("all")}>
                  <span className="label"><Wallet aria-hidden />Money at stake<Hint text="Open cases plus parcels on the way: what a failure could cost today." /></span>
                  <span className="value" style={{ fontSize: 22 }}>{sumStake(rows)}</span>
                  <span className="sub">cases and parcels</span>
                </button>
                <RecoveredKpi s={recovered.data ?? null} onClick={() => go("insights")} />
              </section>}

              {rows && auto.data && (
                <AutopilotCard
                  token={token} data={auto.data} statement={spent.data ?? null}
                  onChange={(m) => { setToast(m); reload(); }}
                  onOpen={(orderId) => { const r = rows?.find((x) => x.order_id === orderId); if (r) setOpen(r); }}
                />
              )}
              {rows && (policy.data && policy.data.issues.length > 0 ? (
                <PolicyCard check={policy.data} />
              ) : (
                !auto.data?.suggestions.length && tips.data && tips.data.length > 0 && <Tip t={tips.data[0]} />
              ))}

              <div className="table-card">
                <div className="head">
                  <div className="row" style={{ gap: 10 }}>
                    {rows ? <h2 style={{ fontSize: 15 }}>{activeMeta.label}</h2> : <span className="skel w-head" aria-hidden />}
                    {rows && <span className="count-chip num">{shown.length}</span>}
                    {changedHere.length > 0 && changed && (
                      <button className="changed-chip" onClick={() => { gridApi.current && revealRows(gridApi.current, changedHere.map((r) => r.order_id)); setChanged(null); }}
                        title="Show the orders that changed">
                        {changedHere.length} updated · {changed.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </button>
                    )}
                  </div>
                  <div className="row" style={{ gap: 8 }}>
                    <label className="grid-search">
                      <Search aria-hidden />
                      <input ref={searchBox} type="search" placeholder="Search, or ask: lost parcels over £50" aria-label="Search orders"
                        value={search} onChange={(e) => setSearch(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); void askGrid(search); } }} />
                      {search.trim().includes(" ") && <span className="ask-hint" aria-hidden>↵ Ask</span>}
                      <button type="button" className="kbd-hint" onClick={() => setCmd(true)} title="Command bar" aria-label="Open the command bar">⌘K</button>
                    </label>
                  </div>
                </div>
                {asking && <div className="ds-sweep grid-sweep" />}
                {reading && gridApi.current && (
                  <Reading api={gridApi.current} answer={reading} currency={rows?.[0]?.currency ?? "GBP"}
                    onUndo={undoReading} onDone={dropReading}
                    onDismiss={() => setReading(undoStack.current.length ? answered.current : null)} />
                )}
                <OrdersGrid rows={shown} search={search} empty={activeMeta.empty} onOpen={onOpen} onReady={onReady} changed={changed}
                  onHandoff={handoff}
                  loading={!rows} guide={(active === "action" || active === "all") && !reading}
                  source={!rows ? "" : rows.some((r) => r.simulated) ? "Demo orders · refunds simulated" : "PayPal sandbox orders"} />
              </div>
              {rows && costs.data && <PlanStrip e={costs.data} />}
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
      {cmd && <CommandBar commands={commands} onClose={() => setCmd(false)} />}
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
  return <span className="hint" aria-hidden data-tip={text}><Info /></span>;
}

/** RC-05: what carriers paid back, and what is still claimable. */
function RecoveredKpi({ s, onClick }: { s: RecoverySummary | null; onClick: () => void }) {
  const open = s ? sumBy(s.open_claim_minor) : "";
  return (
    <button className="kpi" onClick={onClick} title="Open Insights">
      <span className="label"><Landmark aria-hidden />Recovered from carriers<Hint text="Paid back by carriers on claims you filed, this shop only. Insights adds 26 weeks of simulated history in the demo." /></span>
      <span className="value" style={{ fontSize: 22 }}>{s ? sumBy(s.recovered_minor) : "—"}</span>
      <span className="sub">{s && s.open_claims ? `${s.open_claims} open claim${s.open_claims === 1 ? "" : "s"} · ${open}` : "claims you filed"}</span>
    </button>
  );
}
