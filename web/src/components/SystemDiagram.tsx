import { useEffect, useState, type ReactNode } from "react";
import { Banknote, Bot, Database, FileCheck2, FileText, Lock, Scale, ScanLine, ShieldCheck, Smartphone, Webhook, Workflow } from "lucide-react";

/* Inside Doorstep: events in, a coded decision, one guarded path to money. The column that is
   "working" lights up in turn so the order of operations is easy to follow. */

type Node = { icon: ReactNode; title: string; text: string; key?: boolean };
type Column = { label: string; nodes: Node[] };

const COLUMNS: Column[] = [
  { label: "Events", nodes: [
    { icon: <Webhook />, title: "PayPal webhooks", text: "Payments, refunds, disputes" },
    { icon: <ScanLine />, title: "Courier scans", text: "Via Shippo" },
    { icon: <Smartphone />, title: "Buyer taps", text: "Got it · Not here · seal" },
  ] },
  { label: "Inbox", nodes: [
    { icon: <ShieldCheck />, title: "Verified", text: "Signed, stored once" },
    { icon: <Database />, title: "Job queue", text: "Retries never pay twice" },
  ] },
  { label: "Decide", nodes: [
    { icon: <Workflow />, title: "Failure-point engine", text: "Code, not AI. Doubts go to a person" },
    { icon: <Scale />, title: "Rules as files", text: "UK · EU · US, versioned" },
  ] },
  { label: "Money", nodes: [
    { icon: <Lock />, title: "Policy service", text: "The only path to money", key: true },
  ] },
  { label: "Outcome", nodes: [
    { icon: <Banknote />, title: "PayPal refund", text: "Or dispute evidence" },
    { icon: <FileText />, title: "Claim pack", text: "The seller submits it" },
    { icon: <FileCheck2 />, title: "Signed receipt", text: "Anyone can verify" },
  ] },
];

/* One report followed through the columns above, in step with the lit column. */
const TRACE = [
  "Sam taps “Not here” on his £60 order",
  "Stored once, then queued as a job",
  "Lost by carrier · uk_possession_v1 → the seller's risk",
  "£60 is over the £50 auto-refund limit → Maria approves",
  "PayPal refund sent · receipt signed · claim pack ready",
];

export default function SystemDiagram() {
  const [lit, setLit] = useState(0);
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const t = setInterval(() => setLit((l) => (l + 1) % COLUMNS.length), 1900);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="system">
      <div className="pipeline">
        {COLUMNS.map((c, i) => (
          <FlowCell key={c.label} last={i === COLUMNS.length - 1}>
            <div className={`stage-col ${lit === i ? "lit" : ""}`}>
              <span className="col-label">{c.label}</span>
              {c.nodes.map((n) => (
                <div key={n.title} className={`node ${n.key ? "key" : ""}`}>
                  <span className="ni" aria-hidden>{n.icon}</span>
                  <span className="col" style={{ gap: 2 }}>
                    <span className="nt">{n.title}</span>
                    <span className="nd">{n.text}</span>
                  </span>
                </div>
              ))}
            </div>
          </FlowCell>
        ))}
      </div>
      <div className="trace" aria-hidden>
        <span className="trace-k"><i />Live trace</span>
        <span className="trace-steps">{COLUMNS.map((c, i) => <b key={c.label} className={i === lit ? "on" : i < lit ? "done" : ""} />)}</span>
        <code key={lit}>{TRACE[lit]}</code>
      </div>
      <div className="ai-band">
        <span className="ni" aria-hidden><Bot /></span>
        <span className="nt" style={{ fontWeight: 600, fontSize: 13 }}>AI explains and drafts. It never moves money.</span>
        <span className="ai-chips">
          <span>Explanations</span><span>Claim drafts, fact-checked</span><span>Packing photos</span><span>Read-only PayPal agent</span>
        </span>
      </div>
    </div>
  );
}

function FlowCell({ children, last }: { children: ReactNode; last: boolean }) {
  return (
    <>
      {children}
      {!last && <div className="flow" aria-hidden />}
    </>
  );
}
