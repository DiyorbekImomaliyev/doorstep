import { CircleCheck, CircleX, FileCheck2, Info } from "lucide-react";
import { api } from "../api";
import { Badge, Brand, ErrorBox, Loading, useAsync } from "../components/ui";

export default function Verify({ caseId }: { caseId: string }) {
  const { data: r, error } = useAsync(() => api.verify(caseId), [caseId]);
  return (
    <>
      <Brand right={<span className="faint">Public receipt check</span>} />
      <main className="narrow stack-lg">
        <div className="order-head">
          <span className="faint row" style={{ gap: 6 }}><FileCheck2 size={14} />Signed receipts</span>
          <h1>Is this case's record intact?</h1>
          <p className="muted">Every money decision gets an Ed25519-signed receipt, chained to the one before it. Changing any stored receipt breaks the chain.</p>
        </div>
        {error && <ErrorBox message={error} />}
        {!r && !error && <Loading height={120} />}
        {r && (
          <section className="card col">
            {r.status === "ok" && <div className="notice good"><CircleCheck aria-hidden /><span><strong>Verified.</strong>&nbsp;{r.checked} receipts recomputed; every hash and signature matches.</span></div>}
            {r.status === "broken" && <div className="notice bad"><CircleX aria-hidden /><span><strong>Tampered.</strong>&nbsp;Receipt #{r.first_broken_seq}: {r.reason}.</span></div>}
            {r.status === "not_run" && <div className="notice"><Info aria-hidden /><span><strong>Not checked.</strong>&nbsp;{r.reason}.</span></div>}
            {r.receipts.length > 0 && (
              <ol className="checks" style={{ listStyle: "none" }}>
                {r.receipts.map((x) => (
                  <li key={x.seq} style={{ gridTemplateColumns: "20px 1fr auto" }}>
                    {x.valid ? <CircleCheck className="r-supports" aria-hidden /> : <CircleX className="r-contradicts" aria-hidden />}
                    <span>#{x.seq} · {x.kind} <span className="mono faint">{x.hash.slice(0, 20)}…</span></span>
                    <Badge tone={x.valid ? "good" : "bad"}>{x.valid ? "valid" : "broken"}</Badge>
                  </li>
                ))}
              </ol>
            )}
          </section>
        )}
      </main>
    </>
  );
}
