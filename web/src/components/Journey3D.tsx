import { Fragment, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import {
  Banknote, CircleCheck, CircleX, CreditCard, House, Landmark, Pause, Play, QrCode, Scale, ScanLine,
  Smartphone, Store, Truck,
} from "lucide-react";

/* How Doorstep works, as one parcel's journey in an isometric world (CSS 3D, no library).
   The step list is the accessible content; the stage is decoration (aria-hidden). */

type Step = { stage: "Prevent" | "Prove" | "Resolve" | "Recover"; short: string; title: string; text: string; icon: ReactNode };

const STEPS: Step[] = [
  { stage: "Prevent", short: "Pay", title: "Pays with PayPal", text: "Address checked before he's charged.", icon: <CreditCard /> },
  { stage: "Prove", short: "Seal", title: "Packed with a seal", text: "A QR inside the box proves who opened it.", icon: <QrCode /> },
  { stage: "Prove", short: "Ship", title: "Shipped and tracked", text: "Tracking goes on the PayPal payment.", icon: <Truck /> },
  { stage: "Resolve", short: "Not here", title: "“Delivered”, but not here", text: "Wrong street. Sam taps Not here.", icon: <ScanLine /> },
  { stage: "Resolve", short: "Facts", title: "Doorstep checks the facts", text: "Courier data, the seal and the law.", icon: <Scale /> },
  { stage: "Resolve", short: "Refund", title: "Refunded through PayPal", text: "Sam gets £60 back. Maria taps once.", icon: <Banknote /> },
  { stage: "Recover", short: "Claim", title: "A claim on the courier", text: "Checked, with the evidence, before the deadline.", icon: <Landmark /> },
];

const DURATION = 3000;

type Row = { kind: "ok" | "no" | "law" | "seal"; text: ReactNode };
const READOUT: { title: string; icon: ReactNode; rows: Row[] }[] = [
  { title: "PayPal checkout", icon: <CreditCard />, rows: [
    { kind: "ok", text: <>Flat number added <b>before</b> payment</> },
    { kind: "ok", text: <>Paid <b>£60.00</b></> },
  ] },
  { title: "Packing", icon: <QrCode />, rows: [
    { kind: "ok", text: "Photo matches the order" },
    { kind: "seal", text: <>Seal inside the box</> },
  ] },
  { title: "Shipping", icon: <Truck />, rows: [
    { kind: "ok", text: "Tracking on the PayPal payment" },
  ] },
  { title: "Courier: “Delivered”", icon: <ScanLine />, rows: [
    { kind: "no", text: <>Scanned in <b>E8</b>, not N1</> },
    { kind: "no", text: <>Sam taps <b>Not here</b></> },
  ] },
  { title: "The facts", icon: <Scale />, rows: [
    { kind: "no", text: "Scan from the wrong area" },
    { kind: "no", text: "Seal never scanned" },
    { kind: "law", text: <>Seller's risk <span className="faint">(CRA 2015 s.29)</span></> },
  ] },
  { title: "Refunded", icon: <Banknote />, rows: [
    { kind: "ok", text: <>Sam refunded <b>£60.00</b></> },
    { kind: "ok", text: "Approved in one tap" },
    { kind: "ok", text: "Signed receipt" },
  ] },
  { title: "Carrier recovery", icon: <Landmark />, rows: [
    { kind: "ok", text: <>Claim checked: <b>8 of 8</b> facts</> },
    { kind: "ok", text: <>Royal Mail pays <b>£60.00</b></> },
    { kind: "ok", text: <>Maria's loss: <b>£0</b></> },
  ] },
];

function RowIcon({ kind }: { kind: Row["kind"] }) {
  if (kind === "ok") return <CircleCheck className="ok" />;
  if (kind === "no") return <CircleX className="no" />;
  if (kind === "seal") return <QrCode className="law" />;
  return <Scale className="law" />;
}

/* ---- isometric primitives ---- */

type BoxProps = {
  x: number; y: number; w: number; d: number; h: number;
  top: string; front: string; side: string;
  style?: CSSProperties; className?: string; children?: ReactNode; frontChildren?: ReactNode;
};

export function Box({ x, y, w, d, h, top, front, side, style, className = "", children, frontChildren }: BoxProps) {
  return (
    <div className={`obj ${className}`} style={{ left: x, top: y, width: w, height: d, ...style }}>
      <div className="face" style={{ width: h, height: d, background: side, transformOrigin: "0 0", transform: "rotateY(-90deg)" }} />
      <div className="face" style={{ width: w, height: h, top: d, background: front, transformOrigin: "0 0", transform: "rotateX(90deg)" }}>{frontChildren}</div>
      <div className="face" style={{ width: w, height: d, background: top, transform: `translateZ(${h}px)` }}>{children}</div>
    </div>
  );
}

function Tag({ x, y, z, children, tone = "", show = true, style }: { x: number; y: number; z: number; children: ReactNode; tone?: string; show?: boolean; style?: CSSProperties }) {
  return (
    <div className="billboard fade-obj" style={{ left: x, top: y, transform: `translateZ(${z}px)`, opacity: show ? 1 : 0, ...style }}>
      <div className="bb-inner"><span className={`tag3d ${tone}`}>{children}</span></div>
    </div>
  );
}

/* A person as a rounded pictogram, standing on the floor and always facing the viewer. */
type Pose = "stand" | "phone" | "cheer";
const ARMS: Record<Pose, string> = {
  stand: "M12 16 L6.5 26 M12 16 L17.5 26",
  phone: "M12 16 L6.5 26 M12 16 L19 21 L19.5 14",
  cheer: "M12 16 L5 7.5 M12 16 L19 7.5",
};

function Person({ x, y, color, pose = "stand" }: { x: number; y: number; color: string; pose?: Pose }) {
  return (
    <>
      <div className="fig-shadow" style={{ left: x - 13, top: y - 7 }} />
      <div className="billboard" style={{ left: x, top: y }}>
        <div className="bb-inner">
          <svg className={`person ${pose}`} viewBox="0 0 24 44" width="24" height="44" style={{ color }}>
            <circle cx="12" cy="6" r="5" fill="currentColor" />
            <path d={`M12 13 L12 28 M12 28 L7.5 41 M12 28 L16.5 41 ${ARMS[pose]}`} fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
            {pose === "phone" && <rect x="17.5" y="7" width="5" height="8.5" rx="1.3" fill="#1f1a18" stroke="#fff" strokeWidth=".8" />}
          </svg>
        </div>
      </div>
    </>
  );
}

/* Money moving between people. It is drawn on a flat layer above the world (never hidden behind
   a building), at the screen point of a world point: .world is rotateX(58deg) rotateZ(-34deg)
   around (400, 268), orthographic. */
type P3 = [number, number, number];
const COS_Z = Math.cos(-34 * Math.PI / 180), SIN_Z = Math.sin(-34 * Math.PI / 180);
const COS_X = Math.cos(58 * Math.PI / 180), SIN_X = Math.sin(58 * Math.PI / 180);
function screen([x, y, z]: P3): string {
  const sx = x * COS_Z - y * SIN_Z;
  const sy = (x * SIN_Z + y * COS_Z) * COS_X - z * SIN_X;
  return `translate(${(400 + sx).toFixed(1)}px, ${(268 + sy).toFixed(1)}px)`;
}

function Flow({ from, to, children }: { from: P3; to: P3; children: ReactNode }) {
  return (
    <div className="flow2d" style={{ ["--from" as string]: screen(from), ["--to" as string]: screen(to) } as CSSProperties}>
      <span className="tag3d accent">{children}</span>
    </div>
  );
}

function Flows({ step }: { step: number }) {
  if (step === 0) return <Flow key="pay" from={AT_SAM} to={AT_SHOP}><CreditCard />£60 via PayPal</Flow>;
  if (step === 5) return <Flow key="refund" from={AT_SHOP} to={AT_SAM}><Banknote />£60 back to Sam</Flow>;
  if (step === 6) return <Flow key="recover" from={AT_E8} to={[-112, 76, 18]}><Landmark />Royal Mail pays £60</Flow>;
  return null;
}

const AT_SAM: P3 = [150, 14, 64];
const AT_SHOP: P3 = [-206, 34, 66];
const AT_E8: P3 = [250, 52, 40];

/* Door drawn on a front face (front faces are flipped: local y grows upward). */
export function Door({ left, w = 16, h = 30, color = "#7b2d3d" }: { left: number; w?: number; h?: number; color?: string }) {
  return <div style={{ position: "absolute", left, top: 0, width: w, height: h, background: color, borderRadius: "0 0 2px 2px" }} />;
}

function Windows({ count, left, gap = 18, bottom }: { count: number; left: number; gap?: number; bottom: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} style={{ position: "absolute", left: left + i * gap, top: bottom, width: 11, height: 12, background: "#d9cfc6", borderRadius: 2 }} />
      ))}
    </>
  );
}

