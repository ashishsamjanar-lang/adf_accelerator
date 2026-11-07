"use client";

import { useState } from "react";
import { Plus, Users } from "lucide-react";
import { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog } from "@/components/ui/dialog";
import { Alert } from "@/components/ui/alert";
import { EmptyState, resourceStatus } from "@/components/common/empty-state";
import { Pagination } from "@/components/common/pagination";
import { usePagination } from "@/lib/use-pagination";
import { can } from "@/lib/state";
import { api, ApiError } from "@/lib/api/client";

export function TeamsView({ data, user }: FeatureProps) {
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  // Platform-admin team onboarding (§11: "Create team/project" is
  // PLATFORM_ADMIN only) — `admin:platform`, not `admin:team`, which also
  // grants TEAM_ADMIN and would only 403 against the real backend.
  const canCreate = can(user.role, "admin:platform");
  const allTeams = data.teams.data;
  const teams = allTeams.filter((team) => {
    const q = query.trim().toLowerCase();
    return !q || `${team.name} ${team.description}`.toLowerCase().includes(q);
  });
  const { page, setPage, totalPages, totalItems, pageSize, pageItems } = usePagination(teams, { resetKey: query });

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="TEAM CONTEXT"
        title="Teams"
        detail="Team isolation is enforced in application queries and authorization checks."
        action={
          <Button
            disabled={!canCreate}
            title={canCreate ? undefined : "Requires platform admin"}
            onClick={() => setCreating(true)}
          >
            <Plus className="h-3.5 w-3.5" />
            New team
          </Button>
        }
      />
      {allTeams.length > 0 ? (
        <div className="catalog-toolbar mb-4">
          <label className="search-box">
            ⌕
            <Input
              placeholder="Search teams"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="border-0 bg-transparent outline-none ring-0 focus:border-0 focus:ring-0"
            />
          </label>
        </div>
      ) : null}
      {allTeams.length === 0 ? (
        <div className="panel">
          <EmptyState status={resourceStatus(data.teams)} errorMessage={data.teams.error} title="No teams yet" message="Teams group users and projects so access and ownership stay explicit." />
        </div>
      ) : teams.length === 0 ? (
        <div className="panel">
          <EmptyState title="No teams match" message="Try a different search term." />
        </div>
      ) : (
        <>
          <div className="entity-grid">
            {pageItems.map((team) => (
              <article className="panel entity-card" key={team.id}>
                <span className="entity-avatar">
                  <Users className="h-4 w-4" />
                </span>
                <h2>{team.name}</h2>
                <p>{team.description}</p>
                <div className="mt-auto flex w-full items-center justify-between">
                  <span className="text-xs text-muted">
                    {team.memberCount} members · {team.projectCount} projects
                  </span>
                  <Badge variant="blue">Active</Badge>
                </div>
              </article>
            ))}
          </div>
          <Pagination
            page={page}
            totalPages={totalPages}
            totalItems={totalItems}
            pageSize={pageSize}
            onPageChange={setPage}
            itemLabel="teams"
            className="mt-2"
          />
        </>
      )}

      {creating ? (
        <NewTeamDialog
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            data.teams.reload();
          }}
        />
      ) : null}
    </div>
  );
}

function NewTeamDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setError(undefined);
    try {
      await api.teams.create({ name: name.trim(), description: description.trim() });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create the team.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onClose={onClose} eyebrow="TEAMS" title="New team" description="Teams scope projects, pipelines, and access.">
      <form onSubmit={handleSubmit}>
        {error ? <Alert variant="error" description={error} className="mb-4" /> : null}
        <div className="form-fields">
          <label>
            <span>Team name *</span>
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
          <Button type="submit" disabled={saving || !name.trim()}>
            {saving ? "Creating…" : "Create team"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
