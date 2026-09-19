import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  Outlet,
} from "@tanstack/react-router";
import { AlertsScreen } from "@/features/alerts/AlertsScreen";
import { ChannelDetailScreen } from "@/features/alerts/ChannelDetailScreen";
import { ChannelsScreen } from "@/features/alerts/ChannelsScreen";
import { EditRuleScreen, NewRuleScreen, parseNewRuleSearch } from "@/features/alerts/RuleEditor";
import { AccountScreen } from "@/features/auth/AccountScreen";
import { ForgotPasswordScreen } from "@/features/auth/ForgotPasswordScreen";
import { GeneralScreen } from "@/features/auth/GeneralScreen";
import { LoginScreen } from "@/features/auth/LoginScreen";
import { ResetPasswordScreen } from "@/features/auth/ResetPasswordScreen";
import { SignupScreen } from "@/features/auth/SignupScreen";
import { parseLoginSearch, parseTokenSearch } from "@/features/auth/search";
import { VerifyEmailScreen } from "@/features/auth/VerifyEmailScreen";
import { BillingScreen } from "@/features/billing/BillingScreen";
import { DocsScreen } from "@/features/docs/DocsScreen";
import { ErrorsScreen } from "@/features/errors/ErrorsScreen";
import { ERROR_FILTERS } from "@/features/errors/filters";
import { FindingsScreen } from "@/features/findings/FindingsScreen";
import { databaseFilters } from "@/features/infrastructure/databases/filters";
import { infrastructureFilters } from "@/features/infrastructure/filters";
import { InfrastructureScreen } from "@/features/infrastructure/InfrastructureScreen";
import { LogsScreen, scopedService } from "@/features/logs/LogsScreen";
import { severityFilterById } from "@/features/logs/severity";
import { metricFilters } from "@/features/metrics/filters";
import { MetricsScreen } from "@/features/metrics/MetricsScreen";
import { OnboardingScreen } from "@/features/onboarding/OnboardingScreen";
import { OverviewScreen } from "@/features/overview/OverviewScreen";
import { ProfilesScreen, parseProfilesSearch } from "@/features/profiles/ProfilesScreen";
import { ProjectsScreen } from "@/features/projects/ProjectsScreen";
import { rumFilters } from "@/features/rum/filters";
import { RumScreen } from "@/features/rum/RumScreen";
import { ServiceDetailScreen } from "@/features/servicedetail/ServiceDetailScreen";
import { SERVICE_MAP_FILTERS } from "@/features/servicemap/filters";
import { ServiceMapScreen } from "@/features/servicemap/ServiceMapScreen";
import { parseServiceOrder, SERVICE_FILTERS } from "@/features/services/filters";
import { ServicesScreen } from "@/features/services/ServicesScreen";
import { ApiKeysScreen } from "@/features/settings/ApiKeysScreen";
import { CheckDetailScreen, parseCheckSearch } from "@/features/synthetics/CheckDetailScreen";
import { SYNTHETIC_FILTERS } from "@/features/synthetics/filters";
import { ImportScreen } from "@/features/synthetics/ImportScreen";
import { NewCheckScreen, parseNewCheckSearch } from "@/features/synthetics/NewCheckScreen";
import { SyntheticsScreen } from "@/features/synthetics/SyntheticsScreen";
import { parseTraceFilters, toSearchParams } from "@/features/traces/filters";
import { TraceListScreen } from "@/features/traces/TraceListScreen";
import { TraceScreen } from "@/features/traces/TraceScreen";
import { UsageScreen } from "@/features/usage/UsageScreen";
import { ErrorScreen } from "@/shared/components/ErrorScreen";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { parseFilterValues } from "@/shared/filters";

import { AppShell } from "./layout/AppShell";

/**
 * Code-based routing, not file-based.
 *
 * File-based routing generates src/routeTree.gen.ts, and biome.json — which this task
 * may not edit — has no exclusion for generated files, so the generated tree would have
 * to satisfy our lint and format rules on every regeneration. Switching later is a
 * mechanical change plus one ignore entry in biome.json.
 */

export interface RouterContext {
  queryClient: QueryClient;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
});

