import { useMemo, useState } from "react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import { Clock3, FlaskConical, PackageOpen, PackageX, Zap } from "lucide-react";
import type { AutopilotStatement } from "../../api";
import { FAILURE_POINT, money, when, type Tone } from "../../format";
import LedgerGrid, { ItemCell, RIGHT, StatusPill, joinMoney, sumByCurrency } from "../LedgerGrid";
import type { CurrencyLimits, Outcome, ReplayCase, RuleCard, Run } from "./automateApi";
import Chips from "../Chips";
import { Empty, Meter, Segmented, Switch } from "./parts";

export const KIND_TITLE: Record<string, string> = { CARRIER_LOST: "Lost parcels", DAMAGED_IN_TRANSIT: "Damaged in transit" };
export const kindTitle = (fp: string | null) => (fp ? KIND_TITLE[fp] ?? FAILURE_POINT[fp] ?? fp.toLowerCase().replaceAll("_", " ") : "—");
const KIND_ICON: Record<string, React.ReactNode> = { CARRIER_LOST: <PackageX />, DAMAGED_IN_TRANSIT: <PackageOpen /> };

const STATUS: Record<string, { label: string; tone: Tone }> = {
  on: { label: "On", tone: "good" },
  off: { label: "Off", tone: "neutral" },
  dismissed: { label: "Dismissed", tone: "neutral" },
  offered: { label: "Offered", tone: "accent" },
  learning: { label: "Learning", tone: "neutral" },
  idle: { label: "Not offered", tone: "neutral" },
  stopping: { label: "Turning off", tone: "warn" },
};

const time = (iso: string) => {
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : when(iso);
};

/** AP-10: one kind of case a rule may cover, its switch, its limits and what it did. */
export function RuleCardView({ c, limits, stopping, busy, onOn, onOff, onDismiss, onReplay }: {
  c: RuleCard; limits: CurrencyLimits | undefined; stopping: boolean; busy: boolean;
  onOn: () => void; onOff: () => void; onDismiss: () => void; onReplay?: () => void;
}) {
  const title = kindTitle(c.failure_point);
  const st = STATUS[stopping ? "stopping" : c.status];
  const on = c.status === "on" && !stopping;
  const max = c.max_amount_minor != null ? money(c.max_amount_minor, c.currency) : "—";
  const canToggle = (c.status === "on" && !stopping) || c.status === "offered";
  const why = c.status === "off" || c.status === "dismissed" ? "Doorstep doesn't offer a rule again once it is off"
    : c.status === "learning" ? `Offered after ${c.needed} approvals like it`
    : c.status === "idle" ? `Offered after you approve ${c.needed} like it` : undefined;
  return (
    <article className={`ax-rule ${on ? "is-on" : ""} ${c.status === "offered" ? "is-offered" : ""}`} aria-label={`${title} rule`}>
      <header className="ax-rule-head">
        <span className="ax-rule-icon" aria-hidden>{KIND_ICON[c.failure_point] ?? <Zap />}</span>
        <div className="col grow" style={{ gap: 1 }}>
          <span className="ax-rule-title">{title}</span>
          <span className="faint num">{c.status === "idle" ? `After ${c.needed} approvals` : `Up to ${max} per order`}</span>
        </div>
        <StatusPill tone={st.tone}>{st.label}</StatusPill>
        <Switch on={on} label={title} disabled={!canToggle || busy} title={why ?? (on ? "Turn off" : "Turn on")}
          onToggle={() => (on ? onOff() : onOn())} />
      </header>
      <dl className="ax-limits">
        <div title="Largest refund you approved for this kind"><dt>Per order</dt><dd className="num">{max}</dd></div>
        <div title="A rule never goes above this (money_limits_v1)"><dt>Ceiling</dt><dd className="num">{limits ? money(limits.ceiling_minor, c.currency) : "—"}</dd></div>
        <div title="At or under this, refunds are automatic anyway"><dt>Auto limit</dt><dd className="num">{limits ? money(limits.auto_approve_minor, c.currency) : "—"}</dd></div>
        <div title="All refunds per UTC day, rules included"><dt>Daily cap</dt><dd className="num">{limits ? money(limits.daily_cap_minor, c.currency) : "—"}</dd></div>
      </dl>
      {c.status === "on" || c.status === "off" ? (
        <>
          {limits && (
            <div className="ax-cap">
              <div className="ax-cap-line">
                <span>Today</span>
                <span className="num"><strong>{money(c.today_minor, c.currency)}</strong> rule · {money(limits.today_minor, c.currency)} of {money(limits.daily_cap_minor, c.currency)}</span>
              </div>
              <Meter label={`${title}: today's refunds against the daily cap`} total={limits.daily_cap_minor}
                parts={[{ value: c.today_minor, tone: "accent" }, { value: Math.max(0, limits.today_minor - c.today_minor), tone: "soft" }]} />
            </div>
          )}
          <div className="ax-stats">
            <span><span className="faint">Handled</span><strong className="num">{c.handled} · {money(c.handled_minor, c.currency)}</strong></span>
            <span><span className="faint">Last run</span><strong className="num">{c.last_run ? time(c.last_run) : "—"}</strong></span>
            <span><span className="faint">Learned from</span><strong className="num">{c.approvals}</strong></span>
          </div>
        </>
      ) : (
        <div className="ax-progress">
          <div className="ax-cap-line"><span>Approvals</span><span className="num"><strong>{Math.min(c.approvals, c.needed)}</strong> of {c.needed}</span></div>
          <Meter label={`${title}: approvals toward an offer`} total={c.needed} parts={[{ value: Math.min(c.approvals, c.needed), tone: "accent" }]} />
        </div>
      )}
      {(c.status === "offered" || onReplay) && (
        <footer className="ax-rule-foot">
          {onReplay && <button type="button" className="btn ghost sm" onClick={onReplay}><FlaskConical aria-hidden />What if</button>}
          <span className="grow" />
          {c.status === "offered" && <button type="button" className="btn ghost sm" disabled={busy} onClick={onDismiss}>Not now</button>}
          {c.status === "offered" && <button type="button" className="btn primary sm" disabled={busy} onClick={onOn}>Turn on</button>}
        </footer>
      )}
    </article>
  );
}

