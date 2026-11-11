import { useMemo } from "react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import type { DisputeRow } from "../api";
import { ErrorBox } from "../components/ui";
import { FAILURE_POINT, REASONS, money, type Tone } from "../format";
import LedgerGrid, { RIGHT, CopyId, ItemCell, Kpis, PageHead, StatusPill, joinMoney, sumByCurrency, type Totalled } from "./LedgerGrid";

type Row = DisputeRow & Totalled;

const ROUTE: Record<string, string> = { evidence: "Evidence sent", offer: "Offer made", waiting: "Waiting" };
const PROTECTION: Record<string, { label: string; tone: Tone }> = {
  ELIGIBLE: { label: "Eligible", tone: "good" },
  PARTIALLY_ELIGIBLE: { label: "Partly", tone: "warn" },
  NOT_ELIGIBLE: { label: "Not eligible", tone: "bad" },
};
const reasonOf = (r: DisputeRow) =>
  (r.reason && (FAILURE_POINT[r.reason] ?? REASONS.find((x) => x.value === r.reason)?.label)) || (r.reason ? r.reason.replaceAll("_", " ").toLowerCase() : "—");
const outcomeOf = (o: string | null) => (o ? o.replaceAll("_", " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase()) : "—");

function Protection({ v }: { v: string | null }) {
  if (!v) return <span className="faint">—</span>;
  const p = PROTECTION[v] ?? { label: v.toLowerCase().replaceAll("_", " "), tone: "neutral" as Tone };
  return <StatusPill tone={p.tone}>{p.label}</StatusPill>;
}

export default function DisputesPage({ rows, error, onOpenOrder }: { rows: DisputeRow[] | null; error: string | null; onOpenOrder: (orderId: string) => void; token: string; onChanged: () => void }) {
  const kpis = useMemo(() => {
    const r = rows ?? [];
    const open = r.filter((x) => x.status === "open");
    return { open: open.length, stake: joinMoney(sumByCurrency(open, (x) => x.amount_minor)), resolved: r.length - open.length };
  }, [rows]);
  const totals = useMemo((): Row[] => {
    const by = sumByCurrency((rows ?? []).filter((x) => x.status === "open"), (x) => x.amount_minor);
    return Object.entries(by).map(([currency, amount_minor]) => ({ __total: `Open ${currency}`, currency, amount_minor }) as Row);
  }, [rows]);

  const columns = useMemo((): ColDef<Row>[] => [
    { headerName: "Item", field: "item", flex: 2, minWidth: 200,
      cellRenderer: (p: ICellRendererParams<Row>) => p.data && <ItemCell item={p.data.item} sub={p.data.order_ref} total={p.data.__total} /> },
    { headerName: "PayPal dispute", field: "paypal_dispute_id", width: 170,
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? <CopyId value={p.data.paypal_dispute_id} /> : null) },
    { headerName: "Reason", field: "reason", flex: 1, minWidth: 190, valueFormatter: (p) => (p.data && !p.data.__total ? reasonOf(p.data) : "") },
    { headerName: "Amount", field: "amount_minor", ...RIGHT, width: 120,
      valueFormatter: (p) => (p.data ? money(p.data.amount_minor, p.data.currency) : "") },
    { headerName: "Status", field: "status", width: 110,
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? <StatusPill tone={p.data.status === "open" ? "bad" : "good"}>{p.data.status === "open" ? "Open" : "Resolved"}</StatusPill> : null),
      valueFormatter: (p) => (p.value === "open" ? "Open" : "Resolved") },
    { headerName: "Doorstep", field: "route", width: 130, valueFormatter: (p) => (p.data?.__total ? "" : ROUTE[p.value] ?? p.value) },
    { headerName: "Protection", field: "protection", width: 130, headerTooltip: "PayPal Seller Protection verdict on the payment",
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? <Protection v={p.data.protection} /> : null) },
    { headerName: "Outcome", field: "outcome", flex: 1, minWidth: 120, valueFormatter: (p) => (p.data?.__total ? "" : outcomeOf(p.value)) },
  ], []);

  const card = (r: Row) => (
    <>
      <span className="lc-top"><span className="lc-item">{r.item}</span><span className="lc-money num">{money(r.amount_minor, r.currency)}</span></span>
      <span className="lc-meta">{r.order_ref} · {reasonOf(r)} · {ROUTE[r.route] ?? r.route}</span>
      <span className="lc-bottom">
        <StatusPill tone={r.status === "open" ? "bad" : "good"}>{r.status === "open" ? "Open" : "Resolved"}</StatusPill>
        <Protection v={r.protection} />
      </span>
    </>
  );

  return (
    <>
      <PageHead title="Disputes" line="PayPal disputes on your orders" />
      {error && <ErrorBox message="Disputes did not load. Refresh to try again." />}
      <Kpis items={[
        { label: "Open", value: String(kpis.open), sub: kpis.open ? kpis.stake : "none open", tone: kpis.open ? "attention" : undefined },
        { label: "Resolved", value: String(kpis.resolved), sub: "decided by PayPal" },
      ]} />
      <LedgerGrid<Row>
        label="PayPal disputes" rows={rows as Row[] | null} columns={columns} getRowId={(r) => r.dispute_id}
        onOpen={(r) => onOpenOrder(r.order_id)} empty="No disputes." totals={totals} card={card}
        totalLines={totals.map((t) => `${t.__total}: ${money(t.amount_minor, t.currency)}`)}
      />
    </>
  );
}
