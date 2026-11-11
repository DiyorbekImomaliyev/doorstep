import { useEffect, useMemo, useState } from "react";
import { Check, CircleCheck, Copy, ExternalLink, FileCheck, FileText, RefreshCw, Scale, ScrollText, ShieldCheck } from "lucide-react";
import { api, type PolicyCheck, type PolicyFact } from "../api";
import { ErrorBox, Loading } from "../components/ui";
import { PageHead, StatusPill } from "./LedgerGrid";
import { automateApi, type PolicyDesk, type RuleFile } from "./automate/automateApi";
import { Empty, Segmented, useToast } from "./automate/parts";
import { clauses, locate, pieces } from "./automate/policyText";
import "./pages.css";

type Issue = PolicyCheck["issues"][number];
const SETTINGS = "doorstep_settings";
const hhmm = (d: Date) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

/** High: it contradicts who carries the loss (the liability rule for the shop's region).
 *  Medium: it contradicts only how the shop's refunds are set up in Doorstep. */
const severity = (it: Issue) => (it.facts.some((f) => f.source !== SETTINGS) ? "High" : "Medium");

function useCopy() {
  const [done, setDone] = useState<string | null>(null);
  const copy = async (key: string, text: string) => {
    try { await navigator.clipboard.writeText(text); setDone(key); window.setTimeout(() => setDone(null), 1600); } catch { /* clipboard blocked: the text stays selectable */ }
  };
  return { done, copy };
}

/** The rule file a fact came from: the check's own liability rule, or the money limits file. */
function fileOf(source: string, check: PolicyCheck, desk: PolicyDesk | null): RuleFile | null {
  const files = desk?.rules ?? [];
  if (source === SETTINGS) return files.find((f) => f.id.startsWith("money_limits")) ?? null;
  const f = files.find((x) => x.id === source);
  if (f && check.rule_id === source && check.rule_version) return { ...f, version: check.rule_version };
  return f ?? null;
}

function RuleChip({ file, own }: { file: RuleFile; own?: boolean }) {
  const body = <><Scale aria-hidden /><span className="mono">{file.id}</span><span>· {file.version}</span>{file.url && <ExternalLink aria-hidden />}</>;
  const title = `${own ? "Your own limits, else " : ""}${file.source}`;
  return file.url
    ? <a className="ax-chip" href={file.url} target="_blank" rel="noreferrer" title={title}>{body}</a>
    : <span className="ax-chip" title={title}>{body}</span>;
}

/** PL-10/11: one conflicting clause, the rule it breaks, and the wording the check suggested. */
function Finding({ it, n, active, check, desk, onFocus }: { it: Issue; n: number; active: boolean; check: PolicyCheck; desk: PolicyDesk | null; onFocus: () => void }) {
  const { done, copy } = useCopy();
  const sev = severity(it);
  const sources = [...new Set(it.facts.map((f) => f.source))];
  const law = sources.filter((s) => s !== SETTINGS).map((s) => fileOf(s, check, desk)?.title ?? s)[0];
  return (
    <article id={`ax-f-${n}`} className={`ax-finding ${active ? "is-active" : ""}`} aria-label={`Finding ${n}`} onClick={onFocus}>
      <div className="ax-finding-head">
        <span className="ax-finding-n" aria-hidden>{n}</span>
        <span className="ax-finding-title">{law ? `Conflicts with ${law}` : "Conflicts with your settings"}</span>
        <StatusPill tone={sev === "High" ? "bad" : "warn"}>{sev}</StatusPill>
      </div>
      <blockquote className="ax-quote">“{it.quote}”</blockquote>
      <p>{it.problem}</p>
      <div className="ax-fix">
        <div className="ax-fix-head">
          <span>Suggested</span>
          <button type="button" className="btn ghost sm" onClick={(e) => { e.stopPropagation(); void copy("s", it.suggestion); }} aria-label={`Copy suggested wording ${n}`}>
            {done === "s" ? <><Check aria-hidden />Copied</> : <><Copy aria-hidden />Copy</>}
          </button>
        </div>
        <p>{it.suggestion}</p>
      </div>
      <div className="ax-chips">
        {sources.map((s) => {
          const f = fileOf(s, check, desk);
          return f ? <RuleChip key={s} file={f} own={s === SETTINGS && desk?.own_limits} /> : <span key={s} className="ax-chip">{s}</span>;
        })}
      </div>
    </article>
  );
}

