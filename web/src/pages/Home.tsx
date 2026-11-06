import { useState } from "react";
import {
  ArrowRight, BadgeCheck, ChevronRight, CircleCheck, CreditCard, FlaskConical, House, RotateCcw,
  Scale, ScanLine, Smartphone, Store, Truck,
} from "lucide-react";
import { api, type SeedOut } from "../api";
import { Brand, ErrorBox, useAsync } from "../components/ui";
import Journey3D from "../components/Journey3D";
import SystemDiagram from "../components/SystemDiagram";

/* Sourced figures (SPEC section 2). */
const STATS = [
  { value: "15M", label: "UK shoppers had a problem with their last delivery", source: "Citizens Advice, 2025", href: "https://www.hellorayo.co.uk/greatest-hits/south-coast/news/record-high-parcel-problems" },
  { value: "29.7%", label: "of US households had a parcel stolen in 2025", source: "Omnisend", href: "https://www.omnisend.com/porch-pirates/" },
  { value: "s.29", label: "UK law: the seller's risk until the buyer holds it", source: "Consumer Rights Act 2015", href: "https://www.legislation.gov.uk/ukpga/2015/15/section/29" },
];

const REAL = ["PayPal orders, refunds, disputes, webhooks", "Shippo courier tracking", "Rules, policy service, signed receipts"];

/* Short names for /healthz "simulated" (the full sentence is the tooltip). */
const SIMULATED: Record<string, string> = {
  lockers: "Locker booking",
  rerouting: "Redelivery choices",
  carrier_claims: "Carrier claim filing",
  scratch_refunds: "Refunds on demo orders",
  scratch_disputes: "Disputes on demo orders",
  risk_model: "Risk model (synthetic data)",
};

const NAV = (
  <nav aria-label="Sections">
    <a href="#how">How it works</a>
    <a href="#product">Product</a>
    <a href="#inside">Architecture</a>
    <a href="#demo">Demo</a>
  </nav>
);

export default function Home() {
  const health = useAsync(() => api.health(), []);
  const [seed, setSeed] = useState<SeedOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function start() {
    setBusy(true); setErr(null);
    try { setSeed(await api.seed(true)); } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }

  return (
    <div className="landing">
      <Brand nav={NAV} right={<a className="btn primary" href="#demo">Try the demo</a>} />

      <header className="hero">
        <span className="eyebrow"><b>For PayPal sellers</b> UK and US</span>
        <h1>No buyer loses money. <em>No honest seller</em> pays for a lost parcel.</h1>
        <p className="lede">Doorstep refunds the buyer in minutes, proves who had the parcel, and makes the courier pay.</p>
        <div className="ctas">
          <a className="btn primary lg" href="#demo">Try the live demo <ArrowRight size={17} /></a>
          <a className="btn lg" href="#how">How it works</a>
        </div>
      </header>

      <section className="stats" aria-label="The problem">
        {STATS.map((s) => (
          <div key={s.value} className="stat">
            <span className="stat-value">{s.value}</span>
            <span className="stat-label">{s.label}</span>
            <a className="stat-src" href={s.href} target="_blank" rel="noreferrer">{s.source}</a>
          </div>
        ))}
      </section>

      <section className="section" id="how" style={{ paddingTop: 24 }}>
        <div className="section-head">
          <span className="kicker">How it works</span>
          <h2>One parcel, start to finish</h2>
          <p>Sam buys a £60 jacket. The courier says &ldquo;Delivered&rdquo;. It wasn't.</p>
        </div>
        <Journey3D />
      </section>

      <section className="section" id="product">
        <div className="section-head">
          <span className="kicker">Product</span>
          <h2>One tap for the buyer. One for the seller.</h2>
          <p>Payments, tracking and scans arrive on their own.</p>
        </div>
        <Previews />
      </section>

      <section className="section" id="inside">
        <div className="section-head">
          <span className="kicker">Architecture</span>
          <h2>AI suggests. Code decides money.</h2>
          <p>One guarded service is the only path to a PayPal refund.</p>
        </div>
        <SystemDiagram />
      </section>

      <section className="section" id="demo">
        <div className="section-head">
          <span className="kicker">Live demo</span>
          <h2>Try it in a minute</h2>
          <p>Your own copy of Maria's shop, in the PayPal sandbox.</p>
        </div>
        <div className="demo">
          <div className="demo-top">
            <div className="col" style={{ gap: 4 }}>
              <h3>Maria's shop</h3>
              <span className="muted">{seed ? "Ready. Pick a role." : "Ready in a few seconds."}</span>
            </div>
            {!seed ? (
              <button className="btn primary lg" disabled={busy} onClick={start}>{busy ? "Setting up…" : "Open my demo shop"}</button>
            ) : (
              <span className="badge good" style={{ height: 28, padding: "0 10px" }}><CircleCheck />Ready</span>
            )}
          </div>
          {err && <div style={{ padding: "0 28px 16px" }}><ErrorBox message={err} /></div>}
          <div className="demo-grid">
            <DemoItem href={seed ? `/seller/${seed.seller_token}` : null} icon={<Store />} title="Maria's Control Room"
              text="Approve refunds. Send evidence." go="Open Maria's Control Room" />
            <DemoItem href={seed ? `/t/${seed.buyers["sam-jacket"]}` : null} newTab icon={<House />} title="Be Sam"
              text="“Delivered”, but not there." go="Tap Not here" />
            <DemoItem href={seed ? `/t/${seed.buyers["failed-attempt"]}` : null} newTab icon={<Truck />} title="Be Priya"
              text="The courier missed her." go="Choose a locker" />
            <DemoItem href={seed ? `/shop/${seed.summary.seller_id}` : null} newTab icon={<CreditCard />} title="Buy with PayPal"
              text="A real sandbox checkout." go="Go to checkout" />
          </div>
          <div className="demo-path">
            <span style={{ fontWeight: 600, color: "var(--text)" }}>Suggested path</span>
            <span>Be Sam</span><ChevronRight className="chev" />
            <span>Approve the refund</span><ChevronRight className="chev" />
            <span>Verify the receipt</span>
            {seed && (
              <button className="btn ghost" style={{ marginLeft: "auto" }} onClick={() => setSeed(null)}>
                <RotateCcw size={14} />Start over
              </button>
            )}
          </div>
        </div>
      </section>

      <section className="section" style={{ paddingTop: 0 }}>
        <div className="truth" aria-label="What is real and what is simulated">
          <div>
            <h3><BadgeCheck color="var(--good)" aria-hidden />Real</h3>
            <ul>{REAL.map((r) => <li key={r}>{r}</li>)}</ul>
          </div>
          <div>
            <h3><FlaskConical color="var(--text-3)" aria-hidden />Simulated</h3>
            <ul>
              {health.data
                ? Object.entries(health.data.simulated).map(([k, v]) => <li key={k} title={v}>{SIMULATED[k] ?? v}</li>)
                : <li>Lockers</li>}
            </ul>
          </div>
        </div>
      </section>

      <footer className="site-foot">
        <div className="inner">
          <span>Doorstep · Information, not legal advice. The seller decides.</span>
          <span title={health.data?.rules.join(", ")}>{health.data ? `${health.data.rules.length} rule files · sandbox keys only` : "Sandbox keys only"}</span>
        </div>
      </footer>
    </div>
  );
}

