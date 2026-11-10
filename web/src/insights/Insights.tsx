/**
 * Control Room Insights on AG Studio (AS-01..05).
 *
 * One page that takes the best of AG's own Studio demos:
 *  - Report Builder (the base): Studio is the app. A reports list, view or edit, and a blank
 *    report the seller builds from Studio's widgets and Doorstep's own.
 *  - In-App Reporting: right-click a carrier anywhere to open that carrier's full report over the
 *    page, every widget filtered to it (drill-through).
 *  - Real-time Dashboard: the shop's changes are applied to Studio's data engine as they happen,
 *    only the rows that moved, so numbers update in place. Pause any time.
 *  - Ask Doorstep (AS-06): a question becomes a chart on the Answers report (ask.ts, AskPanel).
 *
 * Data model: data.ts. Theme: theme.ts. Custom widgets: widgets.tsx. Reports: reports.ts.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AgConditionDefinition, AgContextMenuItem, AgFilterDefinition, AgGetContextMenuParams, AgResponsiveLayoutBreakpoint,
  AgStudioApi, AgStudioProperties, AgWidgetLayoutState,
} from "ag-studio";
import { AgStudioAiModule, AgStudioLicenseManager, enableStudioDevValidations } from "ag-studio";
import { AgStudio, createWidgets } from "ag-studio-react";
import { BarChart3, Bot, Eye, FilePlus2, Pause, Pencil, Play, Sparkles, X } from "lucide-react";
import { doorstepHarness, type AgentActions } from "./agent";
import { OPEN_EVENT, doorstepToolDisplay, type OpenDetail } from "./agentDisplay";
import { ANSWERS_PAGE, withAnswer, withoutAnswer, type Asked } from "./ask";
import { AskPanel } from "./AskPanel";
import { buildSources, diffTables, fetchInsights, type Prepared } from "./data";
import { REPORTS, carrierDrillPage, reportState } from "./reports";
import { paperTheme } from "./theme";
import { doorstepWidgetTypes, type BriefContext, type DoorstepRegistry } from "./widgets";
import { OPEN_ORDER_EVENT, type OpenOrderDetail } from "../events";
import "./insights.css";

if (__AG_STUDIO_LICENSE_KEY__) AgStudioLicenseManager.setLicenseKey(__AG_STUDIO_LICENSE_KEY__);
if (import.meta.env.DEV) enableStudioDevValidations();

type Props = AgStudioProperties<DoorstepRegistry>;

/** Studio's own widgets plus Doorstep's three, filed under "Doorstep" in the widget menu. */
const widgets = createWidgets<DoorstepRegistry>({ additionalTypes: doorstepWidgetTypes });
const LOCALE = {
  widgetPanelWidgetCategoryUncategorized: "Doorstep",
  panelTitleAi: "Doorstep agent",
  aiMessageInputPlaceholder: "Ask what changed, ask for a figure, or ask to change a chart…",
};
/** AS-07: Studio's Agent Framework, on the main board only (the carrier pop-up has no agent). */
const AI_MODULES = [AgStudioAiModule];
type Mode = "view" | "edit" | "agent";
const LIVE_MS = 10_000;

// On a phone-width canvas every widget takes the full width, one under another in reading order
// (top to bottom, then left to right), keeping its authored height: a KPI card stays short, a
// chart stays tall. Wider canvases keep the layout each report was authored with.
const PHONE_CANVAS = 640;
const STACKED: AgResponsiveLayoutBreakpoint[] = [{
  maxWidth: PHONE_CANVAS,
  layout: (items, { columns }) => {
    const order = items.map((_, i) => i).sort((a, b) =>
      items[a].layout.yTrack - items[b].layout.yTrack || items[a].layout.xTrack - items[b].layout.xTrack);
    const out: AgWidgetLayoutState[] = new Array(items.length);
    let y = 0;
    for (const i of order) {
      const ySpan = Math.max(items[i].layout.ySpan, items[i].minSize.ySpan);
      out[i] = { xTrack: 0, yTrack: y, xSpan: columns, ySpan };
      y += ySpan;
    }
    return out;
  },
}];

export default function Insights({ token }: { token: string }) {
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let live = true;
    fetchInsights(token)
      .then((d) => live && setPrepared(buildSources(d, { history: d.orders.some((o) => o.simulated) })))
      .catch(() => live && setError(true));
    return () => { live = false; };
  }, [token]);

  if (error) return <><Head /><div className="ins-empty">Insights could not load. Refresh to try again.</div></>;
  if (!prepared) return <><Head /><div className="ins-empty"><span className="ins-spinner" aria-hidden />Building your reports…</div></>;
  return <Board prepared={prepared} token={token} />;
}

