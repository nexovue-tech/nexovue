import { useLocation, useNavigate, useRouter, useSearch } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useProjects } from "@/shared/projects";
import type { WindowChoice } from "@/shared/selection";
import {
  clearScreenOverride,
  readRememberedSelection,
  readScreenOverride,
  setScreenOverride,
  useSelection,
  windowCacheKey,
} from "@/shared/selection";

import type { AddressProject, DashboardSearch } from "./dashboard-search";
import {
  addressProject,
  addressWindow,
  parseDashboardSearch,
  windowSearch,
} from "./dashboard-search";

/**
 * Where a dashboard's project, window and service come from (F20, FR28, FR53, design
 * section 7): **the address wins for this page**, the top bar agrees with it, and none of
 * it is remembered until the reader chooses it on purpose.
 *
 * - The address's project and window become the screen's override (`selection.ts`), so the
 *   top bar and the page cannot disagree, and `remember()` never sees them (AC69).
 * - **No read runs until the store and the address agree** (`ready`), so a link naming
 *   project B never reaches project A first and never stamps "last opened" on it (AC27).
 * - A drag pushes one history entry, so browser Back returns to the earlier window (AC103).
 *   Any other change replaces the entry.
 * - A reader's own choice in the top bar ends the override for that part, and the address
 *   follows it, replacing the entry.
 */

export interface DashboardAddress {
  search: DashboardSearch;
  project: AddressProject;
  /** The address and the shown selection agree, so a read may run. */
  ready: boolean;
  projectId: string | undefined;
  choice: WindowChoice;
  service: string | undefined;
  display: boolean;
  /** The reader's own project, which "Back to production" returns to. */
  ownProjectId: string | undefined;
  /** The address names a project other than the reader's own. */
  fromLink: boolean;
  /** After a drag: the window it zoomed from, and Back returns to it (FR53). */
  zoomedFrom: WindowChoice | undefined;
  /** A drag: one new history entry. */
  zoom: (window: { from: string; to: string }) => void;
  /** Any change that is not a drag: the entry is replaced. */
  replaceWindow: (choice: WindowChoice | undefined) => void;
  /** Ends the link's project: the reader's own shows again. */
  backToOwnProject: () => void;
  /** Browser Back, to the window before the drag. */
  backFromZoom: () => void;
  setDisplay: (on: boolean) => void;
  /** The picked service: it replaces the entry, is never remembered, and reads once (AC44). */
  setService: (service: string | undefined) => void;
}

interface Held {
  projectId?: string;
  windowKey?: string;
}

