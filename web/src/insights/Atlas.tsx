/**
 * The delivery atlas (AS-15, AS-16): the seller's postcode areas as an isometric 3D tile map, drawn
 * in SVG with no library (the house style of Journey3D). One tile per area, placed by its real
 * position (atlasGeo.ts). Height is orders; colour is the share of orders that failed, on a calm
 * maroon ramp. Hover or focus shows the numbers, a click filters the page to the area.
 *
 * Privacy: only the postcode area is read, never an address or a buyer's name. No model calls.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { AgWidgetDataFormat, AgWidgetFieldReference, AgWidgetParams } from "ag-studio";
import { areaOf, layout, lookup, regionOf, type Region } from "./atlasGeo";
import { keyOf, num, useSelection, useSize, useWidgetRows } from "./widgetKit";

type Ref = AgWidgetFieldReference[];
export type AtlasDef = {
  type: "doorstep-atlas";
  dataMapping: { area: Ref; country: Ref; orders: Ref; failed: Ref; lost?: Ref };
  format?: AgWidgetDataFormat<unknown>;
};

type Tile = {
  area: string; name: string; col: number; row: number; placed: boolean; districts: string[];
  orders: number; failed: number; lost: number; out: boolean;
};

const REGION_NAME: Record<Region, string> = { GB: "UK", US: "US" };
/** Light to dark maroon: low to high failed rate. */
const RAMP = ["#f1e4e7", "#dfb4be", "#bf7486", "#8f3c50", "#5a1d2b"];
const RAMP_RGB = RAMP.map((h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)));
const ramp = (t: number) => {
  const x = Math.max(0, Math.min(1, t)) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(x));
  const f = x - i;
  return RAMP_RGB[i].map((v, k) => Math.round(v + (RAMP_RGB[i + 1][k] - v) * f));
};
const rgb = (c: number[]) => `rgb(${c.map((v) => Math.round(v)).join(",")})`;
/** A side face: the top colour mixed toward a warm dark, so the sides stay maroon, never grey. */
const shade = (c: number[], k: number) => rgb(c.map((v, i) => v + ([46, 20, 28][i] - v) * k));

const A = 26; // half the width of one cell
const F = 0.43; // half the footprint of a tile, in cells
const iso = (c: number, r: number, z = 0): [number, number] => [(c - r) * A, (c + r) * A * 0.5 - z];
const pts = (list: [number, number][]) => list.map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" ");
const ease = (t: number) => 1 - Math.pow(1 - Math.max(0, Math.min(1, t)), 3);
const share = (n: number, of: number) => (of > 0 ? `${((n / of) * 100).toFixed(n / of < 0.1 ? 1 : 0)}%` : "0%");

