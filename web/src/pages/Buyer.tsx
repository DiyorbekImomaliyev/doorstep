import { useState, type ReactNode } from "react";
import { Bot, Check, CircleCheck, History, House, KeyRound, LifeBuoy, Lock, MapPin, Scale, ShieldCheck, TriangleAlert, Truck } from "lucide-react";
import { api, type BuyerView } from "../api";
import { Brand, ErrorBox, Loading, Timeline, Toast, useAsync, usePoll } from "../components/ui";
import { METHOD, REASONS, money } from "../format";

const STEPS = [
  { label: "Paid", states: ["PAID", "PACKED"] },
  { label: "Shipped", states: ["SHIPPED"] },
  { label: "On its way", states: ["IN_TRANSIT", "EXCEPTION"] },
  { label: "Delivered", states: ["DELIVERED", "PICKED_UP"] },
  { label: "Received", states: ["CONFIRMED"] },
];

function stepIndex(state: string): number {
  return STEPS.findIndex((s) => s.states.includes(state));
}

type Hero = { icon: ReactNode; tone: "" | "accent" | "good" | "warn"; title: string; text: string };

function heroFor(v: BuyerView): Hero {
  if (v.case_open) return { icon: <LifeBuoy />, tone: "accent", title: "We're sorting it out", text: v.case_message ?? "Your report is with the seller." };
  if (v.state === "RESOLVED") return { icon: <CircleCheck />, tone: "good", title: "Resolved", text: v.case_message ?? "This order is settled." };
  if (v.state === "EXCEPTION" && v.choice_made === "locker") return { icon: <Lock />, tone: "accent", title: "Going to a locker", text: "The courier takes it to a locker near you. This page updates when it's there." };
  if (v.state === "EXCEPTION" && v.choice_made) return { icon: <Truck />, tone: "accent", title: "Redelivery booked", text: "The courier tries again. This page updates as it moves." };
  if (v.state === "EXCEPTION") return { icon: <TriangleAlert />, tone: "warn", title: "The courier couldn't deliver", text: "Choose what happens next." };
  if (v.state === "CONFIRMED") return { icon: <CircleCheck />, tone: "good", title: "Received", text: "Thanks for confirming." };
  if (v.state === "DELIVERED" || v.state === "PICKED_UP") return { icon: <House />, tone: "accent", title: v.status, text: "Do you have it?" };
  if (v.can_pay_again) {
    return { icon: <TriangleAlert />, tone: "warn", title: "PayPal declined that payment method", text: "Nothing was charged. Choose another card, bank or your PayPal balance." };
  }
  if (v.state === "CREATED" && (v.capture_status === "FAILED" || v.capture_status === "DECLINED")) {
    return { icon: <TriangleAlert />, tone: "warn", title: "Payment didn't go through", text: "Nothing was charged. You can order again from the shop." };
  }
  if (v.state === "CREATED" && v.capture_status === "PENDING") {
    return { icon: <MapPin />, tone: "", title: "Payment processing", text: "This page updates when PayPal finishes." };
  }
  if (v.state === "CREATED") return { icon: <MapPin />, tone: "", title: "Checking your address", text: "We check the address before you're charged." };
  return { icon: <Truck />, tone: "", title: v.status, text: v.tracking_number ? `Tracking ${v.tracking_number}` : "This page updates as it moves." };
}

