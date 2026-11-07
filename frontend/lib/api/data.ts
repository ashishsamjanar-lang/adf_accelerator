"use client";

import {
  EMPTY_ARTIFACTS,
  EMPTY_AUDIT,
  EMPTY_ENVIRONMENTS,
  EMPTY_PIPELINES,
  EMPTY_PROJECTS,
  EMPTY_TEAMS,
  EMPTY_USER,
  EMPTY_USERS,
} from "@/lib/api/empty-values";
import { api } from "./client";
import { useResource } from "./hooks";

// Module-level fetchers *and* fallbacks keep stable identities so
// useResource effects run once instead of on every render.
const fetchCurrentUser = () => api.auth.me();
const fetchTeams = () => api.teams.list();
const fetchProjects = () => api.projects.list();
const fetchEnvironments = () => api.environments.list();
const fetchPipelines = () => api.pipelines.list();
const fetchArtifacts = () => api.artifacts.list();
const fetchUsers = () => api.admin.users();
const fetchAudit = () => api.admin.audit();

export function useAcceleratorData() {
  const currentUser = useResource(fetchCurrentUser, EMPTY_USER, "current-user");
  const teams = useResource(fetchTeams, EMPTY_TEAMS, "teams");
  const projects = useResource(fetchProjects, EMPTY_PROJECTS, "projects");
  const environments = useResource(fetchEnvironments, EMPTY_ENVIRONMENTS, "environments");
  const pipelines = useResource(fetchPipelines, EMPTY_PIPELINES, "pipelines");
  const artifacts = useResource(fetchArtifacts, EMPTY_ARTIFACTS, "artifacts");
  const users = useResource(fetchUsers, EMPTY_USERS, "users");
  const audit = useResource(fetchAudit, EMPTY_AUDIT, "audit");

  return { currentUser, teams, projects, environments, pipelines, artifacts, users, audit };
}

export type AcceleratorData = ReturnType<typeof useAcceleratorData>;
