/**
 * AS-04: Doorstep's own Studio widgets, written as React components (the Bar Gauge pattern from
 * AG's Widget Library demo). Each reads through `widgetApi.getData`, so it follows page filters,
 * cross filters and live updates exactly like a built-in widget.
 *
 *  - Evidence meter: how much proof is on file, per delivery method or carrier, in five segments
 *    like the Control Room's Proof column, read against fixed thresholds.
 *  - Failure points: every case cause ranked, with its share and the money it cost.
 *  - Money flow: refunded to buyers, minus what carriers paid back, equals the net loss.
 */
import { useEffect, useRef, useState } from "react";
import type {
  AgBaseRegistry, AgBaseWidgetDefinition, AgDefaultWidgetDefinition, AgWidgetDataFormat, AgWidgetField,
  AgWidgetFieldReference, AgWidgetFormParams, AgWidgetParams,
} from "ag-studio";

// ---------------------------------------------------------------- types

export type MeterDef = {
  type: "doorstep-evidence-meter";
  dataMapping: { category: AgWidgetFieldReference[]; value: AgWidgetFieldReference[] };
  format?: AgWidgetDataFormat<{ goodFrom?: number; okFrom?: number }>;
};
export type FailureDef = {
  type: "doorstep-failure-points";
  dataMapping: { category: AgWidgetFieldReference[]; value: AgWidgetFieldReference[]; money: AgWidgetFieldReference[] };
  format?: AgWidgetDataFormat<unknown>;
};

export type FlowDef = {
  type: "doorstep-money-flow";
  dataMapping: { refunded: AgWidgetFieldReference[]; recovered: AgWidgetFieldReference[] };
  format?: AgWidgetDataFormat<unknown>;
};

export interface DoorstepRegistry extends AgBaseRegistry {
  widgets: readonly (
    | AgDefaultWidgetDefinition
    | AgBaseWidgetDefinition<"doorstep-evidence-meter", MeterDef>
    | AgBaseWidgetDefinition<"doorstep-failure-points", FailureDef>
    | AgBaseWidgetDefinition<"doorstep-money-flow", FlowDef>
  )[];
}

type Row = Record<string, unknown>;

