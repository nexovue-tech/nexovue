import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { json } from "@codemirror/lang-json";
import { xml } from "@codemirror/lang-xml";
import {
  bracketMatching,
  HighlightStyle,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import {
  Annotation,
  Compartment,
  EditorState,
  type Extension,
  Transaction,
} from "@codemirror/state";
import { EditorView, keymap, lineNumbers } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { useEffect, useId, useRef } from "react";

/**
 * A code field over CodeMirror 6, used through its own packages rather than a React
 * wrapper. The view is imperative: it is built once, and later prop changes are
 * dispatched into it — a rebuilt view would lose the cursor, the selection and undo.
 *
 * Tab is deliberately unbound (`indentWithTab` is not used), so Tab still leaves the
 * editor; binding it would trap a keyboard user inside the field.
 */

export type CodeLanguage = "json" | "xml" | "text";

const DEFAULT_MIN_LINES = 4;
const DEFAULT_MAX_LINES = 24;

/** `.cm-content` pads 4px top and bottom; line heights are the text-xs token's. */
const CONTENT_VERTICAL_PADDING = "8px";
const LINE_HEIGHT = "var(--text-xs--line-height)";

/** Built once: the same instance lets a reconfigure be skipped when nothing changed. */
const LANGUAGE_SUPPORT: Record<CodeLanguage, Extension> = {
  json: json(),
  xml: xml(),
  text: [],
};

const languageSlot = new Compartment();
const editabilitySlot = new Compartment();
const attributesSlot = new Compartment();
const heightSlot = new Compartment();

/** Marks a change that came from the `value` prop, so it is not echoed to `onChange`. */
const fromValueProp = Annotation.define<boolean>();

/**
 * Colours are token variables, so the editor follows the theme with no `dark` variant.
 * Selectors mirror CodeMirror's base theme, which this outranks at equal specificity.
 */
const tokenTheme = EditorView.theme({
  "&": { color: "var(--color-ink)", backgroundColor: "transparent", fontSize: "var(--text-xs)" },
  // The host draws the focus ring, so it wraps the border rather than sitting inside it.
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: LINE_HEIGHT, overflow: "auto" },
  ".cm-content": { caretColor: "var(--color-ink)" },
  ".cm-gutters": {
    backgroundColor: "var(--color-surface)",
    color: "var(--color-ink-dim)",
    borderRight: "1px solid var(--color-line-subtle)",
  },
  "&.cm-focused .cm-matchingBracket": {
    backgroundColor: "var(--color-active)",
    outline: "1px solid var(--color-line-strong)",
  },
  "&.cm-focused .cm-nonmatchingBracket": {
    backgroundColor: "var(--color-critical-subtle)",
    outline: "1px solid var(--color-critical-border)",
  },
});

/**
 * Syntax is carried by weight and the two ink levels, not by hue: every hue in the
 * palette already means a status, a series, or "clickable".
 */
const tokenHighlight = HighlightStyle.define([
  { tag: [tags.propertyName, tags.tagName], fontWeight: "var(--font-weight-medium)" },
  {
    tag: [
      tags.punctuation,
      tags.bracket,
      tags.separator,
      tags.angleBracket,
      tags.attributeName,
      tags.comment,
      tags.processingInstruction,
      tags.documentMeta,
    ],
    color: "var(--color-ink-dim)",
  },
  {
    tag: tags.invalid,
    textDecoration: "underline wavy var(--color-critical-solid)",
  },
]);

function editability(readOnly: boolean): Extension {
  // Read-only stays focusable, so a keyboard user can still move through and copy a response.
  return EditorState.readOnly.of(readOnly);
}

function contentAttributes(label: string, readOnly: boolean, errorId: string | undefined) {
  const attributes: Record<string, string> = { "aria-label": label };
  if (readOnly) attributes["aria-readonly"] = "true";
  if (errorId) {
    attributes["aria-describedby"] = errorId;
    attributes["aria-invalid"] = "true";
  }
  return EditorView.contentAttributes.of(attributes);
}

