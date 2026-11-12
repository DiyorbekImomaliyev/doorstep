/**
 * AS-07: Doorstep on AG Studio's Agent Framework.
 *
 * - The adapter: every agent turn (Doorstep's lead and Studio's own widget and page agents) goes
 *   to Doorstep's backend, which answers through the one model adapter (app/ai/llm.py). No model
 *   key is in the browser, and no turn reaches a model without the backend's budget and guard.
 * - The Doorstep lead agent: scans for anomalies (in code, from the seller's rows), answers a
 *   question with a chart (AS-06's plan, figures computed here), opens a report, and hands chart
 *   and page changes to Studio's built-in widget and page agents.
 * - No agent is offered Studio's `execute_query`, so no data row is sent to a model: the scan
 *   returns findings, and Studio's agents see field names and widget settings only.
 * - No agent can move money: the tools only read the seller's rows and change their charts.
 */
import type {
  AgAiConversationItem, AgAiEvent, AgAiOutputItem, AgAiPromptStarter, AgAiTool, AgAnyAiAgentDefinition, AgConditionDefinition, AgLlmAdapter,
  AgLlmRequest, AgLlmResponse, AgPageState, AgReportState, AgStudioApi,
} from "ag-studio";
import { createAiHarness, directLlmRunner } from "ag-studio";
import { answer, askDoorstep, type Asked } from "./ask";
import type { Prepared } from "./data";
import { explainWidget, widgetNames } from "./explain";
import { KPI_H, W, kpi } from "./reports";
import type { DoorstepRegistry } from "./widgets";

// ---------------------------------------------------------------- the adapter

type Item = { kind: "user" | "assistant" | "call" | "result" | "context"; text: string; name?: string; call_id?: string };
export type TurnOut = { message: string; calls: { call_id: string; name: string; arguments: Record<string, unknown> }[]; mode: "model" | "fallback"; provider?: string | null };

const MAX_ITEMS = 60;
const textOf = (parts: { type: string; text?: string }[]) => parts.map((p) => (p.type === "text" ? p.text ?? "" : "")).join("\n").trim();

/** Studio's conversation as the backend reads it: who said what, which tool ran, what it returned. */
export function toItems(input: AgAiConversationItem[]): Item[] {
  const items: Item[] = [];
  for (const it of input) {
    if (it.type === "message" && it.kind === "input") {
      const text = textOf(it.content);
      if (text) items.push({ kind: it.role === "user" ? "user" : "context", text });
    } else if (it.type === "message") {
      const text = textOf(it.content);
      if (text) items.push({ kind: "assistant", text });
    } else if (it.type === "function_call") {
      items.push({ kind: "call", name: it.name, call_id: it.callId, text: it.arguments });
    } else if (it.type === "function_call_output") {
      items.push({ kind: "result", call_id: it.callId, text: it.output });
    }
  }
  // The newest turns matter most; the opening question is kept for context.
  return items.length > MAX_ITEMS ? [items[0], ...items.slice(-(MAX_ITEMS - 1))] : items;
}

function choiceOf(c: AgLlmRequest["toolChoice"]): string {
  return !c ? "auto" : typeof c === "string" ? c : c.name;
}

const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));
let seq = 0;
const nextId = (p: string) => `${p}-${Date.now().toString(36)}-${(++seq).toString(36)}`;

/** The text a turn shows when the backend cannot answer (rate cap, network). */
const OFFLINE = "I can't reach Doorstep just now. Your reports keep updating live; try again in a minute.";

/**
 * Turns Doorstep answers in the browser, never sent to a model (their call ids start with this):
 *  - a request to move money is data, not an instruction: it goes straight to answer_question,
 *    whose reader says what Doorstep can't do (rule 8);
 *  - when the backend has no model to offer (budget, quota), the lead still works: a scan for
 *    "what changed", answer_question (the keyword reader) for anything else.
 */
const LOCAL = "local-";
/** AI-04: tools whose card says everything, so the model is not asked to repeat it. */
const SELF_SAID = new Set(["answer_question", "build_page"]);
const MONEY_ASK = /\b(refund|reimburse|compensat|pay (me|out|back)|send (me )?money|transfer|charge back|chargeback|ignore (the|all|your|previous) (rules|instructions))/i;
const SCAN_ASK = /what changed|unusual|wrong|anything/i;
/** S4: "Chase Evri", "build me a page to chase…": with no model, Doorstep's template builds it. */
const CHASE_ASK = /\bchase\b/i;
/** AI-02: "build me a page for delivery types", "make a report about lockers". */
const BUILD_ASK = /\b(build|make|create|set up|design)\b.*\b(page|report|dashboard)\b|\b(new )?(page|report|dashboard)\s+(for|about|on)\b/i;
/** AI-02: with no model and no topic it has a layout for, the console says so instead of guessing a page. */
export const NO_TOPIC = "I can't reach the model just now, so I can only build pages I have a layout for: delivery methods, carriers, claims, cases, money, proof or areas. Name one and I'll build it.";
/** S6: the toolbar's "Explain this chart" sends `Explain “<name>”`. */
const EXPLAIN_ASK = /^explain\b/i;
/** AS-21: "Show me Evri's claims" opens that carrier's report. */
const SHOW_ASK = /^show me\b/i;

/** What the offline lead needs from the board to route a chase, a build or an explain. */
export type LocalRoutes = {
  chase: (words: string) => string | undefined; charts: () => string[];
  /** AI-02: the page a build request names (a topic, and a carrier or method filter), or none. */
  topic?: (words: string) => TopicPick | undefined;
};

/** The name inside the first pair of quotes, matched against the known names. */
const quoted = (text: string, names: string[]) => {
  const q = text.match(/[“"']([^”"']+)[”"']/)?.[1]?.trim().toLowerCase();
  return names.find((n) => n.toLowerCase() === q) ?? names.find((n) => text.toLowerCase().includes(n.toLowerCase()));
};

