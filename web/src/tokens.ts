/**
 * Doorstep's design tokens for code that cannot read CSS variables: AG Grid and AG Studio theme
 * params take literal values (AS-03 checks the accent is the literal hex). The same values sit on
 * :root in styles.css; change both together.
 */
export const C = {
  ink: "#1f1a18",
  ink2: "#5a524e",
  ink3: "#736b67",
  grey: "#8b827d",
  paper: "#faf8f6",
  surface: "#ffffff",
  line: "#e8e2dc",
  rule: "#ede8e3",
  hair: "#f3eee9",
  hover: "#fbf7f6",
  accent: "#7b2d3d",
  accentSoft: "#f6ecee",
  accentTint: "#f3e3e7",
  good: "#2f6b4f",
  warn: "#8a5a12",
  bad: "#a3302a",
} as const;

export const SHADOW = {
  s1: "0 1px 2px rgb(31 26 24 / .04)",
  s2: "0 1px 2px rgb(31 26 24 / .04), 0 2px 8px rgb(31 26 24 / .04)",
  pop: "0 12px 32px rgba(31, 26, 24, .14)",
} as const;

/** Motion timings (ms). `glide` is how long AG Grid takes to animate a row to its new place. */
export const MOTION = { fast: 160, base: 240, glide: 450, rail: 8000, ease: "cubic-bezier(.2,.7,.2,1)" } as const;