function Head({ children }: { children?: React.ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>Insights</h1>
      </div>
      {children}
    </div>
  );
}

/** A carrier the seller right-clicked, as a condition on the root table so it reaches every table. */
function carrierCondition(filter: AgFilterDefinition | undefined): AgConditionDefinition | undefined {
  if (!filter) return undefined;
  if ("conditions" in filter) {
    for (const c of filter.conditions) { const hit = carrierCondition(c); if (hit) return hit; }
  } else if (/\.carrier$/.test(filter.field.id) && filter.value != null) {
    return { ...filter, field: { ...filter.field, id: "orders.carrier" } };
  }
  return undefined;
}

function Board({ prepared, token }: { prepared: Prepared; token: string }) {
  const [api, setApi] = useState<AgStudioApi<DoorstepRegistry>>();
  const [reports, setReports] = useState(REPORTS);
  const [selected, setSelected] = useState(REPORTS[0].id);
  const [mode, setMode] = useState<Mode>("view");
  const editing = mode === "edit";
  const [asking, setAsking] = useState(false);
  const [live, setLive] = useState(true);
  const [synced, setSynced] = useState(() => new Date());
  const [moved, setMoved] = useState(0);
  const [drill, setDrill] = useState<AgConditionDefinition | null>(null);
  const current = useRef(prepared);
  const seq = useRef(0);
  const canvas = useRef<HTMLDivElement>(null);
  const actions = useRef<AgentActions>(null as unknown as AgentActions);

  const menu = useCallback(({ filter }: AgGetContextMenuParams): AgContextMenuItem[] => {
    const c = carrierCondition(filter);
    return c ? [{ name: `Open the ${String(c.value)} report`, action: () => setDrill(c) }] : [];
  }, []);

  // Built once: Studio reads its initial properties once and owns its state from then on.
  const PHONE = useMemo(() => window.matchMedia("(max-width: 640px)").matches, []);
  const props = useMemo((): Props => ({
    data: prepared.sources as Props["data"],
    theme: paperTheme,
    mode: "view",
    // Studio's default 720px minimum is wider than the canvas on a phone (and on a 1280 laptop, once
    // the reports rail and the filter rail take their share); lowering it lets the layout fit, and
    // the stacked breakpoint below take over on a phone, instead of drawing it off-screen.
    layout: { widgetBorderEnabled: true, minWidth: PHONE ? 300 : 600 },
    responsiveLayoutBreakpoints: STACKED,
    initialState: reportState,
    widgets,
    localeText: LOCALE,
    // On a phone the filter rail would cover the report; the report's own filters stay on it.
    panels: { edit: { right: ["filters", "edit", "data"] }, view: { right: PHONE ? [] : ["filters"] } },
    getContextMenuItems: menu,
    // S1: the brief reads the newest rows and opens a case (the Control Room's drawer) or a carrier.
    context: {
      brief: {
        latest: () => current.current,
        open: (orderId) => window.dispatchEvent(new CustomEvent<OpenOrderDetail>(OPEN_ORDER_EVENT, { detail: { orderId } })),
        drill: (carrier) => setDrill({ field: { id: "orders.carrier" }, operator: "equals", value: carrier } as AgConditionDefinition),
      },
    } satisfies BriefContext,
    // AS-07: Doorstep's lead agent and Studio's widget and page agents, every turn through the backend.
    ai: ({ api: a }) => doorstepHarness(a as unknown as AgStudioApi, actions),
    aiToolDisplay: doorstepToolDisplay,
    onApiReady: ({ api: a }) => setApi(a as AgStudioApi<DoorstepRegistry>),
    onStudioPreDestroyed: () => setApi(undefined),
  }), [prepared, menu]);

  useEffect(() => {
    if (!api) return;
    const s = api.getState();
    if (s.selectedPageId !== selected) api.setState({ ...s, selectedPageId: selected });
    // A new report opens at its top, not at the last report's scroll position.
    canvas.current?.querySelector(".ag-studio-layout")?.scrollTo({ top: 0 });
  }, [api, selected]);
  useEffect(() => {
    if (!api) return;
    // The agent works in edit mode with its panel alone, so the report keeps the width.
    api.setProperty("panels", mode === "agent"
      ? { edit: { right: ["ai"] }, view: { right: PHONE ? [] : ["filters"] } }
      : { edit: { right: ["filters", "edit", "data"] }, view: { right: PHONE ? [] : ["filters"] } });
    api.setProperty("mode", mode === "view" ? "view" : "edit");
  }, [api, mode, PHONE]);

  // Live: fetch, rebuild the same tables, apply only what changed (add, update, remove).
  useEffect(() => {
    if (!live || !api) return;
    const tick = async () => {
      const engine = api.getDataEngine();
      if (document.visibilityState !== "visible" || !engine?.applyTransaction) return;
      try {
        const next = buildSources(await fetchInsights(token), { history: current.current.history });
        const tx = diffTables(current.current.sources, next.sources);
        current.current = next;
        if (tx.length) await engine.applyTransaction(tx);
        setMoved(tx.reduce((a, t) => a + count(t.add) + count(t.update) + count(t.remove), 0));
        setSynced(new Date());
      } catch { /* keep the last good numbers on screen */ }
    };
    const t = window.setInterval(tick, LIVE_MS);
    return () => window.clearInterval(t);
  }, [live, api, token]);

  const addReport = () => {
    if (!api) return;
    const id = `mine-${++seq.current}`;
    const s = api.getState();
    api.setState({ ...s, pages: [...s.pages, { id, widgets: {}, widgetLayout: {}, filter: {} }], selectedPageId: id });
    setReports((r) => [...r, { id, name: `My report ${seq.current}`, hint: "Your own report: add widgets from the panel" }]);
    setSelected(id);
    setMode("edit");
  };

  // AS-06: each answer is a widget at the top of the Answers report, which appears on the first.
  const answers = useRef(0);
  const addAnswer = useCallback((asked: Asked) => {
    const id = `answer-${++answers.current}`;
    if (!api) return id;
    const s = api.getState();
    api.setState({ ...s, pages: withAnswer(s.pages, id, asked.plan, asked.question), selectedPageId: ANSWERS_PAGE });
    setReports((r) => (r.some((x) => x.id === ANSWERS_PAGE)
      ? r : [...r, { id: ANSWERS_PAGE, name: "Answers", hint: "Charts from your questions to Ask Doorstep, newest first" }]));
    setSelected(ANSWERS_PAGE);
    canvas.current?.querySelector(".ag-studio-layout")?.scrollTo({ top: 0 });
    return id;
  }, [api]);
  const removeAnswer = useCallback((id: string) => {
    if (!api) return;
    const s = api.getState();
    api.setState({ ...s, pages: withoutAnswer(s.pages, id) });
  }, [api]);
  const showAnswers = useCallback(() => setSelected(ANSWERS_PAGE), []);
  const latest = useCallback(() => current.current, []);
  // The agent's tools read the newest rows and act on this board through one ref, since Studio
  // builds the harness once.
  // A finding card's button in the agent panel opens the report (or the carrier's report).
  useEffect(() => {
    const onOpen = (e: Event) => {
      const d = (e as CustomEvent<OpenDetail>).detail;
      if (d.carrier && d.report === "carriers") setDrill({ field: { id: "orders.carrier" }, operator: "equals", value: d.carrier } as AgConditionDefinition);
      else setSelected(d.report);
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_EVENT, onOpen);
  }, []);
  actions.current = {
    token, prepared: latest, addAnswer,
    openReport: (id) => setSelected(id),
    openCarrier: (c) => setDrill(c),
  };

  return (
    <>
      <Head>
        <div className="ins-bar">
          <span className="ins-live" data-live={live} role="status" title={`Checks for changes every ${LIVE_MS / 1000} seconds while this tab is open`}>
            {live ? "Live" : "Paused"}
            <span className="ins-sync">· updated {synced.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}{moved ? `, ${moved} row${moved === 1 ? "" : "s"} changed` : ""}</span>
          </span>
          <button className="btn icon-btn" onClick={() => setLive((v) => !v)} aria-label={live ? "Pause live updates" : "Resume live updates"} title={live ? "Pause live updates" : "Resume live updates"}>
            {live ? <Pause size={15} aria-hidden /> : <Play size={15} aria-hidden />}
          </button>
          <button className={`btn ask-toggle ${asking ? "on" : ""}`} onClick={() => setAsking((a) => !a)} aria-pressed={asking}>
            <Sparkles size={15} aria-hidden />Ask Doorstep
          </button>
          <button className={`btn ask-toggle ${mode === "agent" ? "on" : ""}`} onClick={() => setMode((m) => (m === "agent" ? "view" : "agent"))} aria-pressed={mode === "agent"}
            title="Doorstep's agent finds what changed and edits charts with AG Studio's own agents">
            <Bot size={15} aria-hidden />Agent
          </button>
          <button className={`btn ins-edit ${editing ? "primary" : ""}`} onClick={() => setMode((m) => (m === "edit" ? "view" : "edit"))} aria-pressed={editing}>
            {editing ? <><Eye size={15} aria-hidden />Done editing</> : <><Pencil size={15} aria-hidden />Edit report</>}
          </button>
        </div>
      </Head>

      <div className="ins">
        <div className="rb" data-ask={asking || undefined}>
          <nav className="rb-side" aria-label="Reports">
            <div className="rb-label">Reports</div>
            {reports.map((r) => (
              <button key={r.id} className="rb-item" aria-current={selected === r.id || undefined} onClick={() => setSelected(r.id)}>
                <BarChart3 size={15} aria-hidden />
                {/* The question each report answers: shown, but not part of the button's name. */}
                <span className="rb-text"><span>{r.name}</span>{r.hint && <span className="rb-hint" aria-hidden>{r.hint}</span>}</span>
              </button>
            ))}
            <button className="rb-new" onClick={addReport}><FilePlus2 size={15} aria-hidden />New report</button>
            <div className="rb-foot">
              <ul className="rb-tips">
                <li><strong>Click</strong> a chart to filter the page</li>
                <li><strong>Right-click</strong> a carrier for its report</li>
                <li><strong>Edit</strong> to move, resize or add widgets</li>
              </ul>
            </div>
          </nav>
          <div className="rb-canvas" ref={canvas}><AgStudio<DoorstepRegistry> studioProperties={props} modules={AI_MODULES} className="studio-fill" /></div>
          {asking && (
            <AskPanel token={token} prepared={latest} onAnswer={addAnswer} onShow={showAnswers} onRemove={removeAnswer} onClose={() => setAsking(false)} />
          )}
        </div>
        <p className="ins-note" title={`Money is in ${prepared.home}. An order in another currency counts as an order but never adds to a money total.${prepared.history ? " Use the Data filter to see only your orders." : ""}`}>
          {prepared.history
            ? <><strong>{prepared.live} demo orders</strong> + 26 weeks of simulated history · {prepared.home}</>
            : <>PayPal sandbox orders · {prepared.home}</>}
        </p>
      </div>
      {drill && <CarrierReport prepared={current.current} filter={drill} onClose={() => setDrill(null)} />}
    </>
  );
}

