/**
 * AS-22: who answered each reply in Ask Doorstep, and how long it took, for the quiet line under it
 * ("3 steps · 2.4 s · Claude"). The provider comes from the backend with each turn (the model
 * adapter says which one answered); "Rules" means no model did and Doorstep's keyword reader or
 * a template answered. Kept by the seller's question (its message id), so it survives closing and
 * reopening the console. Never stores a question or an answer, only a name and a duration.
 */
type Turn = { mode: "model" | "fallback"; provider?: string | null };
type Note = { provider?: string | null; model: boolean };

const NAMES: Record<string, string> = { anthropic: "Claude", claude: "Claude", gemini: "Gemini", google: "Gemini" };
const noted = new Map<string, Note>();
const clocks = new Map<string, { start: number; end?: number }>();

/** One more model turn (or a rules turn) in the reply to this question. A model turn beats a rules one. */
export function noteTurn(id: string, t: Turn): void {
  const was = noted.get(id);
  if (t.mode === "model") noted.set(id, { model: true, provider: t.provider ?? was?.provider });
  else if (!was) noted.set(id, { model: false });
}

/** "Claude", "Gemini", "AI" (a model that did not say which), "Rules", or nothing yet. */
export function answeredBy(id: string): string | undefined {
  const n = noted.get(id);
  if (!n) return undefined;
  if (!n.model) return "Rules";
  return NAMES[(n.provider ?? "").toLowerCase()] ?? "AI";
}

export function startClock(id: string): void {
  if (!clocks.has(id)) clocks.set(id, { start: performance.now() });
}
export function stopClock(id: string): void {
  const c = clocks.get(id);
  if (c && c.end === undefined) c.end = performance.now();
}
/** Milliseconds from the question to the finished reply, if this tab watched it happen. */
export function tookMs(id: string): number | undefined {
  const c = clocks.get(id);
  return c?.end !== undefined ? c.end - c.start : undefined;
}

/** "2.4 s", or "850 ms" under a second. */
export const seconds = (ms: number) => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);
