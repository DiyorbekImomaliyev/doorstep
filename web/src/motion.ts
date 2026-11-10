import { useEffect, useState } from "react";
import { MOTION } from "./tokens";

/** Follow a media query, e.g. "(max-width: 640px)". */
export function useMedia(query: string): boolean {
  const [on, setOn] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const f = () => setOn(m.matches);
    f();
    m.addEventListener("change", f);
    return () => m.removeEventListener("change", f);
  }, [query]);
  return on;
}

const REDUCE = "(prefers-reduced-motion: reduce)";

/** The seller asked their system for less motion: rows jump instead of gliding, nothing pulses. */
export const useReducedMotion = () => useMedia(REDUCE);

export const reducedNow = () => typeof window !== "undefined" && window.matchMedia(REDUCE).matches;

/** Run after AG Grid has animated rows to their new places (at once under reduced motion). */
export function afterGlide(fn: () => void): number {
  return window.setTimeout(fn, reducedNow() ? 0 : MOTION.glide);
}
