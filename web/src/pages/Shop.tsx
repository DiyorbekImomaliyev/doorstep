import { useEffect, useState, type ReactNode } from "react";
import { Check, House, Lock, PenLine, ShieldCheck, Shirt } from "lucide-react";
import { api, type Quote } from "../api";
import { Brand, ErrorBox, Loading } from "../components/ui";
import { METHOD, money } from "../format";

const PRODUCT = { name: "Black jacket, size M", amount: 6000, currency: "GBP" };
const HINT: Record<string, string> = {
  home: "Left at your door or with a neighbour.",
  locker: "Collect with a code. The strongest proof you got it.",
  signature: "Someone signs for it at the door.",
};
const ICON: Record<string, ReactNode> = { home: <House size={17} />, locker: <Lock size={17} />, signature: <PenLine size={17} /> };

export default function Shop({ sellerId }: { sellerId: string }) {
  const [country, setCountry] = useState("GB");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [method, setMethod] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const currency = country === "US" ? "USD" : "GBP";

  useEffect(() => {
    setQuote(null);
    api.quote(sellerId, PRODUCT.amount, currency, country)
      .then((q) => { setQuote(q); setMethod(q.recommended); })
      .catch((e) => setErr(String(e.message)));
  }, [sellerId, country, currency]);

  async function pay() {
    if (!method) return;
    setBusy(true); setErr(null);
    try {
      const started = await api.start(sellerId, PRODUCT.amount, currency, country, method, PRODUCT.name);
      sessionStorage.setItem("doorstep:last_buyer_url", started.buyer_url);
      window.location.assign(started.approve_url);
    } catch (e) {
      setErr(String((e as Error).message));
      setBusy(false);
    }
  }

  return (
    <>
      <Brand right={<span className="faint">Maria's Vintage · Checkout</span>} />
      <main className="narrow stack-lg">
        <section className="card row" style={{ alignItems: "center", gap: 16 }}>
          <div aria-hidden style={{ width: 64, height: 64, borderRadius: 12, background: "var(--surface-2)", display: "grid", placeItems: "center", color: "var(--text-2)" }}>
            <Shirt size={28} strokeWidth={1.5} />
          </div>
          <div className="col grow" style={{ gap: 2 }}>
            <h2>{PRODUCT.name}</h2>
            <span className="muted num">{money(PRODUCT.amount, currency)}</span>
          </div>
          <select aria-label="Ship to" value={country} onChange={(e) => setCountry(e.target.value)}>
            <option value="GB">Ship to UK</option>
            <option value="US">Ship to US</option>
          </select>
        </section>

        <section className="card col">
          <h3>Delivery</h3>
          {!quote && !err && <Loading height={150} />}
          {quote && (
            <div className="choices" role="radiogroup" aria-label="Delivery method">
              {quote.options.map((o) => (
                <button key={o.method} type="button" role="radio" aria-checked={method === o.method} className="choice" onClick={() => setMethod(o.method)}>
                  <span className="choice-icon" aria-hidden>{ICON[o.method]}</span>
                  <span className="col grow" style={{ gap: 1 }}>
                    <span className="row" style={{ gap: 8 }}>
                      <strong style={{ fontWeight: 600 }}>{METHOD[o.method] ?? o.label}</strong>
                      {quote.recommended === o.method && <span className="badge accent">Recommended</span>}
                    </span>
                    <span className="faint">{HINT[o.method]}</span>
                  </span>
                  <span className="faint">Free</span>
                  <Check className="tick" size={17} aria-hidden style={{ marginLeft: 0 }} />
                </button>
              ))}
            </div>
          )}
          {quote && quote.reasons.length > 0 && <p className="faint">Why: {quote.reasons[quote.reasons.length - 1]}</p>}
        </section>

        {err && <ErrorBox message={err} />}
        <button className="btn paypal lg block" disabled={!method || busy} onClick={pay}>
          {busy ? "Opening PayPal…" : "Pay with PayPal"}
        </button>
        <p className="footnote"><ShieldCheck aria-hidden />Sandbox only. Your address is checked before you're charged.</p>
      </main>
    </>
  );
}
