import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  type ErrorComponentProps,
  Link,
  lazyRouteComponent,
  Outlet,
} from "@tanstack/react-router";
import { parseNewRuleSearch } from "@/features/alerts/search";
import { LoginScreen } from "@/features/auth/LoginScreen";
import { parseLoginSearch, parseTokenSearch } from "@/features/auth/search";
import { ERROR_FILTERS } from "@/features/errors/filters";
import { databaseFilters } from "@/features/infrastructure/databases/filters";
import { infrastructureFilters } from "@/features/infrastructure/filters";
import { llmCallFilters } from "@/features/llm-observability/filters";
import { scopedService } from "@/features/logs/search";
import { severityFilterById } from "@/features/logs/severity";
import { metricFilters } from "@/features/metrics/filters";
import { parseProfilesSearch } from "@/features/profiles/search";
import { rumFilters } from "@/features/rum/filters";
import { SERVICE_MAP_FILTERS } from "@/features/servicemap/filters";
import { parseServiceOrder, SERVICE_FILTERS } from "@/features/services/filters";
import { SYNTHETIC_FILTERS } from "@/features/synthetics/filters";
import { parseCheckSearch, parseNewCheckSearch } from "@/features/synthetics/search";
import { parseTraceFilters, toSearchParams } from "@/features/traces/filters";
import { ErrorScreen } from "@/shared/components/ErrorScreen";
import { SkeletonRows } from "@/shared/components/Skeleton";
import { parseFilterValues } from "@/shared/filters";

import { HostedOnly } from "./HostedOnly";
import { AppShell } from "./layout/AppShell";

// Each screen is its own chunk, fetched when its route is first opened (group 9,
// D3): eagerly, every route shipped in one 1.3 MB file. Sign-in and the error
// screen stay in the first load; so do the screens whose module also exports
// what a route needs before rendering, such as a search parser.
const OnboardScreen = lazyRouteComponent(
  () => import("@/features/admin/OnboardScreen"),
  "OnboardScreen",
);
const OrganizationScreen = lazyRouteComponent(
  () => import("@/features/admin/OrganizationScreen"),
  "OrganizationScreen",
);
const PlatformScreen = lazyRouteComponent(
  () => import("@/features/admin/PlatformScreen"),
  "PlatformScreen",
);
const AlertsScreen = lazyRouteComponent(
  () => import("@/features/alerts/AlertsScreen"),
  "AlertsScreen",
);
const ChannelDetailScreen = lazyRouteComponent(
  () => import("@/features/alerts/ChannelDetailScreen"),
  "ChannelDetailScreen",
);
const ChannelsScreen = lazyRouteComponent(
  () => import("@/features/alerts/ChannelsScreen"),
  "ChannelsScreen",
);
const IncidentScreen = lazyRouteComponent(
  () => import("@/features/alerts/IncidentScreen"),
  "IncidentScreen",
);
const ApiTokensScreen = lazyRouteComponent(
  () => import("@/features/apitokens/ApiTokensScreen"),
  "ApiTokensScreen",
);
const LogsScreen = lazyRouteComponent(() => import("@/features/logs/LogsScreen"), "LogsScreen");
const ProfilesScreen = lazyRouteComponent(
  () => import("@/features/profiles/ProfilesScreen"),
  "ProfilesScreen",
);
const CheckDetailScreen = lazyRouteComponent(
  () => import("@/features/synthetics/CheckDetailScreen"),
  "CheckDetailScreen",
);
const NewCheckScreen = lazyRouteComponent(
  () => import("@/features/synthetics/NewCheckScreen"),
  "NewCheckScreen",
);
const EditRuleScreen = lazyRouteComponent(
  () => import("@/features/alerts/RuleEditor"),
  "EditRuleScreen",
);
const NewRuleScreen = lazyRouteComponent(
  () => import("@/features/alerts/RuleEditor"),
  "NewRuleScreen",
);
const AuditScreen = lazyRouteComponent(() => import("@/features/audit/AuditScreen"), "AuditScreen");
const AcceptInviteScreen = lazyRouteComponent(
  () => import("@/features/auth/AcceptInviteScreen"),
  "AcceptInviteScreen",
);
const AccountScreen = lazyRouteComponent(
  () => import("@/features/auth/AccountScreen"),
  "AccountScreen",
);
const ForgotPasswordScreen = lazyRouteComponent(
  () => import("@/features/auth/ForgotPasswordScreen"),
  "ForgotPasswordScreen",
);
const GeneralScreen = lazyRouteComponent(
  () => import("@/features/auth/GeneralScreen"),
  "GeneralScreen",
);
const ResetPasswordScreen = lazyRouteComponent(
  () => import("@/features/auth/ResetPasswordScreen"),
  "ResetPasswordScreen",
);
const SignupScreen = lazyRouteComponent(
  () => import("@/features/auth/SignupScreen"),
  "SignupScreen",
);
const VerifyEmailScreen = lazyRouteComponent(
  () => import("@/features/auth/VerifyEmailScreen"),
  "VerifyEmailScreen",
);
const BillingScreen = lazyRouteComponent(
  () => import("@/features/billing/BillingScreen"),
  "BillingScreen",
);
const DocsScreen = lazyRouteComponent(() => import("@/features/docs/DocsScreen"), "DocsScreen");
const ErrorsScreen = lazyRouteComponent(
  () => import("@/features/errors/ErrorsScreen"),
  "ErrorsScreen",
);
const FindingsScreen = lazyRouteComponent(
  () => import("@/features/findings/FindingsScreen"),
  "FindingsScreen",
);
const FindingScreen = lazyRouteComponent(
  () => import("@/features/findings/FindingScreen"),
  "FindingScreen",
);
const InfrastructureScreen = lazyRouteComponent(
  () => import("@/features/infrastructure/InfrastructureScreen"),
  "InfrastructureScreen",
);
const LLMCallsScreen = lazyRouteComponent(
  () => import("@/features/llm-observability/LLMCallsScreen"),
  "LLMCallsScreen",
);
const MembersScreen = lazyRouteComponent(
  () => import("@/features/members/MembersScreen"),
  "MembersScreen",
);
const MetricsScreen = lazyRouteComponent(
  () => import("@/features/metrics/MetricsScreen"),
  "MetricsScreen",
);
const OnboardingScreen = lazyRouteComponent(
  () => import("@/features/onboarding/OnboardingScreen"),
  "OnboardingScreen",
);
const OverviewScreen = lazyRouteComponent(
  () => import("@/features/overview/OverviewScreen"),
  "OverviewScreen",
);
const ProjectsScreen = lazyRouteComponent(
  () => import("@/features/projects/ProjectsScreen"),
  "ProjectsScreen",
);
const RumScreen = lazyRouteComponent(() => import("@/features/rum/RumScreen"), "RumScreen");
const ServiceDetailScreen = lazyRouteComponent(
  () => import("@/features/servicedetail/ServiceDetailScreen"),
  "ServiceDetailScreen",
);
const ServiceMapScreen = lazyRouteComponent(
  () => import("@/features/servicemap/ServiceMapScreen"),
  "ServiceMapScreen",
);
const ServicesScreen = lazyRouteComponent(
  () => import("@/features/services/ServicesScreen"),
  "ServicesScreen",
);
const ApiKeysScreen = lazyRouteComponent(
  () => import("@/features/settings/ApiKeysScreen"),
  "ApiKeysScreen",
);
const SupportAccessScreen = lazyRouteComponent(
  () => import("@/features/support/SupportAccessScreen"),
  "SupportAccessScreen",
);
const ImportScreen = lazyRouteComponent(
  () => import("@/features/synthetics/ImportScreen"),
  "ImportScreen",
);
const SyntheticsScreen = lazyRouteComponent(
  () => import("@/features/synthetics/SyntheticsScreen"),
  "SyntheticsScreen",
);
const TraceListScreen = lazyRouteComponent(
  () => import("@/features/traces/TraceListScreen"),
  "TraceListScreen",
);
const TraceScreen = lazyRouteComponent(
  () => import("@/features/traces/TraceScreen"),
  "TraceScreen",
);
const UsageScreen = lazyRouteComponent(() => import("@/features/usage/UsageScreen"), "UsageScreen");

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

