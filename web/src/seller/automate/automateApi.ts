import { call, type AutopilotStatement } from "../../api";

/** A rule file by id, version and source; `title` is the source without its link. */
export type RuleFile = { id: string; version: string; source: string; title: string; url: string | null };

export type CurrencyLimits = {
  currency: string; auto_approve_minor: number; daily_cap_minor: number; ceiling_minor: number; today_minor: number;
};

export type Outcome = "rule" | "auto" | "you";
export type ReplayCase = {
  case_id: string; order_id: string; order_ref: string; item: string; failure_point: string | null;
  amount_minor: number | null; currency: string; decided_at: string; outcome: Outcome;
  reason: "rule" | "auto" | "over_max" | "other_kind" | "daily_cap" | "held" | "dispute" | "seller_decides" | "reship" | string;
};
export type Replay = { max_amount_minor: number; window_days: number; cases: ReplayCase[] };

export type CardStatus = "on" | "off" | "dismissed" | "offered" | "learning" | "idle";
export type RuleCard = {
  failure_point: string; currency: string; status: CardStatus; rule_id: string | null; max_amount_minor: number | null;
  approvals: number; needed: number; updated_at: string | null; handled: number; handled_minor: number;
  today_minor: number; last_run: string | null; replay: Replay | null;
};

export type Run = {
  case_id: string; order_id: string; order_ref: string; item: string; rule_id: string; failure_point: string | null;
  amount_minor: number; currency: string; at: string; status: string;
};

/** AP-10..12: everything the Autopilot page shows, read-only. */
export type AutopilotDesk = {
  enabled: boolean; money_limits: RuleFile; window_days: number; limits: CurrencyLimits[];
  cards: RuleCard[]; runs: Run[]; statement: AutopilotStatement;
};

/** PL-10: the seller's written policy and the rule files its check reads. */
export type PolicyDesk = { policy_text: string; region: string; rules: RuleFile[]; own_limits: boolean };

export const automateApi = {
  autopilot: (t: string) => call<AutopilotDesk>("GET", `/api/seller/${t}/automate/autopilot`),
  policy: (t: string) => call<PolicyDesk>("GET", `/api/seller/${t}/automate/policy`),
};
