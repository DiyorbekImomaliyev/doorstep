import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "@fontsource-variable/source-serif-4";
import "./styles.css";
import { Wordmark } from "./components/ui";
import Buyer from "./pages/Buyer";
import Home from "./pages/Home";
import Proof from "./pages/Proof";
import Return from "./pages/Return";
import Seal from "./pages/Seal";
import Shop from "./pages/Shop";
import Verify from "./pages/Verify";

// The Control Room carries AG Grid Enterprise; the buyer, seal and home pages never download it.
const Seller = lazy(() => import("./pages/Seller"));

// Shown while the Control Room loads (its grid is a large download on a phone): the page's own
// frame, so the screen is never blank and nothing jumps when the real page arrives.
function ControlRoomSkeleton() {
  return (
    <div className="shell" aria-busy="true" aria-label="Loading your Control Room">
      <aside className="sidebar">
        <Wordmark />
        <div className="skeleton skel-chip" />
        <div className="skeleton skel-nav" />
      </aside>
      <main className="main">
        <div className="page stack-lg">
          <div className="skeleton" style={{ height: 34, width: "min(280px, 70%)" }} />
          <div className="kpis">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 96 }} />)}</div>
          <div className="skeleton" style={{ height: 420 }} />
        </div>
      </main>
    </div>
  );
}

function route() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const [first, second] = parts;
  if (first === "t" && second) return <Buyer token={second} />;
  if (first === "s" && second) return <Seal token={second} />;
  if (first === "seller" && second) return <Seller token={second} />;
  if (first === "shop" && second) return <Shop sellerId={second} />;
  if (first === "verify" && second) return <Verify caseId={second} />;
  if (first === "return") return <Return />;
  if (first === "proof") return <Proof />;
  return <Home />;
}

createRoot(document.getElementById("root")!).render(<StrictMode><Suspense fallback={<ControlRoomSkeleton />}>{route()}</Suspense></StrictMode>);