const count = (x: unknown) => (Array.isArray(x) ? x.length : 0);

/** In-App Reporting's drill-through: the full Carriers report, filtered to one carrier. */
function CarrierReport({ prepared, filter, onClose }: { prepared: Prepared; filter: AgConditionDefinition; onClose: () => void }) {
  const ref = useRef<AgStudio<DoorstepRegistry>>(null);
  const [props] = useState((): Props => ({
    mode: "view",
    theme: paperTheme,
    widgets,
    localeText: LOCALE,
    panels: { view: { right: [] }, edit: { right: ["filters", "edit", "data"] } },
    data: prepared.sources as Props["data"],
    initialState: { pages: [carrierDrillPage], selectedPageId: carrierDrillPage.id },
    drillThrough: { filter },
  }));
  // Point the open report at another carrier without remounting it. Skipped on mount: Studio
  // reads the first filter from its initial properties, before its schema exists.
  const shown = useRef(filter);
  useEffect(() => {
    const api = ref.current?.api;
    if (!api || shown.current === filter) return;
    shown.current = filter;
    api.setProperty("drillThrough", { filter });
    api.setState({ pages: [carrierDrillPage], selectedPageId: carrierDrillPage.id });
  }, [filter]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="ia-pop" role="dialog" aria-modal aria-label={`${String(filter.value)} report`} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="ia-pop-card">
        <div className="ia-pop-head">
          <div><div className="ia-eyebrow">Carrier report</div><h3>{String(filter.value)}</h3></div>
          <button className="btn icon-btn" aria-label="Close" onClick={onClose}><X size={16} aria-hidden /></button>
        </div>
        <div className="ia-pop-body"><AgStudio<DoorstepRegistry> ref={ref} studioProperties={props} className="studio-fill" /></div>
      </div>
    </div>
  );
}
