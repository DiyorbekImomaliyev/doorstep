import { useEffect, useState, type ReactNode } from "react";
import { Banknote, Bot, Database, FileCheck2, FileText, Lock, Scale, ScanLine, Send, ShieldCheck, Smartphone, Webhook, Workflow } from "lucide-react";

/* Inside Doorstep: events in, a coded decision, one guarded path to money. The column that is
   "working" lights up in turn so the order of operations is easy to follow. */

type Node = { icon: ReactNode; title: string; text: string; key?: boolean };
type Column = { label: string; nodes: Node[] };

const COLUMNS: Column[] = [
  { label: "Events", nodes: [
    { icon: <Webhook />, title: "PayPal webhooks", text: "Payments, refunds, disputes" },
    { icon: <ScanLine />, title: "Courier tracking", text: "Every scan, via Shippo" },
    { icon: <Smartphone />, title: "Buyer taps", text: "Got it, Not here, seal scan" },
  ] },
  { label: "Inbox", nodes: [
    { icon: <ShieldCheck />, title: "Verified first", text: "PayPal signatures checked; each event stored once" },
    { icon: <Database />, title: "Worker queue", text: "Retries never repeat a payment" },
  ] },
  { label: "Decide", nodes: [
    { icon: <Workflow />, title: "Failure-point engine", text: "Finds what went wrong, from several signals. Code, not AI." },
    { icon: <Scale />, title: "The law as rules", text: "Versioned files: UK, EU, US seller policy" },
  ] },
  { label: "Money", nodes: [
    { icon: <Lock />, title: "Policy service", text: "The only path to money: limits, daily caps, re-checks", key: true },
  ] },
  { label: "Outcome", nodes: [
    { icon: <Banknote />, title: "PayPal refund", text: "Or evidence to an open dispute" },
    { icon: <FileText />, title: "Courier claim pack", text: "Facts the seller submits" },
    { icon: <FileCheck2 />, title: "Signed receipt", text: "Anyone can verify it" },
  ] },
];

export default function SystemDiagram() {
  const [lit, setLit] = useState(0);
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const t = setInterval(() => setLit((l) => (l + 1) % COLUMNS.length), 1600);
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
      <div className="ai-band">
        <span className="ni" aria-hidden><Bot /></span>
        <span className="col" style={{ gap: 2 }}>
          <span className="nt" style={{ fontWeight: 600, fontSize: 13 }}>AI assists, it never moves money</span>
          <span className="nd" style={{ fontSize: 12.5, color: "var(--text-2)" }}>
            It explains decisions, writes the claim pack (every sentence is checked against the facts), checks packing photos,
            and runs a read-only agent on PayPal's Agent Toolkit. Buyer text is treated as data, never as instructions.
          </span>
        </span>
      </div>
      <div className="legend">
        <span><Send aria-hidden />Every PayPal write carries an idempotency key</span>
        <span><ShieldCheck aria-hidden />Contradictions go to a person, never to an automatic denial</span>
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