export function AtlasWidget(params: AgWidgetParams<AtlasDef>) {
  const { widgetApi, dataMapping: m } = params;
  const fArea = m.area?.[0], fCountry = m.country?.[0], fOrders = m.orders?.[0], fFailed = m.failed?.[0], fLost = m.lost?.[0];
  const got = useWidgetRows(params, [fArea, fCountry, fOrders, fFailed, fLost], fOrders, { min: 4, highlight: true });
  const { picked, toggle } = useSelection(widgetApi, fArea);
  const [box, size] = useSize<HTMLDivElement>();
  const [pick, setPick] = useState<Region | null>(null);
  const [tip, setTip] = useState<{ area: string; x: number; y: number } | null>(null);
  const [grow, setGrow] = useState(1);
  const played = useRef(false);

  // Rows into areas, per region. A filter from another widget keeps every area and fades the rest.
  const { byRegion, home } = useMemo(() => {
    const out: Record<Region, Map<string, Tile>> = { GB: new Map(), US: new Map() };
    const total: Record<Region, number> = { GB: 0, US: 0 };
    if (!got || !fArea || !fCountry || !fOrders || !fFailed) return { byRegion: out, home: "GB" as Region };
    const hi = got.hi ? new Map(got.hi.map((r) => [`${keyOf(r[fArea.key])}|${keyOf(r[fCountry.key])}`, r])) : null;
    for (const r of got.rows) {
      const region = regionOf(String(r[fCountry.key] ?? ""));
      const district = String(r[fArea.key] ?? "");
      const area = areaOf(district, region);
      if (!area) continue;
      const live = hi ? hi.get(`${keyOf(r[fArea.key])}|${keyOf(r[fCountry.key])}`) : r;
      const t = out[region].get(area) ?? {
        area, name: lookup(area, region)?.name ?? area, col: 0, row: 0, placed: false, districts: [], orders: 0, failed: 0, lost: 0, out: true,
      };
      t.districts.push(district);
      t.orders += num(live?.[fOrders.key]);
      t.failed += num(live?.[fFailed.key]);
      t.lost += fLost ? num(live?.[fLost.key]) : 0;
      if (live) t.out = t.out && num(live[fOrders.key]) === 0;
      total[region] += num(r[fOrders.key]);
      out[region].set(area, t);
    }
    return { byRegion: out, home: (total.US > total.GB ? "US" : "GB") as Region };
  }, [got, fArea, fCountry, fOrders, fFailed, fLost]);

  const regions = (["GB", "US"] as Region[]).filter((r) => byRegion[r].size > 0);
  const region: Region = pick && regions.includes(pick) ? pick : regions.includes(home) ? home : regions[0] ?? "GB";
  const scene = useMemo(() => {
    const tiles = [...byRegion[region].values()];
    const cells = layout(tiles.map((t) => ({ area: t.area, weight: t.orders })), region);
    tiles.forEach((t) => Object.assign(t, cells.get(t.area)));
    const cols = Math.max(1, ...tiles.map((t) => t.col + 1)), rows = Math.max(1, ...tiles.map((t) => t.row + 1));
    const maxOrders = Math.max(1, ...tiles.map((t) => t.orders));
    // The colour scale ignores tiny samples (one failure in two orders is not 50%), and never falls below 1.5x the shop average.
    const sumO = tiles.reduce((a, t) => a + t.orders, 0), sumF = tiles.reduce((a, t) => a + t.failed, 0);
    const maxRate = Math.max(0.02, (sumF / Math.max(1, sumO)) * 1.5, ...tiles.filter((t) => t.orders >= 10).map((t) => t.failed / t.orders));
    const maxH = Math.max(52, Math.min(110, (cols + rows) * A * 0.3));
    const ordered = [...tiles].sort((a, b) => a.col + a.row - (b.col + b.row) || a.col - b.col);
    const top = new Set([...tiles].sort((a, b) => b.orders - a.orders).slice(0, 4).map((t) => t.area));
    // The sheet the tiles stand on: every cell in the box, so the map reads as a place.
    const floor = Array.from({ length: cols * rows }, (_, i) => ({ c: i % cols, r: Math.floor(i / cols) }));
    const xs = [iso(-0.5, rows - 0.5)[0], iso(cols - 0.5, -0.5)[0]];
    const y0 = iso(-0.5, -0.5)[1] - maxH - 26, y1 = iso(cols - 0.5, rows - 0.5)[1] + 16;
    const w = Math.max(420, xs[1] - xs[0] + 56), h = Math.max(260, y1 - y0 + 8);
    const cx = (xs[0] + xs[1]) / 2, cy = (y0 + y1) / 2;
    return { ordered, maxOrders, maxRate, maxH, top, floor, view: [cx - w / 2, cy - h / 2, w, h] as const, cols, rows };
  }, [byRegion, region]);

  // The rise on load: tiles stand up one after another, once. Reduced motion skips it.
  const ready = !!got && scene.ordered.length > 0;
  useEffect(() => {
    if (!ready || played.current) return;
    played.current = true;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const start = performance.now();
    let id = 0;
    const tick = (now: number) => {
      const t = (now - start) / 1000;
      setGrow(t);
      if (t < 1.3) id = requestAnimationFrame(tick);
      else setGrow(10);
    };
    setGrow(0);
    id = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(id);
  }, [ready]);

  if (!got || !fArea || !fCountry || !fOrders || !fFailed || !scene.ordered.length) return null;

  const money = (t: Tile) => (fLost && region === home ? widgetApi.formatFieldValue(fLost, t.lost) : null);
  const sel = (t: Tile) => t.districts.length > 0 && t.districts.every((d) => picked.has(d));
  const label = (t: Tile) =>
    `${t.name}${t.name === t.area ? "" : ` (${t.area})`}: ${t.orders} orders, ${t.failed} failed${money(t) ? `, ${money(t)} lost` : ""}`;
  const click = (t: Tile) => t.districts.forEach((d) => toggle(d));
  const showTip = (el: Element, t: Tile) => {
    const b = el.getBoundingClientRect(), w = box.current?.getBoundingClientRect();
    if (w) setTip({ area: t.area, x: b.left - w.left + b.width / 2, y: b.top - w.top });
  };
  const tipTile = tip ? scene.ordered.find((t) => t.area === tip.area) : undefined;
  const anyPicked = scene.ordered.some(sel);
  const [vx, vy, vw, vh] = scene.view;
  // The size of one SVG unit on screen, so labels keep a readable size on a small card.
  const unit = size.w ? Math.min(size.w / vw, size.h / vh) : 1;

  return (
    <div className="ds-atlas" ref={box} data-picking={anyPicked || undefined} onMouseLeave={() => setTip(null)}>
      <svg viewBox={`${vx} ${vy} ${vw} ${vh}`} preserveAspectRatio="xMidYMid meet" role="group" aria-label="Delivery areas">
        <g aria-hidden>
          {scene.floor.map(({ c, r }) => (
            <polygon key={`${c}:${r}`} className="ds-atlas-cell" points={pts([iso(c - 0.5 + 0.04, r - 0.5 + 0.04), iso(c + 0.5 - 0.04, r - 0.5 + 0.04), iso(c + 0.5 - 0.04, r + 0.5 - 0.04), iso(c - 0.5 + 0.04, r + 0.5 - 0.04)])} />
          ))}
        </g>
        {scene.ordered.map((t, i) => {
          const rate = t.orders ? t.failed / t.orders : 0;
          const col = ramp(rate / scene.maxRate);
          const h = Math.max(6, (t.orders / scene.maxOrders) * scene.maxH) * ease(grow - i * 0.045);
          const c = t.col, r = t.row;
          const top = [iso(c - F, r - F, h), iso(c + F, r - F, h), iso(c + F, r + F, h), iso(c - F, r + F, h)] as [number, number][];
          const left = [iso(c - F, r + F, h), iso(c + F, r + F, h), iso(c + F, r + F, 0), iso(c - F, r + F, 0)] as [number, number][];
          const right = [iso(c + F, r - F, h), iso(c + F, r + F, h), iso(c + F, r + F, 0), iso(c + F, r - F, 0)] as [number, number][];
          const ring = [iso(c - F - 0.07, r - F - 0.07), iso(c + F + 0.07, r - F - 0.07), iso(c + F + 0.07, r + F + 0.07), iso(c - F - 0.07, r + F + 0.07)] as [number, number][];
          const [lx, ly] = iso(c, r, h);
          const on = sel(t);
          return (
            <g key={t.area} className="ds-atlas-tile" tabIndex={0} role="button" aria-pressed={on} aria-label={label(t)}
              data-picked={on || undefined} data-out={t.out || undefined} data-tip={tip?.area === t.area || undefined}
              onMouseEnter={(e) => showTip(e.currentTarget, t)} onFocus={(e) => showTip(e.currentTarget, t)}
              onBlur={() => setTip(null)} onClick={() => click(t)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); click(t); } }}>
              <polygon className="ds-atlas-ring" points={pts(ring)} />
              <g className="ds-atlas-body">
                <polygon points={pts(left)} fill={shade(col, 0.13)} />
                <polygon points={pts(right)} fill={shade(col, 0.27)} />
                <polygon className="ds-atlas-top" points={pts(top)} fill={rgb(col)} />
                {(scene.top.has(t.area) || on) && grow > 1 && (
                  <text className="ds-atlas-code" x={lx} y={ly - 7 / Math.max(unit, 0.5)} textAnchor="middle" fontSize={11 / Math.max(unit, 0.5)}>{t.area}</text>
                )}
              </g>
            </g>
          );
        })}
      </svg>

      {regions.length > 1 && (
        <div className="ds-atlas-switch" role="group" aria-label="Region">
          {regions.map((r) => (
            <button key={r} type="button" aria-pressed={r === region} onClick={() => { setPick(r); setTip(null); }}>{REGION_NAME[r]}</button>
          ))}
        </div>
      )}

      <div className="ds-atlas-legend" aria-hidden>
        <span className="ds-atlas-key"><i className="ds-atlas-bars" />Orders</span>
        <span className="ds-atlas-key">
          Failed
          <i className="ds-atlas-ramp" style={{ background: `linear-gradient(90deg, ${RAMP.join(",")})` }} />
          <span className="ds-atlas-ends">0 to {Math.round(scene.maxRate * 100)}%+</span>
        </span>
      </div>

      {tip && tipTile && (
        <div className="ds-atlas-tip" role="tooltip" style={{ left: tip.x, top: tip.y }} data-below={tip.y < 96 || undefined}>
          <strong>{tipTile.name}{tipTile.name === tipTile.area ? "" : <span> {tipTile.area}</span>}</strong>
          <span><b>{tipTile.orders.toLocaleString("en-GB")}</b> orders</span>
          <span><b>{tipTile.failed.toLocaleString("en-GB")}</b> failed · {share(tipTile.failed, tipTile.orders)}</span>
          {money(tipTile) && <span><b>{money(tipTile)}</b> lost</span>}
        </div>
      )}
    </div>
  );
}
