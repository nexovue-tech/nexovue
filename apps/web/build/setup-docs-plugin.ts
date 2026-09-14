/**
 * Compiles `docs/product/setup/` into the bundle, at build time.
 *
 * **The pages already existed and had no address.** T67 wrote fifteen Markdown
 * files into a private repository; nothing published them, so a customer could
 * not read the one thing that gets their telemetry flowing. This is what gives
 * them a URL, and it deliberately does not introduce a second site to host: the
 * console already has a design system, a deploy and a domain.
 *
 * Build time rather than runtime, so no Markdown parser reaches the browser and
 * a page is HTML the moment it is asked for. `markdown-it` is therefore a dev
 * dependency, and stays one.
 *
 * The Markdown is the source of truth and is not copied. Editing a page under
 * `docs/product/setup/` is what publishes it — a generated file checked in
 * beside it would drift the first time somebody edited only one of the two.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import type { StateCore, Token } from "markdown-it";
import MarkdownIt from "markdown-it";
import type { Plugin } from "vite";

const VIRTUAL_ID = "virtual:setup-docs";
const RESOLVED_ID = `\0${VIRTUAL_ID}`;

/** One page, as the docs screen needs it. */
export interface SetupDoc {
  /** URL path under `/docs`, e.g. `languages/go`. `index` is the front page. */
  slug: string;
  /** The `# ` heading, which is what the navigation and the tab title show. */
  title: string;
  /** The folder it sits in, or "" at the top level. Groups the navigation. */
  group: string;
  html: string;
}

function markdownFiles(root: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(root).sort()) {
    const path = join(root, entry);
    if (statSync(path).isDirectory()) found.push(...markdownFiles(path));
    else if (entry.endsWith(".md")) found.push(path);
  }
  return found;
}

/** The `# ` heading, or the slug when a page has none, which is a defect the test catches. */
function heading(markdown: string, slug: string): string {
  return /^#\s+(.+)$/m.exec(markdown)?.[1].trim() ?? slug;
}

/** Where a relative `.md` target lands, as a slug. */
function targetSlug(path: string, group: string): string {
  const resolved = path.startsWith("../") ? path.slice(3) : group ? `${group}/${path}` : path;
  return resolved.replace(/\/index$/, "");
}

/**
 * Rewrite links between pages so they address routes rather than files.
 *
 * `[already-instrumented.md](already-instrumented.md)` is correct in a repository
 * and wrong twice on a website: the href is a 404, and the text names a file the
 * reader cannot see. Both are the same repository convention leaking out, so both
 * are fixed here rather than by editing twenty-six links — the twenty-seventh
 * would be written the same way, and correctly so.
 *
 * Text that is *not* a bare filename is the author's sentence and is left alone.
 */
function asRoutes(html: string, group: string, titles: ReadonlyMap<string, string>): string {
  return html.replace(
    /href="([^"#:]+)\.md(#[^"]*)?"(>)([^<]*)/g,
    (_whole, path: string, hash = "", close: string, text: string) => {
      const slug = targetSlug(path, group);
      const named = text.trim().endsWith(".md") ? (titles.get(slug) ?? text) : text;
      return `href="/docs/${slug}${hash}"${close}${named}`;
    },
  );
}

/**
 * A heading id for every `##` and `###`, because pages link to their own sections.
 *
 * markdown-it emits none, so `index.md`'s `(#languages)` pointed at nothing — a
 * link that silently does nothing when clicked, which is worse than no link.
 */
function withHeadingIds(
  markdown: InstanceType<typeof MarkdownIt>,
): InstanceType<typeof MarkdownIt> {
  markdown.core.ruler.push("nexovue:heading-ids", (state: StateCore) => {
    const tokens = state.tokens;
    tokens.forEach((token: Token, position: number) => {
      if (token.type !== "heading_open") return;
      const text = tokens[position + 1]?.content ?? "";
      const id = text
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "");
      if (id) token.attrSet("id", id);
    });
    return true;
  });
  return markdown;
}

export function readSetupDocs(repositoryRoot: string): SetupDoc[] {
  const root = join(repositoryRoot, "docs/product/setup");
  const markdown = withHeadingIds(
    new MarkdownIt({ html: false, linkify: false, typographer: false }),
  );

  const pages = markdownFiles(root).map((file) => ({
    slug: relative(root, file).replace(/\.md$/, ""),
    source: readFileSync(file, "utf8"),
  }));

  // Every title first: a link's text is replaced with the title of the page it
  // points at, which cannot be known while that page is still unread.
  const titles = new Map(pages.map((page) => [page.slug, heading(page.source, page.slug)]));

  return pages.map((page) => {
    const group = page.slug.includes("/") ? page.slug.split("/")[0] : "";
    return {
      slug: page.slug,
      title: titles.get(page.slug) ?? page.slug,
      group,
      html: asRoutes(markdown.render(page.source), group, titles),
    };
  });
}

export function setupDocs(repositoryRoot: string): Plugin {
  return {
    name: "nexovue:setup-docs",
    resolveId: (id) => (id === VIRTUAL_ID ? RESOLVED_ID : undefined),
    load(id) {
      if (id !== RESOLVED_ID) return undefined;
      const docs = readSetupDocs(repositoryRoot);
      // Watched so editing a page reloads the browser in dev, the same as a
      // source file. Without this the docs are frozen at server start.
      for (const file of markdownFiles(join(repositoryRoot, "docs/product/setup"))) {
        this.addWatchFile(file);
      }
      return `export const SETUP_DOCS = ${JSON.stringify(docs)};`;
    },
  };
}
