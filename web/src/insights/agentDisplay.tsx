/**
 * AS-07: how the Doorstep agent's tool calls read in Studio's chat panel. Each row says what the
 * call does in plain words; the scan and the answer open into cards drawn from the call's result
 * (the same findings and figures the agent read), with a button to the report each one concerns.
 */
import { createContext, useContext, useMemo } from "react";
import type { AgAiToolDetailParams } from "ag-studio";
import type { AgAiToolDisplay } from "ag-studio-react";
import { AlertTriangle, ArrowUpRight, BarChart3, CircleDot, Trash2 } from "lucide-react";
import { Chips, ReadBy } from "../components/ai";
import type { Answered, Finding } from "./agent";
import { answer } from "./ask";
import type { Prepared } from "./data";
import type { Explained } from "./explain";

/** Raised by a card's button; the Insights board opens the report. */
export const OPEN_EVENT = "doorstep:open-report";
export type OpenDetail = { report: Finding["report"]; carrier?: string };
const open = (d: OpenDetail) => window.dispatchEvent(new CustomEvent<OpenDetail>(OPEN_EVENT, { detail: d }));

type Scan = { window: string; findings: Finding[] };

/** What an answer card can do on the board (the console provides it). */
export type AnswerActions = {
  show: (widgetId: string) => void; remove: (widgetId: string) => void; removed: (widgetId: string) => boolean;
  /** The newest rows, and a number that moves when they change (live updates). */
  latest: () => Prepared; version: number;
};
export const AnswerActionsContext = createContext<AnswerActions | null>(null);

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

/**
 * The answer, as a card: the sentence first (figures bold), how the question was read, and the chart
 * it added to Answers. A question that can't be answered shows the reader's note, with no card id.
 */
function AnswerDetail(p: AgAiToolDetailParams) {
  const said = dataOf<Answered>(p);
  const act = useContext(AnswerActionsContext);
  // Live, like the chart beside it: the same plan, recomputed from the newest rows on each update.
  const a = useMemo(() => {
    if (!said?.plan || !act) return said;
    const now = answer(said.plan, act.latest());
    return now.empty ? said : { ...said, headline: now.headline, detail: now.detail, reading: now.reading };
  }, [said, act]);
  if (!a) return null;
  const html = (t: string) => t.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : part));
  if (!a.answerable) return <div className="ask-a ask-note"><p>{a.headline}</p></div>;
  const removed = !!a.widgetId && !!act?.removed(a.widgetId);
  return (
    <div className="ask-a" data-testid="ask-answer">
      <p className="ask-headline">{html(a.headline)}</p>
      {a.detail.map((d, i) => <p key={i} className="ask-detail">{html(d)}</p>)}
      {a.reading.length > 0 && <Chips label="How I read your question" items={a.reading} />}
      {a.widgetId && act && !removed && (
        <div className="ask-actions">
          <button type="button" className="btn" onClick={() => act.show(a.widgetId!)}><BarChart3 size={14} aria-hidden />View chart</button>
          <button type="button" className="btn ghost" onClick={() => act.remove(a.widgetId!)}><Trash2 size={14} aria-hidden />Remove</button>
        </div>
      )}
      {removed && <p className="ask-muted">Chart removed from Answers.</p>}
      <p className="ask-source"><ReadBy mode={a.mode} provider={a.provider} /> · figures from your orders</p>
    </div>
  );
}

type Built = { name: string; carrier?: string | null; method?: string | null; parts: string[] };

/** AI-02: what a topic page holds, said plainly: its name, its filter, and each widget placed. */
function BuiltDetail(p: AgAiToolDetailParams) {
  const b = dataOf<Built>(p);
  if (!b) return null;
  const only = b.carrier ?? b.method;
  return (
    <div className="ask-a ask-built" data-testid="ask-built">
      <p className="ask-headline">Built <strong>{b.name}</strong>{only ? <>, filtered to {only}</> : null}.</p>
      <Chips label="Widgets on the page" items={b.parts} />
    </div>
  );
}

