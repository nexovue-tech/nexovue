import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { installGlobalHandlers } from "./shared/global-handlers";
import { logger } from "./shared/logger";

import "./index.css";

const container = document.getElementById("root");
if (!container) throw new Error("index.html is missing #root");

installGlobalHandlers();

createRoot(container, {
  // React 19 no longer re-throws. Without these two, every render failure would reach
  // window.reportError or console.error and never reach us. See the React 19 upgrade
  // guide: https://react.dev/blog/2024/04/25/react-19-upgrade-guide
  onUncaughtError: (error, errorInfo) => {
    logger.error("Uncaught render error", error, {
      component_stack: errorInfo.componentStack,
      caught_by_boundary: false,
    });
  },
  onCaughtError: (error, errorInfo) => {
    logger.error("Render error caught by a boundary", error, {
      component_stack: errorInfo.componentStack,
      error_boundary: errorInfo.errorBoundary?.constructor?.name,
      caught_by_boundary: true,
    });
  },
  onRecoverableError: (error) => {
    logger.warn("React recovered from an error", { message: String(error) });
  },
}).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
