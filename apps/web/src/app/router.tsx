import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  Outlet,
} from "@tanstack/react-router";

import { LoginScreen } from "@/features/auth/LoginScreen";
import { SignupScreen } from "@/features/auth/SignupScreen";
import { DatabaseScreen } from "@/features/database/DatabaseScreen";
import { ErrorsScreen } from "@/features/errors/ErrorsScreen";
import { InfrastructureScreen } from "@/features/infrastructure/InfrastructureScreen";
import { LogsScreen } from "@/features/logs/LogsScreen";
import { OnboardingScreen } from "@/features/onboarding/OnboardingScreen";
import { RumScreen } from "@/features/rum/RumScreen";
import { ServiceDetailScreen } from "@/features/servicedetail/ServiceDetailScreen";
import { ServiceMapScreen } from "@/features/servicemap/ServiceMapScreen";
import { ServicesScreen } from "@/features/services/ServicesScreen";
import { ApiKeysScreen } from "@/features/settings/ApiKeysScreen";
import { SyntheticsScreen } from "@/features/synthetics/SyntheticsScreen";
import { parseTraceFilters, toSearchParams } from "@/features/traces/filters";
import { TraceListScreen } from "@/features/traces/TraceListScreen";
import { TraceScreen } from "@/features/traces/TraceScreen";
import { UsageScreen } from "@/features/usage/UsageScreen";
import { ErrorScreen } from "@/shared/components/ErrorScreen";
import { SkeletonRows } from "@/shared/components/Skeleton";

import { AppShell } from "./layout/AppShell";
import { placeholderFor } from "./routes/placeholder";

/**
 * Code-based routing, not file-based.
 *
 * File-based routing generates src/routeTree.gen.ts, and biome.json — which this task
 * may not edit — has no exclusion for generated files, so the generated tree would have
 * to satisfy our lint and format rules on every regeneration. Twenty placeholder screens
 * also read better as one table than as twenty near-identical files. Switching later is
 * a mechanical change plus one ignore entry in biome.json.
 */

export interface RouterContext {
  queryClient: QueryClient;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
});

/**
 * Pathless layout route: everything signed-in renders inside the shell. `/login`,
 * `/signup` and `/onboarding` deliberately sit outside it — there is no project, time
 * range or navigation to show yet.
 */
const shellRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "shell",
  component: AppShell,
});

/* ------------------------------------------------------------------ the screens */

const findingsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/",
  component: placeholderFor("/"),
});

const servicesRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/services",
  component: ServicesScreen,
});

const serviceRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/services/$name",
  component: ServiceDetailRoute,
});

/** The route owns the parameter; the screen owns everything after it. */
function ServiceDetailRoute() {
  const { name } = serviceRoute.useParams();
  return <ServiceDetailScreen serviceName={name} />;
}

const serviceMapRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/service-map",
  component: ServiceMapScreen,
});

/**
 * The only route with search params: the trace filters live in the URL so a person
 * can share the view they found. The schema is the feature's, not the router's —
 * this route only declares that it has one.
 */
const tracesRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/traces",
  component: TraceListScreen,
  validateSearch: (search: Record<string, unknown>) => toSearchParams(parseTraceFilters(search)),
});

const traceRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/traces/$traceId",
  component: TraceWaterfallScreen,
});

/** The route owns the parameter; the screen owns everything after it. */
function TraceWaterfallScreen() {
  const { traceId } = traceRoute.useParams();
  return <TraceScreen traceId={traceId} />;
}

const errorsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/errors",
  component: ErrorsScreen,
});

const logsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/logs",
  component: LogsScreen,
});

const infrastructureRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/infrastructure",
  component: InfrastructureScreen,
});

const syntheticsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/synthetics",
  component: SyntheticsScreen,
});

const databaseRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/database",
  component: DatabaseScreen,
});

const rumRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/rum",
  component: RumScreen,
});

const usageRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/usage",
  component: UsageScreen,
});

const settingsApiKeysRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/api-keys",
  component: ApiKeysScreen,
});

/* Bare screens — outside the shell. */

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginScreen,
});

const signupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/signup",
  component: SignupScreen,
});

const onboardingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/onboarding",
  component: OnboardingScreen,
});

/* ---------------------------------------------------------------------- router */

/** Catches every path the routes above do not, so the shell stays around it. */
const notFoundRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "$",
  component: NotFound,
});

const routeTree = rootRoute.addChildren([
  shellRoute.addChildren([
    findingsRoute,
    servicesRoute,
    serviceRoute,
    serviceMapRoute,
    tracesRoute,
    traceRoute,
    errorsRoute,
    logsRoute,
    infrastructureRoute,
    syntheticsRoute,
    databaseRoute,
    rumRoute,
    usageRoute,
    settingsApiKeysRoute,
    notFoundRoute,
  ]),
  loginRoute,
  signupRoute,
  onboardingRoute,
]);

function RouteError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <ErrorScreen error={error} correlationId={crypto.randomUUID()} onRetry={reset} scope="route" />
  );
}

/**
 * Rendered inside the shell, as a child of `shellRoute`. A person who mistypes a URL is
 * already inside the app, and the one thing they need is the way back — which is the
 * sidebar this copy points at.
 */
function NotFound() {
  return (
    <section className="mx-auto max-w-2xl px-8 py-16">
      <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">404</p>
      <h1 className="mt-3 font-semibold font-serif text-2xl text-ink tracking-tight">
        No screen at this address
      </h1>
      <p className="mt-3 text-ink-dim text-md">
        Every Phase 1 route is listed in the sidebar. This one is not one of them.
      </p>
    </section>
  );
}

export function createAppRouter(queryClient: QueryClient) {
  return createRouter({
    routeTree,
    context: { queryClient },
    // Route-level boundary: a screen that throws loses the screen, not the shell.
    defaultErrorComponent: RouteError,
    defaultNotFoundComponent: NotFound,
    defaultPendingComponent: () => <SkeletonRows rows={10} label="Loading screen" />,
    defaultPreload: "intent",
    scrollRestoration: true,
  });
}

export type AppRouter = ReturnType<typeof createAppRouter>;

declare module "@tanstack/react-router" {
  interface Register {
    router: AppRouter;
  }
}
