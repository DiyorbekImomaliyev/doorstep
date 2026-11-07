import { memo, useEffect, useRef, useState } from "react";
import { Scale } from "lucide-react";
import { DOT_ROWS } from "./worldDots";

/* The problem, world first: a dotted map that zooms from the world to the UK, the EU and the US.
   The tabs and figures are the accessible content; the map is decoration (aria-hidden).
   Every figure and law links to its source. */

type Key = "world" | "uk" | "eu" | "us";
type Figure = { value: string; label: string; source: string; href: string };
type Region = {
  key: Key; tab: string; figures: Figure[];
  law: string; lawSource: string; lawHref?: string; rule?: string;
  view: { cx: number; cy: number; k: number };
};

/* Map units: one 2° cell per unit, 180 × 70, top edge at 84°N. */
const W = 180, H = 70;
const at = (lon: number, lat: number) => ({ x: (lon + 180) / 2, y: (84 - lat) / 2 });
const PINS: Record<Exclude<Key, "world">, { x: number; y: number }> = {
  uk: at(-0.13, 51.5), eu: at(8.7, 50.1), us: at(-87.6, 41.9),
};

const REGIONS: Region[] = [
  {
    key: "world", tab: "World",
    figures: [
      { value: "€577bn", label: "spent on parcel and express delivery in 2025, up 5.8%", source: "Ti Insight, 2026", href: "https://www.parcelandpostaltechnologyinternational.com/news/parcels/global-express-and-parcels-market-grew-5-8-to-e577-3bn-in-2025-ti-insight-says.html" },
    ],
    law: "UK and EU: the seller's risk. US: the seller's policy.", lawSource: "Each one a versioned rule file in Doorstep",
    view: { cx: W / 2, cy: H / 2, k: 1 },
  },
  {
    key: "uk", tab: "UK",
    figures: [
      { value: "15M", label: "had a problem with their latest delivery (37%)", source: "Citizens Advice, Nov 2025", href: "https://www.citizensadvice.org.uk/about-us/media-centre/press-releases/parcel-problems-reach-record-as-15-million-people-are-let-down-on-the/" },
      { value: "29%", label: "said the courier left before they reached the door", source: "Citizens Advice, Nov 2025", href: "https://www.citizensadvice.org.uk/about-us/media-centre/press-releases/parcel-problems-reach-record-as-15-million-people-are-let-down-on-the/" },
    ],
    law: "The seller's risk until the buyer holds it.", lawSource: "Consumer Rights Act 2015, s.29",
    lawHref: "https://www.legislation.gov.uk/ukpga/2015/15/section/29", rule: "uk_possession_v1",
    view: { cx: 91, cy: 18, k: 3.1 },
  },
  {
    key: "eu", tab: "EU",
    figures: [
      { value: "35%", label: "of online shoppers hit a problem buying online", source: "Eurostat, Mar 2026", href: "https://ec.europa.eu/eurostat/web/products-eurostat-news/w/ddn-20260311-1" },
      { value: "1 in 5", label: "had a late delivery, the most common problem", source: "Eurostat, Mar 2026", href: "https://ec.europa.eu/eurostat/web/products-eurostat-news/w/ddn-20260311-1" },
    ],
    law: "Risk passes only when the buyer holds it.", lawSource: "Consumer Rights Directive, Art. 20",
    lawHref: "https://www.eccnet.eu/publication/parcel-delivery-challenges-addressed-ecc-net-e-commerce-sector", rule: "eu_possession_v1",
    view: { cx: 97, cy: 19.5, k: 2.6 },
  },
  {
    key: "us", tab: "US",
    figures: [
      { value: "23.1B", label: "parcels shipped in 2025", source: "Pitney Bowes, 2026", href: "https://www.pitneybowes.com/content/dam/pitneybowes/us/en/shipping-index/dmr-5127-parcel-shipping-index-report-2026-ebook.pdf" },
      { value: "80M", label: "Americans have had a package stolen (3 in 10)", source: "Security.org, 2026", href: "https://www.security.org/package-theft/annual-report/" },
    ],
    law: "No single rule. The seller's policy decides.", lawSource: "Doorstep applies the seller's written policy", rule: "us_seller_policy_v1",
    view: { cx: 44, cy: 23, k: 2.3 },
  },
];