function DemoItem({ href, icon, title, text, go, newTab }: { href: string | null; icon: React.ReactNode; title: string; text: string; go: string; newTab?: boolean }) {
  const body = (
    <>
      <span className="di-icon" aria-hidden>{icon}</span>
      <span className="di-title">{title}</span>
      <span className="di-text">{text}</span>
      <span className="di-go">{go} <ArrowRight /></span>
    </>
  );
  if (!href) return <div className="demo-item locked" aria-disabled>{body}</div>;
  return <a className="demo-item" href={href} {...(newTab ? { target: "_blank", rel: "noreferrer" } : {})}>{body}</a>;
}

/** Static previews of the three screens that matter (decorative; the live demo is below). */
function Previews() {
  return (
    <div className="previews" aria-hidden>
      <div className="preview">
        <div className="pv-top">
          <span className="pv-who"><Smartphone />Sam, the buyer</span>
          <span className="pv-title">Taps Not here. Answer in minutes.</span>
        </div>
        <div className="pv-shot">
          <div className="mock-card">
            <div className="row" style={{ gap: 10 }}>
              <span className="status-icon accent" style={{ width: 32, height: 32 }}><House size={16} /></span>
              <span className="col" style={{ gap: 0 }}><b style={{ fontSize: 13.5 }}>Marked as delivered</b><span className="faint">Do you have it?</span></span>
            </div>
            <div className="mock-btn primary">Got it</div>
            <div className="mock-btn">Not here</div>
          </div>
          <div className="mock-card" style={{ borderColor: "var(--accent-line)", background: "var(--accent-soft)" }}>
            <span style={{ color: "var(--accent)", fontWeight: 600, display: "flex", gap: 6, alignItems: "center" }}><Scale size={14} />Your rights</span>
            <span className="muted">The seller carries the risk until you hold it.</span>
          </div>
        </div>
      </div>

      <div className="preview">
        <div className="pv-top">
          <span className="pv-who"><Store />Maria, the seller</span>
          <span className="pv-title">Sees the facts. Approves in one tap.</span>
        </div>
        <div className="pv-shot">
          <div className="mock-card">
            <div className="mock-line"><b>Black jacket, size M</b><b>£60</b></div>
            <div className="mock-line"><span>Likely cause</span><span>Lost by carrier</span></div>
            <div className="mock-line"><span>Courier scan</span><span className="badge bad">Contradicted</span></div>
            <div className="mock-line"><span>Rule</span><span className="mono">uk_possession_v1</span></div>
            <div className="mock-btn primary">Approve refund £60</div>
          </div>
        </div>
      </div>

      <div className="preview">
        <div className="pv-top">
          <span className="pv-who"><Truck />The courier</span>
          <span className="pv-title">Pays for its own mistake.</span>
        </div>
        <div className="pv-shot">
          <div className="mock-card">
            <div className="mock-line"><b>Recover from Royal Mail</b><span className="badge">Ready</span></div>
            <div className="mock-line"><span>Claim</span><b>£60.00</b></div>
            <div className="mock-line"><span>Deadline</span><span>27 days left</span></div>
            <div className="mock-line"><span><ScanLine size={13} style={{ verticalAlign: -2 }} /> Sentences checked</span><span>8 of 8</span></div>
            <div className="mock-btn">Copy claim</div>
          </div>
        </div>
      </div>
    </div>
  );
}
