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
