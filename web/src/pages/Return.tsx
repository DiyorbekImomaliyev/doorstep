import { useEffect, useState } from "react";
import { api } from "../api";
import { CircleX, CreditCard, LoaderCircle } from "lucide-react";
import { Brand, PageState, usePageTitle } from "../components/ui";

export default function Return() {
  const params = new URLSearchParams(window.location.search);
  const ppo = params.get("token");
  const cancelled = params.get("cancelled");
  const [err, setErr] = useState<string | null>(null);
  // The checkout page this payment started from (kept by the shop before it opened PayPal).
  const shop = (() => { try { return sessionStorage.getItem("doorstep:last_shop_url"); } catch { return null; } })();
  usePageTitle(cancelled ? "Payment cancelled" : err ? "Payment not finished" : "Finishing your payment", { private: true });

  useEffect(() => {
    if (!ppo || cancelled) return;
    api.paypalReturn(ppo)
      .then((r) => window.location.replace(`/t/${r.buyer_token}`))
      .catch((e) => setErr(String(e.message)));
  }, [ppo, cancelled]);

  return (
    <>
      <Brand />
      <main className="narrow stack-lg">
        {cancelled ? (
          <PageState icon={<CreditCard />} title="Payment cancelled" text="You cancelled in PayPal. Nothing was charged."
            action={<a className="btn primary" href={shop ?? "/"}>{shop ? "Back to checkout" : "Doorstep home"}</a>} />
        ) : err ? (
          <PageState alert tone="bad" icon={<CircleX />} title="Payment not finished" text={err}
            action={<a className="btn" href="/">Doorstep home</a>} />
        ) : !ppo ? (
          <PageState icon={<CreditCard />} title="No payment to finish" text="Open your order from the link the shop sent."
            action={<a className="btn" href="/">Doorstep home</a>} />
        ) : (
          <PageState icon={<LoaderCircle className="spin" />} tone="accent" title="Checking your address…" text="Back from PayPal. Nothing is charged until the address checks out." />
        )}
      </main>
    </>
  );
}
