// @vitest-environment jsdom

import { deleteCharBackward, insertNewlineAndIndent } from "@codemirror/commands";
import { language } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { CodeEditor, type CodeLanguage } from "./CodeEditor";

const mounted: Root[] = [];

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  for (const root of mounted.splice(0)) act(() => root.unmount());
  document.body.replaceChildren();
});

interface EditorProps {
  label?: string;
  value: string;
  onChange?: (value: string) => void;
  language?: CodeLanguage;
  readOnly?: boolean;
  error?: string;
}

function render(initial: EditorProps) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.push(root);

  const draw = (props: EditorProps) =>
    act(() =>
      root.render(createElement(CodeEditor, { label: "Request body", language: "json", ...props })),
    );
  draw(initial);

  const view = () => {
    const editor = container.querySelector<HTMLElement>(".cm-editor");
    const found = editor ? EditorView.findFromDOM(editor) : null;
    if (!found) throw new Error("no editor view mounted");
    return found;
  };

  return {
    container,
    view,
    rerender: draw,
    content: () => view().contentDOM,
  };
}

describe("CodeEditor", () => {
  it("mounts with the value as its document and names itself", () => {
    const editor = render({ value: '{"ok": true}' });

    expect(editor.view().state.doc.toString()).toBe('{"ok": true}');
    expect(editor.content().getAttribute("aria-label")).toBe("Request body");
    expect(editor.content().getAttribute("role")).toBe("textbox");
  });

  it("reports an edit through onChange", () => {
    const changes: string[] = [];
    const editor = render({ value: "{}", onChange: (value) => changes.push(value) });
    const view = editor.view();

    act(() => view.dispatch({ changes: { from: 1, insert: '"a": 1' }, userEvent: "input.type" }));

    expect(changes).toEqual(['{"a": 1}']);
  });

  it("replaces the document when the value changes from outside, without echoing it", () => {
    const changes: string[] = [];
    const onChange = (value: string) => changes.push(value);
    const editor = render({ value: "{}", onChange });

    editor.rerender({ value: '{"loaded": true}', onChange });

    expect(editor.view().state.doc.toString()).toBe('{"loaded": true}');
    expect(changes).toEqual([]);
  });

  it("keeps the cursor when the value comes back equal to what was typed", () => {
    let latest = "{}";
    const editor = render({ value: latest, onChange: (value) => (latest = value) });
    const view = editor.view();

    act(() =>
      view.dispatch({
        changes: { from: 1, insert: "1" },
        selection: { anchor: 2 },
        userEvent: "input.type",
      }),
    );
    editor.rerender({ value: latest, onChange: (value) => (latest = value) });

    expect(latest).toBe("{1}");
    expect(view.state.selection.main.head).toBe(2);
    expect(editor.view()).toBe(view);
  });

  it("refuses edits while read-only, and says so", () => {
    const editor = render({ value: "{}", readOnly: true });
    const view = editor.view();

    expect(view.state.readOnly).toBe(true);
    expect(insertNewlineAndIndent(view)).toBe(false);
    view.dispatch({ selection: { anchor: 1 } });
    expect(deleteCharBackward(view)).toBe(false);
    expect(view.state.doc.toString()).toBe("{}");
    expect(editor.content().getAttribute("aria-readonly")).toBe("true");
  });

  it("switches read-only and language in place, without rebuilding the view", () => {
    const editor = render({ value: "<a/>", language: "text", readOnly: true });
    const view = editor.view();
    expect(view.state.facet(language)).toBeNull();

    editor.rerender({ value: "<a/>", language: "xml", readOnly: false });

    expect(editor.view()).toBe(view);
    expect(view.state.facet(language)?.name).toBe("xml");
    expect(view.state.readOnly).toBe(false);
    expect(editor.content().hasAttribute("aria-readonly")).toBe(false);
    expect(insertNewlineAndIndent(view)).toBe(true);
  });

  it("lets Tab leave the editor instead of indenting", () => {
    const editor = render({ value: "{}" });
    const tab = new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true });

    act(() => {
      editor.content().dispatchEvent(tab);
    });

    expect(tab.defaultPrevented).toBe(false);
    expect(editor.view().state.doc.toString()).toBe("{}");
  });

  it("describes itself by its error", () => {
    const editor = render({ value: "{", error: "Body is not valid JSON." });

    const describedBy = editor.content().getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")?.textContent).toBe("Body is not valid JSON.");
    expect(editor.content().getAttribute("aria-invalid")).toBe("true");

    editor.rerender({ value: "{}" });

    expect(editor.content().hasAttribute("aria-describedby")).toBe(false);
    expect(editor.container.textContent).not.toContain("Body is not valid JSON.");
  });

  it("destroys the view on unmount", () => {
    const editor = render({ value: "{}" });
    const view = editor.view();
    const root = mounted.pop();

    act(() => root?.unmount());

    expect(editor.container.querySelector(".cm-editor")).toBeNull();
    expect(view.dom.isConnected).toBe(false);
  });
});
