import type { ExcelRow, ExcelStyle, GridApi, ProcessCellForExportParams } from "ag-grid-community";
import { KIND, currencies, figures, statusOf } from "./ledger";
import type { DeskRow, RecoveryFlow } from "./moneyApi";
import type { Row } from "./MoneyGrid";

export const ledgerExcelStyles: ExcelStyle[] = [
  { id: "mnTitle", font: { bold: true, size: 14 } },
  { id: "mnText", font: { color: "#736b67" } },
  { id: "mnHead", font: { bold: true }, interior: { color: "#f6ecee", pattern: "Solid" } },
  { id: "mnNumber", dataType: "Number", numberFormat: { format: "#,##0.00" } },
  { id: "mnDate", dataType: "DateTime", numberFormat: { format: "d mmm yyyy hh:mm" } },
];

const text = (value: string, styleId?: string, mergeAcross?: number) => ({ styleId, mergeAcross, data: { value, type: "String" as const } });
const num = (minor: number) => ({ styleId: "mnNumber", data: { value: (minor / 100).toFixed(2), type: "Number" as const } });

/** MN-07: per-currency totals under the ledger. Currencies are never added together. */
function totals(rows: DeskRow[], recoveries: RecoveryFlow[]): ExcelRow[] {
  return currencies(rows, recoveries).flatMap((c) => {
    const f = figures(rows, recoveries, c);
    return [
      { cells: [] },
      { cells: [text(`Totals ${c}`, "mnHead"), text("Currency", "mnHead"), text("Amount", "mnHead")] },
      { cells: [text("Refunded"), text(c), num(f.refunded)] },
      { cells: [text("of which simulated", "mnText"), text(c), num(f.simulated)] },
      { cells: [text("Recovered from carriers"), text(c), num(f.recovered)] },
      { cells: [text("Net cost"), text(c), num(f.net)] },
      { cells: [text("At PayPal"), text(c), num(f.atPayPal)] },
    ];
  });
}

const iso = (v: string | null) => (v ? new Date(v).toISOString().slice(0, 19) : "");

function cell(p: ProcessCellForExportParams<Row>): string {
  const r = p.node?.data;
  if (!r) return p.value ?? "";
  switch (p.column.getColId()) {
    case "created_at": return iso(r.created_at);
    case "completed_at": return iso(r.completed_at);
    case "amount_minor": return (r.amount_minor / 100).toFixed(2);
    case "kind": return KIND[r.kind] ?? r.kind;
    case "status": return statusOf(r.status).label;
    case "receipts": return String(r.receipts);
    default: return p.value == null ? "" : String(p.value);
  }
}

/** One workbook: a cover line, every money action (whatever the filter), then totals per currency. */
export function exportLedger(api: GridApi<Row>, rows: DeskRow[], recoveries: RecoveryFlow[]) {
  const stamp = new Date();
  api.exportDataAsExcel({
    fileName: `doorstep-money-${stamp.toISOString().slice(0, 10)}.xlsx`,
    sheetName: "Money",
    author: "Doorstep",
    columnKeys: ["created_at", "completed_at", "order_ref", "item", "kind", "amount_minor", "currency", "status", "paypal_refund_id", "request_id", "receipts"],
    exportedRows: "all",
    skipPinnedBottom: true,
    processCellCallback: cell,
    prependContent: [
      { cells: [text("Doorstep · Money ledger", "mnTitle", 4)] },
      { cells: [text(`Exported ${stamp.toLocaleString("en-GB", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" })} · ${rows.length} money action${rows.length === 1 ? "" : "s"} · totals per currency`, "mnText", 6)] },
      { cells: [] },
    ],
    appendContent: totals(rows, recoveries),
  });
}
