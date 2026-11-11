import { useEffect, useState, type ReactNode } from "react";
import { Check, House, Lock, PenLine, ShieldCheck, Shirt, Sparkles } from "lucide-react";
import { api, type Product, type Quote } from "../api";
import { Brand, ErrorBox, Loading } from "../components/ui";
import { METHOD, money } from "../format";

const HINT: Record<string, string> = {
  home: "Left at your door or with a neighbour.",
  locker: "Collect with a code. The strongest proof you got it.",
  signature: "Someone signs for it at the door.",
};
const ICON: Record<string, ReactNode> = { home: <House size={17} />, locker: <Lock size={17} />, signature: <PenLine size={17} /> };

/** The risk model's buyer-facing line: the order's risk and why the recommended method helps.
 *  The seller-side reasons (shop history) stay in the tooltip, not in the buyer's face. */
function RiskNote({ q }: { q: Quote }) {
  const pct = Math.round(q.risk * 100);
  const why = q.reasons[0]?.split(": ").slice(1).join(": ");
  const name = q.recommended === "locker" ? "Locker pickup" : q.recommended === "signature" ? "Signed-for delivery" : "Home delivery";
  return (
    <p className="risk-note" title={[`Delivery risk ${pct}%`, ...q.reasons.slice(1)].join(" · ")}>
      <Sparkles aria-hidden />
      <span><b>{name}</b> suits this order{why ? `: ${why}.` : "."}</span>
    </p>
  );
}

export default function Shop({ sellerId }: { sellerId: string }) {
  const [country, setCountry] = useState("GB");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [method, setMethod] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // CK-07: the item and its price come from the seller's product table; the server charges that.
  const [product, setProduct] = useState<Product | null>(null);
  const currency = country === "US" ? "USD" : "GBP";
  const amount = product?.prices[currency] ?? null;

  useEffect(() => {
    api.products(sellerId)
      .then((items) => items[0] ? setProduct(items[0]) : setErr("This shop has nothing for sale."))
      .catch((e) => setErr(String(e.message)));
  }, [sellerId]);

  useEffect(() => {
    if (amount === null) return;
    setQuote(null);
    api.quote(sellerId, amount, currency, country)
      .then((q) => { setQuote(q); setMethod(q.recommended); })
      .catch((e) => setErr(String(e.message)));
  }, [sellerId, country, currency, amount]);

  async function pay() {
    if (!method || !product || amount === null) return;
    setBusy(true); setErr(null);
    try {
      const started = await api.start(sellerId, amount, currency, country, method, product.sku);
      sessionStorage.setItem("doorstep:last_buyer_url", started.buyer_url);
      window.location.assign(started.approve_url);
    } catch (e) {
      setErr(String((e as Error).message));
      setBusy(false);
    }
  }

  return (
    <>
      <Brand right={<span className="faint">Malika's Vintage · Checkout</span>} />
      <main className="narrow stack-lg">
        <section className="card row" style={{ alignItems: "center", gap: 16 }}>
          <div aria-hidden style={{ width: 64, height: 64, borderRadius: 12, background: "var(--surface-2)", display: "grid", placeItems: "center", color: "var(--text-2)" }}>
            <Shirt size={28} strokeWidth={1.5} />
          </div>
          <div className="col grow" style={{ gap: 2 }}>
            <h2>{product?.name ?? "\u00a0"}</h2>
            <span className="muted num">{amount !== null ? money(amount, currency) : "\u00a0"}</span>
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
          {quote && <RiskNote q={quote} />}
        </section>

        <section className="card col summary" aria-label="Order summary">
          <div className="sum-line"><span>{product?.name}</span><span className="num">{amount !== null && money(amount, currency)}</span></div>
          <div className="sum-line"><span>Delivery{method ? ` · ${METHOD[method] ?? method}` : ""}</span><span>Free</span></div>
          <div className="sum-line total"><span>Total</span><span className="num">{amount !== null && money(amount, currency)}</span></div>
        </section>

        {err && <ErrorBox message={err} />}
        <button className="btn paypal lg block" disabled={!method || !product || busy} onClick={pay}>
          {busy ? "Opening PayPal…" : "Pay with PayPal"}
        </button>
        <p className="footnote"><ShieldCheck aria-hidden />Sandbox only. Your address is checked before you're charged.</p>
      </main>
    </>
  );
}
