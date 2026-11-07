"use client";

import { useEffect, useMemo, useState } from "react";
import { Cable, Database, FileText, Pencil, Plus, Sparkles, Trash2, X } from "lucide-react";
import type { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState } from "@/components/common/empty-state";
import { Pagination } from "@/components/common/pagination";
import { usePagination } from "@/lib/use-pagination";
import { can } from "@/lib/state";
import { cn } from "@/lib/utils";
import {
  ALL_CONNECTORS,
  connectorSchema,
  validateLinkedServiceDraft,
  type ParsedLinkedServiceValues,
} from "@/lib/adf/connections";
import {
  cloneDataset,
  cloneLinkedService,
  connectToProject,
  deleteConnection,
  newConnection,
  upsertConnection,
  useConnections,
  type ConnectionRole,
  type SavedConnection,
} from "@/lib/adf/connection-store";
import {
  DatasetEditor,
  LinkedServiceEditor,
  SourceObjectsEditor,
} from "@/features/build/connection-editors";

const ROLE_LABELS: Record<ConnectionRole, string> = {
  source: "Source",
  target: "Target",
  both: "Source & target",
};

export function ConnectionsView({ data, user }: FeatureProps) {
  const allConnections = useConnections();
  const canEdit = can(user.role, "pipeline:create") || can(user.role, "admin:team");
  const [editing, setEditing] = useState<SavedConnection | null>(null);
  const [query, setQuery] = useState("");

  // Point the store at this project's backend connection library (§24.17).
  // No project-switcher exists yet for Connections, so — same precedent as
  // `project-settings-view.tsx` — this defaults to the first project; the
  // localStorage mirror keeps everything working even when unreachable.
  const projectId = data.projects.data[0]?.id;
  useEffect(() => {
    if (projectId) connectToProject(projectId);
  }, [projectId]);

  const connections = allConnections.filter((connection) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    const schema = connectorSchema(connection.linkedService.connectorType);
    return `${connection.name} ${schema.displayName} ${connection.description ?? ""}`.toLowerCase().includes(q);
  });

  const { page, setPage, totalPages, totalItems, pageSize, pageItems } = usePagination(connections, {
    resetKey: query,
  });

  if (editing) {
    return (
      <ConnectionEditor
        key={editing.id}
        initial={editing}
        isNew={!connections.some((c) => c.id === editing.id)}
        onCancel={() => setEditing(null)}
        onSave={(next) => {
          upsertConnection(next);
          setEditing(null);
        }}
      />
    );
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="DELIVERY · CONNECTIONS"
        title="Connections"
        detail="Configure every detail an ADF linked service needs — connector, authentication, integration runtime, Key Vault reference and all connector-specific parameters — once. Saved connections are offered as source and target in the Create Pipeline wizard and become linked services automatically at generation time."
        action={
          canEdit ? (
            <Button onClick={() => setEditing(newConnection("source"))}>
              <Plus className="h-3.5 w-3.5" />
              New connection
            </Button>
          ) : undefined
        }
      />

      {!canEdit ? (
        <Alert
          variant="warning"
          title="Read-only"
          description="Your role can view connections but not create or edit them. Ask a team or platform admin."
          className="mb-4"
        />
      ) : null}

      {allConnections.length > 0 ? (
        <div className="catalog-toolbar mb-4">
          <label className="search-box">
            ⌕
            <Input
              placeholder="Search connections by name or connector"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="border-0 bg-transparent outline-none ring-0 focus:border-0 focus:ring-0"
            />
          </label>
        </div>
      ) : null}
      <section className="panel">
        {allConnections.length === 0 ? (
          <EmptyState
            title="No connections yet"
            message="Create a connection for each source and target system. Each one carries the full linked-service configuration plus its table/file onboarding list."
            action={
              canEdit ? (
                <Button size="sm" variant="outline" onClick={() => setEditing(newConnection("source"))}>
                  <Plus className="h-3.5 w-3.5" />
                  New connection
                </Button>
              ) : undefined
            }
          />
        ) : connections.length === 0 ? (
          <EmptyState title="No connections match" message="Try a different search term." />
        ) : (
          <div className="divide-y divide-line">
            {pageItems.map((connection) => {
              const schema = connectorSchema(connection.linkedService.connectorType);
              return (
                <div key={connection.id} className="flex items-center gap-4 px-5 py-4">
                  <span className="pipeline-glyph">
                    <ConnectorGlyph kind={schema.kind} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <strong className="text-xs text-ink">{connection.name}</strong>
                      <Badge variant="teal">{schema.adfType}</Badge>
                      <Badge variant="blue">{ROLE_LABELS[connection.role]}</Badge>
                    </div>
                    <p className="m-0 mt-1 text-xs text-muted">
                      {schema.displayName} · {connection.linkedService.authType.replace(/_/g, " ")} ·
                      IR {connection.linkedService.integrationRuntime || "—"} ·{" "}
                      {connection.objects.length} onboarded object{connection.objects.length === 1 ? "" : "s"}
                      {connection.description ? ` · ${connection.description}` : ""}
                    </p>
                  </div>
                  {canEdit ? (
                    <div className="flex items-center gap-2">
                      <Button size="sm" variant="outline" onClick={() => setEditing(cloneConnection(connection))}>
                        <Pencil className="h-3.5 w-3.5" />
                        Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          if (window.confirm(`Delete connection "${connection.name}"?`)) deleteConnection(connection.id);
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
        <Pagination
          page={page}
          totalPages={totalPages}
          totalItems={totalItems}
          pageSize={pageSize}
          onPageChange={setPage}
          itemLabel="connections"
        />
      </section>
    </div>
  );
}

function ConnectorGlyph({ kind }: { kind: ReturnType<typeof connectorSchema>["kind"] }) {
  if (kind === "file") return <FileText className="h-4 w-4" />;
  if (kind === "llm") return <Sparkles className="h-4 w-4" />;
  if (kind === "service") return <Cable className="h-4 w-4" />;
  return <Database className="h-4 w-4" />;
}

function cloneConnection(connection: SavedConnection): SavedConnection {
  return {
    ...connection,
    linkedService: cloneLinkedService(connection.linkedService),
    dataset: cloneDataset(connection.dataset),
    objects: connection.objects.map((o) => ({ ...o })),
  };
}

interface ConnectionEditorProps {
  initial: SavedConnection;
  isNew: boolean;
  onCancel: () => void;
  onSave: (next: SavedConnection) => void;
}

function ConnectionEditor({ initial, isNew, onCancel, onSave }: ConnectionEditorProps) {
  const [draft, setDraft] = useState<SavedConnection>(initial);
  const [showErrors, setShowErrors] = useState(false);

  const schema = connectorSchema(draft.linkedService.connectorType);

  const lsErrors = useMemo(
    () => validateLinkedServiceDraft(draft.linkedService, "linkedService").map((i) => i.message),
    [draft.linkedService],
  );
  const nameError = !draft.name.trim() ? "A connection name is required." : null;
  const errors = [...(nameError ? [nameError] : []), ...lsErrors];

  function patch(part: Partial<SavedConnection>) {
    setDraft((prev) => ({ ...prev, ...part }));
  }

  function handleSave() {
    if (errors.length) {
      setShowErrors(true);
      return;
    }
    onSave(draft);
  }

  /** Extra rows from a bulk linked-service upload (beyond the first, which
   * fills this form as usual) become their own saved connections outright —
   * same role/objects/dataset shape as the one being edited, just a
   * different linked service. */
  function handleBulkCreate(extra: ParsedLinkedServiceValues[]) {
    for (const parsed of extra) {
      const linkedService = {
        ...cloneLinkedService(draft.linkedService),
        name: parsed.name || draft.linkedService.name,
        integrationRuntime: parsed.integrationRuntime || draft.linkedService.integrationRuntime,
        keyVaultLinkedService: parsed.keyVaultLinkedService || draft.linkedService.keyVaultLinkedService,
        fields: { ...draft.linkedService.fields, ...parsed.fields },
      };
      upsertConnection({
        ...newConnection(draft.role),
        name: parsed.name || linkedService.name,
        role: draft.role,
        description: draft.description,
        linkedService,
        objectMode: draft.objectMode,
        objects: draft.objects.map((o) => ({ ...o })),
        dataset: cloneDataset(draft.dataset),
      });
    }
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow={isNew ? "DELIVERY · CONNECTIONS · NEW" : "DELIVERY · CONNECTIONS · EDIT"}
        title={isNew ? "New connection" : `Edit ${initial.name}`}
        detail="Pick the connector, then complete every field its ADF linked service requires. Secret fields hold the Key Vault secret name only — never the secret value."
        action={
          <div className="flex gap-2">
            <Button variant="outline" onClick={onCancel}>
              <X className="h-3.5 w-3.5" />
              Cancel
            </Button>
            <Button onClick={handleSave}>
              <Cable className="h-3.5 w-3.5" />
              Save connection
            </Button>
          </div>
        }
      />

      {showErrors && errors.length ? (
        <Alert
          variant="error"
          title="Fix the highlighted fields"
          description={errors[0]}
          className="mb-4"
        />
      ) : null}

      <div className="wizard-step-content">
        <div className="form-card">
          <h3 className="form-card-title">Connection identity</h3>
          <div className="form-fields">
            <label>
              <span>Connection name</span>
              <Input
                value={draft.name}
                onChange={(e) => {
                  const name = e.target.value;
                  patch({ name, linkedService: { ...draft.linkedService, name } });
                }}
                placeholder="e.g. LS_sql_server_erp"
              />
              <small>Also used as the ADF linked service name.</small>
              {showErrors && nameError ? <small className="text-red">{nameError}</small> : null}
            </label>
            <label>
              <span>Used as</span>
              <Select
                value={draft.role}
                onChange={(e) => patch({ role: e.target.value as ConnectionRole })}
              >
                <option value="source">Source</option>
                <option value="target">Target</option>
                <option value="both">Source &amp; target</option>
              </Select>
            </label>
            <label className="col-span-2">
              <span>Description (optional)</span>
              <Textarea
                value={draft.description}
                onChange={(e) => patch({ description: e.target.value })}
                rows={2}
                placeholder="What system this connects to and any usage notes."
              />
            </label>
          </div>
        </div>

        <div className="form-card">
          <h3 className="form-card-title">Linked service</h3>
          <LinkedServiceEditor
            title="Connection parameters"
            ls={draft.linkedService}
            connectors={ALL_CONNECTORS}
            onChange={(ls) => patch({ linkedService: { ...ls, name: draft.name || ls.name } })}
            errors={showErrors ? lsErrors : []}
            onBulkCreate={handleBulkCreate}
          />
        </div>

        <div className="form-card">
          <h3 className="form-card-title">
            {schema.kind === "file" ? "File onboarding" : "Table onboarding"}
          </h3>
          <p className="form-section-hint">
            The objects this connection loads. Type them in, or upload a CSV/TSV/TXT/JSON list — each row
            becomes a control-table seed row when the connection is used in a pipeline.
          </p>
          <SourceObjectsEditor
            mode={draft.objectMode}
            rows={draft.objects}
            errors={[]}
            onChange={(mode, rows) => patch({ objectMode: mode, objects: rows })}
          />
        </div>

        <div className="form-card">
          <h3 className="form-card-title">
            {schema.kind === "file" ? "Path & file customization" : "Schema & table customization"}
          </h3>
          <p className="form-section-hint">
            Default dataset shape for this connection.{" "}
            {schema.kind === "file"
              ? "Folder path, file name and delimited-text format."
              : "Schema and table, parameterizable for the metadata-driven pattern."}
          </p>
          <DatasetEditor
            title={draft.role === "target" ? "Target dataset" : "Source dataset"}
            ds={draft.dataset}
            ls={draft.linkedService}
            fallbackName={`DS_${draft.name || "connection"}`}
            onChange={(ds) => patch({ dataset: ds })}
            errors={[]}
          />
        </div>

        <div className="wizard-actions">
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button onClick={handleSave} className={cn(errors.length && showErrors && "opacity-70")}>
            <Cable className="h-3.5 w-3.5" />
            Save connection
          </Button>
        </div>
      </div>
    </div>
  );
}