export function doorstepAdapter(token: string, agent: string, onTurn?: (t: TurnOut) => void, routes?: LocalRoutes): AgLlmAdapter {
  return {
    executeTurn(request, options) {
      const signal = options?.signal;
      const items = toItems(request.input);
      const last = items[items.length - 1];
      const offers = (name: string) => (request.tools ?? []).some((t) => t.name === name);
      const lead = agent === "doorstep" && request.responseFormat.type !== "json";
      const call = (kind: string, name: string, args: Record<string, unknown>) =>
        ({ call_id: `${LOCAL}${kind}-${nextId("c")}`, name, arguments: args });

      // AI-04: after answer_question the card is the answer (figure, reading, chart), and after
      // build_page the result names every widget it placed, so no second model turn is spent
      // repeating them. Only when every result since the model's last call is one of those.
      const tail = items.slice(items.map((i) => i.kind).lastIndexOf("call") + 1);
      const callName = (id?: string) => items.find((i) => i.kind === "call" && i.call_id === id)?.name;
      const selfSaid = lead && last?.kind === "result" && tail.length > 0
        && tail.every((i) => i.kind === "result" && SELF_SAID.has(callName(i.call_id) ?? ""));
      let turn: Promise<TurnOut>;
      let local = false;
      if (lead && last?.kind === "result" && (last.call_id?.startsWith(LOCAL) || selfSaid)) {
        // After a local call (or one whose card says it all) nothing more is said.
        turn = Promise.resolve({ message: "", calls: [], mode: "fallback" });
        local = true;
      } else if (lead && last?.kind === "user" && MONEY_ASK.test(last.text) && offers("answer_question")) {
        turn = Promise.resolve({ message: "", calls: [call("guard", "answer_question", { question: last.text.slice(0, 300) })], mode: "fallback" });
        local = true;
      } else {
        turn = fetch(`/api/seller/${encodeURIComponent(token)}/insights/agent`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            agent,
            instructions: request.instructions ?? "",
            items,
            tools: (request.tools ?? []).map((t) => ({ name: t.name, description: t.description ?? "", parameters: t.parameters ?? {} })),
            tool_choice: choiceOf(request.toolChoice),
            response_schema: request.responseFormat.type === "json" ? request.responseFormat.schema : null,
          }),
          signal,
        }).then(async (r) => (r.ok ? ((await r.json()) as TurnOut) : { message: OFFLINE, calls: [], mode: "fallback" as const }))
          .catch((e: unknown) => {
            if (signal?.aborted) throw e;
            return { message: OFFLINE, calls: [], mode: "fallback" as const };
          })
          .then((t): TurnOut => {
            if (!lead || t.mode !== "fallback" || t.calls.length || last?.kind !== "user") return t;
            if (routes && EXPLAIN_ASK.test(last.text) && offers("explain_chart")) {
              const name = quoted(last.text, routes.charts());
              if (name) return { message: "", calls: [call("offline", "explain_chart", { name })], mode: "fallback" };
            }
            // AS-21: "Show me Evri's claims" (a starter) opens that carrier's report without a model.
            if (routes && SHOW_ASK.test(last.text) && offers("open_carrier_report")) {
              const carrier = routes.chase(last.text);
              if (carrier) return { message: "", calls: [call("offline", "open_carrier_report", { carrier })], mode: "fallback" };
            }
            if (routes && CHASE_ASK.test(last.text) && offers("build_report_template")) {
              const carrier = routes.chase(last.text);
              if (carrier) return { message: "", calls: [call("offline", "build_report_template", { carrier })], mode: "fallback" };
            }
            // AI-02: a page on a topic Doorstep has a layout for, and never a page about something else.
            if (routes?.topic && BUILD_ASK.test(last.text) && offers("build_page")) {
              const pick = routes.topic(last.text);
              if (pick) return { message: "", calls: [call("offline", "build_page", pick)], mode: "fallback" };
              return { message: NO_TOPIC, calls: [], mode: "fallback" };
            }
            const name = SCAN_ASK.test(last.text) && offers("scan_for_anomalies") ? "scan_for_anomalies" : offers("answer_question") ? "answer_question" : null;
            if (!name) return t;
            return { message: "", calls: [call("offline", name, name === "answer_question" ? { question: last.text.slice(0, 300) } : {})], mode: "fallback" };
          });
      }
      // A turn the browser answered itself says nothing about the model, so it is not reported: the
      // console never reads "keywords" while the model is up (answer_question reports its own read).
      if (!local) turn.then((t) => onTurn?.(t)).catch(() => undefined);

      const messageId = nextId("msg");
      const stream = (async function* (): AsyncGenerator<AgAiEvent> {
        const t = await turn;
        if (t.message) {
          yield { type: "TEXT_MESSAGE_START", messageId, role: "assistant" };
          // A few words at a time, so a reply reads as it arrives rather than landing at once.
          const words = t.message.split(/(?<=\s)/);
          for (let i = 0; i < words.length; i += 4) {
            if (signal?.aborted) return;
            yield { type: "TEXT_MESSAGE_CONTENT", messageId, delta: words.slice(i, i + 4).join("") };
            await pause(14);
          }
          yield { type: "TEXT_MESSAGE_END", messageId };
        }
        for (const c of t.calls) {
          yield { type: "TOOL_CALL_START", toolCallId: c.call_id, toolCallName: c.name, parentMessageId: messageId };
          yield { type: "TOOL_CALL_ARGS", toolCallId: c.call_id, delta: JSON.stringify(c.arguments) };
          yield { type: "TOOL_CALL_END", toolCallId: c.call_id };
        }
      })();

      const complete = turn.then((t): AgLlmResponse => {
        const output: AgAiOutputItem[] = [];
        if (t.message) {
          output.push({ id: messageId, kind: "output", type: "message", role: "assistant", status: "completed", content: [{ type: "text", text: t.message, annotations: [] }] });
        }
        for (const c of t.calls) {
          output.push({ id: c.call_id, kind: "output", type: "function_call", callId: c.call_id, name: c.name, arguments: JSON.stringify(c.arguments), status: "completed" });
        }
        // A JSON answer (a thread title, a plan) cannot be a fallback sentence: report it failed.
        if (t.mode === "fallback" && request.responseFormat.type === "json") {
          return { id: messageId, createdAt: Date.now(), output: [], status: "failed", error: { code: "unavailable", message: t.message } };
        }
        return { id: messageId, createdAt: Date.now(), output, status: "completed", model: t.mode === "model" ? "doorstep" : "doorstep-fallback" };
      });
      return { stream, complete };
    },
  };
}

// ---------------------------------------------------------------- the anomaly scan, in code

