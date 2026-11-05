import { useEffect, useState } from "react";
import { api } from "../api";
import { Info } from "lucide-react";
import { Brand, ErrorBox, Loading } from "../components/ui";

export default function Return() {
  const params = new URLSearchParams(window.location.search);
  const ppo = params.get("token");
  const cancelled = params.get("cancelled");
  const [err, setErr] = useState<string | null>(null);

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
          <div className="notice"><Info aria-hidden /><span>You cancelled in PayPal. Nothing was charged.</span></div>
        ) : err ? (
          <ErrorBox message={err} />
        ) : (
          <div className="col"><h2>Checking your address…</h2><Loading height={80} /></div>
        )}
      </main>
    </>
  );
}