/** Shared loading: one query, superseded loads ignored, display states set like a built-in widget. */
function useWidgetRows<D extends MeterDef | FailureDef | FlowDef>(
  params: AgWidgetParams<D>, fields: (AgWidgetField | undefined)[], sortBy: AgWidgetField | undefined,
) {
  // Rows are kept with the fields they were fetched for: when Studio reuses this widget on another
  // page, the old rows are never drawn against the new fields.
  const sig = fields.map((f) => f?.key ?? "").join("|");
  const [got, setGot] = useState<{ sig: string; rows: Row[] } | null>(null);
  const loaded = useRef(false);
  useEffect(() => {
    let current = true;
    const { widgetApi } = params;
    const need = fields.filter(Boolean) as AgWidgetField[];
    if (need.length < 2) { widgetApi.setDisplayState("incompleteDataMapping"); return; }
    widgetApi.setDisplayState("loading", { prominent: !loaded.current });
    widgetApi.getData({ fields: need, sort: sortBy ? [{ field: sortBy, direction: "desc" }] : [] }).then(({ results }) => {
      if (!current) return;
      const rows = results.rows as Row[];
      loaded.current = true;
      widgetApi.setDisplayState(rows.length ? "displayed" : "noData");
      setGot({ sig, rows });
    });
    return () => { current = false; };
    // Studio hands over new params on every refresh, so each one loads again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);
  return got?.sig === sig ? got.rows : null;
}

// ---------------------------------------------------------------- evidence meter

const SEGMENTS = 5;

export function EvidenceMeter(params: AgWidgetParams<MeterDef>) {
  const { widgetApi, dataMapping, format } = params;
  const cat = dataMapping.category?.[0];
  const val = dataMapping.value?.[0];
  const rows = useWidgetRows(params, [cat, val], val);
  if (!rows || !cat || !val) return null;
  const good = format?.style?.goodFrom ?? 0.8;
  const ok = format?.style?.okFrom ?? 0.5;
  return (
    <div className="ds-meter">
      {rows.map((r, i) => {
        const v = Number(r[val.key] ?? 0);
        const lit = Math.round(Math.max(0, Math.min(1, v)) * SEGMENTS);
        const tone = v >= good ? "good" : v >= ok ? "ok" : "low";
        return (
          <div className="ds-meter-row" key={`${i}:${String(r[cat.key])}`} data-tone={tone}>
            <span className="ds-meter-label">{widgetApi.formatFieldValue(cat, r[cat.key])}</span>
            <span className="ds-meter-track" aria-hidden>
              {Array.from({ length: SEGMENTS }, (_, n) => <i key={n} data-on={n < lit || undefined} />)}
            </span>
            <span className="ds-meter-value">{widgetApi.formatFieldValue(val, v)}</span>
          </div>
        );
      })}
      <div className="ds-meter-key">
        <span data-tone="good">{Math.round(good * 100)}%+ strong proof</span>
        <span data-tone="ok">{Math.round(ok * 100)}%+ partial</span>
        <span data-tone="low">thin</span>
      </div>
    </div>
  );
}

const METER_ICON = `<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg"><g fill="var(--ag-chart-palette-fills-1-color,#7b2d3d)"><rect x="2" y="6" width="5" height="5" rx="1"/><rect x="8" y="6" width="5" height="5" rx="1"/><rect x="14" y="6" width="5" height="5" rx="1"/><rect x="20" y="6" width="5" height="5" rx="1"/><rect x="2" y="14" width="5" height="5" rx="1"/><rect x="8" y="14" width="5" height="5" rx="1"/><rect x="2" y="22" width="5" height="5" rx="1"/></g><g fill="var(--ag-border-color,#d8d0c8)"><rect x="26" y="6" width="5" height="5" rx="1"/><rect x="14" y="14" width="5" height="5" rx="1"/><rect x="20" y="14" width="5" height="5" rx="1"/><rect x="26" y="14" width="5" height="5" rx="1"/><rect x="8" y="22" width="5" height="5" rx="1"/><rect x="14" y="22" width="5" height="5" rx="1"/><rect x="20" y="22" width="5" height="5" rx="1"/><rect x="26" y="22" width="5" height="5" rx="1"/></g></svg>`;

export const evidenceMeterWidget: AgBaseWidgetDefinition<"doorstep-evidence-meter", MeterDef> = {
  id: "doorstep-evidence-meter",
  label: "Evidence meter",
  icon: METER_ICON,
  dataMapping: {
    category: { type: "field", supportedRoles: ["category"], required: true },
    value: { type: "field", supportedRoles: ["numeric"], required: true },
  },
  form: (p: AgWidgetFormParams<MeterDef>) => p.createDefaults({
    dataMappingItems: [{ key: "category", label: "Group by" }, { key: "value", label: "Share of proof (0 to 1)" }],
  }),
  comp: EvidenceMeter,
  defaultSize: { width: 420, height: 260 },
  minSize: { width: 260, height: 160 },
};

// ---------------------------------------------------------------- failure points

export function FailurePoints(params: AgWidgetParams<FailureDef>) {
  const { widgetApi, dataMapping } = params;
  const cat = dataMapping.category?.[0];
  const val = dataMapping.value?.[0];
  const cost = dataMapping.money?.[0];
  const rows = useWidgetRows(params, [cat, val, cost], val);
  if (!rows || !cat || !val) return null;
  const total = rows.reduce((a, r) => a + Number(r[val.key] ?? 0), 0) || 1;
  const top = Math.max(...rows.map((r) => Number(r[val.key] ?? 0)), 1);
  return (
    <ol className="ds-fp">
      {rows.map((r, i) => {
        const n = Number(r[val.key] ?? 0);
        return (
          <li key={`${i}:${String(r[cat.key])}`} data-rank={i}>
            <div className="ds-fp-line">
              <span className="ds-fp-name">{widgetApi.formatFieldValue(cat, r[cat.key])}</span>
              <span className="ds-fp-nums">
                <strong>{widgetApi.formatFieldValue(val, n)}</strong>
                <span>{Math.round((n / total) * 100)}%</span>
                {cost && <span className="ds-fp-cost">{r[cost.key] != null ? widgetApi.formatFieldValue(cost, r[cost.key]) : "no refund"}</span>}
              </span>
            </div>
            <span className="ds-fp-bar" aria-hidden><i style={{ width: `${(n / top) * 100}%` }} /></span>
          </li>
        );
      })}
    </ol>
  );
}

const FP_ICON = `<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg"><g fill="var(--ag-chart-palette-fills-1-color,#7b2d3d)"><rect x="3" y="5" width="26" height="4" rx="2"/><rect x="3" y="12" width="18" height="4" rx="2" fill-opacity=".75"/><rect x="3" y="19" width="12" height="4" rx="2" fill-opacity=".55"/><rect x="3" y="26" width="7" height="3" rx="1.5" fill-opacity=".35"/></g></svg>`;

export const failurePointsWidget: AgBaseWidgetDefinition<"doorstep-failure-points", FailureDef> = {
  id: "doorstep-failure-points",
  label: "Failure points",
  icon: FP_ICON,
  dataMapping: {
    category: { type: "field", supportedRoles: ["category"], required: true },
    value: { type: "field", supportedRoles: ["numeric"], required: true },
    money: { type: "field", supportedRoles: ["numeric"] },
  },
  form: (p: AgWidgetFormParams<FailureDef>) => p.createDefaults({
    dataMappingItems: [{ key: "category", label: "Failure point" }, { key: "value", label: "Cases" }, { key: "money", label: "Money" }],
  }),
  comp: FailurePoints,
  defaultSize: { width: 460, height: 300 },
  minSize: { width: 280, height: 180 },
};

// ---------------------------------------------------------------- money flow

export function MoneyFlow(params: AgWidgetParams<FlowDef>) {
  const { widgetApi, dataMapping } = params;
  const out = dataMapping.refunded?.[0];
  const back = dataMapping.recovered?.[0];
  const rows = useWidgetRows(params, [out, back], undefined);
  if (!rows || !out || !back) return null;
  const refunded = Number(rows[0]?.[out.key] ?? 0);
  const recovered = Number(rows[0]?.[back.key] ?? 0);
  const net = refunded - recovered;
  const share = refunded > 0 ? Math.round((recovered / refunded) * 100) : 0;
  const w = (v: number) => `${refunded > 0 ? Math.max(0, Math.min(100, (v / refunded) * 100)) : 0}%`;
  return (
    <div className="ds-flow">
      <div className="ds-flow-row">
        <span>Refunded to buyers</span><strong>{widgetApi.formatFieldValue(out, refunded)}</strong>
        <span className="ds-flow-bar" aria-hidden><i data-kind="out" style={{ width: w(refunded) }} /></span>
      </div>
      <div className="ds-flow-row">
        <span>Recovered from carriers</span><strong className="ds-flow-good">− {widgetApi.formatFieldValue(back, recovered)}</strong>
        <span className="ds-flow-bar" aria-hidden><i data-kind="back" style={{ width: w(recovered) }} /></span>
      </div>
      <div className="ds-flow-row ds-flow-total">
        <span>Net delivery loss</span><strong>{widgetApi.formatFieldValue(out, net)}</strong>
        <span className="ds-flow-bar" aria-hidden><i data-kind="net" style={{ width: w(net) }} /></span>
      </div>
      <p className="ds-flow-note">Carriers paid back <strong>{share}%</strong> of what you refunded to buyers.</p>
    </div>
  );
}

const FLOW_ICON = `<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg"><rect x="3" y="6" width="26" height="5" rx="2" fill="var(--ag-chart-palette-fills-1-color,#7b2d3d)"/><rect x="3" y="14" width="9" height="5" rx="2" fill="var(--ag-chart-palette-fills-3-color,#3f6f73)"/><rect x="3" y="22" width="17" height="5" rx="2" fill="var(--ag-chart-palette-fills-1-color,#7b2d3d)" fill-opacity=".55"/></svg>`;

export const moneyFlowWidget: AgBaseWidgetDefinition<"doorstep-money-flow", FlowDef> = {
  id: "doorstep-money-flow",
  label: "Money flow",
  icon: FLOW_ICON,
  dataMapping: {
    refunded: { type: "field", supportedRoles: ["numeric"], required: true },
    recovered: { type: "field", supportedRoles: ["numeric"], required: true },
  },
  form: (p: AgWidgetFormParams<FlowDef>) => p.createDefaults({
    dataMappingItems: [{ key: "refunded", label: "Refunded" }, { key: "recovered", label: "Recovered" }],
  }),
  comp: MoneyFlow,
  defaultSize: { width: 380, height: 260 },
  minSize: { width: 260, height: 180 },
};

export const doorstepWidgetTypes = [evidenceMeterWidget, failurePointsWidget, moneyFlowWidget];
