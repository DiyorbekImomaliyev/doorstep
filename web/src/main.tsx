import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "@fontsource-variable/source-serif-4";
import "./styles.css";
import Buyer from "./pages/Buyer";
import Home from "./pages/Home";
import Return from "./pages/Return";
import Seal from "./pages/Seal";
import Shop from "./pages/Shop";
import Verify from "./pages/Verify";

// The Control Room carries AG Grid Enterprise; the buyer, seal and home pages never download it.
const Seller = lazy(() => import("./pages/Seller"));

function route() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const [first, second] = parts;
  if (first === "t" && second) return <Buyer token={second} />;
  if (first === "s" && second) return <Seal token={second} />;
  if (first === "seller" && second) return <Seller token={second} />;
  if (first === "shop" && second) return <Shop sellerId={second} />;
  if (first === "verify" && second) return <Verify caseId={second} />;
  if (first === "return") return <Return />;
  return <Home />;
}

createRoot(document.getElementById("root")!).render(<StrictMode><Suspense fallback={null}>{route()}</Suspense></StrictMode>);
