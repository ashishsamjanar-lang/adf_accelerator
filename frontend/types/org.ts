/**
 * Org/tenancy shapes: users, teams, projects, and the per-environment
 * connection info a project's environments resolve against (AGENTS.md
 * section 4, section 12 roles).
 */

import type { EnvironmentName, Role } from "./core";
import type { ProjectSettings } from "./project-settings";

export interface User {
  id: string;
  name: string;
  email: string;
  /** Login name for AUTH_MODE=password (lowercase, unique). Absent on SSO-only accounts. */
  username?: string | null;
  role: Role;
  teamId: string;
  active: boolean;
  lastSeen?: string;
  /** AUTH_MODE=password only (AGENTS.md §27.2 extension) — always false under mock/SSO. */
  mustChangePassword?: boolean;
}

export interface Team {
  id: string;
  name: string;
  description: string;
  memberCount: number;
  projectCount: number;
}

export interface Project {
  id: string;
  teamId: string;
  name: string;
  description: string;
  pipelineCount: number;
  /** Saved project settings aggregate (falls back to mock defaults). */
  settings?: ProjectSettings;
}

export interface EnvironmentConnection {
  name: EnvironmentName;
  storageAccount: string;
  sqlServer: string;
  database: string;
  keyVault: string;
  /** ADF factory this environment's generated global parameters (A-2) resolve against. */
  factoryName: string;
  /** Azure subscription the factory (and this environment's other resources) lives in. */
  subscriptionId: string;
  resourceGroup: string;
}

export interface Environment {
  name: EnvironmentName;
  label: string;
  status: "connected" | "approval_gated" | "not_configured";
  description: string;
  configuration: EnvironmentConnection;
}
