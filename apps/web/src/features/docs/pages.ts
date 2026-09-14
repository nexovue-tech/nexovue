/**
 * The compiled setup pages, and the order they are read in.
 *
 * The pages come from `docs/product/setup/` through a build-time plugin; this
 * file owns only how they are arranged, because arrangement is a product
 * decision and the Markdown should not have to encode it.
 */

import { SETUP_DOCS } from "virtual:setup-docs";

export interface SetupDoc {
  slug: string;
  title: string;
  group: string;
  html: string;
}

export const DOCS: readonly SetupDoc[] = SETUP_DOCS;

/** The front page, which every other page is reached from. */
export const INDEX_SLUG = "index";

/**
 * Group order, and what each group is called on screen.
 *
 * **Named here rather than taken from the folder names.** `languages` and
 * `platforms` are directories; "Your language" and "Where it runs" are what a
 * reader is actually choosing between, and the difference is the whole
 * navigation.
 */
const GROUPS: ReadonlyArray<{ id: string; label: string }> = [
  { id: "", label: "Start here" },
  { id: "languages", label: "Your language" },
  { id: "platforms", label: "Where it runs" },
];

export interface DocGroup {
  label: string;
  pages: readonly SetupDoc[];
}

/**
 * The navigation, in reading order.
 *
 * A group the Markdown grew that nobody named here still appears, under its own
 * folder name — an unlisted page is worse than an ugly heading, because the page
 * then exists and cannot be reached.
 */
export function docGroups(docs: readonly SetupDoc[] = DOCS): DocGroup[] {
  const named = new Map(GROUPS.map((group) => [group.id, group.label]));
  const order = GROUPS.map((group) => group.id);
  const seen = [...new Set(docs.map((doc) => doc.group))].sort(
    (a, b) => index(order, a) - index(order, b),
  );

  return seen.map((group) => ({
    label: named.get(group) ?? group,
    pages: docs
      .filter((doc) => doc.group === group)
      // The front page first within its group; the rest as the folder sorts.
      .sort((a, b) => Number(b.slug === INDEX_SLUG) - Number(a.slug === INDEX_SLUG)),
  }));
}

function index(order: readonly string[], group: string): number {
  const at = order.indexOf(group);
  return at === -1 ? order.length : at;
}

export function docBySlug(slug: string, docs: readonly SetupDoc[] = DOCS): SetupDoc | undefined {
  return docs.find((doc) => doc.slug === slug);
}
