import { useMemo } from "react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import { ExternalLink } from "lucide-react";
import type { MoneyRow } from "../api";
import { ErrorBox } from "../components/ui";
import { money, when, type Tone } from "../format";
import LedgerGrid, { RIGHT, CopyId, ItemCell, Kpis, PageHead, StatusPill, joinMoney, sumByCurrency, type Totalled } from "./LedgerGrid";

type Row = MoneyRow & Totalled;

const KIND: Record<string, string> = { refund: "Refund", partial_refund: "Partial refund", offer: "Dispute offer", evidence: "Evidence" };
const STATUS: Record<string, { label: string; tone: Tone }> = {
  completed: { label: "Completed", tone: "good" },
  pending: { label: "Pending", tone: "warn" },
  queued: { label: "Queued", tone: "neutral" },
  failed: { label: "Failed", tone: "bad" },
  blocked: { label: "Blocked", tone: "bad" },
  unconfirmed: { label: "Unconfirmed", tone: "warn" },
  simulated: { label: "Simulated", tone: "accent" },
};
const statusOf = (s: string) => STATUS[s] ?? { label: s, tone: "neutral" as Tone };
const MOVES = ["refund", "partial_refund"];
/** Money that has left, or will: a refund that is completed, pending or simulated. */
const counts = (r: MoneyRow) => MOVES.includes(r.kind) && ["completed", "pending", "simulated"].includes(r.status);
const keyShort = (k: string | null) => (k ? (k.length > 14 ? `${k.slice(0, 8)}…${k.slice(-4)}` : k) : "");
const amountOf = (r: MoneyRow) => (r.kind === "evidence" ? "—" : money(r.amount_minor, r.currency));

function Receipt({ r }: { r: MoneyRow }) {
  if (!r.case_id) return <span className="faint">—</span>;
  return <a className="lg-link" href={`/verify/${r.case_id}`} target="_blank" rel="noreferrer">Receipt<ExternalLink aria-hidden /></a>;
}

export default function MoneyPage({ rows, error, onOpenOrder }: { rows: MoneyRow[] | null; error: string | null; onOpenOrder: (orderId: string) => void }) {
  const kpis = useMemo(() => {
    const r = rows ?? [];
    return {
      refunded: joinMoney(sumByCurrency(r.filter(counts), (x) => x.amount_minor)),
      pending: r.filter((x) => ["pending", "queued"].includes(x.status)).length,
      simulated: r.filter((x) => x.status === "simulated").length,
    };
  }, [rows]);
  const totals = useMemo((): Row[] => {
    const by = sumByCurrency((rows ?? []).filter(counts), (x) => x.amount_minor);
    return Object.entries(by).map(([currency, amount_minor]) => ({ __total: `Refunded ${currency}`, currency, amount_minor }) as Row);
  }, [rows]);

  const columns = useMemo((): ColDef<Row>[] => [
    { headerName: "Date", field: "created_at", width: 150, sort: "desc", cellClass: "num",
      valueFormatter: (p) => (p.data?.__total ? "" : p.value ? when(p.value) : "") },
    { headerName: "Item", field: "item", flex: 2, minWidth: 220,
      cellRenderer: (p: ICellRendererParams<Row>) => p.data && <ItemCell item={p.data.item} sub={p.data.order_ref} total={p.data.__total} /> },
    { headerName: "Kind", field: "kind", width: 110, valueFormatter: (p) => (p.data?.__total ? "" : KIND[p.value] ?? p.value) },
    { headerName: "Amount", field: "amount_minor", ...RIGHT, width: 110,
      valueFormatter: (p) => (p.data ? (p.data.__total ? money(p.data.amount_minor, p.data.currency) : amountOf(p.data)) : "") },
    { headerName: "Status", field: "status", width: 130,
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? <StatusPill tone={statusOf(p.data.status).tone}>{statusOf(p.data.status).label}</StatusPill> : null),
      valueFormatter: (p) => statusOf(p.value ?? "").label },
    { headerName: "Refund id", field: "paypal_refund_id", width: 130,
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? <CopyId value={p.data.paypal_refund_id} /> : null) },
    { headerName: "Request key", field: "request_id", width: 130, headerTooltip: "PayPal-Request-Id: one key per refund, so a retry never pays twice",
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? <CopyId value={p.data.request_id} shown={keyShort(p.data.request_id)} /> : null) },
    { headerName: "Receipt", field: "case_id", width: 100, sortable: false,
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? <Receipt r={p.data} /> : null) },
  ], []);

  const card = (r: Row) => (
    <>
      <span className="lc-top"><span className="lc-item">{r.item || "Order"}</span><span className="lc-money num">{amountOf(r)}</span></span>
      <span className="lc-meta">{r.order_ref} · {KIND[r.kind] ?? r.kind} · {when(r.created_at)}</span>
      <span className="lc-bottom">
        <StatusPill tone={statusOf(r.status).tone}>{statusOf(r.status).label}</StatusPill>
        <Receipt r={r} />
      </span>
    </>
  );

  return (
    <>
      <PageHead title="Money" line="Every refund, offer and evidence send" />
      {error && <ErrorBox message="Money did not load. Refresh to try again." />}
      <Kpis items={[
        { label: "Refunded", value: kpis.refunded, sub: "completed and pending" },
        { label: "Pending", value: String(kpis.pending), sub: "waiting at PayPal", tone: kpis.pending ? "attention" : undefined },
        { label: "Simulated", value: String(kpis.simulated), sub: "demo orders, no real money" },
      ]} />
      <LedgerGrid<Row>
        label="Money actions" rows={rows as Row[] | null} columns={columns} getRowId={(r) => r.action_id}
        onOpen={(r) => onOpenOrder(r.order_id)} empty="No money moved yet." totals={totals} card={card}
        totalLines={totals.map((t) => `${t.__total}: ${money(t.amount_minor, t.currency)}`)}
      />
    </>
  );
}
