import { useState } from "react";
import { Check, Copy, FileCheck, FileWarning } from "lucide-react";
import type { PolicyCheck } from "../api";
import { Loading } from "../components/ui";
import { PageHead } from "./LedgerGrid";
import "./pages.css";

/** PC-05: sentences in the seller's own policy that the rules contradict. The model reads; code
 *  checked every quote word for word against the policy before it reached this card. */
function PolicyCard({ check }: { check: PolicyCheck }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);
  const n = check.issues.length;
  const law = check.source.split(" — ")[0];
  async function copy(i: number, text: string) {
    try { await navigator.clipboard.writeText(text); setCopied(i); window.setTimeout(() => setCopied(null), 1600); } catch { /* clipboard blocked: the text stays selectable */ }
  }
  return (
    <section className="autopilot policy" aria-label="Policy check">
      <span className="ap-icon" aria-hidden><FileWarning /></span>
      <div className="col grow" style={{ gap: 6, minWidth: 0 }}>
        <div className="ap-rule">
          <span className="col" style={{ gap: 2, minWidth: 0 }}>
            <span className="ap-title">{n === 1 ? "One sentence" : `${n} sentences`} in your delivery policy {n === 1 ? "conflicts" : "conflict"} with {law}</span>
            {!open && <span className="ap-text">{check.issues[0].problem}</span>}
          </span>
          <button className="btn ghost" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide" : "Review"}</button>
        </div>
        {open && (
          <ol className="pc-issues">
            {check.issues.map((it, i) => (
              <li key={i}>
                <blockquote>“{it.quote}”</blockquote>
                <p>{it.problem}</p>
                <div className="pc-fix">
                  <span className="faint">Suggested wording</span>
                  <p>{it.suggestion}</p>
                  <button className="btn ghost" onClick={() => copy(i, it.suggestion)}>{copied === i ? <><Check aria-hidden />Copied</> : <><Copy aria-hidden />Copy</>}</button>
                </div>
                <span className="faint">Based on: {it.facts.map((f) => (f.source === "doorstep_settings" ? "your Doorstep settings" : `${f.source} (${check.rule_version})`)).filter((v, j, a) => a.indexOf(v) === j).join(", ")}</span>
              </li>
            ))}
          </ol>
        )}
        {open && <span className="faint">Read by AI; every quote was matched word for word to your policy before it was shown. Nothing changes until you edit your policy. {check.disclaimer}</span>}
      </div>
    </section>
  );
}

/** NV-03: the shop policy against the law. */
export default function PolicyPage({ check, failed }: { check: PolicyCheck | null; failed?: boolean }) {
  return (
    <>
      <PageHead title="Policy" line="Your shop policy against the law" />
      {!check && !failed ? <Loading height={160} /> : !check ? (
        <section className="autopilot on" aria-label="Policy check">
          <span className="ap-icon" aria-hidden><FileCheck /></span>
          <div className="col grow" style={{ gap: 2 }}><span className="ap-title">Not checked yet</span><span className="ap-text">Doorstep checks it when it can</span></div>
        </section>
      ) : check.issues.length > 0 ? <PolicyCard check={check} /> : (
        <section className="autopilot on" aria-label="Policy check">
          <span className="ap-icon" aria-hidden><FileCheck /></span>
          <div className="col grow" style={{ gap: 2 }}>
            <span className="ap-title">{check.status === "checked" ? "No conflicts" : check.status === "no_policy" ? "No policy yet" : "Not checked yet"}</span>
            <span className="ap-text">{check.status === "checked" ? `Checked against ${check.source.split(" — ")[0]}` : "Doorstep checks it when it can"}</span>
          </div>
        </section>
      )}
    </>
  );
}
