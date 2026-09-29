/** Windowing for long lists: render only rows near the viewport. */

/** Fixed row height (px) so only visible rows need rendering. Matches styles.css. */
export const ROW_HEIGHT = 27
const OVERSCAN = 12

/** Rows to render for a scroll position: [start, end) plus spacer heights. */
export function visibleWindow(total: number, scrollTop: number, viewport: number) {
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)
  const end = Math.min(total, Math.ceil((scrollTop + viewport) / ROW_HEIGHT) + OVERSCAN)
  return { start, end, before: start * ROW_HEIGHT, after: (total - end) * ROW_HEIGHT }
}
