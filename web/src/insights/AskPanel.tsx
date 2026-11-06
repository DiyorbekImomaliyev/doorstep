/**
 * AS-06 Ask Doorstep: a question box beside the reports. Each answer is a sentence whose figures
 * are computed from the seller's rows, the plan it was read as, and a Studio widget on the
 * Answers report. See ask.ts (browser) and app/ai/insights_agent.py (server).
 */
import { useEffect, useRef, useState } from "react";
import { ArrowUp, BarChart3, Sparkles, Trash2, X } from "lucide-react";
import { answer, askDoorstep, type Answer, type Asked } from "./ask";
import type { Prepared } from "./data";

export const STARTERS = [
  "Which carrier cost me most this month?",
  "Where do deliveries fail most?",
  "Which delivery method has the lowest case rate?",
  "How much have carriers paid back in the last 90 days?",
  "How is my case rate trending?",
];

type Turn = {
  id: string;
  question: string;
  state: "thinking" | "done" | "failed";
  asked?: Asked;
  result?: Answer;
  at?: Date;
  widgetId?: string;
  removed?: boolean;
};

/** "**£558** net loss" -> bold figure, as React nodes (no HTML from strings). */
function Bold({ text }: { text: string }) {
  return <>{text.split("**").map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : part))}</>;
}

export function AskPanel({ token, prepared, onAnswer, onShow, onRemove, onClose }: {
  token: string;
  /** The latest rows (live updates included). */
  prepared: () => Prepared;
  /** Adds the answer's widget to Studio and returns its id. */
  onAnswer: (asked: Asked) => string;
  onShow: (widgetId: string) => void;
  onRemove: (widgetId: string) => void;
  onClose: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const busy = turns.some((t) => t.state === "thinking");
  const log = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const seq = useRef(0);

  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" }); }, [turns]);

  const update = (id: string, patch: Partial<Turn>) => setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  const ask = async (question: string) => {
    const q = question.trim().replace(/\s+/g, " ");
    if (q.length < 2 || busy) return;
    const id = `q${++seq.current}`;
    setTurns((ts) => [...ts, { id, question: q, state: "thinking" }]);
    setDraft("");
    try {
      const asked = await askDoorstep(token, q);
      if (!asked.plan.answerable) { update(id, { state: "done", asked }); return; }
      const result = answer(asked.plan, prepared());
      const widgetId = result.empty ? undefined : onAnswer(asked);
      update(id, { state: "done", asked, result, widgetId, at: new Date() });
    } catch {
      update(id, { state: "failed" });
    }
  };

  return (
    <aside className="ask" aria-label="Ask Doorstep">
      <div className="ask-head">
        <div>
          <h2><Sparkles size={16} aria-hidden />Ask Doorstep</h2>
          <p>Ask about your deliveries. Each answer becomes a chart in <strong>Answers</strong>.</p>
        </div>
        <button className="btn icon-btn" aria-label="Close Ask Doorstep" onClick={onClose}><X size={16} aria-hidden /></button>
      </div>

      <div className="ask-log" ref={log} role="log" aria-live="polite" aria-label="Conversation">
        {turns.length === 0 && (
          <div className="ask-start">
            <p className="ask-start-title">Try one of these</p>
            {STARTERS.map((s) => (
              <button key={s} className="ask-starter" onClick={() => ask(s)} disabled={busy}>{s}</button>
            ))}
          </div>
        )}
        {turns.map((t) => (
          <div key={t.id} className="ask-turn">
            <p className="ask-q">{t.question}</p>
            {t.state === "thinking" && <p className="ask-thinking"><span className="ins-spinner" aria-hidden />Reading your question…</p>}
            {t.state === "failed" && <p className="ask-a ask-muted">I couldn't reach Doorstep just now. Try again in a moment.</p>}
            {t.state === "done" && t.asked && !t.asked.plan.answerable && (
              <div className="ask-a">
                <p>{t.asked.plan.note}</p>
                <div className="ask-chips">{STARTERS.slice(0, 2).map((s) => <button key={s} className="ask-starter small" onClick={() => ask(s)} disabled={busy}>{s}</button>)}</div>
              </div>
            )}
            {t.state === "done" && t.result && (
              <div className="ask-a" data-testid="ask-answer">
                <p className="ask-headline"><Bold text={t.result.headline} /></p>
                {t.result.detail.map((d) => <p key={d} className="ask-detail">{d}</p>)}
                <div className="ask-chips" aria-label="How I read your question">
                  {t.result.reading.map((r) => <span key={r} className="ask-chip">{r}</span>)}
                </div>
                {t.widgetId && !t.removed && (
                  <div className="ask-actions">
                    <button className="btn" onClick={() => onShow(t.widgetId!)}><BarChart3 size={14} aria-hidden />View chart</button>
                    <button className="btn ghost" onClick={() => { onRemove(t.widgetId!); update(t.id, { removed: true }); }}>
                      <Trash2 size={14} aria-hidden />Remove
                    </button>
                  </div>
                )}
                {t.removed && <p className="ask-muted">Chart removed from Answers.</p>}
                <p className="ask-source">
                  {t.asked?.mode === "model" ? "Read by the model" : "Read by keywords (model busy)"}; figures from your data at{" "}
                  {t.at?.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}. The chart stays live.
                </p>
              </div>
            )}
          </div>
        ))}
      </div>

      <form className="ask-form" onSubmit={(e) => { e.preventDefault(); ask(draft); }}>
        <textarea
          ref={input}
          rows={2}
          maxLength={300}
          value={draft}
          placeholder="e.g. Which carrier cost me most this month?"
          aria-label="Your question"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(draft); } }}
        />
        <button className="btn primary icon-btn" type="submit" aria-label="Ask" disabled={busy || draft.trim().length < 2}>
          <ArrowUp size={16} aria-hidden />
        </button>
      </form>
      <p className="ask-foot">The model sees only your question and a list of measures, never your data. It can't move money.</p>
    </aside>
  );
}