/** S6: a widget's settings as short labelled lines (what it shows, by what, in which order). */
function ExplainDetail(p: AgAiToolDetailParams) {
  const e = dataOf<Explained>(p);
  if (!e) return null;
  return (
    <div className="ask-a ask-explain" data-testid="ask-explain">
      <p className="ask-explain-kind">{e.kind}</p>
      <dl>
        {e.lines.map((l) => <div key={l.label}><dt>{l.label}</dt><dd>{l.value}</dd></div>)}
      </dl>
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
    label: ({ args, status, result }) => {
      const a = result?.success ? (result as { data?: Answered }).data : undefined;
      const q = `“${String(args.question ?? "").slice(0, 60)}”`;
      if (a && !a.answerable) return { text: `Read ${q}` };
      return { text: `${status === "done" ? "Answered" : "Answering"} ${q}`, pill: a?.chart ? "Answers page" : undefined };
    },
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
  // S4: the build. The pill names the carrier the page is filtered to.
  create_report: {
    label: ({ args, result }) => {
      const d = result?.success ? (result as { data?: { name?: string } }).data : undefined;
      return { text: `New report ‘${String(d?.name ?? args.name ?? "")}’`, pill: args.carrier ? String(args.carrier) : undefined };
    },
  },
  check_page: {
    label: ({ status, result }) => {
      const n = result?.success ? (result as { data?: { issues?: number } }).data?.issues : undefined;
      return { text: status === "done" ? "Checked the page" : "Checking the page", pill: n == null ? undefined : n ? count(n, "issue") : "no issues" };
    },
  },
  build_report_template: {
    label: ({ args, status }) => ({ text: status === "done" ? "Built from template" : "Building from template", pill: args.carrier ? String(args.carrier) : undefined }),
  },
  build_page: {
    label: ({ args, status, result }) => {
      const b = result?.success ? (result as { data?: Built }).data : undefined;
      const name = String(b?.name ?? args.name ?? "").slice(0, 40);
      return { text: `${status === "done" ? "Built" : "Building"} ‘${name}’`, pill: b ? count(b.parts.length, "widget") : undefined };
    },
    detail: BuiltDetail,
  },
  // S6: Explain this chart.
  explain_chart: {
    label: ({ args, status }) => ({ text: `${status === "done" ? "Read" : "Reading"} “${String(args.name ?? "").slice(0, 48)}”` }),
    detail: ExplainDetail,
  },
  // Studio's own agents, in plain words. Reading steps are not shown: they change nothing.
  add_widget: { label: ({ args, status }) => ({ text: `${status === "done" ? "Added" : "Adding"} ${widgetLabel(args.type)}` }) },
  configure_widget: { label: ({ args, status, widgetName }) => ({ text: `${status === "done" ? "Set up" : "Setting up"} ${named(widgetName, args.widgetId, "the chart")}` }) },
  position_widget: { label: ({ args, status, widgetName }) => ({ text: `${status === "done" ? "Moved" : "Moving"} ${named(widgetName, args.widgetId, "a widget")}` }) },
  remove_widget: { label: ({ args, status, widgetName }) => ({ text: `${status === "done" ? "Removed" : "Removing"} ${named(widgetName, args.widgetId, "a widget")}` }) },
  add_page_filter: { label: ({ status }) => ({ text: status === "done" ? "Filtered the page" : "Filtering the page" }) },
  remove_page_filter: { label: ({ status }) => ({ text: status === "done" ? "Removed a page filter" : "Removing a page filter" }) },
  add_widget_filter: { label: ({ args, widgetName }) => ({ text: `Filtered ${named(widgetName, args.widgetId, "a widget")}` }) },
  remove_widget_filter: { label: ({ args, widgetName }) => ({ text: `Removed a filter from ${named(widgetName, args.widgetId, "a widget")}` }) },
  // The target is `args.type.id` ("widget" or "page").
  delegate_to: { label: ({ args }) => ({ text: `Asked AG Studio's ${(args.type as { id?: string } | undefined)?.id === "page" ? "page" : "widget"} agent` }) },
  ...Object.fromEntries(["view_report", "view_page", "view_widget", "view_schema", "view_plan", "rename_thread", "create_plan", "update_plan", "clear_plan", "complete_task"]
    .map((n) => [n, { label: () => ({ text: "" }), hidden: true }])),
};

/** Widget types as a seller reads them ("a claim clock", "a bar chart"). */
const WIDGET_WORDS: Record<string, string> = {
  "doorstep-claim-clock": "a claim clock", "doorstep-failure-points": "failure points", "doorstep-evidence-meter": "an evidence meter",
  "doorstep-journey": "a parcel journey", "doorstep-money-flow": "a liability waterfall", "doorstep-brief": "Doorstep's brief",
  value: "a number", grid: "a table", "pivot-grid": "a pivot table", "donut-chart": "a donut chart", "line-chart": "a line chart",
};
export function widgetLabel(type: unknown): string {
  const t = String(type ?? "");
  if (WIDGET_WORDS[t]) return WIDGET_WORDS[t];
  if (/chart/.test(t)) return `a ${t.replace(/-chart.*$/, "").replace(/-/g, " ")} chart`;
  return t ? `a ${t.replace(/-/g, " ")}` : "a widget";
}
const named = (name: (id: string) => string | undefined, id: unknown, fallback: string) => {
  const n = id ? name(String(id)) : undefined;
  return n ? `“${n}”` : fallback;
};

/** S9: one to three plain words per tool, for the Steps list under a reply. Hidden tools have none. */
const TRACE_LABEL: Record<string, string> = {
  scan_for_anomalies: "Read orders", answer_question: "Answered", open_report: "Opened report", open_carrier_report: "Opened carrier",
  create_report: "Built report", build_report_template: "Built report", build_page: "Built report", check_page: "Checked page", explain_chart: "Read chart",
  add_widget: "Built chart", configure_widget: "Set up chart", position_widget: "Moved chart", remove_widget: "Removed chart",
  add_page_filter: "Filtered page", remove_page_filter: "Cleared filter", add_widget_filter: "Filtered chart",
  remove_widget_filter: "Cleared filter", delegate_to: "Asked Studio",
};
export const traceLabel = (name: string): string | null =>
  doorstepToolDisplay[name]?.hidden ? null : TRACE_LABEL[name] ?? name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