const CYCLE = 3800;

/* One path per dot class: "M x y h0" with round caps draws a dot, so ~3,900 dots are 4 elements. */
function decode(): Record<"l" | "u" | "e" | "s", string> {
  const out = { l: "", u: "", e: "", s: "" };
  DOT_ROWS.forEach((row, r) => {
    let c = 0;
    for (const [, ch, n] of row.matchAll(/([.lues])(\d+)/g)) {
      const len = Number(n);
      if (ch !== ".") for (let i = 0; i < len; i++) out[ch as keyof typeof out] += `M${c + i + 0.5} ${r + 0.5}h0`;
      c += len;
    }
  });
  return out;
}

const Dots = memo(function Dots() {
  const d = decode();
  return (
    <>
      <path className="dots land" d={d.l} />
      <path className="dots r-uk" d={d.u} />
      <path className="dots r-eu" d={d.e} />
      <path className="dots r-us" d={d.s} />
    </>
  );
});

export default function WorldProblem() {
  const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const [i, setI] = useState(0);
  const [auto, setAuto] = useState(!reduced);
  const [seen, setSeen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") { setSeen(true); return; }
    const io = new IntersectionObserver(([e]) => setSeen(e.isIntersecting), { threshold: 0.4 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!auto || !seen) return;
    const t = setTimeout(() => setI((n) => (n + 1) % REGIONS.length), CYCLE);
    return () => clearTimeout(t);
  }, [auto, seen, i]);

  const r = REGIONS[i];
  const { cx, cy, k } = r.view;
  const zoom = `translate(${W / 2 - cx * k}px, ${H / 2 - cy * k}px) scale(${k})`;
  const pins = r.key === "world" ? (["uk", "eu", "us"] as const) : [r.key];

  return (
    <div className="wp" ref={ref}>
      <div className="wp-map" aria-hidden>
        <svg viewBox={`0 0 ${W} ${H}`} data-r={r.key} preserveAspectRatio="xMidYMid meet">
          <g className="wp-zoom" style={{ transform: zoom }}>
            <Dots />
            {pins.map((p) => (
              <g key={p} transform={`translate(${PINS[p].x} ${PINS[p].y})`}>
                <g className="pin" style={{ transform: `scale(${(1 + (k - 1) * 0.5) / k})` }}>
                  <circle className="pin-pulse" r="2.2" />
                  <circle className="pin-dot" r="1.1" />
                </g>
              </g>
            ))}
          </g>
        </svg>
      </div>

      <div className="wp-side">
        <div className="region-tabs" role="tablist" aria-label="Region">
          {REGIONS.map((g, n) => (
            <button key={g.key} role="tab" aria-selected={n === i} onClick={() => { setI(n); setAuto(false); }}>
              {g.tab}
              {n === i && auto && seen && <i key={i} className="seg-time" style={{ animationDuration: `${CYCLE}ms` }} aria-hidden />}
            </button>
          ))}
        </div>
        <div className="wp-body" role="tabpanel" aria-label={r.tab} key={r.key}>
          <div className="wp-figs">
            {r.figures.map((f) => (
              <div key={f.value} className="wp-fig">
                <span className="stat-value">{f.value}</span>
                <span className="stat-label">{f.label}</span>
                <a className="stat-src" href={f.href} target="_blank" rel="noreferrer">{f.source}</a>
              </div>
            ))}
          </div>
          <div className="wp-law">
            <span className="wp-law-icon" aria-hidden><Scale /></span>
            <div className="col" style={{ gap: 4, minWidth: 0, flex: 1 }}>
              <b>{r.law}</b>
              <span className="wp-law-src">
                {r.lawHref
                  ? <a className="stat-src" href={r.lawHref} target="_blank" rel="noreferrer">{r.lawSource}</a>
                  : <span className="stat-src">{r.lawSource}</span>}
                {r.rule && <code className="wp-rule">{r.rule}</code>}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