// What every finding card and message links to (D12): one finding, its evidence and
// what a person may do about it.
const findingRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/findings/$findingId",
  component: FindingScreen,
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

const llmCallsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/llm",
  component: LLMCallsScreen,
  validateSearch: (search: Record<string, unknown>) => llmCallFilters(search),
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

// What every alert message links to: the incident outlives the check it was about.
const incidentRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/alerts/incidents/$incidentId",
  component: IncidentScreen,
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

/* nexovue's own area (`docs/changes/super-admin-in-the-console/`): inside the
   console, refused by the API and by the screen to anyone but the operator. */
const platformRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/admin",
  component: PlatformScreen,
});

const platformOnboardRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/admin/onboard",
  component: OnboardScreen,
});

const platformOrganizationRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/admin/organizations/$orgId",
  component: PlatformOrganizationRoute,
});

/** The route owns the address; the screen owns what is at it. */
function PlatformOrganizationRoute() {
  return <OrganizationScreen orgId={platformOrganizationRoute.useParams().orgId} />;
}

const settingsApiTokensRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/api-tokens",
  component: ApiTokensScreen,
});

const settingsMembersRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/members",
  component: MembersScreen,
});

const settingsAuditRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/audit",
  component: AuditScreen,
});

const settingsSupportRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/settings/support",
  component: () => (
    <HostedOnly fallback={<NotFound />}>
      <SupportAccessScreen />
    </HostedOnly>
  ),
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
  component: () => (
    <HostedOnly fallback={<NotFound />}>
      <BillingScreen />
    </HostedOnly>
  ),
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

// F26: where an invite email lands. Signed out, like the two recovery links above.
const acceptInviteRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/invite",
  component: AcceptInviteRoute,
  validateSearch: parseTokenSearch,
});

/** The route owns the search param; the screen owns everything after it. */
function AcceptInviteRoute() {
  return <AcceptInviteScreen token={acceptInviteRoute.useSearch().token} />;
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
    findingRoute,
    alertsRoute,
    newRuleRoute,
    ruleRoute,
    incidentRoute,
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
    llmCallsRoute,
    settingsGeneralRoute,
    settingsAccountRoute,
    settingsApiKeysRoute,
    settingsMembersRoute,
    platformRoute,
    platformOnboardRoute,
    platformOrganizationRoute,
    settingsApiTokensRoute,
    settingsAuditRoute,
    settingsSupportRoute,
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
  acceptInviteRoute,
  forgotPasswordRoute,
]);

// A route can throw anything, not only an Error; ErrorScreen describes whatever arrives.
function RouteError({ error, reset }: ErrorComponentProps) {
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
        Nothing is served at this address. Every screen this console has is in the menu.
      </p>
      {/* A link, not a sentence pointing at the sidebar: on a phone the sidebar is
          a drawer, and this screen used to leave a reader with nothing to press
          (`docs/quality/console-review-2026-09-20.md`). */}
      <Link to="/" className="mt-4 inline-block font-medium text-brand-text hover:underline">
        Back to Overview
      </Link>
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
