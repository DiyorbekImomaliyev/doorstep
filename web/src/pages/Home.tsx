import { useState } from "react";
import {
  ArrowRight, BadgeCheck, ChevronRight, CircleCheck, CreditCard, FlaskConical, House, RotateCcw,
  Scale, ScanLine, Smartphone, Store, Truck,
} from "lucide-react";
import { api, type SeedOut } from "../api";
import { Brand, ErrorBox, useAsync } from "../components/ui";
import Journey3D from "../components/Journey3D";
import SystemDiagram from "../components/SystemDiagram";
import WorldProblem from "../components/WorldProblem";

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
    <a href="#problem">Problem</a>
    <a href="#how">How it works</a>
    <a href="#demo">Demo</a>
    <a href="#inside">Architecture</a>
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
        <h1>No buyer loses money.<br /><em>No honest seller</em> pays for a lost parcel.</h1>
        <p className="lede">Doorstep refunds the buyer in minutes, proves who had the parcel, and makes the courier pay.</p>
        <div className="ctas">
          <a className="btn primary lg" href="#demo">Try the live demo <ArrowRight size={17} /></a>
          <a className="btn lg" href="#how">How it works</a>
        </div>
      </header>

      <section className="section" id="problem" style={{ paddingTop: 8 }} aria-label="The problem">
        <WorldProblem />
      </section>

      <section className="section" id="how">
        <div className="section-head">
          <span className="kicker">How it works</span>
          <h2>One parcel, start to finish</h2>
          <p>Sam buys a £60 jacket. The courier says &ldquo;Delivered&rdquo;. It wasn't.</p>
        </div>
        <Journey3D />
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
              <span className="muted">{seed ? "Ready. Pick a role." : "A seller, her buyers and their parcels. Ready in seconds."}</span>
            </div>
            {!seed ? (
              <button className="btn primary lg" disabled={busy} onClick={start}>{busy ? "Setting up…" : "Open my demo shop"}</button>
            ) : (
              <div className="row" style={{ gap: 8 }}>
                <span className="badge good" style={{ height: 28, padding: "0 10px" }}><CircleCheck />Ready</span>
                <button className="btn ghost" onClick={() => setSeed(null)}><RotateCcw size={14} />Start over</button>
              </div>
            )}
          </div>
          {err && <div style={{ padding: "16px 28px 0" }}><ErrorBox message={err} /></div>}
          <Previews seed={seed} />
          <div className="demo-path">
            <span style={{ fontWeight: 600, color: "var(--text)" }}>Suggested path</span>
            <span>Be Sam</span><ChevronRight className="chev" />
            <span>Approve the refund</span><ChevronRight className="chev" />
            <span>Verify the receipt</span>
            <span className="more">
              <Go href={seed ? `/t/${seed.buyers["failed-attempt"]}` : null} icon={<Truck />}>Be Priya, missed by the courier</Go>
              <Go href={seed ? `/shop/${seed.summary.seller_id}` : null} icon={<CreditCard />}>Buy with PayPal</Go>
            </span>
          </div>
        </div>
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

      <section className="section" id="inside">
        <div className="section-head">
          <span className="kicker">Architecture</span>
          <h2>AI suggests. Code decides money.</h2>
          <p>One guarded service is the only path to a PayPal refund.</p>
        </div>
        <SystemDiagram />
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

/** A demo link that is inactive until the demo shop exists. */
function Go({ href, icon, children, newTab = true }: { href: string | null; icon?: React.ReactNode; children: React.ReactNode; newTab?: boolean }) {
  const body = <>{icon}{children}<ArrowRight className="go-arrow" aria-hidden /></>;
  if (!href) return <span className="go locked" aria-disabled>{body}</span>;
  return <a className="go" href={href} {...(newTab ? { target: "_blank", rel: "noreferrer" } : {})}>{body}</a>;
}

/** The three sides of one case, each with the live demo link that opens it. */
function Previews({ seed }: { seed: SeedOut | null }) {
  const seller = seed ? `/seller/${seed.seller_token}` : null;
  return (
    <div className="previews">
      <div className="preview">
        <div className="pv-top">
          <span className="pv-who"><Smartphone aria-hidden />Sam, the buyer</span>
          <span className="pv-title">Taps Not here. Answer in minutes.</span>
        </div>
        <div className="pv-shot" aria-hidden>
          <div className="mock-card">
            <div className="row" style={{ gap: 10 }}>
              <span className="status-icon accent" style={{ width: 32, height: 32 }}><House size={16} /></span>
              <span className="col" style={{ gap: 0 }}><b style={{ fontSize: 13.5 }}>Marked as delivered</b><span className="faint">Do you have it?</span></span>
            </div>
            <div className="row" style={{ gap: 6 }}><div className="mock-btn primary grow">Got it</div><div className="mock-btn grow">Not here</div></div>
            <span className="mock-note"><Scale size={13} />The seller carries the risk until you hold it.</span>
          </div>
        </div>
        <div className="pv-go"><Go href={seed ? `/t/${seed.buyers["sam-jacket"]}` : null}>Be Sam</Go></div>
      </div>

      <div className="preview">
        <div className="pv-top">
          <span className="pv-who"><Store aria-hidden />Maria, the seller</span>
          <span className="pv-title">Sees the facts. Approves in one tap.</span>
        </div>
        <div className="pv-shot" aria-hidden>
          <div className="mock-card">
            <div className="mock-line"><b>Black jacket, size M</b><b>£60</b></div>
            <div className="mock-line"><span>Courier scan</span><span className="badge bad">Contradicted</span></div>
            <div className="mock-line"><span>Rule</span><span className="mono">uk_possession_v1</span></div>
            <div className="mock-btn primary">Approve refund £60</div>
          </div>
        </div>
        <div className="pv-go"><Go href={seller} newTab={false}>Open Maria's Control Room</Go></div>
      </div>

      <div className="preview">
        <div className="pv-top">
          <span className="pv-who"><Truck aria-hidden />The courier</span>
          <span className="pv-title">Pays for its own mistake.</span>
        </div>
        <div className="pv-shot" aria-hidden>
          <div className="mock-card">
            <div className="mock-line"><b>Recover from Royal Mail</b><span className="badge">Ready</span></div>
            <div className="mock-line"><span>Claim</span><b>£60.00</b></div>
            <div className="mock-line"><span><ScanLine size={13} style={{ verticalAlign: -2 }} /> Facts checked</span><span>8 of 8</span></div>
            <div className="mock-btn">Copy claim</div>
          </div>
        </div>
        <div className="pv-go"><Go href={seller} newTab={false}>See the claim pack</Go></div>
      </div>
    </div>
  );
}
