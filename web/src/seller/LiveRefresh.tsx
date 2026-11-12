import { RefreshCw } from "lucide-react";

/** The page head's live status and Refresh, the same on every seller page: a pulsing dot, "Live"
 *  (or what the page is following), the time of the last load, and one icon button. */
export default function LiveRefresh({ text = "Live", title, updated, onRefresh, label = "Refresh", busy = false }: {
  text?: string; title: string; updated: Date | null; onRefresh: () => void; label?: string; busy?: boolean;
}) {
  return (
    <>
      <span className="live" title={title}>
        <span className="live-dot" aria-hidden />{text}{updated ? ` · ${updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}
      </span>
      <button type="button" className="btn icon-btn" onClick={onRefresh} aria-label={label} title="Refresh now" disabled={busy}>
        <RefreshCw size={15} aria-hidden />
      </button>
    </>
  );
}