const RUN_STATUS: Record<string, { label: string; tone: Tone }> = {
  completed: { label: "Refunded", tone: "good" },
  simulated: { label: "Simulated", tone: "neutral" },
  queued: { label: "Processing", tone: "warn" },
  pending: { label: "Processing", tone: "warn" },
  failed: { label: "Failed", tone: "bad" },
  blocked: { label: "Blocked", tone: "bad" },
};

/** AP-11: what the rules did, newest first; a row opens its case. */
export function Runs({ runs, statement, onOpen }: { runs: Run[]; statement: AutopilotStatement; onOpen: (orderId: string) => void }) {
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
  return (
    <section className="table-card ax-runs" aria-label="Autopilot statement">
      <div className="head">
        <div className="row" style={{ gap: 10 }}>
          <h2 style={{ fontSize: 15 }}><Clock3 aria-hidden className="ax-h-icon" />What it did</h2>
          <span className="count-chip num">{runs.length}</span>
        </div>
        <span className="ax-statement num">
          {statement.totals.length === 0 ? `Last ${statement.days} days: none` : statement.totals.map((t) => (
            <span key={t.currency}>
              Last {statement.days} days: {plural(t.refunds, "automatic refund")}, {money(t.refunded_minor, t.currency)}
              {t.claimable_minor > 0 && <span className="faint"> · {money(t.claimable_minor, t.currency)} to claim back</span>}
              {t.recovered_minor > 0 && <span className="faint"> · {money(t.recovered_minor, t.currency)} recovered</span>}
            </span>
          ))}
        </span>
      </div>
      {runs.length === 0 ? (
        <Empty icon={<Clock3 />} title="No automatic refunds yet" />
      ) : (
        <ol className="ax-timeline">
          {runs.map((r) => {
            const st = RUN_STATUS[r.status] ?? { label: r.status, tone: "neutral" as Tone };
            return (
              <li key={r.case_id}>
                <button type="button" className="ax-run" onClick={() => onOpen(r.order_id)} title={`Open ${r.order_ref}`}>
                  <span className="ax-run-time num">{time(r.at)}</span>
                  <span className="ax-run-dot" aria-hidden />
                  <span className="ax-run-main">
                    <span className="ax-run-item">{r.item}</span>
                    <span className="faint num">{r.order_ref} · {kindTitle(r.failure_point)}</span>
                  </span>
                  <span className="ax-run-amt num">{money(r.amount_minor, r.currency)}</span>
                  <StatusPill tone={st.tone}>{st.label}</StatusPill>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

const OUTCOME: Record<Outcome, { label: string; tone: Tone }> = {
  rule: { label: "Rule refunds", tone: "accent" },
  auto: { label: "Automatic", tone: "neutral" },
  you: { label: "Left to you", tone: "warn" },
};

function reasonText(x: ReplayCase, max: string, limits: CurrencyLimits | undefined): string {
  switch (x.reason) {
    case "rule": return `Within ${max}`;
    case "auto": return limits && x.currency === limits.currency ? `Under ${money(limits.auto_approve_minor, x.currency)}` : "Under auto limit";
    case "over_max": return `Over ${max}`;
    case "other_kind": return "Other kind";
    case "daily_cap": return "Daily cap";
    case "held": return "Held for review";
    case "dispute": return "PayPal dispute";
    case "seller_decides": return "You decide";
    case "reship": return "Reship";
    default: return x.reason;
  }
}

type Filter = "all" | Outcome;

/** AP-12: the shop's past cases, replayed as if the rule had been on. Read-only. */
export function WhatIf({ cards, selected, onSelect, limits, onOpen }: {
  cards: RuleCard[]; selected: number; onSelect: (i: number) => void; limits: CurrencyLimits[]; onOpen: (orderId: string) => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const c = cards[selected];
  const replay = c?.replay ?? null;
  const lim = limits.find((l) => l.currency === c?.currency);
  const max = c?.max_amount_minor != null ? money(c.max_amount_minor, c.currency) : "";
  const cases = replay?.cases ?? [];
  const by = (o: Outcome) => cases.filter((x) => x.outcome === o);
  const sum = (xs: ReplayCase[]) => joinMoney(sumByCurrency(xs, (x) => x.amount_minor ?? 0));
  const shown = filter === "all" ? cases : by(filter);
  const columns = useMemo((): ColDef<ReplayCase>[] => [
    { headerName: "Item", field: "item", flex: 2, minWidth: 200,
      cellRenderer: (p: ICellRendererParams<ReplayCase>) => p.data && <ItemCell item={p.data.item} sub={p.data.order_ref} /> },
    { headerName: "Problem", colId: "fp", flex: 1.4, minWidth: 210, valueGetter: (p) => kindTitle(p.data?.failure_point ?? null), tooltipValueGetter: (p) => kindTitle(p.data?.failure_point ?? null) },
    { headerName: "Refund", field: "amount_minor", ...RIGHT, width: 110, valueFormatter: (p) => (p.data ? money(p.data.amount_minor, p.data.currency) : "") },
    { headerName: "Outcome", field: "outcome", width: 140,
      cellRenderer: (p: ICellRendererParams<ReplayCase>) => p.data && <StatusPill tone={OUTCOME[p.data.outcome].tone}>{OUTCOME[p.data.outcome].label}</StatusPill>,
      valueFormatter: (p) => (p.data ? OUTCOME[p.data.outcome].label : "") },
    { headerName: "Why", colId: "why", flex: 1, minWidth: 140, valueGetter: (p) => (p.data ? reasonText(p.data, max, lim) : "") },
    { headerName: "Decided", field: "decided_at", width: 130, valueFormatter: (p) => (p.data ? when(p.data.decided_at) : ""), sort: "desc" },
  ], [max, lim]);
  if (!c || !replay) {
    return (
      <section className="table-card ax-whatif" aria-label="What if">
        <div className="head"><h2 style={{ fontSize: 15 }}><FlaskConical aria-hidden className="ax-h-icon" />What if</h2></div>
        <Empty icon={<FlaskConical />} title="Nothing to replay yet" />
      </section>
    );
  }
  const rule = by("rule"), auto = by("auto"), you = by("you");
  const n = Math.max(1, cases.length);
  return (
    <section className="table-card ax-whatif" aria-label="What if">
      <div className="head">
        <div className="row wrap" style={{ gap: 10 }}>
          <h2 style={{ fontSize: 15 }}><FlaskConical aria-hidden className="ax-h-icon" />What if</h2>
          <span className="faint num" title="Your decided cases, run again through the same rule check the policy uses">Last {replay.window_days} days · up to {max}</span>
        </div>
        {cards.length > 1 && (
          <Segmented label="Rule to replay" value={String(selected)} onChange={(k) => { onSelect(Number(k)); setFilter("all"); }}
            items={cards.map((x, i) => ({ key: String(i), label: kindTitle(x.failure_point) + (cards.some((y, j) => j !== i && y.failure_point === x.failure_point) ? ` ${x.currency}` : "") }))} />
        )}
      </div>
      <div className="ax-wi-sum">
        <div className="ax-wi-tile is-rule">
          <span className="faint">Rule refunds</span>
          <strong className="num">{rule.length}</strong>
          <span className="num">{rule.length ? sum(rule) : "—"}</span>
        </div>
        <div className="ax-wi-tile">
          <span className="faint">Automatic anyway</span>
          <strong className="num">{auto.length}</strong>
          <span className="num">{auto.length ? sum(auto) : "—"}</span>
        </div>
        <div className="ax-wi-tile is-you">
          <span className="faint">Left to you</span>
          <strong className="num">{you.length}</strong>
          <span className="num">{you.length ? sum(you) : "—"}</span>
        </div>
        <div className="ax-wi-bar" aria-hidden>
          <span className="is-rule" style={{ width: `${(rule.length / n) * 100}%` }} />
          <span className="is-auto" style={{ width: `${(auto.length / n) * 100}%` }} />
          <span className="is-you" style={{ width: `${(you.length / n) * 100}%` }} />
        </div>
      </div>
      <div className="ax-wi-filter">
        <Chips<Filter> label="Filter replayed cases" value={filter} onChange={setFilter} items={[
          { key: "all", label: "All", count: cases.length, title: "Every replayed case" },
          { key: "rule", label: "Rule", count: rule.length, tone: OUTCOME.rule.tone, title: "The rule would have refunded these" },
          { key: "auto", label: "Automatic", count: auto.length, tone: OUTCOME.auto.tone, title: "Refunded automatically anyway, under the shop's auto limit" },
          { key: "you", label: "You", count: you.length, tone: OUTCOME.you.tone, title: "Still yours to decide" },
        ]} />
      </div>
      <LedgerGrid<ReplayCase>
        label="Replayed cases" rows={shown} columns={columns} getRowId={(r) => r.case_id}
        onOpen={(r) => onOpen(r.order_id)} empty="No cases here."
        card={(r) => (
          <>
            <span className="lc-top"><span className="lc-item">{r.item}</span><span className="lc-money num">{money(r.amount_minor, r.currency)}</span></span>
            <span className="lc-meta">{r.order_ref} · {kindTitle(r.failure_point)}</span>
            <span className="lc-bottom">
              <StatusPill tone={OUTCOME[r.outcome].tone}>{OUTCOME[r.outcome].label}</StatusPill>
              <span className="faint">{reasonText(r, max, lim)}</span>
            </span>
          </>
        )}
      />
    </section>
  );
}
