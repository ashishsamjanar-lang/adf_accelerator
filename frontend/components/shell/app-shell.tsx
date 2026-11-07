"use client";

import { useCallback, useState } from "react";
import { ApiStatusProvider, useAuthGate, useDevIdentity } from "@/lib/api/hooks";
import { LoginView } from "@/features/auth/login-view";
import { ChangePasswordView } from "@/features/auth/change-password-view";
import { useAcceleratorData, type AcceleratorData } from "@/lib/api/data";
import { buildSpec, type SpecDraft } from "@/lib/adf/spec";
import type { PipelineDefinition, Role, User } from "@/types";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import { ApiErrorBanner } from "./api-error-banner";
import { SessionExpiryBanner } from "./session-expiry-banner";
import type { ViewId } from "./navigation";
import type { NavParams, OnboardingPrefill } from "@/features/types";
import { HomeView } from "@/features/home/home-view";
import { TeamsView } from "@/features/teams/teams-view";
import { ProjectsView } from "@/features/projects/projects-view";
import { PipelinesView } from "@/features/pipelines/pipelines-view";
import { DraftsView } from "@/features/drafts/drafts-view";
import { GitView } from "@/features/git/git-view";
import { ConnectionsView } from "@/features/build/connections-view";
import { TemplatesView } from "@/features/templates/templates-view";
import { BuildView } from "@/features/build/build-view";
import { ArtifactCategoryView } from "@/features/artifacts/artifacts-view";
import { CatalogView } from "@/features/catalog/catalog-view";
import { OnboardingView } from "@/features/onboarding/onboarding-view";
import { SamplesView } from "@/features/samples/samples-view";
import { AdministrationView } from "@/features/administration/administration-view";
import { StandardsView } from "@/features/administration/standards-view";
import { ProjectSettingsView } from "@/features/projects/project-settings-view";
import type { ArtifactType } from "@/types";

export function AppShell() {
  const authGate = useAuthGate();

  // Gate the whole shell behind a real session when AUTH_MODE=password
  // (AGENTS.md §27.2 extension). Under `mock`/`oidc`/`saml` this resolves to
  // "ready" immediately (see `useAuthGate`) — zero change from before. While
  // still "checking" (the async /auth/config + /auth/me round trip hasn't
  // resolved yet), render the shell as usual rather than blocking on it —
  // `mock` mode's own resource fetches already tolerate a slower API via
  // their own loading state, and this keeps first paint synchronous instead
  // of flashing a blank screen on every load.
  if (authGate.status === "login_required") {
    return <LoginView onSignedIn={authGate.recheck} sessionExpired={authGate.sessionExpired} />;
  }
  if (authGate.status === "must_change_password") {
    return <ChangePasswordView onChanged={authGate.recheck} />;
  }

  return (
    <ApiStatusProvider>
      <Shell
        onLogout={authGate.user ? authGate.logout : undefined}
        idleTimeoutMinutes={authGate.user ? authGate.idleTimeoutMinutes : null}
      />
    </ApiStatusProvider>
  );
}

