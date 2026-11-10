/**
 * AS-03: Doorstep's Studio themes, built the way AG's demos build theirs (Acme, Carbon, Grafana):
 * one set of type and shape params, then colours per surface.
 *
 *  - paper: the Control Room's own look. Inter, warm paper canvas, maroon accent, a palette
 *    that keeps its colours apart (maroon, rose, teal, sand, stone).
 */
import type { AgStudioThemeParams } from "ag-studio";
import { studioTheme } from "ag-studio";
import { C, SHADOW } from "../tokens";

const INTER = "'Inter Variable', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";

/** Maroon first, then colours that stay apart from it and from each other in both modes. */
// Seven hues that stay apart for colour-blind readers and hold 3:1 against the paper background.
const PALETTE = ["#7b2d3d", "#2f6f73", "#b7791f", "#4a5a8a", "#6b625e", "#a3573e", "#6a8a5a"];

const shared: Partial<AgStudioThemeParams> = {
  fontFamily: INTER,
  fontSize: 13,
  studioPanelHeaderFontFamily: INTER,
  studioPanelHeaderFontSize: 13,
  studioPanelHeaderFontWeight: 600,
  gridFontFamily: INTER,
  gridFontSize: 13,
  gridHeaderFontFamily: INTER,
  gridHeaderFontSize: 11,
  gridHeaderFontWeight: 600,
  gridCellFontFamily: INTER,
  gridCellFontSize: 13,
  chartFontFamily: INTER,
  studioCanvasFontFamily: INTER,
  studioWidgetTitleFontFamily: INTER,
  studioWidgetTitleFontSize: 14,
  studioWidgetTitleFontWeight: 600,
  studioWidgetSubtitleFontFamily: INTER,
  studioWidgetSubtitleFontSize: 12,
  studioWidgetCaptionFontFamily: INTER,
  studioWidgetCaptionFontSize: 12,
  spacing: 8,
  borderRadius: 8,
  studioWidgetBorderRadius: 12,
  gridRowBorder: true,
  chartTooltipBorder: false,
  ...Object.fromEntries(PALETTE.flatMap((c, i) => [[`chartPaletteFills${i + 1}Color`, c], [`chartPaletteStrokes${i + 1}Color`, c]])),
};

const paperColors: Partial<AgStudioThemeParams> = {
  accentColor: C.accent,
  backgroundColor: C.surface,
  foregroundColor: C.ink,
  borderColor: C.line,
  studioWrapperBackgroundColor: C.paper,
  studioCanvasBackgroundColor: C.paper,
  chartGridLineColor: "#f1ece7",
  valueChangeUpColor: C.good,
  valueChangeDownColor: C.bad,
  valueChangeHighlightBackgroundColor: C.accentTint,
  studioWidgetShadow: SHADOW.s1,
  browserColorScheme: "light",
};

export const paperTheme = studioTheme.withParams({ ...shared, ...paperColors }).withParams(paperColors, "light");