/* ---- the scene ---- */

function Scene({ step }: { step: number }) {
  // After the "Delivered" scan the van drives off, leaving the parcel at the wrong door.
  const vanX = step >= 3 ? 470 : 0;
  const parcel = [
    { x: -262, y: -20, z: 0, o: 0 },
    { x: -262, y: 18, z: 0, o: 1 },
    { x: -78, y: 39, z: 44, o: 1 },
    { x: 238, y: 16, z: 0, o: 1 },
    { x: 238, y: 16, z: 0, o: 1 },
    { x: 238, y: 16, z: 0, o: 1 },
    { x: 238, y: 16, z: 0, o: 1 },
  ][step];
  const sam: Pose = step === 0 || step === 3 || step === 4 ? "phone" : step >= 5 ? "cheer" : "stand";
  const maria: Pose = step === 5 ? "phone" : step === 6 ? "cheer" : "stand";
  const parcelStyle: CSSProperties = { transform: `translate3d(${parcel.x}px, ${parcel.y}px, ${parcel.z}px)`, opacity: parcel.o, left: 0, top: 0 };
  const sealed = step >= 1;

  return (
    <div className="world">
      <div className="floor" />
      <div className="road" />

      {/* Maria's shop */}
      <Box x={-300} y={-72} w={104} d={84} h={66} top="#ffffff" front="#efe8e2" side="#e0d6cc"
        frontChildren={<><Door left={44} /><Windows count={2} left={10} bottom={40} gap={64} /></>}>
        <div style={{ position: "absolute", inset: 10, borderRadius: 4, background: "#f3eee9" }} />
      </Box>
      <Box x={-304} y={12} w={112} d={10} h={9} top="#a24c5e" front="#7b2d3d" side="#64222f" className="awning" style={{ transform: "translateZ(50px)" }} />
      <Tag x={-248} y={-30} z={96}><Store />Maria's shop</Tag>
      <Person x={-206} y={34} color="#4a403c" pose={maria} />

      {/* courier van (moves) */}
      <div className="obj parcel" style={{ left: 0, top: 0, transform: `translate3d(${vanX}px, 0px, 0px)`, opacity: step >= 3 ? 0 : 1 }}>
        <Box x={-112} y={34} w={96} d={38} h={44} top="#ffffff" front="#ece5de" side="#ddd2c8"
          frontChildren={<div style={{ position: "absolute", left: 10, top: 18, width: 60, height: 4, background: "#a24c5e", borderRadius: 2 }} />} />
        <Box x={-16} y={34} w={30} d={38} h={30} top="#f6f1ec" front="#e3dad1" side="#d3c7bb"
          frontChildren={<div style={{ position: "absolute", left: 6, top: 14, width: 18, height: 10, background: "#cfc3b8", borderRadius: 2 }} />} />
        <Tag x={-64} y={52} z={74} show={step < 3}><Truck />Courier</Tag>
      </div>

      {/* Sam's home, N1 */}
      <Box x={58} y={-96} w={92} d={84} h={74} top="#ffffff" front="#efe8e2" side="#e0d6cc"
        frontChildren={<><Door left={38} /><Windows count={2} left={10} bottom={44} gap={60} /></>} />
      <Tag x={104} y={-54} z={98}><House />Sam <span className="sub">N1</span></Tag>
      <div className="ghost-box" style={{ left: 92, top: 18, width: 30, height: 30, opacity: step >= 3 ? 1 : 0 }} />
      <Person x={150} y={14} color="#7b2d3d" pose={sam} />
      <Tag x={150} y={14} z={64} tone="accent" show={step === 3 || step === 4}><Smartphone />Not here</Tag>

      {/* another street, E8 */}
      <Box x={206} y={-96} w={92} d={84} h={58} top="#f8f5f2" front="#e8e0d8" side="#d8cdc2"
        frontChildren={<><Door left={60} color="#8b827d" /><Windows count={2} left={12} bottom={34} gap={22} /></>} />
      <Tag x={252} y={-54} z={84} show={!(step === 3 || step === 4)}><House />Another street <span className="sub">E8</span></Tag>
      <Tag x={252} y={-54} z={84} tone="bad" show={step === 3 || step === 4}><ScanLine />&ldquo;Delivered&rdquo; here <span className="sub">E8 · 14:02</span></Tag>
      {(step === 3 || step === 4) && <div className="pulse" style={{ left: 223, top: 1, width: 60, height: 60 }} />}

      {/* the parcel */}
      <Box x={0} y={0} w={30} d={30} h={24} top="#e8d6bf" front="#d8c0a0" side="#c7ab88" className="parcel" style={parcelStyle}
        frontChildren={<div style={{ position: "absolute", left: 12, top: 0, width: 6, height: 24, background: "#a24c5e" }} />}>
        <div style={{ position: "absolute", left: 12, top: 0, width: 6, height: 30, background: "#a24c5e" }} />
        {sealed && <div style={{ position: "absolute", left: 3, top: 9, width: 9, height: 9, background: "#1f1a18", borderRadius: 1, outline: "1.5px solid #fff" }} />}
      </Box>
      <Tag x={-170} y={44} z={34} show={step === 1}><QrCode />Seal inside</Tag>


    </div>
  );
}

