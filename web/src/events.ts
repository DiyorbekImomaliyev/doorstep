/**
 * Window events between parts of the seller's app that load separately (Insights is its own chunk).
 * Kept here, with no imports, so the Control Room can listen without pulling in AG Studio.
 */

/** Insights asks the Control Room to open one order's drawer: `{ orderId }`. */
export const OPEN_ORDER_EVENT = "doorstep:open-order";
export type OpenOrderDetail = { orderId: string };