/**
 * Pathless layout route: everything signed-in renders inside the shell. The bare screens
 * — `/login`, `/signup`, `/onboarding` and the three recovery screens — deliberately sit
 * outside it: there is no project, time range or navigation to show yet.
 */
const shellRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "shell",
  component: AppShell,
});

/* ------------------------------------------------------------------ the screens */

/**
 * Overview holds `/` until the insight engine exists (OD2).
 *
 * Not "a dashboard became the front door": the founder's 2026-09-12 decision is
 * that insights and dashboards are both first-class, and the feed is still what
 * a person lands on once it is real. Until then `/` showing nothing was the
 * worse answer — a front door that says the product has not started.
 */
const overviewRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/",
  component: OverviewScreen,
});

const findingsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/findings",
  component: FindingsScreen,
});

const servicesRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/services",
  component: ServicesScreen,
  validateSearch: (search: Record<string, unknown>) => ({
    ...parseFilterValues(SERVICE_FILTERS, search),
    ...(parseServiceOrder(search.order) === "worst" ? {} : { order: "slowest" as const }),
  }),
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
  validateSearch: (search: Record<string, unknown>) =>
    parseFilterValues(SERVICE_MAP_FILTERS, search),
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
  validateSearch: (search: Record<string, unknown>) => parseFilterValues(ERROR_FILTERS, search),
});

const logsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/logs",
  component: LogsScreen,
  // The service and the severity live here so arriving from another screen can
  // land already narrowed, and so a narrowed view is a link someone can send.
  validateSearch: (search: Record<string, unknown>) => ({
    ...(scopedService(search.service) ? { service: scopedService(search.service) as string } : {}),
    ...(severityFilterById(String(search.severity ?? "")).id === "all"
      ? {}
      : { severity: severityFilterById(String(search.severity ?? "")).id }),
  }),
});

const infrastructureRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/infrastructure",
  component: InfrastructureScreen,
  // Hosts and databases are two views of one route, so it validates both sets.
  // Their keys are disjoint, so neither view can see the other's filters.
  validateSearch: (search: Record<string, unknown>) => ({
    ...parseFilterValues(infrastructureFilters([]), search),
    ...parseFilterValues(databaseFilters([]), search),
  }),
});

const metricsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/metrics",
  component: MetricsScreen,
  // The chosen metric and the filters both live in the address bar, so "look at
  // this one" and "look at these" are links somebody can paste rather than
  // clicks somebody has to describe.
  validateSearch: (search: Record<string, unknown>) => ({
    ...metricFilters(search),
    ...(typeof search.metric === "string" && search.metric ? { metric: search.metric } : {}),
  }),
});

const syntheticsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/synthetics",
  component: SyntheticsScreen,
  validateSearch: (search: Record<string, unknown>) => parseFilterValues(SYNTHETIC_FILTERS, search),
});

// Declared before `$checkId` reads nothing into order: TanStack ranks a static
// segment above a parameter, so `/synthetics/new` is never taken for a check id.
const newCheckRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/synthetics/new",
  component: NewCheckScreen,
  validateSearch: parseNewCheckSearch,
});

const importChecksRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/synthetics/import",
  component: ImportScreen,
});

const checkRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/synthetics/$checkId",
  component: CheckDetailScreen,
  validateSearch: parseCheckSearch,
});

const alertsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/alerts",
  component: AlertsScreen,
});

// A static segment outranks the parameter, so `new` is never read as a rule id.
const newRuleRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/alerts/rules/new",
  component: NewRuleScreen,
  validateSearch: parseNewRuleSearch,
});

const ruleRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/alerts/rules/$ruleId",
  component: EditRuleScreen,
});

const profilesRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/profiles",
  component: ProfilesRoute,
  validateSearch: parseProfilesSearch,
});

/** The route owns the search; the screen owns what it means. */
function ProfilesRoute() {
  return <ProfilesScreen search={profilesRoute.useSearch()} />;
}

const rumRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/rum",
  component: RumScreen,
  validateSearch: (search: Record<string, unknown>) => parseFilterValues(rumFilters({}), search),
});

/* Usage sits with the other settings now: it is an account fact — what this
   organization has ingested and what that costs — rather than a telemetry screen,
   and it was the one entry whose own time control disagreed with the top bar's. */
const settingsUsageRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/usage",
  component: UsageScreen,
});

const settingsApiKeysRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/api-keys",
  component: ApiKeysScreen,
});

const settingsChannelsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/channels",
  component: ChannelsScreen,
});

const settingsChannelRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/channels/$channelId",
  component: ChannelDetailScreen,
});

const settingsProjectsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/projects",
  component: ProjectsScreen,
});

const settingsBillingRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/billing",
  component: BillingScreen,
});

/**
 * Inside the shell like every other signed-in screen. It reads and writes the caller's
 * own account, so it is only reachable with a session — and the shell's own `/auth/me`
 * is what enforces that, the same way it does for the rest.
 */
/* "Profile & security" was two things wearing one name. What you are called and
   how the console looks to you is a preference; what is signed in as you is not. */
const settingsGeneralRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/general",
  component: GeneralScreen,
});

const settingsAccountRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/account",
  component: AccountScreen,
});

/* Bare screens — outside the shell. */

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginRoute,
  validateSearch: parseLoginSearch,
});

/** The route owns the search param; the screen owns everything after it. */
function LoginRoute() {
  return <LoginScreen notice={loginRoute.useSearch().notice} />;
}

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

/**
 * The setup documentation, signed out.
 *
 * **Outside the shell deliberately, and outside authentication.** Somebody
 * evaluating nexovue has to know what instrumenting their service costs before
 * they sign up, and somebody whose telemetry stopped at three in the morning
 * should not be asked to sign in to read the troubleshooting page. Nothing here
 * is tenant data.
 *
 * A splat rather than a route per page: the pages are folders of Markdown and
 * the router must not need editing every time one is written.
 */
const docsIndexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/docs",
  component: DocsScreen,
});

const docsPageRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/docs/$",
  component: DocsPageRoute,
});

function DocsPageRoute() {
  const { _splat } = docsPageRoute.useParams();
  // `/docs/` and `/docs` are the same page, not a missing one.
  return <DocsScreen slug={_splat?.replace(/\/$/, "") || undefined} />;
}

/**
 * The two screens a mailed link lands on, plus the form that asks for one.
 *
 * Unauthenticated and outside the shell like the rest: the customer clicks these from a
 * mail client, which is a different browser session from the one they signed up in. The
 * API builds the addresses in `auth/recovery.py::_link` and puts the token in the query
 * string, so these paths and that function have to agree.
 *
 * None of them belongs in route-manifest.ts — that table drives the sidebar, and neither
 * does /login or /signup.
 */

const verifyEmailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/verify-email",
  component: VerifyEmailRoute,
  validateSearch: parseTokenSearch,
});

/** The route owns the search param; the screen owns everything after it. */
function VerifyEmailRoute() {
  return <VerifyEmailScreen token={verifyEmailRoute.useSearch().token} />;
}

const resetPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/reset-password",
  component: ResetPasswordRoute,
  validateSearch: parseTokenSearch,
});

/** The route owns the search param; the screen owns everything after it. */
function ResetPasswordRoute() {
  return <ResetPasswordScreen token={resetPasswordRoute.useSearch().token} />;
}

const forgotPasswordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/forgot-password",
  component: ForgotPasswordScreen,
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
    overviewRoute,
    findingsRoute,
    alertsRoute,
    newRuleRoute,
    ruleRoute,
    servicesRoute,
    serviceRoute,
    serviceMapRoute,
    tracesRoute,
    traceRoute,
    errorsRoute,
    logsRoute,
    infrastructureRoute,
    metricsRoute,
    syntheticsRoute,
    newCheckRoute,
    importChecksRoute,
    checkRoute,
    rumRoute,
    profilesRoute,
    settingsGeneralRoute,
    settingsAccountRoute,
    settingsApiKeysRoute,
    settingsChannelsRoute,
    settingsChannelRoute,
    settingsProjectsRoute,
    settingsBillingRoute,
    settingsUsageRoute,
    notFoundRoute,
  ]),
  loginRoute,
  signupRoute,
  onboardingRoute,
  docsIndexRoute,
  docsPageRoute,
  verifyEmailRoute,
  resetPasswordRoute,
  forgotPasswordRoute,
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
