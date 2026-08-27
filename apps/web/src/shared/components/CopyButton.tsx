import { Check, Copy } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { logger } from "../logger";
import { showToast } from "../toast/toast-store";

/**
 * Copy to the clipboard, with the outcome shown on the control itself.
 *
 * `navigator.clipboard` exists only in a secure context, so a deployment served over
 * plain HTTP has no clipboard at all. There is no fallback: `document.execCommand` is
 * deprecated and unreliable, and every value we offer to copy is already `select-all`,
 * so the manual path is one gesture away. We say so instead of failing silently.
 */

const CONFIRMATION_MS = 2000;

export function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), CONFIRMATION_MS);
    return () => clearTimeout(timer);
  }, [copied]);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch (cause) {
      logger.warn("Clipboard write refused", { secure_context: window.isSecureContext });
      showToast("Could not reach the clipboard.", {
        tone: "warning",
        key: "clipboard-unavailable",
        detail:
          cause instanceof Error && !window.isSecureContext
            ? "The browser only allows this over HTTPS. Select the value and copy it."
            : "Select the value and copy it.",
      });
    }
  }, [value]);

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? `${label} copied` : `Copy ${label}`}
      className="flex shrink-0 items-center gap-1.5 rounded-sm border border-line px-2 py-1 font-mono text-2xs text-ink-dim uppercase tracking-caps hover:border-line-strong hover:text-ink"
    >
      {copied ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
