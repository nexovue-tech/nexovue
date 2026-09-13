/**
 * The project list the top bar selects from and every data screen is scoped by.
 *
 * It lives in shared/ rather than in a feature because both sides need it and neither
 * may import the other: the top bar is app/, the screens are features/.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { apiClient } from "./api-client";
import { setSelectedProjectId, useSelection } from "./selection";

export interface Project {
  id: string;
  slug: string;
  name: string;
  /**
   * The first day anything this project sent reached us, or null when nothing
   * ever has. Read by the API from the ingest meter, whose rows outlive every
   * retention window — so a project that reported once answers with a date for
   * ever, however long it has been quiet since.
   */
  first_reported_on: string | null;
}

interface ProjectsResponse {
  projects: Project[];
}

export const projectsQueryKey = ["projects"] as const;

export function useProjects() {
  return useQuery({
    queryKey: projectsQueryKey,
    queryFn: () => apiClient.get<ProjectsResponse>("projects"),
    select: (response) => response.projects,
  });
}

/**
 * Whether anything has ever arrived for this project — the fact an empty screen
 * needs before it decides which empty screen it is.
 *
 * `undefined` until the list has answered, and a screen must treat that as "do
 * not know" rather than as "never": telling a customer with working
 * instrumentation to go and instrument it is the one mistake here that costs
 * something. The opposite mistake costs a sentence.
 *
 * No request of its own. The project list is fetched once by the top bar and
 * carries this, which is why the field lives on the summary rather than on the
 * project's settings document.
 */
export function useEverReported(projectId: string | undefined): boolean | undefined {
  const { data: projects } = useProjects();
  const project = projects?.find((candidate) => candidate.id === projectId);
  return project === undefined ? undefined : project.first_reported_on !== null;
}

/**
 * Nothing can be queried without a project, so the first one becomes the selection
 * until the person chooses otherwise. Mounted once, by the top bar.
 */
export function useProjectSelection() {
  const { data: projects, isPending, isError } = useProjects();
  const { projectId } = useSelection();

  useEffect(() => {
    const first = projects?.[0];
    if (!first) return;
    if (projectId && projects.some((project) => project.id === projectId)) return;
    setSelectedProjectId(first.id);
  }, [projects, projectId]);

  return { projects: projects ?? [], projectId, isPending, isError };
}