function height(minLines: number, maxLines: number): Extension {
  const linesTall = (lines: number) =>
    `calc(${lines} * ${LINE_HEIGHT} + ${CONTENT_VERTICAL_PADDING})`;
  return EditorView.theme({
    "&": { maxHeight: linesTall(Math.max(minLines, maxLines)) },
    ".cm-content, .cm-gutter": { minHeight: linesTall(minLines) },
  });
}

export function CodeEditor({
  label,
  value,
  onChange,
  language,
  readOnly = false,
  minLines = DEFAULT_MIN_LINES,
  maxLines = DEFAULT_MAX_LINES,
  error,
}: {
  /** Accessible name. */
  label: string;
  value: string;
  onChange?: (value: string) => void;
  language: CodeLanguage;
  readOnly?: boolean;
  /** Lines of height before scrolling. */
  minLines?: number;
  maxLines?: number;
  /** Error text under the editor, wired to aria-describedby. */
  error?: string;
}) {
  const errorId = `${useId()}-error`;
  const describedBy = error ? errorId : undefined;
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const props = { value, onChange, language, readOnly, minLines, maxLines, label, describedBy };
  const latest = useRef(props);

  useEffect(() => {
    latest.current = props;
  });

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const initial = latest.current;

    const view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc: initial.value,
        extensions: [
          lineNumbers(),
          history(),
          indentOnInput(),
          bracketMatching(),
          syntaxHighlighting(tokenHighlight),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          EditorView.lineWrapping,
          tokenTheme,
          languageSlot.of(LANGUAGE_SUPPORT[initial.language]),
          editabilitySlot.of(editability(initial.readOnly)),
          attributesSlot.of(
            contentAttributes(initial.label, initial.readOnly, initial.describedBy),
          ),
          heightSlot.of(height(initial.minLines, initial.maxLines)),
          EditorView.updateListener.of((update) => {
            if (!update.docChanged) return;
            if (update.transactions.some((transaction) => transaction.annotation(fromValueProp))) {
              return;
            }
            latest.current.onChange?.(update.state.doc.toString());
          }),
        ],
      }),
    });
    viewRef.current = view;

    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, []);

  // Typing round-trips through the parent and comes back equal to the doc, so it is
  // skipped here; only a change made outside the editor replaces the document.
  useEffect(() => {
    const view = viewRef.current;
    if (!view || view.state.doc.toString() === value) return;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: value },
      // Not undoable: undo would otherwise walk the reader back into a document they never typed.
      annotations: [fromValueProp.of(true), Transaction.addToHistory.of(false)],
    });
  }, [value]);

  useEffect(() => {
    const view = viewRef.current;
    const support = LANGUAGE_SUPPORT[language];
    if (!view || languageSlot.get(view.state) === support) return;
    view.dispatch({ effects: languageSlot.reconfigure(support) });
  }, [language]);

  useEffect(() => {
    viewRef.current?.dispatch({
      effects: [
        editabilitySlot.reconfigure(editability(readOnly)),
        attributesSlot.reconfigure(contentAttributes(label, readOnly, describedBy)),
      ],
    });
  }, [label, readOnly, describedBy]);

  useEffect(() => {
    viewRef.current?.dispatch({ effects: heightSlot.reconfigure(height(minLines, maxLines)) });
  }, [minLines, maxLines]);

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div
        ref={hostRef}
        className={`min-w-0 overflow-hidden rounded-sm border bg-surface focus-within:outline-2 focus-within:outline-brand focus-within:outline-offset-1 ${
          error ? "border-critical-solid" : "border-line hover:border-line-strong"
        }`}
      />
      {error ? (
        <p id={errorId} className="text-critical-text text-xs">
          {error}
        </p>
      ) : null}
    </div>
  );
}
