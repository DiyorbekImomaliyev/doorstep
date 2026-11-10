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
  AgAiConversationItem, AgAiEvent, AgAiOutputItem, AgAiTool, AgAnyAiAgentDefinition, AgConditionDefinition, AgLlmAdapter,
  AgLlmRequest, AgLlmResponse, AgStudioApi,
} from "ag-studio";
import { createAiHarness, directLlmRunner } from "ag-studio";
import { answer, askDoorstep, type Asked } from "./ask";
import type { Prepared } from "./data";

// ---------------------------------------------------------------- the adapter

type Item = { kind: "user" | "assistant" | "call" | "result" | "context"; text: string; name?: string; call_id?: string };
type TurnOut = { message: string; calls: { call_id: string; name: string; arguments: Record<string, unknown> }[]; mode: "model" | "fallback" };

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

export function doorstepAdapter(token: string, agent: string, onTurn?: (t: TurnOut) => void): AgLlmAdapter {
  return {
    executeTurn(request, options) {
      const signal = options?.signal;
      const turn: Promise<TurnOut> = fetch(`/api/seller/${encodeURIComponent(token)}/insights/agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent,
          instructions: request.instructions ?? "",
          items: toItems(request.input),
          tools: (request.tools ?? []).map((t) => ({ name: t.name, description: t.description ?? "", parameters: t.parameters ?? {} })),
          tool_choice: choiceOf(request.toolChoice),
          response_schema: request.responseFormat.type === "json" ? request.responseFormat.schema : null,
        }),
        signal,
      }).then(async (r) => (r.ok ? ((await r.json()) as TurnOut) : { message: OFFLINE, calls: [], mode: "fallback" as const }))
        .catch((e: unknown) => {
          if (signal?.aborted) throw e;
          return { message: OFFLINE, calls: [], mode: "fallback" as const };
        });
      turn.then((t) => onTurn?.(t)).catch(() => undefined);

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

export type AgentActions = {
  token: string;
  prepared: () => Prepared;
  addAnswer: (asked: Asked) => string;
  openReport: (id: "overview" | "carriers" | "cases") => void;
  openCarrier: (condition: AgConditionDefinition) => void;
};

const LEAD = `You are Doorstep's analyst, working inside a small online seller's Insights reports.
The reports: Overview (orders, case rate, money), Carriers (claims, recovery, deadlines), Cases.
- "What's unusual", "anything wrong", "what changed": call scan_for_anomalies, then explain the
  top findings in plain words and offer to open the report each one points to.
- A figure or a breakdown ("net loss by carrier this month"): call answer_question with the
  seller's question. It adds a chart to the Answers page and returns the figure: quote it.
- "Show me Evri": call open_carrier_report. "Open the cases": call open_report.
- To change how an existing chart looks (chart type, sorting, labels, title), delegate to the
  widget agent. To add, move or remove widgets or filters on a page, delegate to the page agent.
Never compute or guess a number yourself. Two or three short sentences per reply.`;

/** The tools Studio's own agents keep: everything except reading data rows. */
const withoutRows = (def: AgAnyAiAgentDefinition): AgAnyAiAgentDefinition => ({
  ...def,
  tools: def.tools ? (ctx, p) => def.tools!(ctx, p).filter((t) => t.name !== "execute_query") : undefined,
});

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
      const asked = await askDoorstep(a().token, String(args.question ?? "").slice(0, 300), ctx.signal);
      if (!asked.plan.answerable) return ctx.success(`Not answerable from the reports: ${asked.plan.note}`, { headline: asked.plan.note, detail: [], chart: false });
      const result = answer(asked.plan, a().prepared());
      if (!result.empty) a().addAnswer(asked);
      const plain = (t: string) => t.replace(/\*\*/g, "");
      const text = [plain(result.headline), ...result.detail.map(plain), result.empty ? "No chart added." : "Chart added to the Answers page."].join("\n");
      return ctx.success(text, { headline: result.headline, detail: result.detail, chart: !result.empty });
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

  const adapterFor = (agent: string) => doorstepAdapter(a().token, agent, (t) => onTurn?.(agent, t));
  return createAiHarness(api, ({ tools, builtIn }) => ({
    agents: [
      directLlmRunner({
        id: "doorstep",
        name: "Doorstep",
        description: "Doorstep's analyst: finds what changed, answers questions with charts, and hands chart edits to Studio's agents.",
        instructions: () => LEAD,
        tools: () => [scan, ask, report, carrier, tools.studio.viewReport(), tools.studio.viewPage(), tools.delegateTo(["widget", "page"])],
        maxTurns: 8,
        adapter: adapterFor("doorstep"),
      }),
      directLlmRunner({ ...withoutRows(builtIn.widget), maxTurns: 6, adapter: adapterFor("widget") }),
      directLlmRunner({ ...withoutRows(builtIn.page), maxTurns: 6, adapter: adapterFor("page") }),
    ],
    primary: "doorstep",
    promptStarters: [
      { label: "What changed in the last four weeks?", prompt: "What changed in the last four weeks? Anything I should act on?" },
      { label: "Which carrier cost me most this quarter?", prompt: "Which carrier cost me the most net delivery loss in the last 90 days?" },
      { label: "Make the case rate chart a bar chart", prompt: "On the Overview, make 'Case rate by delivery method' a horizontal bar chart." },
    ],
  }));
}
