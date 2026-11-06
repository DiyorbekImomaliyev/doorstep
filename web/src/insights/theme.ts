/**
 * AS-03: Doorstep's Studio themes, built the way AG's demos build theirs (Acme, Carbon, Grafana):
 * one set of type and shape params, then colours per surface.
 *
 *  - paper: the Control Room's own look. Inter, warm paper canvas, maroon accent, a palette
 *    that keeps its colours apart (maroon, rose, teal, sand, stone).
 */
import type { AgStudioThemeParams } from "ag-studio";
import { studioTheme } from "ag-studio";

const INTER = "'Inter Variable', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
export const SERIF = "'Source Serif 4 Variable', 'Source Serif 4', Georgia, serif";

/** Maroon first, then colours that stay apart from it and from each other in both modes. */
const PALETTE = ["#7b2d3d", "#c98a97", "#3f6f73", "#c9a46a", "#5a524e", "#a24c5e", "#8fb3a5"];

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
  accentColor: "#7b2d3d",
  backgroundColor: "#ffffff",
  foregroundColor: "#1f1a18",
  borderColor: "#e8e2dc",
  studioWrapperBackgroundColor: "#faf8f6",
  studioCanvasBackgroundColor: "#faf8f6",
  chartGridLineColor: "#f1ece7",
  valueChangeUpColor: "#2f6b4f",
  valueChangeDownColor: "#a3302a",
  valueChangeHighlightBackgroundColor: "#f3e3e7",
  browserColorScheme: "light",
};



export const paperTheme = studioTheme.withParams({ ...shared, ...paperColors }).withParams(paperColors, "light");