type Row = Record<string, unknown>;
/** The numbers behind a finding, for the Overview brief (story.ts) to word its own way. */
export type Facts =
  | { kind: "rate"; field: "carrier" | "method" | "all"; key: string; now: number; before: number; cases: number; orders: number }
  | { kind: "loss"; now: number; before: number }
  | { kind: "claim"; key: string; days: number; amount: number };
export type Finding = {
  severity: "high" | "watch"; title: string; detail: string; report: "overview" | "carriers" | "cases"; carrier?: string; facts?: Facts;
};

const DAY = 864e5;
const RECENT_DAYS = 28;
const BASE_DAYS = 84;
const MIN_ORDERS = 20;
const MIN_CASES = 3;

export const tableRows = (p: Prepared, id: string) => ((p.sources.sources.find((t) => t.id === id) as unknown as { data?: Row[] } | undefined)?.data ?? []);
const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
const money = (v: number, home: Prepared["home"]) => v.toLocaleString("en-GB", { style: "currency", currency: home, maximumFractionDigits: 0 });

/**
 * The last four weeks against the twelve before: case rate per carrier and per delivery method,
 * the weekly net loss, and carrier claims due within a week. Only groups with enough orders count,
 * so one bad parcel never reads as a trend. Returns findings, never rows.
 */
export function scanAnomalies(prepared: Prepared, now = new Date()): { window: string; findings: Finding[] } {
  const end = now.getTime();
  const recentFrom = end - RECENT_DAYS * DAY;
  const baseFrom = recentFrom - BASE_DAYS * DAY;
  const at = (r: Row, f: string) => (r[f] instanceof Date ? (r[f] as Date).getTime() : Date.parse(String(r[f])));
  const orders = tableRows(prepared, "orders");
  const recent = orders.filter((r) => at(r, "created_at") >= recentFrom && at(r, "created_at") < end);
  const base = orders.filter((r) => at(r, "created_at") >= baseFrom && at(r, "created_at") < recentFrom);
  const findings: Finding[] = [];

  const rate = (rows: Row[]) => (rows.length ? rows.reduce((a, r) => a + Number(r.had_case ?? 0), 0) / rows.length : 0);
  const check = (field: "carrier" | "method") => {
    const keys = new Set(recent.map((r) => String(r[field])));
    for (const k of keys) {
      if (/^Not /.test(k)) continue;
      const now4 = recent.filter((r) => String(r[field]) === k);
      const before = base.filter((r) => String(r[field]) === k);
      const cases = now4.reduce((a, r) => a + Number(r.had_case ?? 0), 0);
      if (now4.length < MIN_ORDERS || cases < MIN_CASES) continue;
      const a = rate(now4), b = rate(before);
      if (a >= Math.max(b * 1.5, b + 0.01)) {
        findings.push({
          severity: b > 0 && a >= b * 2 ? "high" : "watch",
          title: `${k}: case rate up to ${pct(a)}`,
          detail: `${cases} of ${now4.length} orders in the last 4 weeks had a problem, against ${pct(b)} in the 12 weeks before (${before.length} orders).`,
          report: field === "carrier" ? "carriers" : "overview",
          carrier: field === "carrier" ? k : undefined,
          facts: { kind: "rate", field, key: k, now: a, before: b, cases, orders: now4.length },
        });
      }
    }
  };
  if (recent.length >= MIN_ORDERS) {
    const a = rate(recent), b = rate(base);
    if (a >= Math.max(b * 1.5, b + 0.01)) {
      findings.push({
        severity: b > 0 && a >= b * 2 ? "high" : "watch",
        title: `Case rate up to ${pct(a)} overall`,
        detail: `Across ${recent.length} orders in the last 4 weeks, against ${pct(b)} in the 12 weeks before.`,
        report: "overview",
        facts: { kind: "rate", field: "all", key: "All orders", now: a, before: b, cases: recent.reduce((n, r) => n + Number(r.had_case ?? 0), 0), orders: recent.length },
      });
    }
  }
  check("carrier");
  check("method");

  const cases = tableRows(prepared, "cases");
  const loss = (from: number, to: number) => cases
    .filter((c) => at(c, "opened_at") >= from && at(c, "opened_at") < to)
    .reduce((a, c) => a + Number(c.refunded ?? 0) - Number(c.recovered ?? 0), 0);
  const lossNow = loss(recentFrom, end) / (RECENT_DAYS / 7);
  const lossBefore = loss(baseFrom, recentFrom) / (BASE_DAYS / 7);
  if (lossNow > 0 && lossNow >= Math.max(lossBefore * 1.5, lossBefore + 20)) {
    findings.push({
      severity: lossBefore > 0 && lossNow >= lossBefore * 2 ? "high" : "watch",
      title: `Net delivery loss up to ${money(lossNow, prepared.home)} a week`,
      detail: `Refunds minus carrier payments averaged ${money(lossBefore, prepared.home)} a week in the 12 weeks before.`,
      report: "overview",
      facts: { kind: "loss", now: lossNow, before: lossBefore },
    });
  }

  for (const r of tableRows(prepared, "recoveries")) {
    const due = at(r, "deadline_at");
    if (r.next_step === "Done" || !Number.isFinite(due) || due < end - DAY || due > end + 7 * DAY) continue;
    const days = Math.max(0, Math.ceil((due - end) / DAY));
    findings.push({
      severity: days <= 3 ? "high" : "watch",
      title: `${String(r.carrier)} claim due in ${days} day${days === 1 ? "" : "s"}`,
      detail: `${money(Number(r.claimed ?? 0), prepared.home)} claimed. Next step: ${String(r.next_step).toLowerCase()}.`,
      report: "carriers",
      carrier: String(r.carrier),
      facts: { kind: "claim", key: String(r.carrier), days, amount: Number(r.claimed ?? 0) },
    });
  }
  findings.sort((x, y) => (x.severity === y.severity ? 0 : x.severity === "high" ? -1 : 1));
  const fmt = (t: number) => new Date(t).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  return { window: `${fmt(recentFrom)} to ${fmt(end)}, against ${fmt(baseFrom)} to ${fmt(recentFrom)}`, findings };
}

