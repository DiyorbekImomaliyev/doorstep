import { themeQuartz } from "ag-grid-community";
import { C, SHADOW } from "../tokens";

/**
 * The Control Room grid's theme: AG Grid's Quartz with Doorstep's tokens. Hairlines only (one rule
 * under the header and between rows, no column lines); maroon accent; quiet grey headers.
 * Note params are inert until NotesModule is registered.
 */
export const gridTheme = themeQuartz.withParams({
  fontFamily: "inherit",
  fontSize: 13.5,
  foregroundColor: C.ink,
  backgroundColor: C.surface,
  headerFontWeight: 600,
  headerFontSize: 11,
  headerBackgroundColor: C.surface,
  headerTextColor: C.grey,
  // Columns still resize from their edge; the handle itself stays invisible.
  headerColumnResizeHandleColor: "transparent",
  headerColumnBorder: false,
  borderColor: C.rule,
  rowBorder: { color: C.hair },
  columnBorder: false,
  rowHoverColor: C.hover,
  selectedRowBackgroundColor: C.accentSoft,
  // Cell selection (drag for a live sum): a maroon outline over a faint maroon wash.
  rangeSelectionBorderColor: C.accent,
  rangeSelectionBackgroundColor: "rgb(123 45 61 / .07)",
  rangeSelectionHighlightColor: C.accentTint,
  rangeHeaderHighlightColor: C.hover,
  accentColor: C.accent,
  iconColor: C.grey,
  wrapperBorder: false,
  wrapperBorderRadius: 0,
  rowHeight: 62,
  headerHeight: 42,
  spacing: 7,
  cellHorizontalPadding: 16,
  valueChangeValueHighlightBackgroundColor: C.accentTint,
  tooltipBackgroundColor: "transparent",
  tooltipBorder: false,
  sideBarBackgroundColor: "#fcfbfa",
  menuBackgroundColor: C.surface,
  menuBorder: { color: C.line },
  menuShadow: SHADOW.pop,
  statusBarLabelColor: C.ink3,
  statusBarLabelFontWeight: 500,
  statusBarValueColor: C.ink,
  statusBarValueFontWeight: 600,
  // Cell Notes (AG Grid 35.3): a maroon corner and a paper popup.
  noteIndicatorColor: C.accent,
  noteIndicatorSize: 7,
  notePopupBackgroundColor: "#fffdfb",
  notePopupBorder: { color: C.line },
  notePopupTextColor: C.ink,
  // Room for the calm empty state (and its 3D doorstep) when nothing needs the seller.
  autoHeightMinBodyHeight: 260,
});
