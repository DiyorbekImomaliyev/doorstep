/**
 * AS-07: how the Doorstep agent's tool calls read in Studio's chat panel. Each row says what the
 * call does in plain words; the scan and the answer open into cards drawn from the call's result
 * (the same findings and figures the agent read), with a button to the report each one concerns.
 */
import type { AgAiToolDetailParams } from "ag-studio";
import type { AgAiToolDisplay } from "ag-studio-react";
import { AlertTriangle, ArrowUpRight, CircleDot } from "lucide-react";
import type { Finding } from "./agent";

/** Raised by a card's button; the Insights board opens the report. */
export const OPEN_EVENT = "doorstep:open-report";
export type OpenDetail = { report: Finding["report"]; carrier?: string };
const open = (d: OpenDetail) => window.dispatchEvent(new CustomEvent<OpenDetail>(OPEN_EVENT, { detail: d }));

type Scan = { window: string; findings: Finding[] };
type Answered = { headline: string; detail: string[]; chart: boolean };

const dataOf = <T,>(p: AgAiToolDetailParams): T | undefined =>
  p.result && p.result.success ? (p.result as { data?: T }).data : undefined;

function ScanDetail(p: AgAiToolDetailParams) {
  const scan = dataOf<Scan>(p);
  if (!scan) return null;
  if (!scan.findings.length) return <p className="ag-find-none">Nothing stands out between {scan.window}.</p>;
  return (
    <div className="ag-find">
      <p className="ag-find-window">{scan.window}</p>
      <ul>
        {scan.findings.slice(0, 6).map((f, i) => (
          <li key={i} data-severity={f.severity}>
            {f.severity === "high" ? <AlertTriangle size={14} aria-hidden /> : <CircleDot size={14} aria-hidden />}
            <div>
              <strong>{f.title}</strong>
              <span>{f.detail}</span>
            </div>
            <button type="button" onClick={() => open({ report: f.report, carrier: f.carrier })}
              aria-label={`Open ${f.carrier ? `the ${f.carrier}` : `the ${f.report}`} report`}>
              {f.carrier ? f.carrier : f.report === "overview" ? "Overview" : f.report === "carriers" ? "Carriers" : "Cases"}
              <ArrowUpRight size={13} aria-hidden />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AnswerDetail(p: AgAiToolDetailParams) {
  const a = dataOf<Answered>(p);
  if (!a) return null;
  const html = (t: string) => t.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : part));
  return (
    <div className="ag-answer">
      <p>{html(a.headline)}</p>
      {a.detail.map((d, i) => <p key={i} className="ag-answer-more">{html(d)}</p>)}
      {a.chart && <p className="ag-answer-more">Chart added to the Answers page.</p>}
    </div>
  );
}

const count = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;
const REPORT = { overview: "Overview", carriers: "Carriers", cases: "Cases" } as const;

export const doorstepToolDisplay: Record<string, AgAiToolDisplay> = {
  scan_for_anomalies: {
    label: ({ status, result }) => {
      const scan = result?.success ? (result as { data?: Scan }).data : undefined;
      return {
        text: status === "done" ? "Scanned the last 4 weeks" : "Scanning the last 4 weeks",
        pill: scan ? (scan.findings.length ? count(scan.findings.length, "finding") : "Nothing unusual") : undefined,
      };
    },
    detail: ScanDetail,
  },
  answer_question: {
    label: ({ args, status }) => ({
      text: `${status === "done" ? "Answered" : "Answering"} “${String(args.question ?? "").slice(0, 60)}”`,
      pill: "Answers page",
    }),
    detail: AnswerDetail,
  },
  open_report: {
    label: ({ args, status }) => ({
      text: `${status === "done" ? "Opened" : "Opening"} the ${REPORT[(args.report as keyof typeof REPORT) ?? "overview"] ?? "report"} report`,
    }),
  },
  open_carrier_report: {
    label: ({ args, status }) => ({ text: `${status === "done" ? "Opened" : "Opening"} the ${String(args.carrier ?? "carrier")} report` }),
  },
};