/** The scan as the model reads it: one line per finding, high first, at most six. */
export function scanText(scan: { window: string; findings: Finding[] }): string {
  if (!scan.findings.length) return `Window: ${scan.window}. Nothing stands out: no case rate, loss or claim deadline moved enough to flag.`;
  const top = scan.findings.slice(0, 6).map((f, i) => `${i + 1}. [${f.severity}] ${f.title}. ${f.detail} (report: ${f.report}${f.carrier ? `, carrier: ${f.carrier}` : ""})`);
  const more = scan.findings.length - top.length;
  return [`Window: ${scan.window}.`, ...top, ...(more > 0 ? [`${more} more, lower priority.`] : [])].join("\n");
}

// ---------------------------------------------------------------- the harness

const ASK_FAILED = "Couldn't read that just now. Try again in a moment.";

/** What answer_question hands the console: the sentence, how it was read, and the chart it added. */
export type Answered = {
  answerable: boolean; headline: string; detail: string[]; reading: string[]; chart: boolean; widgetId?: string; mode: Asked["mode"];
  /** AI-03: the provider that read the question, when a model did. */
  provider?: string | null;
  /** The plan the figures came from, so the card can recompute them as the data moves. */
  plan?: Asked["plan"];
};

export type AgentActions = {
  token: string;
  prepared: () => Prepared;
  addAnswer: (asked: Asked) => string;
  openReport: (id: "overview" | "carriers" | "cases") => void;
  openCarrier: (condition: AgConditionDefinition) => void;
  /** S4: names a report an agent made, so the rail lists it ("by Doorstep"). */
  nameReport: (pageId: string, name: string) => void;
};

// ---------------------------------------------------------------- S4: a page to chase a carrier

/** The carriers the seller's orders went with, as the reports name them. */
export const liveCarriers = (p: Prepared) =>
  [...new Set(tableRows(p, "orders").map((r) => String(r.carrier)))].filter((k) => k && !/^Not /.test(k)).sort();

/**
 * Which carrier to chase: the one named in her words, else the one the scan flags, else the one
 * with the most money in open claims.
 */
export function chaseCarrier(p: Prepared, words = ""): string | undefined {
  const known = liveCarriers(p);
  const said = ` ${words.toLowerCase().replace(/[^a-z0-9()]+/g, " ")} `;
  const named = known.find((k) => said.includes(` ${k.toLowerCase().replace(/[^a-z0-9()]+/g, " ").trim()} `));
  if (named) return named;
  const flagged = scanAnomalies(p).findings.find((f) => f.facts?.kind === "rate" && f.facts.field === "carrier")?.carrier;
  if (flagged && known.includes(flagged)) return flagged;
  const open = new Map<string, number>();
  for (const r of tableRows(p, "recoveries")) {
    if (r.next_step !== "Done") open.set(String(r.carrier), (open.get(String(r.carrier)) ?? 0) + Number(r.claimed ?? 0));
  }
  return [...open.entries()].filter(([k]) => known.includes(k)).sort((x, y) => y[1] - x[1])[0]?.[0] ?? known[0];
}

type Page = AgPageState<DoorstepRegistry>;
let pageSeq = 0;
/** After a reload: the saved reports keep their ids, so new ones count on from the highest. */
export const resumePageIds = (after: number) => { pageSeq = Math.max(pageSeq, after); };
const slug = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 32) || "report";
const carrierFilter = (carrier: string | null | undefined): Page["filter"] =>
  carrier ? { page: [{ field: { id: "orders.carrier" }, model: { operator: "equals", value: carrier } }] } : {};

/** Doorstep's template for chasing one carrier: what was claimed and won, deadlines, causes, proof. */
export function templatePage(id: string, carrier: string): Page {
  return {
    id,
    widgets: {
      k1: kpi("claimed", "Claims filed"), k2: kpi("recovered", "Paid back"),
      clock: W.claimClock, fp: W.failurePoints, ev: W.evidenceByCarrier,
    },
    widgetLayout: {
      k1: { xTrack: 0, yTrack: 0, xSpan: 12, ySpan: 11 }, k2: { xTrack: 12, yTrack: 0, xSpan: 12, ySpan: 11 },
      clock: { xTrack: 0, yTrack: 11, xSpan: 24, ySpan: 15 },
      fp: { xTrack: 0, yTrack: 26, xSpan: 13, ySpan: 20 }, ev: { xTrack: 13, yTrack: 26, xSpan: 11, ySpan: 20 },
    },
    filter: carrierFilter(carrier),
  };
}

// ---------------------------------------------------------------- AI-02: a page on any topic

type Widget = NonNullable<Page["widgets"]>[string];
type Piece = { key: string; label: string; widget: Widget; size: "kpi" | "half" | "wide" | "full"; h?: number };

/** A table per delivery method: the exact numbers behind the method charts. */
const methodScorecard: Widget = {
  type: "pivot-grid",
  dataMapping: { rows: [{ id: "orders.method" }], columns: [], values: [{ id: "order_count" }, { id: "case_rate" }, { id: "prevention_rate" }, { id: "evidence_avg" }, { id: "transit_days" }] },
  sort: [{ field: { id: "order_count" }, direction: "desc" }],
  format: { title: { enabled: true, text: "Method scorecard" }, subtitle: { enabled: false }, style: { grandTotalRow: { enabled: true } } },
} as Widget;

