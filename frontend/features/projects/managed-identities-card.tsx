"use client";

/**
 * §32.6/§32.8 (MI3) — the team's user-assigned managed identity registry, shown in Project
 * Settings' ADF section. The registry is team-scoped server-side (`GET /managed-identities`), so
 * this lists what the Linked Service designer's identity picker offers for this project's team.
 * Register/remove is TEAM_ADMIN+ (server-enforced; the buttons just mirror it). References only —
 * resource ID and client ID are identifiers, never secrets.
 */
import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api/client";
import type { ManagedIdentity } from "@/types";

const EMPTY_DRAFT = { name: "", resourceId: "", clientId: "" };

export function ManagedIdentitiesCard({ canManage }: { canManage: boolean }) {
  const [rows, setRows] = useState<ManagedIdentity[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState(EMPTY_DRAFT);

  const load = useCallback(() => {
    api.managedIdentities
      .list()
      .then((r) => {
        setRows(r);
        setError(null);
      })
      .catch((err) => {
        setRows([]);
        setError(err instanceof ApiError ? err.message : "Unable to load managed identities.");
      });
  }, []);

  useEffect(load, [load]);

  async function register() {
    try {
      await api.managedIdentities.create({
        name: draft.name.trim(),
        resourceId: draft.resourceId.trim(),
        clientId: draft.clientId.trim() || null,
      });
      setDraft(EMPTY_DRAFT);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't register the identity.");
    }
  }

  async function remove(identity: ManagedIdentity) {
    if (!window.confirm(`Remove managed identity "${identity.name}"? Linked services that reference it keep their snapshot.`)) return;
    try {
      await api.managedIdentities.remove(identity.id);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't remove the identity.");
    }
  }

  return (
    <div className="form-card">
      <h3 className="form-card-title">User-assigned managed identities</h3>
      <p className="form-section-hint">
        Shared across your team. Linked services pick one of these when their authentication is a user-assigned
        managed identity; the generator emits a matching credential artifact.
      </p>
      {error ? <p className="upload-warning mb-3">{error}</p> : null}
      <div className="table-wrap mb-3">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Resource ID</th>
              <th>Client ID</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows === null ? (
              <tr>
                <td colSpan={4} className="text-xs text-muted">Loading…</td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={4} className="text-xs text-muted">No identities registered for this team yet.</td>
              </tr>
            ) : (
              rows.map((identity) => (
                <tr key={identity.id}>
                  <td>{identity.name}</td>
                  <td className="break-all text-xs">{identity.resourceId}</td>
                  <td className="text-xs">{identity.clientId || "—"}</td>
                  <td>
                    {canManage ? (
                      <button type="button" className="editor-remove" title="Remove" onClick={() => remove(identity)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {canManage ? (
        <>
          <div className="form-fields">
            <label>
              <span>Name *</span>
              <Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="e.g. uami-adf-prod" />
            </label>
            <label>
              <span>Resource ID *</span>
              <Input
                value={draft.resourceId}
                onChange={(e) => setDraft({ ...draft, resourceId: e.target.value })}
                placeholder="/subscriptions/…/userAssignedIdentities/…"
              />
            </label>
            <label>
              <span>Client ID</span>
              <Input value={draft.clientId} onChange={(e) => setDraft({ ...draft, clientId: e.target.value })} />
            </label>
          </div>
          <Button
            className="mt-3"
            variant="outline"
            size="sm"
            disabled={!draft.name.trim() || !draft.resourceId.trim()}
            onClick={register}
          >
            <Plus className="h-3.5 w-3.5" />
            Register identity
          </Button>
        </>
      ) : (
        <p className="editor-hint">Only team admins can register or remove identities.</p>
      )}
    </div>
  );
}
