import { api } from "../api";
import { Brand, ErrorBox, Loading, Pill, useAsync } from "../components/ui";

export default function Verify({ caseId }: { caseId: string }) {
  const { data: r, error } = useAsync(() => api.verify(caseId), [caseId]);
  return (
    <>
      <Brand right={<span className="faint">Public receipt check</span>} />
      <main className="narrow stack-lg">
        <div className="col" style={{ gap: 4 }}>
          <h1 style={{ fontSize: 22 }}>Is this case's record intact?</h1>
          <p className="muted">Every money decision gets an Ed25519-signed receipt chained to the one before it. Changing any stored receipt breaks the chain here.</p>
        </div>
        {error && <ErrorBox message={error} />}
        {!r && !error && <Loading height={120} />}
        {r && (
          <section className="card col">
            {r.status === "ok" && <div className="notice good"><strong>Verified.</strong>&nbsp;{r.checked} receipts recomputed; every hash and signature matches.</div>}
            {r.status === "broken" && <div className="notice bad"><strong>Tampered.</strong>&nbsp;Receipt #{r.first_broken_seq}: {r.reason}.</div>}
            {r.status === "not_run" && <div className="notice info"><strong>Not checked.</strong>&nbsp;{r.reason}.</div>}
            {r.receipts.length > 0 && (
              <ol className="timeline">
                {r.receipts.map((x) => (
                  <li key={x.seq}>
                    <span className="tdot" style={{ background: x.valid ? "var(--good)" : "var(--bad)" }} aria-hidden />
                    <span>#{x.seq} · {x.kind} <span className="mono faint">{x.hash.slice(0, 20)}…</span></span>
                    <Pill tone={x.valid ? "good" : "bad"} plain>{x.valid ? "valid" : "broken"}</Pill>
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