/** The topics Doorstep has a layout for, each read top to bottom: numbers, then charts, then detail. */
export const TOPICS = {
  delivery_methods: {
    name: "Delivery methods",
    words: /deliver(y|ies)? (type|method|option|choice)s?|\bmethods?\b|locker|signature|signed for|home deliver/i,
    pieces: [
      { key: "k1", label: "Orders", widget: kpi("order_count", "All orders"), size: "kpi" },
      { key: "k2", label: "Case rate", widget: kpi("case_rate", "Orders with a problem"), size: "kpi" },
      { key: "k3", label: "Prevention rate", widget: kpi("prevention_rate", "Locker, signed or fixed address"), size: "kpi" },
      { key: "k4", label: "Proof on file", widget: kpi("evidence_avg", "Possession proof per order"), size: "kpi" },
      { key: "mix", label: "Delivery choice", widget: W.methodMix, size: "half" },
      { key: "rate", label: "Case rate by method", widget: W.caseRateByMethod, size: "wide" },
      { key: "ev", label: "Proof by method", widget: W.evidenceByMethod, size: "half" },
      { key: "fp", label: "What goes wrong", widget: W.failurePoints, size: "wide" },
      { key: "table", label: "Method scorecard", widget: methodScorecard, size: "full", h: 14 },
    ],
  },
  carriers: {
    name: "Carriers",
    words: /carrier|courier|royal mail|evri|dpd|usps|\bups\b|fedex/i,
    pieces: [
      { key: "k1", label: "Orders", widget: kpi("order_count", "All orders"), size: "kpi" },
      { key: "k2", label: "Case rate", widget: kpi("case_rate", "Orders with a problem"), size: "kpi" },
      { key: "k3", label: "Days in transit", widget: kpi("transit_days", "Label to delivered"), size: "kpi" },
      { key: "k4", label: "Recovery rate", widget: kpi("recovery_rate", "Recovered ÷ claimed"), size: "kpi" },
      { key: "cost", label: "Cost by carrier", widget: W.costByCarrier, size: "half" },
      { key: "causes", label: "Causes by carrier", widget: W.failuresByCarrier, size: "wide" },
      { key: "score", label: "Carrier scorecard", widget: W.carrierScorecard, size: "full", h: 16 },
    ],
  },
  claims: {
    name: "Carrier claims",
    words: /claim|recover|paid back|deadline/i,
    pieces: [
      { key: "k1", label: "Claims filed", widget: kpi("claimed", "Claimed from carriers"), size: "kpi" },
      { key: "k2", label: "Paid back", widget: kpi("recovered", "Paid by carriers"), size: "kpi" },
      { key: "k3", label: "Recovery rate", widget: kpi("recovery_rate", "Recovered ÷ claimed"), size: "kpi" },
      { key: "clock", label: "Claim deadlines", widget: W.claimClock, size: "full", h: 15 },
      { key: "status", label: "Claims by status", widget: W.claimsByStatus, size: "half" },
      { key: "ev", label: "Proof by carrier", widget: W.evidenceByCarrier, size: "wide" },
      { key: "grid", label: "Carrier claims", widget: W.claimsGrid, size: "full", h: 18 },
    ],
  },
  cases: {
    name: "Cases",
    words: /\bcases?\b|problem|complaint|fail|went wrong|lost|damaged|missing|dispute/i,
    pieces: [
      { key: "k1", label: "Cases", widget: kpi("case_count", "Problems reported"), size: "kpi" },
      { key: "k2", label: "Open cases", widget: kpi("open_cases", "Not yet settled"), size: "kpi" },
      { key: "k3", label: "Refunded", widget: kpi("paid_out", "Refunded via PayPal"), size: "kpi" },
      { key: "month", label: "Cases by month", widget: W.casesByMonth, size: "full", h: 18 },
      { key: "route", label: "How cases were decided", widget: W.decisions, size: "half" },
      { key: "fp", label: "What goes wrong", widget: W.failurePoints, size: "wide" },
      { key: "grid", label: "Every case", widget: W.casesGrid, size: "full", h: 20 },
    ],
  },
  money: {
    name: "Money",
    words: /money|refund|loss|cost|spend|paid|revenue|sales|profit|£|\$/i,
    pieces: [
      { key: "k1", label: "Net delivery loss", widget: kpi("net_loss", "Refunds minus recoveries"), size: "kpi" },
      { key: "k2", label: "Refunded", widget: kpi("paid_out", "Refunded via PayPal"), size: "kpi" },
      { key: "k3", label: "Recovered", widget: kpi("recovered", "Paid by carriers"), size: "kpi" },
      { key: "k4", label: "Sales", widget: kpi("sales", "Value of orders"), size: "kpi" },
      { key: "flow", label: "Money flow", widget: W.moneyFlow, size: "half" },
      { key: "cost", label: "Cost by carrier", widget: W.costByCarrier, size: "wide" },
      { key: "money", label: "Money by carrier", widget: W.moneyByCarrier, size: "full", h: 15 },
    ],
  },
  proof: {
    name: "Proof",
    words: /proof|evidence|seal|photo|signature proof|possession/i,
    pieces: [
      { key: "k1", label: "Proof on file", widget: kpi("evidence_avg", "Possession proof per order"), size: "kpi" },
      { key: "k2", label: "Prevention rate", widget: kpi("prevention_rate", "Locker, signed or fixed address"), size: "kpi" },
      { key: "journey", label: "Parcel journey", widget: W.journey, size: "full", h: 20 },
      { key: "ev", label: "Proof by method", widget: W.evidenceByMethod, size: "half" },
      { key: "evc", label: "Proof by carrier", widget: W.evidenceByCarrier, size: "wide" },
    ],
  },
  areas: {
    name: "Areas",
    words: /area|postcode|district|region|where|map|london|manchester|city|zip/i,
    pieces: [
      { key: "atlas", label: "Delivery atlas", widget: W.atlas, size: "full", h: 30 },
      { key: "table", label: "Areas", widget: W.areaTable, size: "full", h: 16 },
    ],
  },
} satisfies Record<string, { name: string; words: RegExp; pieces: Piece[] }>;
export type Topic = keyof typeof TOPICS;
export type TopicPick = { topic: Topic; name?: string; carrier?: string | null; method?: string | null };
const TOPIC_ORDER: Topic[] = ["claims", "delivery_methods", "proof", "areas", "cases", "money", "carriers"];

/** The live delivery methods, as the reports name them ("Home", "Locker", "Signature"). */
export const liveMethods = (p: Prepared) =>
  [...new Set(tableRows(p, "orders").map((r) => String(r.method)))].filter((k) => k && !/^Not /.test(k)).sort();

/** AI-02, offline: the topic a build request names, and the carrier or method it narrows to. */
export function pickTopic(p: Prepared, words: string): TopicPick | undefined {
  const said = ` ${words.toLowerCase().replace(/[^a-z0-9()£$]+/g, " ")} `;
  const carrier = liveCarriers(p).find((k) => said.includes(` ${k.toLowerCase().replace(/[^a-z0-9()]+/g, " ").trim()} `)) ?? null;
  const method = liveMethods(p).find((m) => new RegExp(`\\b${m.toLowerCase()}s?\\b`).test(said)) ?? null;
  const topic = TOPIC_ORDER.find((t) => TOPICS[t].words.test(said));
  if (!topic) return undefined;
  const t: Topic = method && !carrier && topic !== "claims" ? "delivery_methods" : topic;
  const only = carrier ?? method;
  // Every argument the tool's schema asks for, so Studio runs the offline call as it runs a model's.
  return { topic: t, name: only ? `${only} · ${TOPICS[t].name}` : TOPICS[t].name, carrier, method: carrier ? null : method };
}

