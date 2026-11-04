import { useState } from "react";
import { api, type SeedOut } from "../api";
import { Brand, ErrorBox, useAsync } from "../components/ui";

const STAGES = [
  { n: "1", title: "Prevent", text: "Catches a missing flat number before payment and suggests a locker or signature when the order is risky." },
  { n: "2", title: "Prove", text: "Adds tracking to the PayPal payment and records every handover: scan, locker code, signature, buyer's tap." },
  { n: "3", title: "Resolve", text: "Finds the likely failure point, applies the local law (UK CRA s.29) and settles through PayPal. An agent on PayPal's Agent Toolkit gathers the evidence and stops at your limit." },
];

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
    <>
      <Brand right={<a href="https://github.com" className="faint" style={{ display: "none" }}>GitHub</a>} />
      <main className="page stack-lg" style={{ maxWidth: 980 }}>
        <section className="col" style={{ gap: 10, paddingTop: 24 }}>
          <span className="pill tone-info plain" style={{ alignSelf: "flex-start" }}>PayPal sandbox · Shippo test mode</span>
          <h1 style={{ fontSize: 36, lineHeight: 1.15, maxWidth: 760 }}>No buyer loses money. No honest seller pays for a failure they couldn't see.</h1>
          <p className="muted" style={{ fontSize: 16, maxWidth: 700 }}>
            Doorstep is a delivery agent for small PayPal sellers. In the UK 15 million people had a problem with their last parcel;
            the law says the seller carries the risk until the buyer holds it. Doorstep proves who had the package and settles fairly.
          </p>
        </section>

        <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
          {STAGES.map((s) => (
            <div key={s.n} className="card col" style={{ gap: 6 }}>
              <span className="faint">Stage {s.n}</span>
              <h2>{s.title}</h2>
              <p className="muted">{s.text}</p>
            </div>
          ))}
        </section>

        <section className="card col">
          <div className="spread wrap">
            <div className="col" style={{ gap: 2 }}>
              <h2>Try it in 30 seconds</h2>
              <p className="muted">Get your own copy of Maria's shop with {seed ? seed.summary.orders.length : 10} orders. Nothing you do touches anyone else's demo.</p>
            </div>
            {!seed && <button className="btn primary lg" disabled={busy} onClick={start}>{busy ? "Setting up…" : "Open my demo shop"}</button>}
          </div>
          {err && <ErrorBox message={err} />}
          {seed && (
            <div className="col">
              <a className="btn primary lg block" href={`/seller/${seed.seller_token}`}>1 · Open Maria's Control Room</a>
              <a className="btn lg block" href={`/t/${seed.buyers["sam-jacket"]}`} target="_blank" rel="noreferrer">2 · Be Sam: the jacket says "Delivered" but it isn't there</a>
              <a className="btn lg block" href={`/t/${seed.buyers["failed-attempt"]}`} target="_blank" rel="noreferrer">3 · Be Priya: the courier couldn't deliver</a>
              <a className="btn lg block" href={`/shop/${seed.summary.seller_id}`} target="_blank" rel="noreferrer">4 · Buy something with PayPal (sandbox)</a>
              <p className="faint">Tip: tap "Not here" as Sam, then approve the refund in the Control Room and open the signed receipt check.</p>
            </div>
          )}
        </section>

        {health.data && (
          <section className="col">
            <h3>What's real and what's simulated</h3>
            <p className="muted">Real: PayPal sandbox orders, captures, tracking, refunds and webhooks; Shippo test tracking; the rule engine; signed receipts.</p>
            <ul className="muted" style={{ margin: 0, paddingLeft: 18 }}>
              {Object.entries(health.data.simulated).map(([k, v]) => <li key={k}>{v}</li>)}
            </ul>
            <p className="faint">Rules loaded: {health.data.rules.join(", ")} · explanations: {health.data.model}. Information, not legal advice.</p>
          </section>
        )}
      </main>
    </>
  );
}
