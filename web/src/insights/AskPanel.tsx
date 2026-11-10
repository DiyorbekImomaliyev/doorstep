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
import { ArrowUp, ChevronDown, Square, X } from "lucide-react";
import { ReadBy } from "../components/ai";
import { STARTERS } from "./agent";
import { AnswerActionsContext, doorstepToolDisplay, type AnswerActions } from "./agentDisplay";

const NO_MESSAGES: ReadonlyArray<AgAiChatMessage> = [];
/** Steps whose detail opens by itself: the findings and the answer are the point of the turn. */
const OPEN = new Set(["scan_for_anomalies", "answer_question"]);

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

export function AskPanel({ harness, session, onSession, pending, onPendingSent, actions, names, mode, onClose }: {
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
}) {
  const { messages, status } = useSession(session);
  const running = status === "running";
  const [draft, setDraft] = useState("");
  const [starting, setStarting] = useState(false);
  const log = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const turns = turnsOf(messages);

  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" }); }, [messages]);

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
        <p>
          Figures become charts on <strong>Answers</strong>. Chart and page changes go to AG Studio's agents.
          {mode && <> <ReadBy mode={mode === "model" ? "model" : "keywords"} />.</>}
        </p>
      </div>
      {busy && <div className="ds-sweep ask-sweep" aria-hidden />}

      <AnswerActionsContext.Provider value={actions}>
        <div className="ask-log" ref={log} role="log" aria-live="polite" aria-label="Conversation">
          {turns.length === 0 && (
            <div className="ask-start">
              <p className="ask-start-title">Try one of these</p>
              {STARTERS.map((s) => (
                <button key={s.label} className="ask-starter" onClick={() => send(s.prompt)} disabled={busy || !harness}>{s.label}</button>
              ))}
              <p className="ask-start-more">Or ask for a figure, a breakdown, or a change to any chart on the page.</p>
            </div>
          )}
          {turns.map((t) => (
            <div key={t.id} className="ask-turn">
              <p className="ask-q">{t.question}</p>
              {harness && <Parts parts={t.parts} harness={harness} names={names} />}
              {busy && t === last && !t.parts.length && <p className="ask-thinking"><span className="ins-spinner" aria-hidden />Reading your question…</p>}
            </div>
          ))}
        </div>
      </AnswerActionsContext.Provider>

      <form className="ask-form" onSubmit={(e) => { e.preventDefault(); void send(draft); }}>
        <textarea
          ref={input}
          rows={2}
          maxLength={300}
          value={draft}
          placeholder="e.g. Which carrier cost me most this month?"
          aria-label="Your question"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(draft); } }}
        />
        {running
          ? <button className="btn icon-btn" type="button" aria-label="Stop" onClick={() => session?.cancel()}><Square size={14} aria-hidden /></button>
          : <button className="btn primary icon-btn" type="submit" aria-label="Ask" disabled={busy || draft.trim().length < 2}><ArrowUp size={16} aria-hidden /></button>}
      </form>
      <p className="ask-foot">Sees chart settings and figures, never buyer details. Can't move money.</p>
    </aside>
  );
}
