import { Component, type ReactNode } from "react";

/*
 * Crash guard. If anything below throws during render, the user sees a calm
 * fallback instead of a white screen. Error boundaries must be class
 * components — hooks can't catch render errors.
 *
 * One boundary, at the app root, rendering the full-page recovery card. A
 * `fallback` prop for containing smaller failures used to live here with no
 * caller — add it back when a second boundary actually needs it.
 */
interface ErrorBoundaryState {
  hasError: boolean;
}

export class ErrorBoundary extends Component<
  { children: ReactNode },
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="mx-auto w-full max-w-3xl px-4 sm:px-6 py-16">
          <div className="rounded-lg border border-divider bg-surface p-6 flex flex-col items-start gap-3 shadow-xs">
            <h3>Something went wrong.</h3>
            <p className="m-0 text-foreground/70">
              The page hit an unexpected error. Nothing is lost — restart to try
              again.
            </p>
            <button
              onClick={() => this.setState({ hasError: false })}
              className="inline-flex items-center justify-start gap-1.5 text-left font-semibold text-sm bg-accent text-white hover:bg-accent-600 active:bg-accent-700 cursor-pointer min-h-9 px-3.5 py-2 rounded-sm"
            >
              Restart
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
