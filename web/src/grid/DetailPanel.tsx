import { useEffect, useMemo, useState } from "react";
import { AgGridReact, type CustomDetailCellRendererProps } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";
import { ArrowUpRight, Check, Circle } from "lucide-react";
import { api, type OrderRow, type TimelineItem } from "../api";
import { FAILURE_POINT, ago, pct, riskTone } from "../format";
import { useMedia } from "../motion";
import { COUNTRY, STEPS, STEP_OF, cap, proofList, routeLabel, rowTone } from "../pages/OrdersGrid";
import { gridTheme } from "./theme";

const MAX_ROWS = 6;
const timelineTheme = gridTheme.withParams({
  rowHeight: 32, headerHeight: 30, spacing: 5, cellHorizontalPadding: 10, backgroundColor: "transparent",
  headerBackgroundColor: "transparent",
});
const tip = (p: { value?: string }) => <span title={p.value}>{p.value}</span>;
const timelineCols = (phone: boolean): ColDef<TimelineItem>[] => [
  { field: "at", headerName: "When", width: 110,     cellRenderer: (p: { value: string }) => <span title={new Date(p.value).toLocaleString()}>{ago(p.value)}</span> },
  { field: "title", headerName: "Event", flex: 1, minWidth: 140, cellRenderer: tip },
  ...(phone ? [] : [{ field: "detail", headerName: "Detail", flex: 2, minWidth: 160, cellRenderer: tip,
    cellStyle: { color: "var(--text-3)" } } as ColDef<TimelineItem>]),
];

/** The case timeline: a small grid inside the panel, fetched the first time the panel opens. */
function CaseTimeline({ caseId }: { caseId: string }) {
  const token = window.location.pathname.split("/").filter(Boolean)[1] ?? "";
  const [items, setItems] = useState<TimelineItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [all, setAll] = useState(false);
  const phone = useMedia("(max-width: 640px)");
  useEffect(() => {
    let live = true;
    api.casePage(token, caseId)
      .then((c) => { if (live) setItems([...c.timeline].sort((a, b) => b.at.localeCompare(a.at))); })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [token, caseId]);
  const cols = useMemo(() => timelineCols(phone), [phone]);
  const shown = items ? (all ? items : items.slice(0, MAX_ROWS)) : [];
  return (
    <div className="detail-timeline" data-own-click>
      <h4>Timeline</h4>
      {failed ? <p className="faint">Unavailable.</p> : !items ? (
        <div className="skeleton-lines" aria-busy="true" aria-label="Loading">
          {[0, 1, 2].map((i) => <span key={i} className="skeleton" style={{ height: 14, width: `${90 - i * 18}%` }} />)}
        </div>
      ) : items.length === 0 ? <p className="faint">None yet.</p> : (
        <>
          <div className="timeline-grid" role="region" aria-label="Timeline">
            <AgGridReact<TimelineItem>
              theme={timelineTheme} rowData={shown} columnDefs={cols} domLayout="autoHeight"
              suppressMovableColumns suppressCellFocus
              defaultColDef={{ sortable: false, suppressHeaderMenuButton: true }}
              rowHeight={32} headerHeight={30} animateRows={false}
            />
          </div>
          {items.length > MAX_ROWS && (
            <button className="btn ghost timeline-more" aria-expanded={all} onClick={() => setAll((v) => !v)}>
              {all ? "Show less" : "Show all"}
            </button>
          )}
        </>
      )}
    </div>
  );
}

/** Master/detail: the order's proof, journey, risk and case, without leaving the table. */
export default function DetailPanel(p: CustomDetailCellRendererProps<OrderRow> & { onOpen: (r: OrderRow) => void }) {
  const r = p.data;
  if (!r) return null;
  const list = proofList(r);
  const done = STEP_OF[r.state] ?? 0;
  const tone = rowTone(r);
  return (
    <div className="detail">
      <div className="detail-inner">
        <section className="detail-journey">
          <h4>Journey</h4>
          <ol className={`stepper tone-${tone}`}>
            {STEPS.map((s, i) => (
              <li key={s} className={i < done ? (i === done - 1 ? "on last" : "on") : ""}>
                <span className="dot" aria-hidden>{i < done && <Check />}</span>{s}
              </li>
            ))}
          </ol>
          <p className="faint" style={{ marginTop: 10 }}>{r.status} · updated {ago(r.last_update)}</p>
        </section>
        <section>
          <h4>Proof on file</h4>
          <ul className="checklist">
            {list.map((x) => <li key={x.name} className={x.ok ? "ok" : "miss"}>{x.ok ? <Check aria-hidden /> : <Circle aria-hidden />}{cap(x.name)}</li>)}
          </ul>
        </section>
        <section>
          <h4>Delivery risk</h4>
          {r.delivery_risk == null ? <p className="faint">Not scored.</p> : (
            <>
              <span className={`pill ${riskTone(r.delivery_risk) === "neutral" ? "plain" : riskTone(r.delivery_risk)}`}>{pct(r.delivery_risk)}</span>
              <ul className="reasons">{r.risk_reasons.slice(0, 3).map((x) => <li key={x}>{x}</li>)}</ul>
            </>
          )}
          <h4 style={{ marginTop: 14 }}>Buyer</h4>
          <p style={{ margin: 0, fontSize: 13 }}>{r.buyer_area || "—"}, {COUNTRY[r.buyer_country] ?? r.buyer_country}</p>
        </section>
        <section>
          <h4>Case</h4>
          {r.case_id ? (
            <dl className="mini-kv">
              <dt>Likely cause</dt><dd>{r.failure_point ? FAILURE_POINT[r.failure_point] ?? r.failure_point : "Being worked out"}</dd>
              <dt>Route</dt><dd>{routeLabel(r) || "—"}</dd>
              <dt>Rule</dt><dd className="mono">{r.rule_id ?? "—"}</dd>
              {r.confidence && <><dt>Confidence</dt><dd>{cap(r.confidence)}</dd></>}
            </dl>
          ) : <p className="faint">No problem reported.</p>}
          <button className="btn" style={{ marginTop: 14 }} onClick={() => p.onOpen(r)}>Open order<ArrowUpRight size={15} aria-hidden /></button>
        </section>
        {r.case_id && <CaseTimeline caseId={r.case_id} />}
      </div>
    </div>
  );
}
