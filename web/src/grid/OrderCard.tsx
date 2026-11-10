import type { ComponentType } from "react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import type { OrderRow } from "../api";
import { JourneyCell, itemKind } from "../components/gridCells";
import { money } from "../format";

/** Phone: below this width the grid is one card column. */
export const CARD_QUERY = "(max-width: 599px)";
export const CARD_HEIGHT = 144;

type ActionRenderer = ComponentType<ICellRendererParams<OrderRow> & { onOpen: (r: OrderRow) => void }>;
type CardParams = ICellRendererParams<OrderRow> & { onOpen: (r: OrderRow) => void; Action: ActionRenderer };

/** One order as a card: item, amount, ref and area, journey, and the same Next button as the table. */
export function OrderCard(p: CardParams) {
  const r = p.data;
  if (!r) return null;
  const k = itemKind(r.item);
  const stake = r.money_at_stake_minor;
  const Action = p.Action;
  return (
    <div className="order-card">
      <div className="oc-top">
        <span className="item-tile" aria-hidden>{k.icon}</span>
        <span className="item-name oc-item">{r.item}</span>
        <span className={`oc-money num ${stake ? "" : "none"}`} title="At stake">{money(stake, r.currency)}</span>
      </div>
      <span className="oc-meta num">{r.order_ref} · {r.buyer_area || r.buyer_country}</span>
      <div className="oc-bottom">
        <JourneyCell r={r} quiet />
        <Action {...p} onOpen={p.onOpen} />
      </div>
    </div>
  );
}

/** The columns as they are, or, on a phone, the one card column (the others hide; their ids stay). */
export function withCard(
  cols: ColDef<OrderRow>[], card: boolean, Action: ActionRenderer, onOpen: (r: OrderRow) => void, quickText: (r?: OrderRow) => string,
): ColDef<OrderRow>[] {
  if (!card) return cols;
  const rest = cols.map((c) => (c.colId === "lane" || c.colId === "urgency" || c.hide ? c : { ...c, hide: true, pinned: null }));
  return [
    ...rest.slice(0, 2),
    {
      colId: "card", headerName: "Orders", flex: 1, minWidth: 0, sortable: false, resizable: false, filter: false,
      suppressColumnsToolPanel: true, suppressMovable: true, cellRenderer: OrderCard, cellRendererParams: { onOpen, Action },
      getQuickFilterText: (p) => quickText(p.data),
    },
    ...rest.slice(2),
  ];
}
