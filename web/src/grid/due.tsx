import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import type { ColDef, GridApi } from "ag-grid-community";
import type { CustomCellRendererProps } from "ag-grid-react";
import type { OrderRow } from "../api";
import { fetchInsights } from "../insights/data";
import { packingColumns } from "./packing";

/** An open carrier claim: the real deadline to file or follow up. */
export type Claim = { at: number; carrier: string; windowDays?: number };

const DAY = 86_400_000;
const OPEN = new Set(["PREPARED", "SUBMITTED"]);
const NAMES: Record<string, string> = { royal_mail: "Royal Mail", ups: "UPS", usps: "USPS", fedex: "FedEx", dpd: "DPD", evri: "Evri", dhl: "DHL" };
const carrierName = (c: string) => NAMES[c.toLowerCase()] ?? c.replace(/[_-]+/g, " ").replace(/\b\w/g, (x) => x.toUpperCase());

/** The seller token from /seller/<token> (the grid is only drawn there). */
const tokenFromPath = () => /\/seller\/([^/?#]+)/.exec(location.pathname)?.[1] ?? "";

/** Whole days left, rounded up, 0 on the day itself or past. */
export const daysLeft = (at: number, now = Date.now()) => Math.max(0, Math.ceil((at - now) / DAY));

/** Open claims by case: fetched with the grid's rows, again whenever the rows change. */
export function useClaims(rows: OrderRow[]): Map<string, Claim> {
  const [claims, setClaims] = useState<Map<string, Claim>>(() => new Map());
  // A cheap signature, so a poll that changed nothing does not refetch.
  const sig = useMemo(() => rows.map((r) => `${r.order_id}:${r.case_state ?? ""}:${r.last_update}`).join("|"), [rows]);
  useEffect(() => {
    const token = tokenFromPath();
    if (!token || !rows.some((r) => r.case_id)) return;
    let live = true;
    fetchInsights(token).then((d) => {
      if (!live) return;
      const m = new Map<string, Claim>();
      for (const r of d.recoveries) {
        if (!OPEN.has(r.status)) continue;
        const at = Date.parse(r.deadline_at);
        const old = m.get(r.case_id);
        if (!Number.isNaN(at) && (!old || at < old.at)) {
          m.set(r.case_id, { at, carrier: carrierName(r.carrier), windowDays: (r as { window_days?: number }).window_days });
        }
      }
      setClaims(m);
    }).catch(() => { /* the column stays blank */ });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig]);
  return claims;
}

function DueCell(p: CustomCellRendererProps<OrderRow> & { claims: RefObject<Map<string, Claim>> }) {
  const c = p.data?.case_id ? p.claims.current.get(p.data.case_id) : undefined;
  if (!c) return null;
  const d = daysLeft(c.at);
  const tone = d < 3 ? "var(--bad)" : d < 7 ? "var(--warn)" : "var(--text-3)";
  const pct = c.windowDays ? Math.min(100, Math.round((d / c.windowDays) * 100)) : null;
  return (
    <div className="due-cell" style={{ display: "flex", flexDirection: "column", justifyContent: "center", gap: 5, height: "100%", width: "100%" }}>
      <span className="num" style={{ color: tone, fontWeight: d < 7 ? 600 : 500, lineHeight: 1 }}>{d === 0 ? "Today" : `${d}d`}</span>
      {pct != null && (
        <span aria-hidden style={{ height: 3, borderRadius: 2, background: "#ece6e1", overflow: "hidden", width: 44 }}>
          <span style={{ display: "block", height: "100%", width: `${Math.max(pct, 6)}%`, background: d < 3 ? "var(--bad)" : d < 7 ? "var(--warn)" : "var(--accent-3)" }} />
        </span>
      )}
    </div>
  );
}

/**
 * The "Due" column (soonest carrier claim deadline) plus the hidden columns of the packing list.
 * The definitions stay the same object while deadlines arrive (a new columnDefs array would make AG
 * rebuild every column and drop the seller's filters); new deadlines refresh only the Due cells.
 */
export function useDueColumns(rows: OrderRow[], phone: boolean, api: RefObject<GridApi<OrderRow> | null>): ColDef<OrderRow>[] {
  const claims = useClaims(rows);
  const ref = useRef(claims);
  ref.current = claims;
  useEffect(() => {
    const a = api.current;
    if (!a || a.isDestroyed()) return;
    a.refreshCells({ columns: ["due"], force: true });
    if (a.getColumn("due")?.getSort()) a.onSortChanged();
  }, [claims, api]);
  return useMemo((): ColDef<OrderRow>[] => [
    {
      colId: "due", headerName: "Due", width: 84, hide: phone, filter: false, chartDataType: "excluded",
      valueGetter: (p) => (p.data?.case_id ? ref.current.get(p.data.case_id)?.at ?? null : null),
      // Soonest first; rows without a deadline stay last in both directions.
      comparator: (a: number | null, b: number | null, _x, _y, desc) => (a == null ? (b == null ? 0 : desc ? -1 : 1) : b == null ? (desc ? 1 : -1) : a - b),
      tooltipValueGetter: (p) => {
        const c = p.data?.case_id ? ref.current.get(p.data.case_id) : undefined;
        return c ? `Carrier claim · ${c.carrier} · ${new Date(c.at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}` : undefined;
      },
      cellRenderer: DueCell, cellRendererParams: { claims: ref },
    },
    ...packingColumns(),
  ], [phone]);
}