function Shell({
  onLogout,
  idleTimeoutMinutes,
}: {
  onLogout?: () => Promise<void>;
  idleTimeoutMinutes: number | null;
}) {
  const data = useAcceleratorData();
  const devIdentity = useDevIdentity();
  const [view, setViewRaw] = useState<ViewId>("home");
  const [navOpen, setNavOpen] = useState(false);
  const [roleOverride, setRoleOverride] = useState<Role | null>(null);
  const [created, setCreated] = useState<PipelineDefinition[]>([]);
  // Which pipeline the Build route should reopen in edit mode — set by Pipelines' "Edit", Drafts'
  // "Continue setup", or Build's own "add to existing pipeline" picker (all via `onNavigate("pipelines.create",
  // { editPipelineId })`). Cleared on any navigation away from that route so a later plain "Build"
  // click never reopens a stale edit.
  const [editPipelineId, setEditPipelineId] = useState<string | null>(null);
  // Search-box seed for the `delivery.catalog` route — set by Drafts' "Review artifacts" so all of
  // one pipeline's artifact types (not just its 2 `pipeline`-type files) land in view together.
  // Cleared on any navigation away from Catalog so a later plain "Catalog" click starts unfiltered.
  const [catalogQuery, setCatalogQuery] = useState<string | null>(null);

  // Onboarding's "Build pipeline" seed for a create-mode Build — cleared like the two above.
  const [onboardingPrefill, setOnboardingPrefill] = useState<OnboardingPrefill | null>(null);

  const setView = useCallback((next: ViewId, params?: NavParams) => {
    setViewRaw(next);
    setNavOpen(false);
    setEditPipelineId(next === "pipelines.create" ? params?.editPipelineId ?? null : null);
    setOnboardingPrefill(next === "pipelines.create" ? params?.onboardingPrefill ?? null : null);
    setCatalogQuery(next === "delivery.catalog" ? params?.catalogQuery ?? null : null);
  }, []);

  const workspaceName = data.teams.data[0]?.name ?? "Platform engineering";
  const user: User = roleOverride ? { ...data.currentUser.data, role: roleOverride } : data.currentUser.data;

  // New pipelines created through the wizard become visible immediately.
  const pipelinesData: AcceleratorData = {
    ...data,
    pipelines: { ...data.pipelines, data: [...created, ...data.pipelines.data] },
  };

  const handleCreated = useCallback(
    (draft: SpecDraft, persisted?: PipelineDefinition) => {
      // The backend saved (and possibly generated) this pipeline for real —
      // use that record as-is so it carries a server-tracked id the Artifacts
      // plane can actually push to Git, instead of the synthesized one below.
      if (persisted) {
        setCreated((current) => [persisted, ...current]);
        // Also refresh the shared artifacts list so a successful server-side
        // generate shows up in the Artifacts plane immediately.
        data.artifacts.reload();
        return;
      }
      const now = new Date().toISOString();
      const project = data.projects.data.find((p) => p.id === draft.projectId);
      const team = data.teams.data.find((t) => t.id === project?.teamId);
      setCreated((current) => [
        {
          id: `pipeline-${draft.name}-${now}`,
          name: draft.name,
          type: draft.templateId,
          projectId: draft.projectId,
          projectName: project?.name ?? "—",
          teamId: team?.id ?? "",
          teamName: team?.name ?? "—",
          specVersion: "1.0",
          // AGENTS.md section 9: lifecycle begins at DRAFT, then advances
          // deterministically through GENERATED -> VALIDATED -> COMMITTED -> DEV.
          state: "DRAFT",
          createdAt: now,
          updatedAt: now,
          createdBy: user.id,
          spec: buildSpec(draft),
        },
        ...current,
      ]);
    },
    [data.artifacts, data.projects.data, data.teams.data, user.id],
  );

  /** Build wizard's edit mode (append objects / rename master+child) — `PUT /pipelines/{id}`
   * succeeded. Patch it into the locally-created list in case it's one of those (immediate UI
   * update) and refresh the backend-sourced lists either way. */
  const handleUpdated = useCallback(
    (pipeline: PipelineDefinition) => {
      setCreated((current) => current.map((p) => (p.id === pipeline.id ? pipeline : p)));
      data.pipelines.reload();
      data.artifacts.reload();
    },
    [data.artifacts, data.pipelines],
  );

  const retryAll = useCallback(() => {
    data.teams.reload();
    data.projects.reload();
    data.environments.reload();
    data.pipelines.reload();
    data.artifacts.reload();
    data.currentUser.reload();
    data.audit.reload();
  }, [data]);

  // Switching the acting mock user changes what every subsequent request is
  // authorized to see/do — reload everything so the shell reflects it right away.
  const switchIdentity = useCallback(
    (userId: string) => devIdentity.switchTo(userId, retryAll),
    [devIdentity, retryAll],
  );

  return (
    <main className="app-shell">
      <Sidebar
        activeView={view}
        onNavigate={setView}
        user={user}
        workspaceName={workspaceName}
        devIdentity={{ ...devIdentity, switchTo: switchIdentity }}
        mobileOpen={navOpen}
      />
      {navOpen ? <button className="nav-scrim" aria-label="Close navigation" onClick={() => setNavOpen(false)} /> : null}
      <section className="content">
        <Topbar
          view={view}
          workspaceName={workspaceName}
          user={user}
          onNavigate={setView}
          onToggleNav={() => setNavOpen((open) => !open)}
          onRolePreview={setRoleOverride}
          rolePreview={!!roleOverride}
          onLogout={onLogout}
        />
        <ApiErrorBanner />
        <SessionExpiryBanner idleTimeoutMinutes={idleTimeoutMinutes} />
        <div className="page-content">
          {renderView(view, pipelinesData, user, setView, handleCreated, editPipelineId, handleUpdated, catalogQuery, onboardingPrefill)}
        </div>
      </section>
    </main>
  );
}
function renderView(
  view: ViewId,
  data: AcceleratorData,
  user: User,
  onNavigate: (view: ViewId, params?: NavParams) => void,
  onCreated: (draft: SpecDraft) => void,
  editPipelineId: string | null,
  onUpdated: (pipeline: PipelineDefinition) => void,
  catalogQuery: string | null,
  onboardingPrefill: OnboardingPrefill | null,
) {
  const props = { data, user, onNavigate };
  switch (view) {
    case "home":
      return <HomeView {...props} />;
    case "teams":
      return <TeamsView {...props} />;
    case "projects":
      return <ProjectsView {...props} />;
    case "pipelines":
      return <PipelinesView {...props} />;
    case "pipelines.create":
      return (
        <BuildView
          {...props}
          onCreated={onCreated}
          editPipelineId={editPipelineId}
          onUpdated={onUpdated}
          onboardingPrefill={onboardingPrefill}
        />
      );
    case "pipelines.drafts":
      return <DraftsView {...props} />;
    case "pipelines.git":
      return <GitView {...props} />;
    case "pipelines.connections":
      return <ConnectionsView {...props} />;
    case "pipelines.templates":
      return <TemplatesView {...props} />;
    case "delivery.catalog":
      return <CatalogView {...props} initialQuery={catalogQuery ?? undefined} />;
    case "delivery.onboarding":
      return <OnboardingView {...props} />;
    case "delivery.samples":
      return <SamplesView {...props} />;
    case "artifacts.adf_pipelines":
    case "artifacts.datasets":
    case "artifacts.linked_services":
    case "artifacts.parameters":
    case "artifacts.triggers":
      return <ArtifactCategoryView {...props} categoryId={artifactTypeOf(view)} />;
    case "admin":
      return <AdministrationView {...props} section="overview" />;
    case "admin.project_settings":
      return <ProjectSettingsView {...props} />;
    case "admin.standards":
      return <StandardsView {...props} />;
    case "admin.users":
      return <AdministrationView {...props} section="users" />;
    case "admin.environments":
      return <AdministrationView {...props} section="environments" />;
    case "admin.audit":
      return <AdministrationView {...props} section="audit" />;
    default:
      return <HomeView {...props} />;
  }
}

function artifactTypeOf(view: ViewId): ArtifactType {
  const map: Record<string, ArtifactType> = {
    "artifacts.adf_pipelines": "pipeline",
    "artifacts.datasets": "dataset",
    "artifacts.linked_services": "linked_service",
    "artifacts.parameters": "parameter",
    "artifacts.triggers": "trigger",
  };
  return map[view] ?? "pipeline";
}