/** What the check compared the policy with: numbered facts, by rule file. */
function Facts({ facts, check, desk }: { facts: PolicyFact[]; check: PolicyCheck; desk: PolicyDesk | null }) {
  if (!facts.length) return null;
  return (
    <details className="ax-facts">
      <summary><ShieldCheck size={15} aria-hidden />Checked against<span className="count-chip num">{facts.length}</span></summary>
      <ol>
        {facts.map((f) => {
          const file = fileOf(f.source, check, desk);
          return (
            <li key={f.id}>
              <span className="mono">{f.id}</span>
              <span>{f.text} <span className="faint">({file ? `${file.id} · ${file.version}` : f.source})</span></span>
            </li>
          );
        })}
      </ol>
    </details>
  );
}

/** NV-03, PL-10..12: the shop's written policy, checked against the law clause by clause. */
export default function PolicyPage({ token, check, failed }: { check: PolicyCheck | null; failed?: boolean; token: string; onChanged: () => void }) {
  const [res, setRes] = useState<PolicyCheck | null>(check);
  const [at, setAt] = useState<Date | null>(check ? new Date() : null);
  const [desk, setDesk] = useState<PolicyDesk | null>(null);
  const [deskErr, setDeskErr] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const [view, setView] = useState<"original" | "revised">("original");
  const toast = useToast();
  const { done, copy } = useCopy();

  useEffect(() => { if (check) { setRes(check); setAt((a) => a ?? new Date()); } }, [check]);
  useEffect(() => { automateApi.policy(token).then(setDesk).catch(() => setDeskErr(true)); }, [token]);

  /** PL-12: runs the existing check again. The same policy and rules reuse the reading already made. */
  async function recheck() {
    setBusy(true); setErr(null);
    try {
      const r = await api.policyCheck(token);
      setRes(r); setAt(new Date()); setActive(null);
      toast.show(r.status === "checked" ? (r.issues.length ? `Checked · ${plural(r.issues.length, "conflict")}` : "Checked · no conflicts")
        : r.status === "no_policy" ? "No policy to check" : "Not checked · try again soon");
    } catch {
      setErr("The check did not run. Try again.");
    } finally { setBusy(false); }
  }

  const text = desk?.policy_text ?? "";
  const issues = res?.status === "checked" ? res.issues : [];
  const spans = useMemo(() => locate(text, issues.map((i) => i.quote)), [text, issues]);
  const parts = useMemo(() => pieces(text, spans), [text, spans]);
  const revised = parts.map((p) => (p.issue != null ? issues[p.issue].suggestion : p.text)).join("");
  const n = issues.length;
  const law = res?.source ? res.source.split(" — ")[0] : desk?.rules[0]?.title ?? "";
  const versions = desk ? desk.rules.map((f) => `${f.id} ${res?.rule_id === f.id && res.rule_version ? res.rule_version : f.version}`).join(" · ") : "";

  const focus = (i: number, target: "mark" | "finding") => {
    setActive(i);
    document.getElementById(target === "mark" ? `ax-m-${i + 1}` : `ax-f-${i + 1}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  const head = (
    <div className="row" style={{ gap: 10 }}>
      {at && res?.status === "checked" && <span className="ax-checked num" title={versions ? `Rules: ${versions}` : undefined}><FileCheck size={14} aria-hidden />Checked {hhmm(at)}</span>}
      <button type="button" className="btn" onClick={recheck} disabled={busy}>
        <RefreshCw size={15} aria-hidden className={busy ? "ax-spin" : undefined} />Check again
      </button>
    </div>
  );

  const loading = (!res && !failed) || (!desk && !deskErr);
  return (
    <div className="ax-page stack-lg">
      <PageHead title="Policy" line="Your shop policy against the law" right={head} />
      {err && <ErrorBox message={err} />}
      {loading ? (
        <>
          <section className="kpis ax-kpis3" aria-hidden>
            {[0, 1, 2].map((i) => <div key={i} className="kpi kpi-skel"><span className="skel w40" /><span className="skel w20 tall" /><span className="skel w60" /></div>)}
          </section>
          <div className="ax-policy"><Loading height={260} /><Loading height={260} /></div>
        </>
      ) : !res ? (
        <section aria-label="Policy check" className="table-card">
          <ErrorBox message="The policy check did not load. Try again." />
        </section>
      ) : (
        <>
          <section className="kpis ax-kpis3" aria-label="Summary">
            <div className={`kpi ${n ? "attention" : ""}`}>
              <span className="label"><FileText aria-hidden />Conflicts</span>
              <span className="value num">{res.status === "checked" ? n : "—"}{text && res.status === "checked" && <span className="ax-of"> of {plural(clauses(text), "clause")}</span>}</span>
              <span className="sub">{res.status === "checked" ? (n ? `${issues.filter((i) => severity(i) === "High").length} high` : "none found") : res.status === "no_policy" ? "no policy" : "not checked"}</span>
            </div>
            <div className="kpi ax-law">
              <span className="label"><Scale aria-hidden />Law</span>
              <span className="value ax-text">{law || "—"}</span>
              <span className="sub num">{res.rule_id ? `${res.rule_id} · ${res.rule_version}` : "no rule for this region"}</span>
            </div>
            <div className="kpi" title={res.provider ? `Read by ${res.provider}; every quote matched to your policy in code` : undefined}>
              <span className="label"><FileCheck aria-hidden />Checked</span>
              <span className="value num" style={{ fontSize: 22 }}>{at && res.status === "checked" ? hhmm(at) : "—"}</span>
              <span className="sub">{res.status === "checked" ? "AI read · code verified" : res.status === "no_policy" ? "nothing to read" : "waiting for AI"}</span>
            </div>
          </section>

          <section aria-label="Policy check" className="stack-lg" style={{ minWidth: 0 }}>
            {res.status === "no_policy" ? (
              <div className="table-card"><Empty icon={<ScrollText />} title="No written policy" /></div>
            ) : (
              <>
                {res.status === "unchecked" && (
                  <div className="notice"><FileText aria-hidden /><span><strong>Not checked yet</strong></span></div>
                )}
                {res.status === "checked" && n === 0 && (
                  <div className="ax-good"><CircleCheck aria-hidden /><span><strong>No conflicts</strong> <span className="muted">with {law}</span></span></div>
                )}
                <div className={n ? "ax-policy" : ""}>
                  <div className="ax-doc">
                    <div className="head">
                      <h2 style={{ fontSize: 15 }}><ScrollText aria-hidden className="ax-h-icon" />Your policy</h2>
                      <div className="row" style={{ gap: 8 }}>
                        {n > 0 && <Segmented label="Policy view" value={view} onChange={setView} items={[{ key: "original", label: "Original" }, { key: "revised", label: "Revised" }]} />}
                        <button type="button" className="btn ghost sm" onClick={() => copy("doc", view === "revised" ? revised : text)}
                          aria-label={view === "revised" ? "Copy revised policy" : "Copy policy"}>
                          {done === "doc" ? <><Check aria-hidden />Copied</> : <><Copy aria-hidden />Copy</>}
                        </button>
                      </div>
                    </div>
                    <div className="ax-doc-body" aria-label={view === "revised" ? "Revised policy" : "Policy text"}>
                      {parts.map((p, i) => p.issue == null ? <span key={i}>{p.text}</span>
                        : view === "revised" ? <ins key={i} className="ax-ins">{issues[p.issue].suggestion}</ins>
                        : (
                          <mark key={i} id={`ax-m-${p.issue + 1}`} className={`ax-mark ${active === p.issue ? "is-active" : ""}`} tabIndex={0} role="button"
                            aria-label={`Conflict ${p.issue + 1}: ${p.text}`}
                            onClick={() => focus(p.issue!, "finding")}
                            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); focus(p.issue!, "finding"); } }}>
                            {p.text}<sup aria-hidden>{p.issue + 1}</sup>
                          </mark>
                        ))}
                    </div>
                  </div>
                  {n > 0 && (
                    <div className="ax-findings" aria-label="Findings">
                      <div className="ax-findings-head"><h2>Findings</h2><span className="count-chip num">{n}</span></div>
                      {issues.map((it, i) => <Finding key={i} it={it} n={i + 1} active={active === i} check={res} desk={desk} onFocus={() => focus(i, "mark")} />)}
                    </div>
                  )}
                </div>
              </>
            )}
            <Facts facts={res.facts} check={res} desk={desk} />
            <p className="faint" style={{ textAlign: "center" }}>{res.disclaimer} Nothing changes until you edit your policy.</p>
          </section>
        </>
      )}
      {toast.msg && <div className="toast" role="status"><CircleCheck size={16} aria-hidden />{toast.msg}</div>}
    </div>
  );
}
