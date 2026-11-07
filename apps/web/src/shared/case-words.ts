/**
 * Case sentences more than one feature shows (F22). Here, in `shared/`, because a
 * feature never imports another feature, and each sentence must read the same on
 * every screen that carries it.
 */

/** FR53, D58: on the finding page's linked alerts, and on the incident page. */
export const CASE_IS_NOT_AN_ALERT =
  "A case tracks work on a detected finding. A linked alert is a rule's own record, and cannot become a case.";

/** BR16, FR54: on the screens a person leaves by. */
export const NOTES_STAY =
  "Notes you wrote stay, in your own words, as your organization's record. Delete any you want gone before you leave.";

/**
 * BR16, FR54: said to an owner or admin removing someone else (founder,
 * 2026-11-07). They are not the author and are not leaving, so it states the fact
 * without the leaver's request. Only the members screen shows it; it sits beside
 * `NOTES_STAY` so the two wordings of BR16 change together. `name` is the person
 * being removed, rendered as plain text.
 */
export function notesStayFor(name: string): string {
  return `Notes ${name} wrote stay, in their own words, as your organization's record.`;
}
