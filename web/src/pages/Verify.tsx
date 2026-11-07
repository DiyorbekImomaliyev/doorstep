import { CircleCheck, CircleX, FileCheck2, Info } from "lucide-react";
import { api } from "../api";
import { Badge, Brand, ErrorBox, Loading, useAsync } from "../components/ui";

const KIND: Record<string, string> = {
  decision: "Decision", approval: "Approval", settlement: "Settled", refund: "Refund", evidence: "Dispute evidence",
  "send-evidence": "Evidence approved", "make-offer": "Refund offer approved", offer: "Refund offered in the PayPal dispute",
};
const kindLabel = (k: string) => KIND[k] ?? k.charAt(0).toUpperCase() + k.slice(1);

export default function Verify({ caseId }: { caseId: string }) {
  const { data: r, error } = useAsync(() => api.verify(caseId), [caseId]);
  return (
    <>
      <Brand right={<span className="faint">Public receipt check</span>} />
      <main className="narrow stack-lg">
        <div className="order-head">
          <span className="faint row" style={{ gap: 6 }}><FileCheck2 size={14} />Signed receipts</span>
          <h1>Is this case's record intact?</h1>
          <p className="muted">Each money decision has an Ed25519-signed receipt, chained to the last. Any change breaks the chain.</p>
        </div>
        {error && <ErrorBox message={error} />}
        {!r && !error && <Loading height={120} />}
        {r && (
          <section className="card col">
            {r.status === "ok" && <div className="notice good"><CircleCheck aria-hidden /><span><strong>Verified.</strong>&nbsp;This case's {r.receipts.length} receipt{r.receipts.length === 1 ? "" : "s"} {r.receipts.length === 1 ? "is" : "are"} intact. To prove it, all {r.checked.toLocaleString()} receipts in the shared chain up to this case were recomputed: every hash, link and signature matches.</span></div>}
            {r.status === "broken" && <div className="notice bad"><CircleX aria-hidden /><span><strong>Tampered.</strong>&nbsp;Receipt #{r.first_broken_seq}: {r.reason}.</span></div>}
            {r.status === "not_run" && <div className="notice"><Info aria-hidden /><span><strong>Not checked.</strong>&nbsp;{r.reason}.</span></div>}
            {r.receipts.length > 0 && (
              <ol className="checks chain" style={{ listStyle: "none" }} aria-label="Receipt chain, oldest first">
                {r.receipts.map((x) => (
                  <li key={x.seq} style={{ gridTemplateColumns: "20px 1fr auto" }}>
                    {x.valid ? <CircleCheck className="r-supports" aria-hidden /> : <CircleX className="r-contradicts" aria-hidden />}
                    <span className="col" style={{ gap: 2, minWidth: 0 }}>
                      <span><b style={{ fontWeight: 600 }}>{kindLabel(x.kind)}</b> <span className="faint num">#{x.seq}</span></span>
                      <span className="mono faint hash" title={x.hash}>{x.hash.slice(0, 24)}…</span>
                    </span>
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
