import { useEffect, useMemo, useRef } from "react";
import { AgCharts, AllCommunityModule, ModuleRegistry, type AgCartesianChartOptions, type AgChartInstance } from "ag-charts-enterprise";
import { C } from "../../tokens";
import { money } from "../../format";
import type { Week } from "./ledger";

ModuleRegistry.registerModules([AllCommunityModule]);

const OUT = C.accent;
const BACK = "#2f6f73"; // the Insights palette's second hue, so money back reads the same everywhere

type Props = { weeks: Week[]; currency: string; compact?: boolean };

/** Narrow x labels for a phone: the month only where it changes ("5 Oct", "12", "19", "2 Nov"). */
function shortLabels(weeks: Week[]): Map<string, string> {
  const out = new Map<string, string>();
  let month = "";
  for (const w of weeks) {
    const [day, mon] = w.week.split(" ");
    out.set(w.week, mon && mon !== month ? w.week : day);
    month = mon ?? month;
  }
  return out;
}

/** MN-03: weekly money out (refunds) against money back (carrier claims), one currency. */
export default function FlowChart({ weeks, currency, compact = false }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<AgChartInstance | null>(null);
  const fmt = (major: number) => money(Math.round(major * 100), currency);
  const short = useMemo(() => shortLabels(weeks), [weeks]);
  const options = useMemo((): AgCartesianChartOptions => ({
    data: weeks,
    height: 216,
    padding: { top: 6, right: 4, bottom: 0, left: 0 },
    background: { fill: "transparent" },
    theme: {
      baseTheme: "ag-default",
      params: { fontFamily: "Inter Variable, Inter, system-ui, sans-serif", foregroundColor: C.ink, textColor: C.ink3 },
      overrides: { common: { legend: { item: { label: { color: C.ink2, fontSize: 12 } } } } },
    },
    legend: { enabled: true, position: "top", spacing: 12, item: { marker: { shape: "square", size: 9 } } },
    series: (["out", "back"] as const).map((key) => ({
      type: "bar" as const, xKey: "week", yKey: key,
      yName: key === "out" ? "Refunds out" : "Carrier money back",
      fill: key === "out" ? OUT : BACK, strokeWidth: 0, cornerRadius: 3,
      tooltip: { renderer: (p: { datum: Week; yKey: string }) => ({
        heading: `Week of ${p.datum.week}`,
        title: p.yKey === "out" ? "Refunds out" : "Carrier money back",
        data: [{ label: p.yKey === "out" ? "Out" : "Back", value: fmt(p.datum[p.yKey as "out" | "back"]) },
               { label: "Net", value: fmt(p.datum.out - p.datum.back) }],
      }) },
    })),
    axes: {
      x: { type: "category", position: "bottom", line: { enabled: false }, paddingInner: 0.35, groupPaddingInner: 0.12,
        label: { color: C.ink3, fontSize: 11.5, autoRotate: false, avoidCollisions: true, wrapping: "never",
          ...(compact ? { formatter: (p: { value: string }) => short.get(p.value) ?? p.value } : {}) } },
      y: { type: "number", position: "left", line: { enabled: false }, nice: true, gridLine: { style: [{ stroke: C.hair, lineDash: [] }] },
        label: { color: C.ink3, fontSize: 11.5, formatter: (p: { value: number }) => fmt(p.value) } },
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [weeks, currency, compact, short]);

  useEffect(() => {
    if (!host.current) return;
    if (!chart.current) chart.current = AgCharts.create({ ...options, container: host.current });
    else void chart.current.update({ ...options, container: host.current });
  }, [options]);
  useEffect(() => () => { chart.current?.destroy(); chart.current = null; }, []);

  const totalOut = weeks.reduce((a, w) => a + w.out, 0);
  const totalBack = weeks.reduce((a, w) => a + w.back, 0);
  return (
    <figure className="mn-chart" aria-label={`Money flow by week, ${currency}`}>
      <div ref={host} className="mn-chart-host" role="img"
        aria-label={`Last ${weeks.length} weeks: ${fmt(totalOut)} out, ${fmt(totalBack)} back`} />
      <table className="sr-only">
        <caption>Money flow by week, {currency}</caption>
        <thead><tr><th>Week of</th><th>Refunds out</th><th>Carrier money back</th></tr></thead>
        <tbody>{weeks.map((w) => <tr key={w.start}><td>{w.week}</td><td>{fmt(w.out)}</td><td>{fmt(w.back)}</td></tr>)}</tbody>
      </table>
    </figure>
  );
}
