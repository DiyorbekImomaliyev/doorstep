import { createContext, useContext } from "react";
import { Info } from "lucide-react";
import type { RecoveryView } from "../../api";
import type { DeskRow, Mark } from "./claimsApi";

/** What every part of the desk shares: the latest rows, and the actions a claim allows. */
export type Desk = {
  token: string;
  now: number;
  phone: boolean;
  rows: Map<string, DeskRow>;
  busy: string | null;
  paying: string | null;
  error: { id: string; message: string } | null;
  act: (r: DeskRow, m: Mark, amountMinor?: number) => Promise<void>;
  setPaying: (id: string | null) => void;
  openCase: (r: DeskRow) => void;
  drafted: (id: string, v: RecoveryView) => void;
};

export const DeskContext = createContext<Desk | null>(null);

export function useDesk(): Desk {
  const d = useContext(DeskContext);
  if (!d) throw new Error("useDesk outside the claims desk");
  return d;
}

/** A small (i) with the longer text, on hover or keyboard focus. Inside a button it is only drawn
 * (`inButton`): the button carries the text as its title, and nothing focusable nests in it. */
export function Hint({ text, inButton }: { text: string; inButton?: boolean }) {
  if (inButton) return <span className="hint" aria-hidden data-tip={text}><Info /></span>;
  return <span className="hint" tabIndex={0} role="note" aria-label={text} data-tip={text}><Info aria-hidden /></span>;
}

/** "£" for GBP, "$" for USD: the prefix of the amount field. */
export function symbolOf(currency: string): string {
  try {
    const parts = new Intl.NumberFormat(currency === "USD" ? "en-US" : "en-GB", { style: "currency", currency }).formatToParts(0);
    return parts.find((p) => p.type === "currency")?.value ?? currency;
  } catch {
    return currency;
  }
}
