import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GridApi } from "ag-grid-community";
import { ArrowRight, BarChart3, BookOpen, Check, Columns3, Copy, Download, FileWarning, FolderOpen, Info, Inbox, Landmark, Lightbulb, List, RefreshCw, Search, Truck, Wallet, Zap } from "lucide-react";
import { api, type Autopilot, type AutopilotStatement, type OrderRow, type PolicyCheck, type PreventionTip, type RecoverySummary } from "../api";
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
          <span className="ap-title">Let Doorstep refund {kind} up to {max} for you</span>
          <span className="ap-text">
            You approved {s.approved} of {s.approved} like this in the last {s.window_days} days. Disputes, held cases and anything over {max} still come to you.
          </span>
          {err && <ErrorBox message={err} />}
        </div>
        <div className="ap-actions">
          <button className="btn ghost" disabled={busy} onClick={() => act(() => api.autopilotDismiss(token, s.failure_point, s.currency), "Okay. These keep coming to you.")}>Not now</button>
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
                <span className="ap-text">Refunded automatically, within your daily cap. Learned from {r.precedents} refunds you approved.</span>
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
  if (!s.cases.length) return <span className="ap-statement muted">No automatic refunds in the last {s.days} days.</span>;
  return (
    <div className="ap-statement" aria-label="Autopilot statement">
      {s.totals.map((t) => (
        <span key={t.currency}>
          <strong>Last {s.days} days: {plural(t.refunds, "automatic refund")}, {money(t.refunded_minor, t.currency)}</strong>
          {t.claimable_minor > 0 && <> · {money(t.claimable_minor, t.currency)} to claim back from couriers{t.ready_to_send > 0 && ` (${plural(t.ready_to_send, "claim")} ready to send)`}</>}
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
  const tips = useAsync(() => api.prevention(token), [token]);
  const auto = useAsync(() => api.autopilot(token), [token]);
  const spent = useAsync(() => api.autopilotStatement(token), [token]);
  const policy = useAsync(() => api.policyCheck(token), [token]);
  const reload = () => { reloadOrders(); recovered.reload(); tips.reload(); auto.reload(); spent.reload(); policy.reload(); };
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
  const [updated, setUpdated] = useState<Date | null>(null);
  useEffect(() => { if (rows) setUpdated(new Date()); }, [rows]);
  // The one thing to do first: a PayPal dispute (it has a deadline), then a refund to approve, then a review.
  const next = useMemo(() => {
    const rank: Record<string, number> = { DISPUTE_EVIDENCE: 0, AWAITING_SELLER: 1, HOLD_FOR_REVIEW: 2 };
    return filterRows(rows ?? [], "action")
      .filter((r) => r.case_state && r.case_state in rank)
      .sort((a, b) => rank[a.case_state!] - rank[b.case_state!] || b.money_at_stake_minor - a.money_at_stake_minor)[0] ?? null;
  }, [rows]);
  const pick = (f: Filter) => { setFilter(f); go("orders"); };
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
              <p className="greet">
                {greeting(me.data?.name)}
                {rows ? <> · <b>{counts.action ? `${counts.action} need${counts.action === 1 ? "s" : ""} you` : "nothing needs you"}</b></> : null}
                {next && <> · <button className="next-link" onClick={() => setOpen(next)}>Next: {NEXT_TEXT[next.case_state!]}{next.money_at_stake_minor ? ` (${money(next.money_at_stake_minor, next.currency)})` : ""}<ArrowRight aria-hidden /></button></>}
              </p>
              <h1>Delivery Control Room</h1>
            </div>
            <div className="row" style={{ gap: 10 }}>
              <span className="live" title="Refreshes every 10 seconds while this tab is open"><span className="live-dot" aria-hidden />Live{updated ? ` · ${updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}</span>
              <button className="btn icon-btn" onClick={reload} aria-label="Refresh" title="Refresh now"><RefreshCw size={15} aria-hidden /></button>
            </div>
          </div>

          {error && <ErrorBox message="This seller link isn't valid. Open a fresh demo from the home page." />}
          {!rows && !error && <Loading height={320} />}

          {rows && (
            <>
              <section className="kpis" aria-label="Summary">
                <button className={`kpi ${counts.action ? "attention" : ""}`} aria-pressed={page === "orders" && active === "action"} onClick={() => pick("action")}>
                  <span className="label"><Inbox aria-hidden />Needs you<Hint text="Cases to approve or review, and orders waiting for a packing check." /></span>
                  <span className="value">{counts.action}</span>
                  <span className="sub">to approve or review</span>
                </button>
                <button className="kpi" aria-pressed={page === "orders" && active === "flight"} onClick={() => pick("flight")}>
                  <span className="label"><Truck aria-hidden />In transit<Hint text="Shipped parcels the buyer has not received yet." /></span>
                  <span className="value">{counts.flight}</span>
                  <span className="sub">on the way</span>
                </button>
                <button className="kpi" aria-pressed={page === "orders" && active === "all"} onClick={() => pick("all")}>
                  <span className="label"><Wallet aria-hidden />Money at stake<Hint text="The value of open cases plus parcels still on the way: what a delivery failure could cost you today." /></span>
                  <span className="value" style={{ fontSize: 22 }}>{sumStake(rows)}</span>
                  <span className="sub">open cases and parcels in transit</span>
                </button>
                <RecoveredKpi s={recovered.data ?? null} onClick={() => go("insights")} />
              </section>

              {auto.data && (
                <AutopilotCard
                  token={token} data={auto.data} statement={spent.data ?? null}
                  onChange={(m) => { setToast(m); reload(); }}
                  onOpen={(orderId) => { const r = rows?.find((x) => x.order_id === orderId); if (r) setOpen(r); }}
                />
              )}
              {policy.data && policy.data.issues.length > 0 ? (
                <PolicyCard check={policy.data} />
              ) : (
                !auto.data?.suggestions.length && tips.data && tips.data.length > 0 && <Tip t={tips.data[0]} />
              )}

              <div className="table-card">
                <div className="head">
                  <div className="row" style={{ gap: 10 }}>
                    <h2 style={{ fontSize: 15 }}>{activeMeta.label}</h2>
                    <span className="count-chip num">{shown.length}</span>
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
  return <span className="hint" aria-hidden data-tip={text}><Info /></span>;
}

const NEXT_TEXT: Record<string, string> = {
  DISPUTE_EVIDENCE: "answer a PayPal dispute",
  AWAITING_SELLER: "approve or decline a refund",
  HOLD_FOR_REVIEW: "review a held case",
};

/** RC-05: what carriers paid back, and what is still claimable. */
function RecoveredKpi({ s, onClick }: { s: RecoverySummary | null; onClick: () => void }) {
  const open = s ? sumBy(s.open_claim_minor) : "";
  return (
    <button className="kpi" onClick={onClick} title="Open Insights">
      <span className="label"><Landmark aria-hidden />Recovered from carriers<Hint text="What carriers paid back on claims Doorstep prepared and you submitted, in this shop only. Insights adds 26 weeks of simulated history to the demo shop." /></span>
      <span className="value" style={{ fontSize: 22 }}>{s ? sumBy(s.recovered_minor) : "—"}</span>
      <span className="sub">{s && s.open_claims ? `${s.open_claims} open claim${s.open_claims === 1 ? "" : "s"} · ${open}` : "this shop, from claims you filed"}</span>
    </button>
  );
}
