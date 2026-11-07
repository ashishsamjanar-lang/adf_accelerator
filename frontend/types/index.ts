/**
 * Domain types for the ADF Pipeline Accelerator frontend.
 *
 * These mirror the backend API contracts described in AGENTS.md sections
 * 4 (core domain model), 5 (logical pipeline specification), 10 (artifact
 * state model), 12 (roles), and 13 (API design). The UI is driven by these
 * typed contracts; raw ADF JSON is never part of the primary domain model.
 *
 * This file is just a re-export barrel — `import type { X } from "@/types"`
 * keeps working exactly as before. The actual definitions live in one file
 * per domain, so a task touching only one area (e.g. Git, or the connector
 * catalog) can read that file instead of this whole module:
 *
 *   - core.ts              Role, EnvironmentName, DeploymentState, Severity, ...
 *   - connectors.ts        SourceTargetType, AuthenticationType, ConnectorInfo, ...
 *   - pipeline.ts          LogicalPipelineSpec and everything it's built from
 *   - org.ts               User, Team, Project, Environment
 *   - git.ts                GitSettings, GitPushRequestBody, GitPushResponse
 *   - project-settings.ts  ProjectSettings and its A/B/C/D sub-sections
 *   - api.ts               ConnectionRecord, ValidationResult, AuditEntry, ...
 *   - onboarding.ts        OnboardingObjectEntry (§26 bronze object registry)
 *   - sttm.ts              RegistryObject, SttmParseReport (§33 Silver/Gold object registry)
 *   - catalog.ts           CatalogEntry, CatalogChange, OnboardingGitDrift (§24.8 Git catalog, §26.6 drift)
 */

export * from "./core";
export * from "./connectors";
export * from "./pipeline";
export * from "./org";
export * from "./git";
export * from "./project-settings";
export * from "./api";
export * from "./onboarding";
export * from "./sttm";
export * from "./catalog";
