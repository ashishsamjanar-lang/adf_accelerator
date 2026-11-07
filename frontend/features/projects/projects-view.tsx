"use client";

import { useState } from "react";
import { FolderKanban, Plus } from "lucide-react";
import { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Dialog } from "@/components/ui/dialog";
import { Alert } from "@/components/ui/alert";
import { EmptyState, resourceStatus } from "@/components/common/empty-state";
import { Pagination } from "@/components/common/pagination";
import { usePagination } from "@/lib/use-pagination";
import { can } from "@/lib/state";
import { api, ApiError } from "@/lib/api/client";

export function ProjectsView({ data, user, onNavigate }: FeatureProps) {
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const allProjects = data.projects.data;
  const pipelines = data.pipelines.data;
  const teams = data.teams.data;
  // §11: "Create team/project" is PLATFORM_ADMIN only — `admin:platform`,
  // not `admin:team` (which also grants TEAM_ADMIN and would only 403
  // against the real backend's `require_permission("project", "create")`).
  const canCreate = can(user.role, "admin:platform");

  const projects = allProjects.filter((project) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    const team = teams.find((t) => t.id === project.teamId);
    return `${project.name} ${project.description} ${team?.name ?? ""}`.toLowerCase().includes(q);
  });

  const { page, setPage, totalPages, totalItems, pageSize, pageItems } = usePagination(projects, { resetKey: query });

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="PROJECT CONTEXT"
        title="Projects"
        detail="Every pipeline belongs to a project under a team. Keep ownership, access, and delivery scope explicit."
        action={
          <Button
            disabled={!canCreate}
            title={canCreate ? undefined : "Requires platform admin"}
            onClick={() => setCreating(true)}
          >
            <Plus className="h-3.5 w-3.5" />
            New project
          </Button>
        }
      />
      {allProjects.length > 0 ? (
        <div className="catalog-toolbar mb-4">
          <label className="search-box">
            ⌕
            <Input
              placeholder="Search projects by name, team, or description"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="border-0 bg-transparent outline-none ring-0 focus:border-0 focus:ring-0"
            />
          </label>
        </div>
      ) : null}
      {allProjects.length === 0 ? (
        <div className="panel">
          <EmptyState
            status={resourceStatus(data.projects)}
            errorMessage={data.projects.error}
            title="No projects yet"
            message="Create a project to scope pipelines, environments, and deployment permissions."
          />
        </div>
      ) : projects.length === 0 ? (
        <div className="panel">
          <EmptyState title="No projects match" message="Try a different search term." />
        </div>
      ) : (
        <>
        <div className="entity-grid">
          {pageItems.map((project) => {
            const team = teams.find((t) => t.id === project.teamId);
            const ownPipelines = pipelines.filter((p) => p.projectId === project.id);
            return (
              <article className="panel entity-card" key={project.id}>
                <span className="entity-avatar">
                  <FolderKanban className="h-4 w-4" />
                </span>
                <h2>{project.name}</h2>
                <p>{project.description}</p>
                <div className="mt-auto w-full">
                  <div className="mb-3 flex items-center justify-between text-xs text-muted">
                    <span>{team?.name ?? "Unassigned team"}</span>
                    <span>{ownPipelines.length} pipelines</span>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      className="flex-1"
                      onClick={() => onNavigate("admin.project_settings")}
                    >
                      Settings
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="flex-1"
                      onClick={() => onNavigate("pipelines")}
                    >
                      Pipelines
                    </Button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
        <Pagination
          page={page}
          totalPages={totalPages}
          totalItems={totalItems}
          pageSize={pageSize}
          onPageChange={setPage}
          itemLabel="projects"
          className="mt-2"
        />
        </>
      )}

      {creating ? (
        <NewProjectDialog
          teams={teams}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            data.projects.reload();
          }}
        />
      ) : null}
    </div>
  );
}

function NewProjectDialog({
  teams,
  onClose,
  onCreated,
}: {
  teams: FeatureProps["data"]["teams"]["data"];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [teamId, setTeamId] = useState(teams[0]?.id ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || !teamId) return;
    setSaving(true);
    setError(undefined);
    try {
      await api.projects.create({ teamId, name: name.trim(), description: description.trim() });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create the project.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      eyebrow="PROJECTS"
      title="New project"
      description="Every project belongs to exactly one team."
    >
      <form onSubmit={handleSubmit}>
        {error ? <Alert variant="error" description={error} className="mb-4" /> : null}
        {teams.length === 0 ? (
          <Alert variant="warning" description="Create a team first — a project must belong to one." className="mb-4" />
        ) : null}
        <div className="form-fields">
          <label>
            <span>Team *</span>
            <Select value={teamId} onChange={(e) => setTeamId(e.target.value)} required disabled={teams.length === 0}>
              {teams.map((team) => (
                <option key={team.id} value={team.id}>
                  {team.name}
                </option>
              ))}
            </Select>
          </label>
          <label>
            <span>Project name *</span>
            <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus required />
          </label>
          <label>
            <span>Description</span>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={saving || !name.trim() || !teamId}>
            {saving ? "Creating…" : "Create project"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
