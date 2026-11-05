import { useState } from "react";
import { api, type ReportOut } from "../api";
import { Brand, ErrorBox, Loading, useAsync } from "../components/ui";
import { RightsCard } from "./Buyer";

/** SL-02..06: the page the QR (or NFC sticker) inside the parcel opens. No app, no login, no
 * typing, no location. "I have it" proves possession; "Something's wrong" opens a case. */
const PROBLEMS: { value: "damaged" | "wrong_item" | "missing_item"; label: string; icon: string }[] = [
  { value: "damaged", label: "It's damaged", icon: "💥" },
  { value: "wrong_item", label: "Wrong item", icon: "🔄" },
  { value: "missing_item", label: "Something's missing", icon: "🧩" },
];

export default function Seal({ token }: { token: string }) {
  const { data: v, error, setData } = useAsync(() => api.seal(token), [token]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(false);
  const [report, setReport] = useState<ReportOut | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true); setErr(null);
    try { return await fn(); }
    catch (e) { setErr(String((e as Error).message)); return undefined; }
    finally { setBusy(false); }
  }

  if (error) return (<><Brand /><main className="narrow"><ErrorBox message="This seal isn't valid. Ask the shop for help." /></main></>);
  if (!v) return (<><Brand /><main className="narrow col"><Loading height={160} /><Loading height={120} /></main></>);

  return (
    <>
      <Brand right={<span className="faint">Order {v.order_ref}</span>} />
      <main className="narrow stack-lg seal-page">
        <div className="col" style={{ gap: 4 }}>
          <span className="faint">From {v.seller_name || "the shop"}</span>
          <h1 style={{ fontSize: 22 }}>{v.item || "Your parcel"}</h1>
        </div>

        {report ? (
          <>
            <section className="card col" role="status">
              <div className="hero-status">
                <div className="hero-icon" style={{ background: "var(--warn-soft)" }} aria-hidden>🛟</div>
                <div className="col" style={{ gap: 2 }}>
                  <h2>Thanks, the seller has it</h2>
                  <p className="muted">{report.message || "We'll sort it out with the seller."}</p>
                </div>
              </div>
            </section>
            {report.rights && <RightsCard text={report.rights} />}
          </>
        ) : v.status === "confirmed" ? (
          <Done text="Thanks! The seller knows you have it." />
        ) : v.status === "case_open" ? (
          <Done text={v.scanned ? "Your scan is recorded. Your report is with the seller." : "Your report is with the seller."} tone="var(--warn-soft)" icon="🛟" />
        ) : v.status === "not_shipped" ? (
          <section className="card col">
            <h2>Not shipped yet</h2>
            <p className="muted" style={{ margin: 0 }}>This seal works once the parcel is on its way. Scan it again when it arrives.</p>
          </section>
        ) : problem ? (
          <section className="col" aria-label="What's wrong?">
            <h2>What's wrong?</h2>
            {PROBLEMS.map((p) => (
              <button key={p.value} className="btn lg block seal-choice" disabled={busy}
                onClick={async () => { const r = await run(() => api.sealReport(token, p.value)); if (r) setReport(r); }}>
                <span aria-hidden>{p.icon}</span> {p.label}
              </button>
            ))}
            <button className="btn ghost block" disabled={busy} onClick={() => setProblem(false)}>Back</button>
          </section>
        ) : (
          <section className="col">
            <p className="muted" style={{ margin: 0 }}>Opened your parcel? One tap tells the seller it arrived.</p>
            <button className="btn primary lg block seal-main" disabled={busy}
              onClick={async () => { const r = await run(() => api.sealConfirm(token)); if (r) setData(r); }}>
              I have it
            </button>
            <button className="btn lg block" disabled={busy} onClick={() => setProblem(true)}>Something's wrong</button>
          </section>
        )}

        {err && <ErrorBox message={err} />}
        <p className="faint" style={{ textAlign: "center" }}>
          No app, no login, no location. Not scanning is never held against you.
        </p>
      </main>
    </>
  );
}

function Done({ text, tone = "var(--good-soft)", icon = "📦" }: { text: string; tone?: string; icon?: string }) {
  return (
    <section className="card col" role="status">
      <div className="hero-status">
        <div className="hero-icon" style={{ background: tone }} aria-hidden>{icon}</div>
        <div className="col" style={{ gap: 2 }}>
          <h2>All set</h2>
          <p className="muted">{text}</p>
        </div>
      </div>
    </section>
  );
}
