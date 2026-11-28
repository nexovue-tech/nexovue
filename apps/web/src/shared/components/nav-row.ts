/**
 * The one look of a menu row, shared by the main menu and the settings menu
 * (design-system.md, *Menus*): 28 px rows, 13 px text, 16 px from the edge, and the chosen
 * row marked by a left rule and weight. They drifted apart while each had its own (20 px
 * and 28 px rows, a highlight 8 px off the edge), so neither keeps a copy. The chosen row has
 * a 3 px rule and a green-tinted fill (sketch G).
 *
 * 44 px high below `md`, where a menu is a drawer of tap targets.
 */
export const NAV_ROW =
  "relative flex min-h-11 items-center justify-between gap-2 border-l-[3px] pr-3 pl-[13px] text-ink-dim text-sm hover:bg-hover hover:text-ink md:min-h-7";

/**
 * The rule's colour is the state's alone: with both an idle and a chosen colour on one
 * element the stylesheet's order decided, and transparent won (UX review, 2026-11-28).
 */
export const NAV_ROW_IDLE = "border-transparent";

export const NAV_ROW_ACTIVE = "border-brand bg-brand-surface font-semibold text-ink";

/** A group's label above its rows. */
export const NAV_LABEL = "px-4 pb-1 font-mono text-2xs text-ink-dim uppercase tracking-caps";