export function useDashboardAddress(options: {
  defaultRange: string | null | undefined;
  zone: string | undefined;
}): DashboardAddress {
  const raw = useSearch({ strict: false }) as Record<string, unknown>;
  const search = useMemo(() => parseDashboardSearch(raw), [raw]);
  const projects = useProjects();
  const shown = useSelection();
  const router = useRouter();
  const navigate = useNavigate();
  const location = useLocation();
  const [zoomed, setZoomed] = useState<{ key: string; before: WindowChoice } | undefined>();
  const held = useRef<Held>({});
  // Where the page was opened: a later effect must not write to another screen's address.
  const path = useRef(location.pathname);

  const project = addressProject(search, projects.data, projects.isError);
  const wanted = useMemo(
    () => addressWindow(search, options.defaultRange),
    [search, options.defaultRange],
  );
  const wantedKey = wanted ? windowCacheKey(wanted) : undefined;
  const projectWanted = project.state === "ready" ? project.id : undefined;

  const write = useCallback(
    (next: DashboardSearch, replace: boolean) => {
      void navigate({
        href: `${path.current}${router.options.stringifySearch(next)}`,
        replace,
      });
    },
    [navigate, router],
  );

  // Apply the address to the override, or follow a choice the reader made on purpose.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `write` is stable and `search` is read through the values below
  useEffect(() => {
    // Gone to another screen (a service link adopts the selection): nothing to write.
    if (router.history.location.pathname !== path.current) return;
    const own = readScreenOverride();
    const droppedProject = held.current.projectId !== undefined && own.projectId === undefined;
    const droppedWindow = held.current.windowKey !== undefined && own.window === undefined;
    if (droppedProject || droppedWindow) {
      // The top bar changed it on purpose, which ends the override for that part: the
      // address follows the reader's choice, and the choice is remembered as anywhere.
      const remembered = readRememberedSelection();
      held.current = {
        projectId: droppedProject ? undefined : held.current.projectId,
        windowKey: droppedWindow ? undefined : held.current.windowKey,
      };
      write(
        {
          ...search,
          ...(droppedProject && remembered.projectId ? { project: remembered.projectId } : {}),
          ...(droppedWindow
            ? {
                range: undefined,
                from: undefined,
                to: undefined,
                ...windowSearch(remembered.window),
              }
            : {}),
        },
        true,
      );
      return;
    }

    if (projectWanted !== undefined) {
      if (own.projectId !== projectWanted) setScreenOverride({ projectId: projectWanted });
      held.current.projectId = projectWanted;
    } else if (project.state === "none" && own.projectId !== undefined) {
      setScreenOverride({ projectId: undefined });
      held.current.projectId = undefined;
    }

    if (wanted !== undefined) {
      if (own.window === undefined || windowCacheKey(own.window) !== wantedKey) {
        setScreenOverride({ window: wanted });
      }
      held.current.windowKey = wantedKey;
    } else if (own.window !== undefined) {
      setScreenOverride({ window: undefined });
      held.current.windowKey = undefined;
    }

    if (own.zone !== options.zone) setScreenOverride({ zone: options.zone });
  }, [
    projectWanted,
    project.state,
    wantedKey,
    options.zone,
    shown.projectId,
    shown.window,
    router,
  ]);

  // The screen's project, window and clock end with it: the reader's own show again (AC69).
  useEffect(() => clearScreenOverride, []);

  const projectAgrees =
    project.state === "none" || (project.state === "ready" && shown.projectId === project.id);
  const windowAgrees = wanted === undefined || windowCacheKey(shown.window) === wantedKey;
  const ready = projectAgrees && windowAgrees;

  const zoom = useCallback(
    (window: { from: string; to: string }) => {
      const next: WindowChoice = { kind: "absolute", ...window };
      const before = readScreenOverride().window ?? readRememberedSelection().window;
      setZoomed({ key: windowCacheKey(next), before });
      write({ ...search, range: undefined, from: window.from, to: window.to }, false);
    },
    [search, write],
  );

  const replaceWindow = useCallback(
    (choice: WindowChoice | undefined) => {
      const { range: _range, from: _from, to: _to, ...rest } = search;
      write({ ...rest, ...(choice ? windowSearch(choice) : {}) }, true);
    },
    [search, write],
  );

  const backToOwnProject = useCallback(() => {
    const { project: _project, ...rest } = search;
    write(rest, true);
  }, [search, write]);

  const backFromZoom = useCallback(() => router.history.back(), [router]);

  const setDisplay = useCallback(
    (on: boolean) => {
      const { display: _display, ...rest } = search;
      write({ ...rest, ...(on ? { display: 1 as const } : {}) }, true);
    },
    [search, write],
  );

  const setService = useCallback(
    (service: string | undefined) => {
      const { service: _service, ...rest } = search;
      write({ ...rest, ...(service === undefined ? {} : { service }) }, true);
    },
    [search, write],
  );

  const ownProjectId = readRememberedSelection().projectId;
  return {
    search,
    project,
    ready,
    projectId: shown.projectId,
    choice: shown.window,
    service: search.service,
    display: search.display === 1,
    ownProjectId,
    fromLink: projectWanted !== undefined && projectWanted !== ownProjectId,
    zoomedFrom: zoomed?.key === windowCacheKey(shown.window) ? zoomed.before : undefined,
    zoom,
    replaceWindow,
    backToOwnProject,
    backFromZoom,
    setDisplay,
    setService,
  };
}
