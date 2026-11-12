import { useMemo, type RefObject } from "react";
import type { ColDef } from "ag-grid-community";
import type { CustomCellRendererProps } from "ag-grid-react";
import type { OrderRow } from "../api";
import { ClaimTag, claimOf } from "./claim";
import { packingColumns } from "./packing";

const DAY = 86_400_000;

/** Whole days left, rounded up, 0 on the day itself or past. */
export const daysLeft = (at: number, now = Date.now()) => Math.max(0, Math.ceil((at - now) / DAY));

/** The deadline that still matters: filing a ready claim. A filed claim has met its window. */
const fileBy = (r: OrderRow | undefined): number | null => {
  const c = claimOf(r);
  if (c?.status !== "PREPARED") return null;
  const at = Date.parse(c.deadline_at);
  return Number.isNaN(at) ? null : at;
};

/** The claim tag (opens Claims) and, for a claim still to file, the days left with a small bar. */
function ClaimCell(p: CustomCellRendererProps<OrderRow> & { onClaim: RefObject<((id: string) => void) | undefined> }) {
  const c = claimOf(p.data);
  if (!c) return null;
  const at = fileBy(p.data);
  const d = at == null ? null : daysLeft(at);
  const tone = d == null ? "" : d < 3 ? "var(--bad)" : d < 7 ? "var(--warn)" : "var(--text-3)";
  const pct = d != null && c.window_days ? Math.min(100, Math.round((d / c.window_days) * 100)) : null;
  return (
    <div className="claim-cell">
      <ClaimTag claim={c} short onOpen={(id) => p.onClaim.current?.(id)} />
      {d != null && (
        <span className="due-line">
          <span className="due-cell"><span className="num" style={{ color: tone, fontWeight: d < 7 ? 600 : 500 }}>{d === 0 ? "Today" : `${d}d`}</span></span>
          {pct != null && (
            <span className="due-bar" aria-hidden>
              <span style={{ width: `${Math.max(pct, 6)}%`, background: d < 3 ? "var(--bad)" : d < 7 ? "var(--warn)" : "var(--accent-3)" }} />
            </span>
          )}
        </span>
      )}
    </div>
  );
}

/**
 * The "Claim" column (FP-W1: the carrier claim behind a case, which opens the Claims page; column id
 * "due" kept for saved layouts) plus the hidden columns of the packing list. The claim rides on the
 * row's data, so a new claim redraws its own cell with the row.
 */
export function useDueColumns(phone: boolean, onClaim: RefObject<((id: string) => void) | undefined>): ColDef<OrderRow>[] {
  return useMemo((): ColDef<OrderRow>[] => [
    {
      colId: "due", headerName: "Claim", initialWidth: 116, hide: phone, filter: false, chartDataType: "excluded",
      cellClass: "claim-col", headerClass: "claim-col",
      headerTooltip: "Carrier claim: amount, status, days left to file",
      valueGetter: (p) => fileBy(p.data),
      // Soonest deadline first; rows without one stay last in both directions.
      comparator: (a: number | null, b: number | null, _x, _y, desc) => (a == null ? (b == null ? 0 : desc ? -1 : 1) : b == null ? (desc ? 1 : -1) : a - b),
      cellRenderer: ClaimCell, cellRendererParams: { onClaim },
    },
    ...packingColumns(),
  ], [phone, onClaim]);
}