const byCarrier = (field: string, value: string, title: string, type: string): Widget => ({
  type, dataMapping: { categoryKey: [{ id: `orders.${field}` }], valueKey: [{ id: value }] },
  sort: [{ field: { id: value }, direction: "desc" }],
  format: { title: { enabled: true, text: title }, subtitle: { enabled: false }, style: { theme: { common: { legend: { enabled: type === "donut-chart", position: "bottom" } } } } },
}) as Widget;
/**
 * A page narrowed to one method or one carrier would split by that same field into one bar, so
 * those widgets split by the other field instead (a lockers page compares carriers).
 */
const NARROWED: Record<"method" | "carrier", Record<string, Piece>> = {
  method: {
    mix: { key: "mix", label: "Orders by carrier", widget: byCarrier("carrier", "order_count", "Orders by carrier", "donut-chart"), size: "half" },
    rate: { key: "rate", label: "Case rate by carrier", widget: byCarrier("carrier", "case_rate", "Case rate by carrier", "column-chart-grouped"), size: "wide" },
    ev: { key: "ev", label: "Proof by carrier", widget: W.evidenceByCarrier, size: "half" },
    table: { key: "table", label: "Carrier scorecard", widget: W.carrierScorecard, size: "full", h: 14 },
  },
  carrier: {
    cost: { key: "cost", label: "Delivery choice", widget: W.methodMix, size: "half" },
    causes: { key: "causes", label: "What goes wrong", widget: W.failurePoints, size: "wide" },
    score: { key: "score", label: "Method scorecard", widget: methodScorecard, size: "full", h: 14 },
    ev: { key: "ev", label: "Proof by method", widget: W.evidenceByMethod, size: "wide" },
    evc: { key: "evc", label: "Proof by method", widget: W.evidenceByMethod, size: "wide" },
  },
};

/** The page for a topic: KPIs in one row, charts two to a row (13 + 11), tables full width. */
export function topicPage(id: string, pick: TopicPick): { page: Page; parts: string[] } {
  const swap = pick.carrier ? NARROWED.carrier : pick.method ? NARROWED.method : {};
  const pieces: Piece[] = (TOPICS[pick.topic].pieces as Piece[]).map((x) => swap[x.key] ?? x)
    .filter((x, i, all) => all.findIndex((y) => y.label === x.label) === i);
  const widgets: Record<string, Widget> = {};
  const layout: NonNullable<Page["widgetLayout"]> = {};
  const kpis = pieces.filter((x) => x.size === "kpi");
  let y = 0;
  kpis.forEach((x, i) => {
    const span = Math.floor(24 / kpis.length);
    widgets[x.key] = x.widget;
    layout[x.key] = { xTrack: i * span, yTrack: 0, xSpan: i === kpis.length - 1 ? 24 - i * span : span, ySpan: KPI_H };
  });
  if (kpis.length) y = KPI_H;
  let x = 0;
  for (const piece of pieces.filter((q) => q.size !== "kpi")) {
    widgets[piece.key] = piece.widget;
    if (piece.size === "full") {
      if (x) { y += 20; x = 0; }
      layout[piece.key] = { xTrack: 0, yTrack: y, xSpan: 24, ySpan: piece.h ?? 18 };
      y += piece.h ?? 18;
    } else {
      const span = piece.size === "half" ? 11 : 13;
      layout[piece.key] = { xTrack: x, yTrack: y, xSpan: x ? 24 - x : span, ySpan: 20 };
      x = x ? 0 : span;
      if (!x) y += 20;
    }
  }
  const filter: Page["filter"] = pick.carrier ? carrierFilter(pick.carrier)
    : pick.method ? { page: [{ field: { id: "orders.method" }, model: { operator: "equals", value: pick.method } }] } : {};
  return { page: { id, widgets, widgetLayout: layout, filter } as Page, parts: pieces.map((q) => q.label) };
}

/** The console's plan for a build: each line ticks when its tool call completes. */
export const BUILD_PLAN = [
  { key: "report", label: "New report" },
  { key: "doorstep-claim-clock", label: "Claim deadlines" },
  { key: "doorstep-failure-points", label: "What goes wrong" },
  { key: "doorstep-evidence-meter", label: "Proof on file" },
  { key: "check", label: "Check the page" },
] as const;

const LEAD = `You are Doorstep's analyst, working inside a small online seller's Insights reports.
The reports: Overview (orders, case rate, money), Carriers (claims, recovery, deadlines), Cases.
The seller may write in any language or misspell words: read what they mean, and reply in their language.
- "What's unusual", "anything wrong", "what changed": call scan_for_anomalies, then name the top
  one or two findings in plain words and offer to open the report each one points to.
- A figure or a breakdown ("net loss by carrier this month"): call answer_question with the
  seller's question. Its card shows the figure and the chart, so add nothing after it.
- "Show me Evri": call open_carrier_report. "Open the cases": call open_report.
- A page, report or dashboard about a topic ("build me a page for delivery types", "a page about
  lockers", "a money dashboard", "a page for Evri"): call build_page once with the closest topic, a
  short name in the seller's words, and the carrier or delivery method they named (lockers ->
  method Locker). It places Doorstep's widgets in one step; its result lists them.
- To chase a carrier's claims ("chase Evri", "a page to chase a carrier"): call
  build_report_template with that carrier (if none is named, the one scan_for_anomalies flags),
  then one sentence.
- Only for a page no topic covers, or widgets the seller names one by one: create_report, delegate
  to the page agent with its pageId, check_page, then one sentence naming what is on the page.
- To change how an existing chart looks (chart type, sorting, labels, title), delegate to the
  widget agent. To add, move or remove widgets on the open page, delegate to the page agent.
  Doorstep's own widgets are in its catalogue; ask it to prefer them for claims, causes and proof.
- "Explain “…”": call explain_chart with that name, then say in two sentences what it shows and
  how to read it.
- Anything about refunds, payments or contacting someone: call answer_question with the seller's
  words. It says what Doorstep can and can't do. Never promise an action.
- Anything else (weather, jokes, other shops): one sentence on what you can do instead.
Never compute or guess a number yourself. Never say a step failed unless a tool returned an error.
At most two short sentences per reply, no headings, no lists.`;

