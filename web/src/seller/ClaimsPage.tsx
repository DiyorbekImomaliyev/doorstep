import { useMemo } from "react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import type { ClaimRow } from "../api";
import { ErrorBox } from "../components/ui";
import { daysLeft } from "../grid/due";
import { money, type Tone } from "../format";
import LedgerGrid, { RIGHT, ItemCell, Kpis, PageHead, StatusPill, joinMoney, sumByCurrency, type Totalled } from "./LedgerGrid";

type Row = ClaimRow & Totalled;

const OPEN = ["PREPARED", "SUBMITTED"];
const STATUS: Record<string, { label: string; tone: Tone }> = {
  PREPARED: { label: "Ready", tone: "accent" },
  SUBMITTED: { label: "Submitted", tone: "warn" },
  PAID: { label: "Paid", tone: "good" },
  DENIED: { label: "Refused", tone: "bad" },
  EXPIRED: { label: "Expired", tone: "neutral" },
  WITHDRAWN: { label: "Withdrawn", tone: "neutral" },
};
const statusOf = (s: string) => STATUS[s] ?? { label: s.toLowerCase(), tone: "neutral" as Tone };

function Due({ r }: { r: ClaimRow }) {
  if (!OPEN.includes(r.status)) return <span className="faint">—</span>;
  const d = daysLeft(Date.parse(r.deadline_at));
  const tone = d < 7 ? "var(--bad)" : "var(--text-3)";
  const pct = r.window_days ? Math.min(100, Math.round((d / r.window_days) * 100)) : null;
  return (
    <div className="due-cell" style={{ display: "flex", flexDirection: "column", justifyContent: "center", gap: 5, height: "100%" }}>
      <span className="num" style={{ color: tone, fontWeight: d < 7 ? 600 : 500, lineHeight: 1 }}>{d === 0 ? "Today" : `${d}d`}</span>
      {pct != null && (
        <span aria-hidden style={{ height: 3, borderRadius: 2, background: "#ece6e1", overflow: "hidden", width: 44 }}>
          <span style={{ display: "block", height: "100%", width: `${Math.max(pct, 6)}%`, background: d < 7 ? "var(--bad)" : "var(--accent-3)" }} />
        </span>
      )}
    </div>
  );
}

export default function ClaimsPage({ rows, error, onOpenOrder }: { rows: ClaimRow[] | null; error: string | null; onOpenOrder: (orderId: string) => void }) {
  const kpis = useMemo(() => {
    const r = rows ?? [];
    const open = r.filter((x) => OPEN.includes(x.status));
    const soon = open.filter((x) => daysLeft(Date.parse(x.deadline_at)) < 7);
    return {
      open: open.length, openSum: joinMoney(sumByCurrency(open, (x) => x.claim_minor)),
      recovered: joinMoney(sumByCurrency(r.filter((x) => x.status === "PAID"), (x) => x.recovered_minor ?? 0)),
      soon: soon.length,
    };
  }, [rows]);

  const totals = useMemo((): Row[] => {
    const by = sumByCurrency(rows ?? [], (x) => x.claim_minor);
    const paid = sumByCurrency(rows ?? [], (x) => x.recovered_minor ?? 0);
    return Object.keys(by).map((currency) => ({
      __total: `Total ${currency}`, currency, claim_minor: by[currency], recovered_minor: paid[currency] ?? 0,
    }) as Row);
  }, [rows]);

  const columns = useMemo((): ColDef<Row>[] => [
    { headerName: "Item", field: "item", flex: 2, minWidth: 220,
      cellRenderer: (p: ICellRendererParams<Row>) => p.data && <ItemCell item={p.data.item} sub={p.data.order_ref} total={p.data.__total} /> },
    { headerName: "Carrier", field: "carrier_name", flex: 1, minWidth: 130, enableRowGroup: true, valueFormatter: (p) => (p.data?.__total ? "" : p.value) },
    { headerName: "Claim", field: "claim_minor", ...RIGHT, width: 120,
      valueFormatter: (p) => (p.data ? money(p.data.claim_minor, p.data.currency) : ""),
      tooltipValueGetter: (p) => (p.data?.cover_minor != null && !p.data.__total ? `Carrier cover ${money(p.data.cover_minor, p.data.currency)}` : undefined) },
    { headerName: "Status", field: "status", width: 130,
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? <StatusPill tone={statusOf(p.data.status).tone}>{statusOf(p.data.status).label}</StatusPill> : null),
      valueFormatter: (p) => statusOf(p.value ?? "").label },
    { headerName: "Due", field: "deadline_at", width: 110,
      cellRenderer: (p: ICellRendererParams<Row>) => (p.data && !p.data.__total ? <Due r={p.data} /> : null) },
    { headerName: "Paid", field: "recovered_minor", ...RIGHT, width: 120,
      valueFormatter: (p) => (p.data && (p.data.__total || p.data.recovered_minor != null) ? money(p.data.recovered_minor, p.data.currency) : "—") },
  ], []);

  const card = (r: Row) => (
    <>
      <span className="lc-top"><span className="lc-item">{r.item}</span><span className="lc-money num">{money(r.claim_minor, r.currency)}</span></span>
      <span className="lc-meta">{r.order_ref} · {r.carrier_name}</span>
      <span className="lc-bottom">
        <StatusPill tone={statusOf(r.status).tone}>{statusOf(r.status).label}</StatusPill>
        <Due r={r} />
        {r.recovered_minor != null && <span className="num lc-paid">Paid {money(r.recovered_minor, r.currency)}</span>}
      </span>
    </>
  );
  const open = (r: Row) => onOpenOrder(r.order_id);

  return (
    <>
      <PageHead title="Claims" line="Carrier claims for lost and damaged parcels" />
      {error && <ErrorBox message="Claims did not load. Refresh to try again." />}
      <Kpis items={[
        { label: "Open", value: String(kpis.open), sub: kpis.open ? kpis.openSum : "none waiting" },
        { label: "Recovered", value: kpis.recovered, sub: "paid by carriers" },
        { label: "Due soon", value: String(kpis.soon), sub: "under 7 days", tone: kpis.soon ? "attention" : undefined },
      ]} />
      <LedgerGrid<Row>
        label="Carrier claims" rows={rows as Row[] | null} columns={columns} getRowId={(r) => r.recovery_id}
        onOpen={open} empty="No claims yet." totals={totals} card={card} groupPanel
        totalLines={totals.map((t) => `Total ${t.currency}: ${money(t.claim_minor, t.currency)} claimed · ${money(t.recovered_minor ?? 0, t.currency)} paid`)}
      />
    </>
  );
}
