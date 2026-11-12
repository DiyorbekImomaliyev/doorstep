import { useEffect, useState } from "react";
import { CircleCheck, CircleX, KeyRound, Pencil } from "lucide-react";
import { api, type ProofCheck } from "../api";
import { Brand, Loading, usePageTitle } from "../components/ui";

/** DP-03: anyone checks a signed delivery proof. The proof arrives in the URL fragment, so
 * opening the link never sends it anywhere; the page posts it once, to be checked. */
const WHEN = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC" : "None";
const CARRIER: Record<string, string> = { royal_mail: "Royal Mail", evri: "Evri", dpd_uk: "DPD", parcelforce: "Parcelforce", usps: "USPS", ups: "UPS", fedex: "FedEx", dhl: "DHL", shippo: "Shippo (test carrier)" };
const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);
const AUDIT: Record<string, string> = { consistent: "Consistent", contradicted: "Contradicted by the carrier's own data", inconclusive: "Not enough data" };

export default function Proof() {
  const [proof, setProof] = useState(() => decodeURIComponent(window.location.hash.slice(1)));
  const [draft, setDraft] = useState("");
  const [check, setCheck] = useState<ProofCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  usePageTitle(check ? (check.valid ? "Proof genuine" : "Proof not valid") : "Check a delivery proof",
    { description: "Doorstep signs the delivery facts it holds with an Ed25519 key. Anyone can check a proof here." });

  useEffect(() => {
    if (!proof) return;
    setCheck(null);
    api.verifyProof(proof).then(setCheck, (e) => setError(String((e as Error).message)));
  }, [proof]);

  // Shows what a changed byte does: the same facts, one character different, no longer verify.
  function tamper() {
    const [h, body, sig] = proof.split(".");
    const i = Math.floor(body.length / 2);
    setProof(`${h}.${body.slice(0, i)}${body[i] === "A" ? "B" : "A"}${body.slice(i + 1)}.${sig}`);
  }

  const c = check?.claims;
  return (
    <>
      <Brand right={<span className="faint">Public proof check</span>} />
      <main className="narrow stack-lg">
        <div className="order-head">
          <span className="faint row" style={{ gap: 6 }}><KeyRound size={14} />Signed delivery proof</span>
          <h1>Is this delivery record genuine?</h1>
          <p className="muted">Doorstep signs the delivery facts it holds with an Ed25519 key. Change one character and the check fails.</p>
        </div>
        {!proof && (
          <section className="card col">
            <label className="col" style={{ gap: 6 }}>
              <span className="faint">Paste a proof</span>
              <textarea className="mono" rows={5} value={draft} onChange={(e) => setDraft(e.target.value)} style={{ fontSize: 12 }} />
            </label>
            <button className="btn primary" style={{ alignSelf: "flex-start" }} disabled={!draft.trim()} onClick={() => setProof(draft.trim())}>Check it</button>
          </section>
        )}
        {error && <div className="notice bad"><CircleX aria-hidden /><span>{error}</span></div>}
        {proof && !check && !error && <Loading height={120} />}
        {check && (
          <section className="card col" aria-live="polite">
            {check.valid && c ? (
              <div className="notice good"><CircleCheck aria-hidden /><span><strong>Genuine.</strong>&nbsp;Signed by Doorstep, and nothing in it has changed since.</span></div>
            ) : (
              <div className="notice bad"><CircleX aria-hidden /><span><strong>Not valid.</strong>&nbsp;{cap(check.reason ?? "")}.</span></div>
            )}
            {c && (
              <>
                <p style={{ margin: 0 }}>{c.statement}</p>
                <dl className="proof-facts">
                  <dt>Order</dt><dd className="num">{c.sub}</dd>
                  <dt>Carrier</dt><dd>{c.carrier ? CARRIER[c.carrier] ?? c.carrier : "Not shipped"}</dd>
                  <dt>Delivered scan</dt><dd>{WHEN(c.delivered_scan_at)}</dd>
                  {c.seal_scanned_after_delivery_at && (<><dt>Seal scanned after it</dt><dd>{WHEN(c.seal_scanned_after_delivery_at)}</dd></>)}
                  {c.scan_audit && (<><dt>Scan audit</dt><dd>{AUDIT[c.scan_audit] ?? c.scan_audit}</dd></>)}
                  {c.rule && (<><dt>Rule</dt><dd className="mono">{c.rule}</dd></>)}
                  {c.receipt_head && (<><dt>Receipt chain head</dt><dd className="mono" title={c.receipt_head}>{c.receipt_head.slice(0, 20)}…</dd></>)}
                  <dt>Signed</dt><dd>{WHEN(new Date(c.iat * 1000).toISOString())}</dd>
                </dl>
                <button className="btn" style={{ alignSelf: "flex-start" }} onClick={tamper}><Pencil size={15} />Change one character and check again</button>
              </>
            )}
            <p className="faint" style={{ fontSize: 12.5, margin: 0 }}>
              Check it yourself: the proof is a JWS (EdDSA). Doorstep's public key is at <a href="/.well-known/jwks.json">/.well-known/jwks.json</a>. It states facts, never who is at fault.
            </p>
          </section>
        )}
      </main>
    </>
  );
}