/** The tools Studio's own agents keep: everything except reading data rows. */
const withoutRows = (def: AgAnyAiAgentDefinition): AgAnyAiAgentDefinition => ({
  ...def,
  tools: def.tools ? (ctx, p) => def.tools!(ctx, p).filter((t) => t.name !== "execute_query") : undefined,
});

/** The console's starters (the words on the button, and what it sends). */
export const STARTERS: AgAiPromptStarter[] = [
  { label: "What changed in the last four weeks?", prompt: "What changed in the last four weeks? Anything I should act on?" },
  { label: "Which carrier cost me most this month?", prompt: "Which carrier cost me most this month?" },
  { label: "Make the case rate chart a bar chart", prompt: "On the Overview, make 'Case rate by delivery method' a horizontal bar chart." },
  { label: "Build a page to chase a carrier", prompt: "Build me a page to chase a carrier" },
];
/** What the brief's "Chase <carrier>" button and the console's chase starter send. */
export const chaseQuestion = (carrier: string) => `Build me a page to chase ${carrier}'s claims`;

export function doorstepHarness(api: AgStudioApi, actions: { current: AgentActions }, onTurn?: (agent: string, t: TurnOut) => void) {
  const a = () => actions.current;
  const scan: AgAiTool = api.defineAiTool({
    name: "scan_for_anomalies",
    description: "Compare the last 4 weeks with the 12 before: case rate by carrier and delivery method, weekly net loss, and carrier claims due within 7 days. Returns findings, never data rows.",
    params: (s) => s.object({}),
    execute: (_args, ctx) => {
      const scan = scanAnomalies(a().prepared());
      // The model reads plain lines; the chat panel draws the same findings as cards.
      return ctx.success(scanText(scan), scan);
    },
  });
  const ask: AgAiTool = api.defineAiTool({
    name: "answer_question",
    description: "Answer one question about the seller's orders, cases or money with a chart on the Answers page. Returns the figure, computed from the seller's rows.",
    params: (s) => s.object({ question: s.string({ description: "The seller's question, in their words" }) }),
    execute: async (args: { question: string }, ctx) => {
      let asked: Awaited<ReturnType<typeof askDoorstep>>;
      try {
        asked = await askDoorstep(a().token, String(args.question ?? "").slice(0, 300), ctx.signal);
      } catch (e) {
        if (ctx.signal?.aborted) throw e;
        // QA-08: a failed read is said, never an empty step.
        const out: Answered = { answerable: false, headline: ASK_FAILED, detail: [], reading: [], chart: false, mode: "keywords" };
        return ctx.success(ASK_FAILED, out);
      }
      // AI-03: who read the question (Claude, Gemini, or the keyword reader), for the line under the reply.
      onTurn?.("doorstep", { message: "", calls: [], mode: asked.mode === "model" ? "model" : "fallback", provider: asked.provider ?? null });
      if (!asked.plan.answerable) {
        const out: Answered = { answerable: false, headline: asked.plan.note, detail: [], reading: [], chart: false, mode: asked.mode, provider: asked.provider };
        return ctx.success(`Not answerable from the reports: ${asked.plan.note}`, out);
      }
      const result = answer(asked.plan, a().prepared());
      const widgetId = result.empty ? undefined : a().addAnswer(asked);
      const plain = (t: string) => t.replace(/\*\*/g, "");
      const text = [plain(result.headline), ...result.detail.map(plain), result.empty ? "No chart added." : "Chart added to the Answers page."].join("\n");
      const out: Answered = { answerable: true, headline: result.headline, detail: result.detail, reading: result.reading, chart: !result.empty, widgetId, mode: asked.mode, provider: asked.provider, plan: asked.plan };
      return ctx.success(text, out);
    },
  });
  const report: AgAiTool = api.defineAiTool({
    name: "open_report",
    description: "Show one of the seller's reports.",
    params: (s) => s.object({ report: s.enum(["overview", "carriers", "cases"] as const) }),
    execute: (args: { report: "overview" | "carriers" | "cases" }, ctx) => {
      a().openReport(args.report);
      return ctx.success(`Opened the ${args.report} report.`);
    },
  });
  const carrier: AgAiTool = api.defineAiTool({
    name: "open_carrier_report",
    description: "Open the full Carriers report filtered to one carrier, in a window over the page.",
    params: (s) => s.object({ carrier: s.string({ description: "The carrier's name as the reports show it, e.g. Evri" }) }),
    execute: (args: { carrier: string }, ctx) => {
      const name = String(args.carrier ?? "").trim();
      const known = new Set(tableRows(a().prepared(), "orders").map((r) => String(r.carrier)));
      const match = [...known].find((k) => k.toLowerCase() === name.toLowerCase());
      if (!match) return ctx.error(`No carrier called ${name}. Carriers: ${[...known].filter((k) => !/^Not /.test(k)).join(", ")}.`);
      a().openCarrier({ field: { id: "orders.carrier" }, operator: "equals", value: match } as AgConditionDefinition);
      return ctx.success(`Opened the ${match} report.`);
    },
  });

  const carriers = () => liveCarriers(a().prepared());
  type Ctx = { run: { agentId?: string } };
  const by = (ctx: Ctx) => ({ type: "ai" as const, agent: ctx.run.agentId ?? "doorstep" });
  /** Adds a page, opens it, and records it in Studio's history as Doorstep's change. */
  const addPage = (page: Page, name: string, label: string, ctx: Ctx) => {
    a().nameReport(page.id, name);
    const s = api.getState() as AgReportState<DoorstepRegistry>;
    api.setState({ ...s, pages: [...s.pages, page], selectedPageId: page.id } as AgReportState,
      { historyLabel: label, historyLabelVariableValues: { name }, source: by(ctx) });
  };
  const reportName = (name: unknown, carrier: string | null | undefined) =>
    String(name ?? "").replace(/\s+/g, " ").trim().slice(0, 40) || (carrier ? `${carrier} recovery` : "New report");

  const createReport: AgAiTool = api.defineAiTool({
    name: "create_report",
    description: "Create a new, empty report page, optionally filtered to one carrier, and open it. Returns its pageId: then delegate to the page agent with that pageId to add widgets.",
    params: (s) => s.object({
      name: s.string({ description: "A short name, e.g. 'Evri recovery'" }),
      carrier: s.enum(() => carriers()).nullable(),
    }),
    execute: (args: { name: string; carrier: string | null }, ctx) => {
      const name = reportName(args.name, args.carrier);
      const id = `ai-${slug(name)}-${++pageSeq}`;
      addPage({ id, widgets: {}, widgetLayout: {}, filter: carrierFilter(args.carrier) }, name, "dsHistoryNewReport", ctx);
      return ctx.success(
        `Created report '${name}'${args.carrier ? `, filtered to ${args.carrier},` : ""} and opened it. It is empty: delegate to the page agent with pageId '${id}' to add widgets.`,
        { pageId: id, name, carrier: args.carrier },
      );
    },
  });
  const checkPage: AgAiTool = api.defineAiTool({
    name: "check_page",
    description: "Check the open page for widgets that are incomplete or badly placed. Returns the issues, if any.",
    params: (s) => s.object({}),
    execute: async (_args, ctx) => {
      // Studio scores each check from 0 to 1; a style nudge (alignment 0.75) is not a broken widget.
      const report = await api.getAiContext().health.page();
      const issues = report.issues.filter((i) => i.score < 0.6);
      const minor = report.issues.filter((i) => i.score >= 0.6 && i.score < 1);
      const list = (xs: typeof issues) => xs.slice(0, 5).map((i) => i.description).join("; ");
      const text = [
        issues.length ? `${issues.length} issue${issues.length === 1 ? "" : "s"}: ${list(issues)}` : "No issues.",
        minor.length ? `Minor, no action needed: ${list(minor)}` : "",
      ].filter(Boolean).join("\n");
      return ctx.success(text, { issues: issues.length });
    },
  });
  const template: AgAiTool = api.defineAiTool({
    name: "build_report_template",
    description: "Build Doorstep's ready-made page for chasing one carrier (claims, deadlines, causes, proof) in one step, filtered to that carrier, and open it.",
    params: (s) => s.object({ carrier: s.enum(() => carriers()) }),
    execute: (args: { carrier: string }, ctx) => {
      const name = reportName(undefined, args.carrier);
      const id = `ai-${slug(name)}-${++pageSeq}`;
      addPage(templatePage(id, args.carrier), name, "dsHistoryTemplate", ctx);
      return ctx.success(`Built '${name}' from Doorstep's template and opened it.`, { pageId: id, name, carrier: args.carrier });
    },
  });
  const methods = () => liveMethods(a().prepared());
  const buildPage: AgAiTool = api.defineAiTool({
    name: "build_page",
    description: "Build a finished report page on one topic from Doorstep's own widgets in one step, optionally narrowed to one carrier or one delivery method, and open it. Topics: delivery_methods (home, locker, signature), carriers, claims (carrier claims and deadlines), cases (problems and decisions), money (refunds, recoveries, loss), proof (possession evidence), areas (postcode map). Returns the widgets it placed.",
    params: (s) => s.object({
      topic: s.enum(Object.keys(TOPICS) as [Topic, ...Topic[]]),
      name: s.string({ description: "A short page name in the seller's words, e.g. 'Lockers' or 'Delivery types'" }),
      carrier: s.enum(() => carriers()).nullable(),
      method: s.enum(() => methods()).nullable(),
    }),
    execute: (args: { topic: Topic; name?: string; carrier?: string | null; method?: string | null }, ctx) => {
      const topic = TOPICS[args.topic] ? args.topic : "delivery_methods";
      const carrier = args.carrier && carriers().includes(args.carrier) ? args.carrier : null;
      const method = !carrier && args.method && methods().includes(args.method) ? args.method : null;
      const name = String(args.name ?? "").replace(/\s+/g, " ").trim().slice(0, 40) || [carrier ?? method, TOPICS[topic].name].filter(Boolean).join(" · ");
      const id = `ai-${slug(name)}-${++pageSeq}`;
      const { page, parts } = topicPage(id, { topic, carrier, method });
      addPage(page, name, "dsHistoryNewReport", ctx);
      const only = carrier ?? method;
      return ctx.success(
        `Built '${name}'${only ? `, filtered to ${only},` : ""} with ${parts.length} widgets: ${parts.join(", ")}. It is open.`,
        { pageId: id, name, topic, carrier, method, parts },
      );
    },
  });
  const explain: AgAiTool = api.defineAiTool({
    name: "explain_chart",
    description: "Describe one widget on the open page: what it measures, how it is grouped and sorted, and its filters. Reads settings only, never data rows.",
    params: (s) => s.object({ name: s.enum(() => widgetNames(api)) }),
    execute: (args: { name: string }, ctx) => {
      const out = explainWidget(api, String(args.name ?? ""));
      return out ? ctx.success(out.text, out) : ctx.error(`No widget called ${String(args.name)} on this page.`);
    },
  });

  const routes: LocalRoutes = {
    chase: (words) => chaseCarrier(a().prepared(), words), charts: () => widgetNames(api),
    topic: (words) => pickTopic(a().prepared(), words),
  };
  const adapterFor = (agent: string) =>
    doorstepAdapter(a().token, agent, (t) => onTurn?.(agent, t), agent === "doorstep" ? routes : undefined);
  return createAiHarness(api, ({ tools, builtIn }) => ({
    agents: [
      directLlmRunner({
        id: "doorstep",
        name: "Doorstep",
        description: "Doorstep's analyst: finds what changed, answers questions with charts, and hands chart edits to Studio's agents.",
        instructions: () => LEAD,
        tools: () => [scan, ask, report, carrier, buildPage, createReport, checkPage, template, explain,
          tools.studio.viewReport(), tools.studio.viewPage(), tools.delegateTo(["widget", "page"])],
        maxTurns: 8,
        adapter: adapterFor("doorstep"),
      }),
      directLlmRunner({ ...withoutRows(builtIn.widget), maxTurns: 6, adapter: adapterFor("widget") }),
      directLlmRunner({ ...withoutRows(builtIn.page), maxTurns: 6, adapter: adapterFor("page") }),
    ],
    primary: "doorstep",
    promptStarters: STARTERS,
  }));
}
