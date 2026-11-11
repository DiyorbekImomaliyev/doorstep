/**
 * S2 Ask Doorstep: one console for every question, on AG Studio's own agent harness ("Your Own UI").
 * The harness runs Doorstep's lead agent (agent.ts) and hands chart and page edits to Studio's widget
 * and page agents; this panel only renders the live session: the seller's question, each step the
 * agents took in plain words (agentDisplay.tsx), nested sub-runs, and the answer cards.
 *
 * It stays in view mode beside the report, so nothing is squeezed into Studio's edit panel. A figure
 * question becomes a chart on the Answers report; a request to move money is data, never an action.
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type ComponentType } from "react";
import type {
  AgAiChatMessage, AgAiChatMessagePart, AgAiChatSession, AgAiHarness, AgAiRunStatus, AgAiToolCallView, AgAiToolDetailParams,
  AgAiToolDisplayStatus, AgAiToolResult,
} from "ag-studio";
import { AlertTriangle, ArrowUp, Check, CheckCircle2, ChevronDown, Redo2, Square, Undo2, X } from "lucide-react";
import { ReadBy } from "../components/ai";
import type { AgAiPromptStarter } from "ag-studio";
import { BUILD_PLAN } from "./agent";
import { answeredBy, seconds, startClock, stopClock, tookMs } from "./answeredBy";
import { AnswerActionsContext, doorstepToolDisplay, traceLabel, type AnswerActions } from "./agentDisplay";

const NO_MESSAGES: ReadonlyArray<AgAiChatMessage> = [];
/** Steps whose detail opens by itself: the findings, the answer and the explanation are the point. */
const OPEN = new Set(["scan_for_anomalies", "answer_question", "explain_chart"]);
const BUILDS = new Set(["create_report", "build_report_template"]);

/** S4/S5: what the console can do with a build it made (the board's ledger). */
export type BuildActions = { has: (pageId: string) => boolean; undo: (pageId: string) => void; redo: () => void; canRedo: boolean };

/**
 * Every tool call in a turn, the delegated runs' included (Studio's page agent adds the widgets),
 * re-read as any of those sessions changes.
 */