export default function Buyer({ token }: { token: string }) {
  const { data: v, error, status, reload, setData } = useAsync(() => api.buyer(token), [token]);
  const [busy, setBusy] = useState(false);
  const [reporting, setReporting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // The page follows the parcel and the case on its own until the order is finished.
  usePoll(reload, !!v && (v.case_open || !["CONFIRMED", "RESOLVED"].includes(v.state)), 5000);

  // CK-09: PayPal restarts the payment as a new order, so the buyer can choose another method.
  async function payAgain() {
    setBusy(true);
    setActionError(null);
    try {
      const next = await api.payAgain(token);
      sessionStorage.setItem("doorstep:last_buyer_url", next.buyer_url);
      window.location.assign(next.approve_url ?? next.buyer_url);
    } catch (e) {
      setActionError(String((e as Error).message));
      setBusy(false);
    }
  }

  async function run(fn: () => Promise<BuyerView | void>, done?: string) {
    setBusy(true);
    setActionError(null);
    try {
      const next = await fn();
      setData(next ?? (await api.buyer(token)));
      if (done) setToast(done);
    } catch (e) {
      setActionError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  }

  if (error && !v && status !== null && status < 500) {
    return (<><Brand /><div className="narrow"><ErrorBox message="This link isn't valid or has expired. Ask the shop for a new one." /></div></>);
  }
  if (error && !v) {
    // A server or network error is ours, not the buyer's: say so, and let them retry.
    return (
      <><Brand /><div className="narrow col">
        <ErrorBox message="We couldn't load your order just now. Your order and payment are safe." />
        <button className="btn" style={{ alignSelf: "flex-start" }} onClick={reload}>Try again</button>
      </div></>
    );
  }
  if (!v) return (<><Brand /><div className="narrow col"><Loading height={160} /><Loading height={80} /></div></>);

  const hero = heroFor(v);
  // The courier couldn't deliver: choosing what happens next is the one thing to do on this page.
  const deciding = v.choice_offered.length > 0 && !v.choice_made;
  const idx = stepIndex(v.progress_state || v.state);

  return (
    <>
      <Brand right={<span className="faint num">Order {v.order_ref}</span>} />
      <main className="narrow stack-lg">
        <div className="order-head">
          <span className="faint">Your order from {v.seller_name || "the shop"}</span>
          <h1>{v.item || "Your order"}</h1>
          <span className="muted num">{money(v.amount_minor, v.currency)}{v.method ? ` · ${METHOD[v.method] ?? v.method} delivery` : ""}</span>
        </div>

        <section className="card status-card" aria-live="polite">
          <div className="status-top">
            <div className={`status-icon ${hero.tone}`} aria-hidden>{hero.icon}</div>
            <div className="col" style={{ gap: 2 }}>
              <h2>{hero.title}</h2>
              <p className="muted">{hero.text}</p>
            </div>
          </div>
          {idx >= 0 && (
            <div className="progress" aria-label="Delivery progress">
              {STEPS.map((s, i) => (
                <div key={s.label} className={`seg ${i < idx ? "done" : ""} ${i === idx ? "now" : ""}`}>
                  <i />
                  <span>{s.label}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        {v.case_open && v.rights && <RightsCard text={v.rights} />}

        {v.can_pay_again && (
          <button className="btn paypal lg block" disabled={busy} onClick={payAgain}>
            {busy ? "Opening PayPal…" : "Pay another way"}
          </button>
        )}

        {actionError && <ErrorBox message={actionError} />}

        {v.address && v.address_issues.length > 0 && (
          <AddressFix token={token} view={v} busy={busy} onDone={() => run(async () => undefined, "Address updated")} onKeep={() => run(() => api.keepAddress(token), "We'll use the address as it is")} />
        )}

        {v.choice_offered.length > 0 && (
          <section className="card col">
            <h3>What should happen next?</h3>
            <div className="col" style={{ gap: 8 }}>
              {v.choice_offered.includes("locker") && (
                <button className="btn lg block primary" disabled={busy} onClick={() => run(() => api.choose(token, "locker"), "Sending it to a locker")}><Lock size={17} />Send to a locker</button>
              )}
              {v.choice_offered.includes("rebook") && (
                <button className="btn lg block" disabled={busy} onClick={() => run(() => api.choose(token, "rebook"), "Redelivery requested")}><Truck size={17} />Try again tomorrow</button>
              )}
            </div>
            <p className="faint">Locker and redelivery bookings are simulated in this demo.</p>
          </section>
        )}
        {v.choice_made && (
          <div className="notice"><Check aria-hidden /><span>You chose: {v.choice_made === "locker" ? "send to a locker" : "try again"}.</span></div>
        )}

        {(v.can_confirm || v.can_report) && !v.case_open && !reporting && !deciding && (
          <section className="col" style={{ gap: 10 }}>
            {v.can_confirm && (
              // "Got it" leads only once the courier says it was delivered; before that it is one option of two.
              <button className={`btn lg block ${v.state === "DELIVERED" || v.state === "PICKED_UP" ? "primary" : ""}`} disabled={busy} onClick={() => run(() => api.gotIt(token), "Thanks! Marked as received")}>
                Got it
              </button>
            )}
            {v.can_report && (
              <button className="btn lg block" disabled={busy} onClick={() => setReporting(true)}>
                {v.state === "CONFIRMED" ? "Report a problem" : "Not here"}
              </button>
            )}
          </section>
        )}

        {/* The poll can show the case before the report call returns (the model is still
            writing the reply); the form closes as soon as the case exists. */}
        {reporting && !v.case_open && v.state !== "RESOLVED" && (
          <Report
            busy={busy}
            onCancel={() => setReporting(false)}
            onSend={(reason, note) =>
              run(async () => {
                const r = await api.notHere(token, reason, note);
                setReporting(false);
                setToast(r.message ? "Report sent" : "Sent");
              })
            }
          />
        )}

        <section className="card col">
          <h3 className="row" style={{ gap: 8 }}><History size={16} color="var(--text-3)" aria-hidden />History</h3>
          <Timeline items={v.timeline} />
        </section>
        {v.carrier && <ProofLink token={token} />}
        {v.can_report && <AgentLink token={token} />}
        <p className="footnote"><ShieldCheck aria-hidden />Doorstep never asks for card details. PayPal handles the money.</p>
      </main>
      <Toast message={toast} onDone={() => setToast(null)} />
    </>
  );
}

/** RT-01: the matching rule file's buyer_rights text, word for word. */
export function RightsCard({ text }: { text: string }) {
  return (
    <section className="card col rights" aria-label="Your rights">
      <h3 className="rights-head"><Scale size={16} aria-hidden />Your rights</h3>
      <p>{text}</p>
      <span className="faint">Information, not legal advice.</span>
    </section>
  );
}

function Report({ busy, onCancel, onSend }: { busy: boolean; onCancel: () => void; onSend: (reason: string, note: string) => void }) {
  const [reason, setReason] = useState("not_received");
  const [note, setNote] = useState("");
  return (
    <section className="card col" aria-label="Report a problem">
      <h3>What happened?</h3>
      <div className="choices" role="radiogroup">
        {REASONS.map((r) => (
          <button key={r.value} type="button" role="radio" aria-checked={reason === r.value} className="choice" onClick={() => setReason(r.value)}>
            <span>{r.label}</span>
            <Check className="tick" size={17} aria-hidden />
          </button>
        ))}
      </div>
      <label className="field">
        Anything else? <span className="faint">(optional)</span>
        <textarea value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} placeholder="e.g. the photo shows a door that isn't mine" />
      </label>
      <div className="row">
        <button className="btn ghost" onClick={onCancel} disabled={busy}>Cancel</button>
        <div className="grow" />
        <button className="btn primary lg" disabled={busy} onClick={() => onSend(reason, note)}>{busy ? "Sending…" : "Send report"}</button>
      </div>
      <p className="faint">You'll get an answer here, usually within minutes.</p>
    </section>
  );
}

function AddressFix({ token, view, busy, onDone, onKeep }: { token: string; view: BuyerView; busy: boolean; onDone: () => void; onKeep: () => void }) {
  const a = view.address!;
  const [unit, setUnit] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const unitWord = a.country === "GB" ? "Flat" : "Apt";
  async function save() {
    setErr(null);
    const line1 = `${unitWord} ${unit.trim()}, ${a.line1}`;
    try {
      const r = await api.fixAddress(token, { ...a, line1 });
      if (!r.valid) setErr(r.issues.map((i) => i.message).join(" "));
      else onDone();
    } catch (e) {
      setErr(String((e as Error).message));
    }
  }
  return (
    <section className="card col">
      <div className="notice warn"><TriangleAlert aria-hidden /><span>{view.address_issues[0]?.message}</span></div>
      <div className="col" style={{ gap: 2 }}>
        <span className="faint">Delivering to</span>
        <span>{a.line1}, {a.city} {a.postal_code}</span>
      </div>
      <label className="field">
        {a.country === "GB" ? "Flat number" : "Apartment or unit"}
        <input inputMode="text" autoFocus value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="e.g. 4" />
      </label>
      {err && <ErrorBox message={err} />}
      <button className="btn primary lg block" disabled={busy || !unit.trim()} onClick={save}>Add {unit.trim() ? `${unitWord} ${unit.trim()}` : "number"} and pay</button>
      <button className="btn ghost block" disabled={busy} onClick={onKeep}>My address is complete — keep it</button>
    </section>
  );
}

/** MC-01: an AI assistant can report a problem for this one order, never touch others. */
/** DP-04: the buyer opens the delivery facts as a signed proof anyone can check. */
function ProofLink({ token }: { token: string }) {
  const [err, setErr] = useState<string | null>(null);
  async function open() {
    try { window.location.assign((await api.buyerProof(token)).check_url); } catch (e) { setErr(String((e as Error).message)); }
  }
  return (
    <div className="col" style={{ gap: 6 }}>
      <button className="btn" style={{ alignSelf: "flex-start" }} onClick={open}><KeyRound size={15} />Signed delivery record</button>
      {err && <ErrorBox message={err} />}
    </div>
  );
}

function AgentLink({ token }: { token: string }) {
  const [link, setLink] = useState<{ order_token: string; mcp_url: string; expires_in_days: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function get() {
    try { setLink(await api.agentLink(token)); } catch (e) { setErr(String((e as Error).message)); }
  }
  return (
    <details className="fold">
      <summary><Bot aria-hidden />Using an AI assistant?</summary>
      <div className="col">
        <p className="muted" style={{ fontSize: 13 }}>
          Let your assistant check the signed delivery record and report a problem (MCP). This order only; it can't move money.
        </p>
        {!link && <button className="btn" style={{ alignSelf: "flex-start" }} onClick={get}>Create an assistant link</button>}
        {err && <ErrorBox message={err} />}
        {link && (
          <div className="col" style={{ gap: 6 }}>
            <span className="faint">MCP server</span>
            <code className="mono" style={{ wordBreak: "break-all" }}>{link.mcp_url}</code>
            <span className="faint">Order token (valid {link.expires_in_days} days)</span>
            <code className="mono" style={{ wordBreak: "break-all" }}>{link.order_token}</code>
          </div>
        )}
      </div>
    </details>
  );
}
