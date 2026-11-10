import type { ColDef, GridApi, IRowNode, RowPosition } from "ag-grid-community";
import type { OrderRow } from "../api";

/** Paid, not yet shipped: the parcels still to pack. */
const TO_PACK = new Set(["PAID", "PACKED"]);

/** The built-in PDF fonts are WinAnsi only: anything else prints as "?". */
const safe = (s: string) => s.replace(/[^\x20-\x7e\xa0-\xff]/g, "?");

const METHODS: Record<string, string> = { home: "Home", locker: "Locker", signature: "Signature" };

/** Hidden columns the packing list exports (they never show in the grid or its Columns panel). */
export function packingColumns(): ColDef<OrderRow>[] {
  const hidden = { hide: true, suppressColumnsToolPanel: true, suppressMovable: true, filter: false, sortable: false } as const;
  return [
    { ...hidden, colId: "pk_ref", headerName: "Order", valueGetter: (p) => p.data?.order_ref ?? "" },
    { ...hidden, colId: "pk_item", headerName: "Item", valueGetter: (p) => p.data?.item ?? "" },
    { ...hidden, colId: "pk_delivery", headerName: "Delivery", valueGetter: (p) => (p.data?.method ? METHODS[p.data.method] ?? p.data.method : "Standard") },
    { ...hidden, colId: "pk_area", headerName: "Buyer area", valueGetter: (p) => `${p.data?.buyer_area ?? ""} ${p.data?.buyer_country ?? ""}`.trim() },
    { ...hidden, colId: "pk_tick", headerName: "Packed", valueGetter: () => "" },
  ];
}

/** Lucide's clipboard-check, drawn like the grid's other toolbar icons. */
export const packingIcon =
  '<svg class="ds-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="m9 14 2 2 4-4"/></svg>';

const WIDTHS: Record<string, number> = { pk_ref: 90, pk_item: 215, pk_delivery: 78, pk_area: 90, pk_tick: 62 };

/** A quiet, self-closing status line (the page's own toast look), for when there is nothing to export. */
function say(text: string) {
  document.querySelector(".packing-status")?.remove();
  const el = document.createElement("div");
  el.className = "toast packing-status";
  el.setAttribute("role", "status");
  el.textContent = text;
  document.body.appendChild(el);
  window.setTimeout(() => el.remove(), 3200);
}

/** Download the packing list (PDF) of the orders still to pack; "Nothing to pack" when there are none. */
export function exportPackingList(api: GridApi<OrderRow>) {
  const positions: RowPosition[] = [];
  let us = false;
  api.forEachNode((n: IRowNode<OrderRow>) => {
    if (n.data?.currency === "USD") us = true;
    if (n.data && !n.group && !n.rowPinned && !n.footer && TO_PACK.has(n.data.state) && n.rowIndex != null) {
      positions.push({ rowIndex: n.rowIndex, rowPinned: null });
    }
  });
  if (!positions.length) { say("Nothing to pack"); return; }
  const now = new Date();
  const date = now.toLocaleDateString(us ? "en-US" : "en-GB", { day: "numeric", month: "short", year: "numeric" });
  api.exportDataAsPdf({
    fileName: `doorstep-packing-list-${now.toISOString().slice(0, 10)}.pdf`,
    columnKeys: Object.keys(WIDTHS),
    rowPositions: positions,
    skipRowGroups: true,
    skipPinnedTop: true,
    documentTitle: `Packing list · ${date}`,
    documentSubtitle: `${positions.length} to pack`,
    page: { size: us ? "Letter" : "A4", orientation: "portrait" },
    headerFooterConfig: { all: { footer: [
      { value: "Doorstep", position: "Left" },
      { value: "Page &[Page] of &[Pages]", position: "Right" },
    ] } },
    repeatHeader: true,
    columnWidth: ({ column }) => WIDTHS[column?.getColId() ?? ""] ?? "auto",
    processCellCallback: (q) => (q.column.getColId() === "pk_tick" ? "[   ]" : safe(String(q.value ?? ""))),
  });
}
