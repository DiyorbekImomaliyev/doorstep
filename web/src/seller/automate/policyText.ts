/** PL-10: where each checked quote sits in the seller's policy. The check (app/ai/policy_check.py)
 *  already matched every quote word for word, ignoring case, runs of spaces and typographic
 *  quotes; this finds the same span in the original text so the page can mark it. */

const CURLY: Record<string, string> = { "‘": "'", "’": "'", "“": '"', "”": '"' };

function normalise(text: string): { norm: string; map: number[] } {
  let norm = "";
  const map: number[] = [];
  let space = false;
  for (let i = 0; i < text.length; i++) {
    const ch = CURLY[text[i]] ?? text[i];
    if (/\s/.test(ch)) {
      if (norm.length && !space) { norm += " "; map.push(i); }
      space = true;
      continue;
    }
    space = false;
    norm += ch.toLowerCase();
    map.push(i);
  }
  return { norm, map };
}

const quoteKey = (q: string) => normalise(q).norm.trim().replace(/^["' ]+|["' ]+$/g, "").replace(/\.+$/, "");

export type Span = { start: number; end: number; issue: number };

/** Non-overlapping spans, in text order; a quote that can't be found is left out. */
export function locate(policy: string, quotes: string[]): Span[] {
  const { norm, map } = normalise(policy);
  const out: Span[] = [];
  quotes.forEach((q, issue) => {
    const key = quoteKey(q);
    if (key.length < 4) return;
    const at = norm.indexOf(key);
    if (at < 0) return;
    const start = map[at];
    let end = map[at + key.length - 1] + 1;
    while (end < policy.length && /[.!?]/.test(policy[end])) end++;
    if (out.some((s) => start < s.end && end > s.start)) return;
    out.push({ start, end, issue });
  });
  return out.sort((a, b) => a.start - b.start);
}

/** Pieces of text between and inside spans, for rendering. */
export function pieces(policy: string, spans: Span[]): { text: string; issue: number | null }[] {
  const out: { text: string; issue: number | null }[] = [];
  let at = 0;
  for (const s of spans) {
    if (s.start > at) out.push({ text: policy.slice(at, s.start), issue: null });
    out.push({ text: policy.slice(s.start, s.end), issue: s.issue });
    at = s.end;
  }
  if (at < policy.length) out.push({ text: policy.slice(at), issue: null });
  return out;
}

/** Sentences in the policy: what "1 of 3 clauses" counts. */
export const clauses = (policy: string) => policy.split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean).length;