export default function Journey3D() {
  const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(!reduced);
  const [cycle, setCycle] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [seen, setSeen] = useState(reduced);

  useEffect(() => {
    const el = wrap.current;
    if (seen || !el || typeof IntersectionObserver === "undefined") { setSeen(true); return; }
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setSeen(true); io.disconnect(); } }, { threshold: 0.35 });
    io.observe(el);
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!playing || !seen) return;
    const t = setTimeout(() => setStep((s) => (s + 1) % STEPS.length), DURATION);
    return () => clearTimeout(t);
  }, [playing, seen, step, cycle]);

  useEffect(() => {
    const el = wrap.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      // The whole box, padding included: on phones the facts card sits under the scene.
      setScale(Math.min(1.3, el.clientWidth / 690, el.clientHeight / 430));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // On phones the steps become one sideways row: keep the current step in view (never the page).
  useEffect(() => {
    const t = track.current;
    const chip = t?.querySelector<HTMLElement>('[aria-current="step"]');
    if (!t || !chip || t.scrollWidth <= t.clientWidth) return;
    t.scrollTo({ left: chip.offsetLeft - t.clientWidth / 2 + chip.clientWidth / 2, behavior: "smooth" });
  }, [step, scale]);

  const pick = (i: number) => { setStep(i); setCycle((c) => c + 1); };
  const r = READOUT[step];

  return (
    <div className="journey">
      <div className="journey-steps" ref={track} role="list" aria-label="How Doorstep works">
        {STEPS.map((s, i) => (
          <Fragment key={s.title}>
          {(i === 0 || STEPS[i - 1].stage !== s.stage) && <span className="jstage" aria-hidden>{s.stage}</span>}
          <button role="listitem" className="jstep" aria-current={i === step ? "step" : undefined} onClick={() => pick(i)}>
            <span className="n" aria-hidden>{s.icon}</span>
            <span>
              <span className="t">{s.title}</span>
              <span className="ts" aria-hidden>{s.short}</span>
              <span className="d" style={{ display: "block" }}>{s.text}</span>
            </span>
            {i === step && (
              <span className={`progress-line ${playing ? "" : "paused"}`} aria-hidden>
                <i key={`${step}-${cycle}`} style={{ ["--dur" as string]: `${DURATION}ms` } as CSSProperties} />
              </span>
            )}
          </button>
          </Fragment>
        ))}
        <div className="journey-controls">
          <span className="faint num">Step {step + 1} of {STEPS.length}</span>
          <button className="btn ghost" onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause the animation" : "Play the animation"}>
            {playing ? <Pause size={15} /> : <Play size={15} />}{playing ? "Pause" : "Play"}
          </button>
        </div>
      </div>
      <div className={`stage-wrap ${seen ? "" : "pre"}`} ref={wrap} aria-hidden>
        <div className="stage-scale" style={{ ["--s" as string]: scale } as CSSProperties}>
          <Scene step={step} />
          {seen && <Flows step={step} />}
        </div>
        <div className="readout" key={step}>
          <span className="r-title">{r.icon}{r.title}</span>
          <span className="r-text">{STEPS[step].text}</span>
          {r.rows.map((row, i) => (
            <div key={i} className="r-row"><RowIcon kind={row.kind} /><span>{row.text}</span></div>
          ))}
        </div>
      </div>
    </div>
  );
}