function useTurnCalls(harness: AgAiHarness, parts: ReadonlyArray<AgAiChatMessagePart>): AgAiToolCallView[] {
  const [, bump] = useState(0);
  const calls: AgAiToolCallView[] = [];
  const subs: AgAiChatSession[] = [];
  const walk = (ps: ReadonlyArray<AgAiChatMessagePart>) => {
    for (const p of ps) {
      if (p.type !== "tool_call") continue;
      calls.push(p.toolCall);
      const s = p.toolCall.childThreadId ? harness.getSession?.(p.toolCall.childThreadId) : undefined;
      if (s && !subs.includes(s)) { subs.push(s); walk(s.messages.filter((m) => m.role === "assistant").flatMap((m) => m.parts)); }
    }
  };
  walk(parts);
  const key = subs.map((s) => s.threadId).join("|");
  useEffect(() => {
    const on = () => bump((n) => n + 1);
    harness.addEventListener("changed", on);
    subs.forEach((s) => s.addEventListener("changed", on));
    return () => { harness.removeEventListener("changed", on); subs.forEach((s) => s.removeEventListener("changed", on)); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [harness, key]);
  return calls;
}

const complete = (c: AgAiToolCallView) => c.status === "complete" && (c.result as AgAiToolResult | undefined)?.success !== false;

/** The build's plan, ticking as Doorstep and Studio's page agent finish each step. */
function PlanCard({ calls, running }: { calls: AgAiToolCallView[]; running: boolean }) {
  const template = calls.some((c) => c.name === "build_report_template" && complete(c));
  const done = (key: string) => template || calls.some((c) => complete(c) && (
    key === "report" ? c.name === "create_report" : key === "check" ? c.name === "check_page" : c.name === "add_widget" && c.args.type === key));
  const states = BUILD_PLAN.map((s) => done(s.key));
  const next = running ? states.indexOf(false) : -1;
  const n = states.filter(Boolean).length;
  return (
    <div className="ask-plan" role="group" aria-label="Plan">
      <div className="ask-plan-head"><span>Plan</span><span className="num">{n}/{BUILD_PLAN.length}</span></div>
      <ol>
        {BUILD_PLAN.map((s, i) => {
          const state = states[i] ? "done" : i === next ? "now" : running ? "todo" : "skipped";
          return (
            <li key={s.key} data-state={state}>
              <span className="ask-plan-mark" aria-hidden>{states[i] && <Check size={11} strokeWidth={3} />}</span>
              {s.label}
              {state === "done" && <span className="sr-only"> (done)</span>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** When each call was first seen running and when it finished, for the Steps list (the views carry no clock). */
function useCallClock(calls: AgAiToolCallView[]) {
  const clock = useRef(new Map<string, { start: number; end?: number }>());
  const now = performance.now();
  for (const c of calls) {
    const t = clock.current.get(c.toolCallId) ?? { start: now };
    if (!clock.current.has(c.toolCallId)) clock.current.set(c.toolCallId, t);
    if (!t.end && (c.status === "complete" || c.status === "error")) t.end = now;
  }
  return (id: string) => {
    const t = clock.current.get(id);
    // A call first seen already finished has no honest duration.
    return t?.end && t.end - t.start > 60 ? t.end - t.start : undefined;
  };
}

/** S9: under a finished reply, "Steps · n" opens to each step the agents took, in order. Labels only: no arguments, no rows. */
function Trace({ calls, took }: { calls: AgAiToolCallView[]; took: (id: string) => number | undefined }) {
  const [open, setOpen] = useState(false);
  const rows = calls.map((c) => ({ c, label: traceLabel(c.name) })).filter((r): r is { c: AgAiToolCallView; label: string } => r.label !== null);
  if (!rows.length) return null;
  const fmt = (ms: number) => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);
  return (
    <div className="ask-trace" data-testid="ask-trace">
      <button type="button" className="ask-trace-btn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <ChevronDown size={13} aria-hidden />Steps · {rows.length}
      </button>
      {open && (
        <ol className="ask-trace-list">
          {rows.map(({ c, label }) => {
            const bad = c.status === "error" || (c.result as AgAiToolResult | undefined)?.success === false;
            const ms = took(c.toolCallId);
            return (
              <li key={c.toolCallId} data-status={bad ? "warn" : "ok"}>
                {bad ? <AlertTriangle size={13} aria-label="Needs a look" /> : <CheckCircle2 size={13} aria-label="Done" />}
                <span>{label}</span>
                {ms !== undefined && <span className="num">{fmt(ms)}</span>}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

/** AS-22: "3 steps · 2.4 s · Claude" (or Gemini, or Rules when no model answered), under a finished reply. */
function AnsweredBy({ id, steps }: { id: string; steps: number }) {
  const who = answeredBy(id);
  const ms = tookMs(id);
  const bits = [steps > 0 ? `${steps} ${steps === 1 ? "step" : "steps"}` : "", ms !== undefined ? seconds(ms) : "", who ?? ""].filter(Boolean);
  if (!who) return null;
  return (
    <p className="ask-by" data-testid="ask-by" data-who={who} title={who === "Rules" ? "No model answered: Doorstep's keyword reader did" : `Answered by ${who}`}>
      {bits.join(" · ")}
    </p>
  );
}

/** A turn: the question, the plan card if it builds a page, each step, and Undo this build. */
function Turn({ id, question, parts, harness, names, busy, build }: {
  id: string; question: string; parts: ReadonlyArray<AgAiChatMessagePart>; harness: AgAiHarness; names: Names; busy: boolean; build?: BuildActions;
}) {
  const calls = useTurnCalls(harness, parts);
  const took = useCallClock(calls);
  // AS-22: the reply's own clock, from the question to the end of the run.
  useEffect(() => { if (busy) startClock(id); else stopClock(id); }, [busy, id]);
  const made = calls.find((c) => BUILDS.has(c.name));
  const pageId = made && complete(made) ? (made.result as { data?: { pageId?: string } }).data?.pageId : undefined;
  return (
    <div className="ask-turn">
      <p className="ask-q">{question}</p>
      {made && <PlanCard calls={calls} running={busy} />}
      <Parts parts={parts} harness={harness} names={names} />
      {!busy && calls.length > 0 && <Trace calls={calls} took={took} />}
      {!busy && <AnsweredBy id={id} steps={calls.filter((c) => traceLabel(c.name) !== null).length} />}
      {busy && !parts.length && <p className="ask-thinking"><span className="ins-spinner" aria-hidden />Reading your question…</p>}
      {pageId && build && !busy && (
        build.has(pageId)
          ? <div className="ask-actions"><button type="button" className="btn ghost" onClick={() => build.undo(pageId)}><Undo2 size={14} aria-hidden />Undo this build</button></div>
          : build.canRedo && <div className="ask-actions"><button type="button" className="btn ghost" onClick={build.redo}><Redo2 size={14} aria-hidden />Redo this build</button></div>
      )}
    </div>
  );
}

/** A session's messages and status, re-read whenever it changes. */
function useSession(s: AgAiChatSession | null | undefined): { messages: ReadonlyArray<AgAiChatMessage>; status: AgAiRunStatus } {
  const subscribe = useCallback((cb: () => void) => {
    if (!s) return () => {};
    s.addEventListener("changed", cb);
    return () => s.removeEventListener("changed", cb);
  }, [s]);
  const messages = useSyncExternalStore(subscribe, () => s?.messages ?? NO_MESSAGES);
  const status = useSyncExternalStore(subscribe, () => s?.status ?? "idle");
  return { messages, status };
}

/** "**£558** net loss" -> bold figure, as React nodes (no HTML from strings). */
function Bold({ text }: { text: string }) {
  return <>{text.split("**").map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : part))}</>;
}

const humanize = (name: string) => name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

type Names = (widgetId: string) => string | undefined;

/** One tool call, as a step on the rail: what it did, a short value, and what it found. */
function Step({ call, harness, names }: { call: AgAiToolCallView; harness: AgAiHarness; names: Names }) {
  const display = doorstepToolDisplay[call.name];
  const [open, setOpen] = useState(OPEN.has(call.name));
  if (display?.hidden) return null;
  const done = call.status === "complete" || call.status === "error";
  const status: AgAiToolDisplayStatus = done ? "done" : (call.status as AgAiToolDisplayStatus);
  const result = done ? (call.result as AgAiToolResult | undefined) : undefined;
  const label = display
    ? display.label({ args: call.args, status, result, fieldName: () => undefined, widgetName: names, tableName: () => undefined })
    : { text: humanize(call.name) };
  const Detail = display?.detail as ComponentType<AgAiToolDetailParams> | undefined;
  const params = { name: call.name, args: call.args, ...(done ? { status: "done" as const, result } : { status: "executing" as const, result: undefined }) } as AgAiToolDetailParams;
  return (
    <li className="ask-step" data-status={call.status === "error" || (result && !result.success) ? "error" : done ? "done" : "running"}>
      <div className="ask-step-row">
        <span className="ask-step-dot" aria-hidden />
        <span className="ask-step-text">{label.text}</span>
        {label.pill && <span className="ask-pill">{label.pill}</span>}
        {Detail && done && (
          <button type="button" className="ask-step-more" aria-expanded={open} aria-label={open ? "Hide details" : "Show details"} onClick={() => setOpen((v) => !v)}>
            <ChevronDown size={14} aria-hidden />
          </button>
        )}
      </div>
      {Detail && done && open && <div className="ask-step-detail"><Detail {...params} /></div>}
      {call.childThreadId && <SubRun harness={harness} threadId={call.childThreadId} names={names} />}
    </li>
  );
}

/** A run Doorstep handed to one of Studio's own agents, drawn with the same steps, indented. */
function SubRun({ harness, threadId, names }: { harness: AgAiHarness; threadId: string; names: Names }) {
  const subscribe = useCallback((cb: () => void) => {
    harness.addEventListener("changed", cb);
    return () => harness.removeEventListener("changed", cb);
  }, [harness]);
  const session = useSyncExternalStore(subscribe, () => harness.getSession?.(threadId));
  const { messages } = useSession(session);
  const agent = harness.getThread(threadId)?.agentId;
  const parts = messages.filter((m) => m.role === "assistant").flatMap((m) => m.parts);
  return (
    <div className="ask-sub">
      <div className="ask-sub-head">{agent === "page" ? "Page agent" : "Widget agent"} · AG Studio</div>
      {parts.length > 0 && <Parts parts={parts} harness={harness} names={names} />}
    </div>
  );
}

function Parts({ parts, harness, names }: { parts: ReadonlyArray<AgAiChatMessagePart>; harness: AgAiHarness; names: Names }) {
  const out: React.ReactNode[] = [];
  let steps: React.ReactNode[] = [];
  const flush = () => { if (steps.length) { out.push(<ol key={`s${out.length}`} className="ask-steps">{steps}</ol>); steps = []; } };
  parts.forEach((p, i) => {
    if (p.type === "tool_call") steps.push(<Step key={p.toolCall.toolCallId} call={p.toolCall} harness={harness} names={names} />);
    else if (p.type === "text" && p.text.trim()) { flush(); out.push(<p key={i} className="ask-reply"><Bold text={p.text} /></p>); }
    else if (p.type === "error") { flush(); out.push(<p key={i} className="ask-muted">{p.message}</p>); }
  });
  flush();
  return <>{out}</>;
}

/** The conversation as turns: each question the seller asked and everything said after it. */
function turnsOf(messages: ReadonlyArray<AgAiChatMessage>) {
  const turns: { id: string; question: string; parts: AgAiChatMessagePart[] }[] = [];
  for (const m of messages) {
    if (m.role === "user") turns.push({ id: m.id, question: m.parts.map((p) => (p.type === "text" ? p.text : "")).join(" "), parts: [] });
    else if (m.role === "assistant" && turns.length) turns[turns.length - 1].parts.push(...m.parts);
  }
  return turns;
}

export function AskPanel({ harness, session, onSession, pending, onPendingSent, actions, names, mode, onClose, build, starters }: {
  harness: AgAiHarness | null;
  /** The live conversation, kept by the board so closing the panel keeps it. */
  session: AgAiChatSession | null;
  onSession: (s: AgAiChatSession | null) => void;
  /** A question to send as the panel opens (the brief's "Chase Evri", a `doorstep:ask` event). */
  pending: string | null;
  onPendingSent: () => void;
  actions: AnswerActions;
  names: Names;
  /** How the lead agent's last turn was read: by the model, or by keywords. */
  mode: "model" | "fallback" | null;
  onClose: () => void;
  /** Undo or redo a page the console built (S4, S5). */
  build?: BuildActions;
  /** Three starters chosen from the seller's own rows (starters.ts). */
  starters: AgAiPromptStarter[];
}) {
  const { messages, status } = useSession(session);
  const running = status === "running";
  const [draft, setDraft] = useState("");
  const [starting, setStarting] = useState(false);
  const log = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const turns = turnsOf(messages);

  useEffect(() => { input.current?.focus(); }, []);
  // Also when the run ends: the final card's Undo this build appears then.
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" }); }, [messages, status]);

  const send = useCallback(async (text: string) => {
    const q = text.trim().replace(/\s+/g, " ");
    if (q.length < 2 || running || starting || !harness) return;
    setDraft("");
    let s = session;
    if (!s) {
      setStarting(true);
      try {
        // Named from the first words, so no turn is spent naming the thread.
        s = await harness.createThread({ agentId: "doorstep", title: q.slice(0, 60) });
        onSession(s);
      } finally { setStarting(false); }
    }
    s.sendMessage(q);
  }, [harness, session, running, starting, onSession]);

  useEffect(() => {
    if (pending && harness && !running && !starting) { onPendingSent(); void send(pending); }
  }, [pending, harness, running, starting, send, onPendingSent]);

  const newChat = () => { session?.close(); onSession(null); setDraft(""); input.current?.focus(); };
  const busy = running || starting;
  const last = turns[turns.length - 1];

  return (
    <aside className="ask" aria-label="Ask Doorstep" onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
      <div className="ask-head">
        <div className="ask-head-top">
          <h2>Ask Doorstep</h2>
          <span className="grow" />
          {turns.length > 0 && <button type="button" className="btn ghost ask-new" onClick={newChat} disabled={busy}>New chat</button>}
          <button className="btn icon-btn" aria-label="Close Ask Doorstep" onClick={onClose}><X size={16} aria-hidden /></button>
        </div>
        <p title="Figures become charts on the Answers report. Chart and page changes go to AG Studio's own agents.">
          Figures, charts and page edits.
          {mode && <> <ReadBy mode={mode === "model" ? "model" : "keywords"} /></>}
        </p>
      </div>
      {busy && <div className="ds-sweep ask-sweep" aria-hidden />}

      <AnswerActionsContext.Provider value={actions}>
        <div className="ask-log" ref={log} role="log" aria-live="polite" aria-label="Conversation">
          {turns.length === 0 && (
            <div className="ask-start">
              <p className="ask-start-title">Try one of these</p>
              {starters.map((s) => (
                <button key={s.label} className="ask-starter" onClick={() => send(s.prompt)} disabled={busy || !harness}>{s.label}</button>
              ))}
            </div>
          )}
          {harness && turns.map((t) => (
            <Turn key={t.id} id={t.id} question={t.question} parts={t.parts} harness={harness} names={names} busy={busy && t === last} build={build} />
          ))}
        </div>
      </AnswerActionsContext.Provider>

      <form className="ask-form" onSubmit={(e) => { e.preventDefault(); void send(draft); }}>
        <textarea
          ref={input}
          rows={2}
          maxLength={300}
          value={draft}
          placeholder="Ask about your orders"
          aria-label="Your question"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(draft); } }}
        />
        {running
          ? <button className="btn icon-btn" type="button" aria-label="Stop" onClick={() => session?.cancel()}><Square size={14} aria-hidden /></button>
          : <button className="btn primary icon-btn" type="submit" aria-label="Ask" disabled={busy || draft.trim().length < 2}><ArrowUp size={16} aria-hidden /></button>}
      </form>
      <p className="ask-foot">No buyer details · Can't move money</p>
    </aside>
  );
}
