import { Component, type ErrorInfo, type ReactNode } from "react";

import { ApiError } from "../api-client";
import { logger } from "../logger";
import { ErrorScreen } from "./ErrorScreen";

/**
 * The last line before a white page.
 *
 * Still a class: React 19 has no hook form of componentDidCatch. Errors it catches are
 * also reported through createRoot's onCaughtError in main.tsx — this one owns what the
 * user sees, that one owns what we record.
 */

interface ErrorBoundaryProps {
  children: ReactNode;
  scope?: "app" | "route";
}

interface ErrorBoundaryState {
  error: unknown;
  correlationId: string;
}

const NO_ERROR: ErrorBoundaryState = { error: undefined, correlationId: "" };

/** A failure with no server correlation id still needs one the user can quote. */
function correlationIdFor(error: unknown): string {
  return error instanceof ApiError ? error.context.correlationId : crypto.randomUUID();
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = NO_ERROR;

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error, correlationId: correlationIdFor(error) };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    logger.error("Render failed inside an error boundary", error, {
      correlation_id: this.state.correlationId,
      scope: this.props.scope ?? "app",
      component_stack: info.componentStack,
    });
  }

  private readonly reset = () => this.setState(NO_ERROR);

  render(): ReactNode {
    if (this.state.error === undefined) return this.props.children;

    return (
      <ErrorScreen
        error={this.state.error}
        correlationId={this.state.correlationId}
        onRetry={this.reset}
        scope={this.props.scope}
      />
    );
  }
}
