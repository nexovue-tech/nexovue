import { Link } from "@tanstack/react-router";

import { Wordmark } from "@/shared/components/Logo";

import type { SetupDoc } from "./pages";
import { DOCS, docBySlug, docGroups, INDEX_SLUG } from "./pages";
import "./docs.css";

/**
 * The setup documentation, readable without an account.
 *
 * **Signed out on purpose.** Somebody evaluating nexovue needs to know what
 * instrumenting their service costs *before* they sign up, and somebody whose
 * telemetry stopped at three in the morning should not be asked to log in to
 * read the troubleshooting page. Nothing here is tenant data — these are the
 * same fifteen pages for everybody.
 *
 * The page is rendered from Markdown compiled at build time, so no parser
 * reaches the browser and the content is our own: `html: false` in the plugin
 * means an author cannot inject markup, and there is no user input in this path
 * at all.
 */
export function DocsScreen({ slug = INDEX_SLUG }: { slug?: string }) {
  const doc = docBySlug(slug);

  return (
    <div className="flex min-h-dvh flex-col bg-page">
      <header className="shrink-0 border-line border-b">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-5 py-3">
          <Link to="/docs" className="text-ink">
            <Wordmark />
          </Link>
          <Link
            to="/login"
            className="text-ink-dim text-sm underline underline-offset-4 hover:text-ink"
          >
            Sign in
          </Link>
        </div>
      </header>

      <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-8 px-5 py-8 md:flex-row md:gap-10">
        <DocsNav current={slug} />
        {doc ? <DocBody doc={doc} /> : <NotAPage />}
      </div>
    </div>
  );
}

/** Every page, grouped. Short enough that all of it is shown rather than collapsed. */
function DocsNav({ current }: { current: string }) {
  return (
    <nav aria-label="Setup documentation" className="w-full shrink-0 md:w-56">
      <ul className="flex flex-col gap-5">
        {docGroups().map((group) => (
          <li key={group.label}>
            <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">{group.label}</p>
            <ul className="mt-1.5 flex flex-col">
              {group.pages.map((page) => (
                <li key={page.slug}>
                  <Link
                    to="/docs/$"
                    params={{ _splat: page.slug === INDEX_SLUG ? "" : page.slug }}
                    className={`-mx-2 block rounded px-2 py-1 text-sm hover:bg-hover ${
                      page.slug === current ? "bg-raised font-medium text-ink" : "text-ink-dim"
                    }`}
                  >
                    {page.title}
                  </Link>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function DocBody({ doc }: { doc: SetupDoc }) {
  return (
    <main className="min-w-0 flex-1">
      {/* The one place this product renders markup it did not build as JSX. It is
          our own Markdown, compiled at build time with HTML disabled, and no
          value on this route comes from a customer. */}
      <article
        className="nv-doc"
        // biome-ignore lint/security/noDangerouslySetInnerHtml: build-time output of our own Markdown, HTML disabled
        dangerouslySetInnerHTML={{ __html: doc.html }}
      />
    </main>
  );
}

/** A slug nobody wrote a page for. Says what exists rather than only that this does not. */
function NotAPage() {
  return (
    <main className="min-w-0 flex-1">
      <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">
        No setup page at this address
      </h1>
      <p className="mt-2 text-ink-dim text-sm leading-relaxed">
        Every page is listed beside this one. {DOCS.length} of them cover sending data from a
        language, a platform, a Collector or a browser.
      </p>
    </main>
  );
}
