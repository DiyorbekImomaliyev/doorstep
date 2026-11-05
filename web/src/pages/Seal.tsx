import { useState, type ReactNode } from "react";
import { ArrowLeftRight, ChevronLeft, CircleCheck, LifeBuoy, PackageMinus, PackageX, ShieldCheck, Truck } from "lucide-react";
import { api, type ReportOut } from "../api";
import { Brand, ErrorBox, Loading, useAsync } from "../components/ui";
import { RightsCard } from "./Buyer";

/** SL-02..06: the page the QR (or NFC sticker) inside the parcel opens. No app, no login, no
 * typing, no location. "I have it" proves possession; "Something's wrong" opens a case. */
const PROBLEMS: { value: "damaged" | "wrong_item" | "missing_item"; label: string; icon: ReactNode }[] = [
  { value: "damaged", label: "It's damaged", icon: <PackageX size={18} /> },
  { value: "wrong_item", label: "Wrong item", icon: <ArrowLeftRight size={18} /> },
  { value: "missing_item", label: "Something's missing", icon: <PackageMinus size={18} /> },
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
      <Brand right={<span className="faint num">Order {v.order_ref}</span>} />
      <main className="narrow stack-lg seal-page">
        <div className="order-head">
          <span className="faint">From {v.seller_name || "the shop"}</span>
          <h1>{v.item || "Your parcel"}</h1>
        </div>

        {report ? (
          <>
            <Status icon={<LifeBuoy />} tone="accent" title="Thanks, the seller has it" text={report.message || "We'll sort it out with the seller."} />
            {report.rights && <RightsCard text={report.rights} />}
          </>
        ) : v.status === "confirmed" ? (
          <Status icon={<CircleCheck />} tone="good" title="All set" text="Thanks! The seller knows you have it." />
        ) : v.status === "case_open" ? (
          <Status icon={<LifeBuoy />} tone="accent" title="All set" text={v.scanned ? "Your scan is recorded. Your report is with the seller." : "Your report is with the seller."} />
        ) : v.status === "not_shipped" ? (
          <Status icon={<Truck />} tone="" title="Not shipped yet" text="This seal works once the parcel is on its way. Scan it again when it arrives." live={false} />
        ) : problem ? (
          <section className="col" aria-label="What's wrong?" style={{ gap: 10 }}>
            <h2>What's wrong?</h2>
            {PROBLEMS.map((p) => (
              <button key={p.value} className="choice" disabled={busy}
                onClick={async () => { const r = await run(() => api.sealReport(token, p.value)); if (r) setReport(r); }}>
                <span className="choice-icon" aria-hidden>{p.icon}</span>
                <span style={{ fontWeight: 550 }}>{p.label}</span>
              </button>
            ))}
            <button className="btn ghost" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={() => setProblem(false)}><ChevronLeft size={16} />Back</button>
          </section>
        ) : (
          <section className="col" style={{ gap: 10 }}>
            <p className="muted">Opened your parcel? One tap tells the seller it arrived.</p>
            <button className="btn primary lg block seal-main" disabled={busy}
              onClick={async () => { const r = await run(() => api.sealConfirm(token)); if (r) setData(r); }}>
              I have it
            </button>
            <button className="btn lg block" disabled={busy} onClick={() => setProblem(true)}>Something's wrong</button>
          </section>
        )}

        {err && <ErrorBox message={err} />}
        <p className="footnote"><ShieldCheck aria-hidden />No app, no login, no location. Not scanning is never held against you.</p>
      </main>
    </>
  );
}

function Status({ icon, tone, title, text, live = true }: { icon: ReactNode; tone: "" | "accent" | "good"; title: string; text: string; live?: boolean }) {
  return (
    <section className="card" role={live ? "status" : undefined}>
      <div className="status-top">
        <div className={`status-icon ${tone}`} aria-hidden>{icon}</div>
        <div className="col" style={{ gap: 2 }}>
          <h2>{title}</h2>
          <p className="muted">{text}</p>
        </div>
      </div>
    </section>
  );
}
