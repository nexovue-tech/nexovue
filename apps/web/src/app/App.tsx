import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { useMemo } from "react";

import { ErrorBoundary } from "@/shared/components/ErrorBoundary";
import { ToastRegion } from "@/shared/toast/ToastRegion";

import { createQueryClient, setLoginRedirect } from "./query-client";
import { createAppRouter } from "./router";

/**
 * Composition root. This is the only module that knows about all of the router, the
 * query cache and the shared failure surfaces at once.
 */
export function App() {
  const { queryClient, router } = useMemo(() => {
    const client = createQueryClient();
    const appRouter = createAppRouter(client);

    // The 401 rule lives in query-client.ts; the router is only how it gets carried out.
    setLoginRedirect(() => appRouter.navigate({ to: "/login", replace: true }));

    return { queryClient: client, router: appRouter };
  }, []);

  return (
    <ErrorBoundary scope="app">
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
        <ToastRegion />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
