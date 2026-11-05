import { useState } from "react";
import {
  ArrowRight, BadgeCheck, ChevronRight, CircleCheck, CreditCard, FlaskConical, House, RotateCcw,
  Scale, ScanLine, Smartphone, Store, Truck,
} from "lucide-react";
import { api, type SeedOut } from "../api";
import { Brand, ErrorBox, useAsync } from "../components/ui";
import Journey3D from "../components/Journey3D";
import SystemDiagram from "../components/SystemDiagram";

const NAV = (
  <nav aria-label="Sections">
    <a href="#how">How it works</a>
    <a href="#product">What people see</a>
    <a href="#inside">Inside</a>
    <a href="#demo">Live demo</a>
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
        <span className="eyebrow"><b>For PayPal sellers</b> UK and US · PayPal sandbox</span>
        <h1>No buyer loses money. <em>No honest seller</em> pays for a lost parcel.</h1>
        <p className="lede">
          When a parcel says &ldquo;Delivered&rdquo; but never arrived, Doorstep refunds the buyer through PayPal in minutes,
          proves who really had it, and claims the loss back from the courier.
        </p>
        <div className="ctas">
          <a className="btn primary lg" href="#demo">Try the live demo <ArrowRight size={17} /></a>
          <a className="btn lg" href="#how">See how it works</a>
        </div>
      </header>

      <section className="section" id="how" style={{ paddingTop: 24 }}>
        <div className="section-head">
          <span className="kicker">How it works</span>
          <h2>One parcel, start to finish</h2>
          <p>Sam buys a £60 jacket from Maria's shop. The courier says it was delivered. It wasn't.</p>
        </div>
        <Journey3D />
      </section>

      <section className="section" id="product">
        <div className="section-head">
          <span className="kicker">What people see</span>
          <h2>One tap for the buyer. One tap for the seller.</h2>
          <p>Everything else arrives by itself: payments, tracking and scans come in through PayPal and the courier.</p>
        </div>
        <Previews />
      </section>

      <section className="section" id="inside">
        <div className="section-head">
          <span className="kicker">Inside Doorstep</span>
          <h2>AI suggests. Code and people decide money.</h2>
          <p>Events are verified and stored once, coded rules apply the law, and one guarded service is the only way to a PayPal refund.</p>
        </div>
        <SystemDiagram />
      </section>

      <section className="section" id="demo">
        <div className="section-head">
          <span className="kicker">Live demo</span>
          <h2>Try it in under a minute</h2>
          <p>You get a private copy of Maria's shop with 10 orders in the PayPal sandbox. Nothing you do affects anyone else.</p>
        </div>
        <div className="demo">
          <div className="demo-top">
            <div className="col" style={{ gap: 4 }}>
              <h3>Maria's shop</h3>
              <span className="muted">{seed ? "Your copy is ready. Pick a role below." : "Seller, buyers and orders, ready to explore."}</span>
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
              text="Every order and case. Approve refunds and send evidence to PayPal." go="Open Maria's Control Room" />
            <DemoItem href={seed ? `/t/${seed.buyers["sam-jacket"]}` : null} newTab icon={<House />} title="Be Sam"
              text="The jacket says “Delivered”, but it isn't there." go="Tap Not here" />
            <DemoItem href={seed ? `/t/${seed.buyers["failed-attempt"]}` : null} newTab icon={<Truck />} title="Be Priya"
              text="The courier couldn't deliver. Choose what happens next." go="Choose a locker" />
            <DemoItem href={seed ? `/shop/${seed.summary.seller_id}` : null} newTab icon={<CreditCard />} title="Buy with PayPal"
              text="Check out in the PayPal sandbox. The address is checked first." go="Go to checkout" />
          </div>
          <div className="demo-path">
            <span style={{ fontWeight: 600, color: "var(--text)" }}>Suggested path</span>
            <span>Be Sam and tap Not here</span><ChevronRight className="chev" />
            <span>Approve the refund in the Control Room</span><ChevronRight className="chev" />
            <span>Check the signed receipt</span>
            {seed && (
              <button className="btn ghost" style={{ marginLeft: "auto" }} onClick={() => setSeed(null)}>
                <RotateCcw size={14} />Start over
              </button>
            )}
          </div>
        </div>
      </section>

      <section className="section" style={{ paddingTop: 8 }}>
        <div className="truth">
          <div className="card">
            <h3><BadgeCheck color="var(--good)" />Real</h3>
            <ul>
              <li>PayPal sandbox orders, captures, tracking, refunds, disputes and webhooks</li>
              <li>Courier tracking from Shippo test mode</li>
              <li>The rules engine, the policy service and the signed receipts</li>
            </ul>
          </div>
          <div className="card">
            <h3><FlaskConical color="var(--text-3)" />Simulated in this demo</h3>
            <ul>
              {health.data
                ? Object.entries(health.data.simulated).map(([k, v]) => <li key={k}>{v}</li>)
                : <li>Lockers and redelivery bookings</li>}
            </ul>
          </div>
        </div>
      </section>

      <footer className="site-foot">
        <div className="inner">
          <span>Doorstep · Information, not legal advice. The seller decides.</span>
          <span>{health.data ? `Rules: ${health.data.rules.join(", ")}` : "Sandbox and test keys only"}</span>
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
          <span className="pv-title">An answer in minutes</span>
          <span className="pv-text">No app, no login. One tap says it arrived, or that it didn't.</span>
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
            <span className="muted">The seller is responsible until you hold the parcel. You don't need to call the courier.</span>
          </div>
        </div>
      </div>

      <div className="preview">
        <div className="pv-top">
          <span className="pv-who"><Store />Maria, the seller</span>
          <span className="pv-title">The facts, the rule, one button</span>
          <span className="pv-text">Who carries the loss, the rule behind it, and what to do next.</span>
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
          <span className="pv-title">Pays for its own mistake</span>
          <span className="pv-text">A claim pack built from the courier's own tracking data. The seller submits it.</span>
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
