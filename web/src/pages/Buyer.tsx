import { useState } from "react";
import { api, type BuyerView } from "../api";
import { Brand, ErrorBox, Loading, Timeline, Toast, useAsync } from "../components/ui";
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

function heroFor(v: BuyerView): { icon: string; tone: string; title: string; text: string } {
  if (v.case_open) return { icon: "🛟", tone: "var(--warn-soft)", title: "We're sorting it out", text: v.case_message ?? "Your report is with the seller." };
  if (v.state === "RESOLVED") return { icon: "✅", tone: "var(--good-soft)", title: "Resolved", text: v.case_message ?? "This order is settled." };
  if (v.state === "EXCEPTION") return { icon: "⚠️", tone: "var(--warn-soft)", title: "The courier couldn't deliver", text: "Choose what happens next below." };
  if (v.state === "CONFIRMED") return { icon: "📦", tone: "var(--good-soft)", title: "Received", text: "Thanks for confirming. Enjoy your order." };
  if (v.state === "DELIVERED" || v.state === "PICKED_UP") return { icon: "🚪", tone: "var(--info-soft)", title: v.status, text: "Do you have it? One tap tells the seller." };
  if (v.state === "CREATED") return { icon: "🧾", tone: "var(--surface-2)", title: "Checking your address", text: "We check the address before you're charged." };
  return { icon: "🚚", tone: "var(--info-soft)", title: v.status, text: v.tracking_number ? `Tracking ${v.tracking_number}` : "We'll update this page as it moves." };
}

export default function Buyer({ token }: { token: string }) {
  const { data: v, error, setData } = useAsync(() => api.buyer(token), [token]);
  const [busy, setBusy] = useState(false);
  const [reporting, setReporting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

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

  if (error) return (<><Brand /><div className="narrow"><ErrorBox message="This link isn't valid or has expired. Ask the shop for a new one." /></div></>);
  if (!v) return (<><Brand /><div className="narrow col"><Loading height={160} /><Loading height={80} /></div></>);

  const hero = heroFor(v);
  const idx = stepIndex(v.progress_state || v.state);

  return (
    <>
      <Brand right={<span className="faint">Order {v.order_ref}</span>} />
      <main className="narrow stack-lg">
        <div className="col" style={{ gap: 4 }}>
          <span className="faint">Your order from {v.seller_name || "the shop"}</span>
          <h1 style={{ fontSize: 22 }}>{v.item || "Your order"}</h1>
          <span className="muted">{money(v.amount_minor, v.currency)}{v.method ? ` · ${METHOD[v.method] ?? v.method} delivery` : ""}</span>
        </div>

        <section className="card col" aria-live="polite">
          <div className="hero-status">
            <div className="hero-icon" style={{ background: hero.tone }} aria-hidden>{hero.icon}</div>
            <div className="col" style={{ gap: 2 }}>
              <h2>{hero.title}</h2>
              <p className="muted">{hero.text}</p>
            </div>
          </div>
          {idx >= 0 && (
            <div className="steps" aria-label="Delivery progress">
              {STEPS.map((s, i) => (
                <div key={s.label} className={`step ${i < idx ? "done" : ""} ${i === idx ? "now" : ""}`}>
                  <div className="bar" />
                  <span>{s.label}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        {actionError && <ErrorBox message={actionError} />}

        {v.address && v.address_issues.length > 0 && (
          <AddressFix token={token} view={v} busy={busy} onDone={() => run(async () => undefined, "Address updated")} onKeep={() => run(() => api.keepAddress(token), "We'll use the address as it is")} />
        )}

        {v.choice_offered.length > 0 && (
          <section className="card col">
            <h3>What should happen next?</h3>
            <div className="row wrap">
              {v.choice_offered.includes("rebook") && (
                <button className="btn lg grow" disabled={busy} onClick={() => run(() => api.choose(token, "rebook"), "Redelivery requested")}>Try again tomorrow</button>
              )}
              {v.choice_offered.includes("locker") && (
                <button className="btn lg grow primary" disabled={busy} onClick={() => run(() => api.choose(token, "locker"), "Sending it to a locker")}>Send to a locker</button>
              )}
            </div>
            <p className="faint">Locker and redelivery bookings are simulated in this demo.</p>
          </section>
        )}
        {v.choice_made && (
          <div className="notice info">You chose: {v.choice_made === "locker" ? "send to a locker" : "try again"}.</div>
        )}

        {(v.can_confirm || v.can_report) && !v.case_open && !reporting && (
          <section className="col">
            {v.can_confirm && (
              <button className="btn primary lg block" disabled={busy} onClick={() => run(() => api.gotIt(token), "Thanks! Marked as received")}>
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

        {reporting && (
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
          <h3>History</h3>
          <Timeline items={v.timeline} />
        </section>
        <p className="faint" style={{ textAlign: "center" }}>
          Doorstep never asks for card details. Money is handled by PayPal.
        </p>
      </main>
      <Toast message={toast} onDone={() => setToast(null)} />
    </>
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
            <span className="dot" aria-hidden />
            <span>{r.label}</span>
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
        <button className="btn primary lg" disabled={busy} onClick={() => onSend(reason, note)}>Send report</button>
      </div>
      <p className="faint">You'll get an answer here, usually within minutes. Nobody is accused automatically.</p>
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
      <div className="notice warn">{view.address_issues[0]?.message}</div>
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
