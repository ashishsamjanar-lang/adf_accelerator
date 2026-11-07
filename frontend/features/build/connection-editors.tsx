"use client";

/**
 * Editors for the connection layer of the Create Pipeline wizard:
 *  - SourceObjectsEditor: table/input-file list, filled by form OR by
 *    uploading a CSV/TSV/TXT/JSON list file;
 *  - LinkedServiceEditor: connector-driven form rendering every required
 *    parameter for the selected connector + authentication mechanism;
 *  - DatasetEditor: dataset name, parameters, and file-format settings.
 *
 * Aligned with AGENTS.md: connector schemas render dynamically from
 * definitions (lib/connections.ts); secrets stay in Key Vault as
 * *secret names*, never inline values.
 */
import { useEffect, useRef, useState } from "react";
import { Database, Download, FileText, KeyRound, Plus, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { downloadTextFile } from "@/lib/download";
import { api } from "@/lib/api/client";
import { ColumnMappingsEditor } from "@/features/build/column-mappings-editor";
import {
  buildLinkedServiceTemplate,
  buildSourceObjectsTemplate,
  CONNECTOR_GROUPS,
  connectorSchema,
  parseLinkedServiceFile,
  parseTablesFile,
  requiredFields,
  visibleFields,
  type ConnectionField,
  type DatasetDraft,
  type LinkedServiceDraft,
  type ParsedLinkedServiceValues,
} from "@/lib/adf/connections";
import type { ManagedIdentity, SourceObjectEntry } from "@/types";

const CONNECTOR_GROUP_LABELS: Record<string, string> = {
  Azure: "Azure data stores",
  Database: "Databases & warehouses",
  File: "Files & object storage",
  Service: "SaaS & applications",
  AI: "LLM & model serving",
  Generic: "Generic protocols",
};

function mapValue<T>(list: T[], index: number, patch: Partial<T>): T[] {
  return list.map((item, i) => (i === index ? { ...item, ...patch } : item));
}

function removeAt<T>(list: T[], index: number): T[] {
  return list.filter((_, i) => i !== index);
}

function ErrorList({ errors }: { errors: string[] }) {
  if (!errors.length) return null;
  return (
    <div className="editor-errors">
      {errors.map((message, i) => (
        <small key={i} className="text-red">
          {message}
        </small>
      ))}
    </div>
  );
}

interface SourceObjectsEditorProps {
  mode: "form" | "file";
  rows: SourceObjectEntry[];
  errors: string[];
  onChange: (mode: "form" | "file", rows: SourceObjectEntry[]) => void;
  /**
   * Show per-object Copy column mappings under "Show all columns" (Build wizard only — applied by the
   * master/child and single-pipeline patterns via the control table's ColumnMapping column).
   */
  showColumnMappings?: boolean;
}

const LOADING_BEHAVIORS: Array<{ value: string; label: string }> = [
  { value: "", label: "Default (from load settings)" },
  { value: "FullLoad", label: "FullLoad" },
  { value: "DeltaLoad", label: "DeltaLoad" },
];

export function SourceObjectsEditor({ mode, rows, errors, onChange, showColumnMappings = false }: SourceObjectsEditorProps) {
  const [pending, setPending] = useState<SourceObjectEntry[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [fileName, setFileName] = useState("");
  const [rawText, setRawText] = useState("");
  const [dragging, setDragging] = useState(false);
  const [showAllColumns, setShowAllColumns] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function ingest(content: string, name: string) {
    const parsed = parseTablesFile(content, name);
    setPending(parsed.objects);
    setWarnings(parsed.warnings);
    setFileName(name);
    setRawText(content);
  }

  async function handleFile(file: File) {
    const text = await file.text();
    ingest(text, file.name);
  }

  function importPending() {
    if (!pending.length) return;
    onChange("file", pending);
    setPending([]);
    setWarnings([]);
  }

  return (
    <div className="editor-block">
      <div className="editor-head">
        <strong>Source objects (tables / input files)</strong>
        <small>{rows.length} object{rows.length === 1 ? "" : "s"}</small>
      </div>
      <p className="editor-hint">
        Each row becomes one control-table seed row. Enter them manually or upload a list file.{" "}
        {mode === "form" ? (
          <button type="button" className="editor-columns-toggle" onClick={() => setShowAllColumns((v) => !v)}>
            {showAllColumns ? "Show fewer columns" : "Show all columns"}
          </button>
        ) : null}
      </p>

      <div className="mode-toggle" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "form"}
          className={cn("mode-toggle-btn", mode === "form" && "active")}
          onClick={() => onChange("form", rows)}
        >
          <FileText className="h-3.5 w-3.5" />
          Fill form
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "file"}
          className={cn("mode-toggle-btn", mode === "file" && "active")}
          onClick={() => onChange("file", rows)}
        >
          <Upload className="h-3.5 w-3.5" />
          Upload file
        </button>
      </div>

      {mode === "form" ? (
        <>
          {rows.length === 0 ? (
            <p className="editor-empty">No source objects yet. Add a table or file path.</p>
          ) : (
            <div className="editor-rows">
              {rows.map((row, i) => (
                <div key={i}>
                  <div className="editor-row three">
                    <Input
                      value={row.object}
                      placeholder="e.g. customer, ORDERS, or file.csv"
                      onChange={(e) => onChange("form", mapValue(rows, i, { object: e.target.value }))}
                    />
                    <Input
                      value={row.watermarkColumn ?? ""}
                      placeholder="Watermark column (optional)"
                      onChange={(e) =>
                        onChange("form", mapValue(rows, i, { watermarkColumn: e.target.value || undefined }))
                      }
                    />
                    <Select
                      value={row.loadingBehavior ?? ""}
                      onChange={(e) =>
                        onChange("form", mapValue(rows, i, {
                          loadingBehavior: (e.target.value || undefined) as SourceObjectEntry["loadingBehavior"],
                        }))
                      }
                    >
                      {LOADING_BEHAVIORS.map((b) => (
                        <option key={b.value} value={b.value}>
                          {b.label}
                        </option>
                      ))}
                    </Select>
                    <button
                      type="button"
                      className="editor-remove"
                      title="Remove object row"
                      onClick={() => onChange("form", removeAt(rows, i))}
                    >
                      ×
                    </button>
                  </div>
                  {showAllColumns ? (
                    <div className="editor-row-secondary">
                      <Input
                        value={row.catalog ?? ""}
                        placeholder="Catalog (3-part sources only)"
                        onChange={(e) => onChange("form", mapValue(rows, i, { catalog: e.target.value || undefined }))}
                      />
                      <Input
                        value={row.schema ?? ""}
                        placeholder="Schema (leave blank if schema-less)"
                        onChange={(e) => onChange("form", mapValue(rows, i, { schema: e.target.value || undefined }))}
                      />
                      <Input
                        value={row.qualifierOverride ?? ""}
                        placeholder="Qualifier override (exact identifier)"
                        onChange={(e) =>
                          onChange("form", mapValue(rows, i, { qualifierOverride: e.target.value || undefined }))
                        }
                      />
                      <Select
                        value={row.caseOverride ?? ""}
                        onChange={(e) =>
                          onChange("form", mapValue(rows, i, {
                            caseOverride: (e.target.value || undefined) as SourceObjectEntry["caseOverride"],
                          }))
                        }
                      >
                        <option value="">Case: auto-conform</option>
                        <option value="as-typed">Case: as-typed</option>
                      </Select>
                    </div>
                  ) : null}
                  {showAllColumns && showColumnMappings ? (
                    <div className="editor-row-secondary">
                      <ColumnMappingsEditor
                        value={row.columnMappings ?? []}
                        title="Object column mappings (optional)"
                        hint="Replaces the pipeline-level column mappings for this object only. Leave empty to use them."
                        labelPrefix={`Object ${i + 1} `}
                        onChange={(next) => onChange("form", mapValue(rows, i, { columnMappings: next }))}
                      />
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
          <button
            type="button"
            className="editor-add"
            disabled={rows.length >= 200}
            onClick={() => onChange("form", [...rows, { object: "" }])}
          >
            <Plus className="h-3 w-3" /> Add object
          </button>
        </>
      ) : (
        <UploadMode
          dragging={dragging}
          setDragging={setDragging}
          fileInputRef={fileInputRef}
          handleFile={handleFile}
          rawText={rawText}
          setRawText={setRawText}
          ingest={ingest}
          setPending={setPending}
          pending={pending}
          warnings={warnings}
          fileName={fileName}
          importPending={importPending}
        />
      )}

      <ErrorList errors={errors} />
    </div>
  );
}

interface UploadModeProps {
  dragging: boolean;
  setDragging: (next: boolean) => void;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  handleFile: (file: File) => void;
  rawText: string;
  setRawText: (next: string) => void;
  ingest: (content: string, name: string) => void;
  setPending: (rows: SourceObjectEntry[]) => void;
  pending: SourceObjectEntry[];
  warnings: string[];
  fileName: string;
  importPending: () => void;
}

function UploadMode({
  dragging,
  setDragging,
  fileInputRef,
  handleFile,
  rawText,
  setRawText,
  ingest,
  setPending,
  pending,
  warnings,
  fileName,
  importPending,
}: UploadModeProps) {
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => downloadTextFile("source_objects_template.csv", buildSourceObjectsTemplate())}
        >
          <Download className="h-3.5 w-3.5" />
          Download template
        </Button>
        <span className="text-xs text-muted">
          Fill in the template with one row per table or file (add catalog/schema for 3-part or schema-less
          sources), then upload or paste it below.
        </span>
      </div>
      <div
        className={cn("upload-zone", dragging && "dragging")}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files?.[0];
          if (file) handleFile(file);
        }}
        onClick={() => fileInputRef.current?.click()}
        role="button"
        tabIndex={0}
      >
        <Upload className="h-4 w-4" />
        <strong>Drop a file here or click to browse</strong>
        <span>
          .csv / .tsv / .txt / .json — one object per row; optional columns: catalog, schema, qualifier_override,
          case_override, watermark_column, loading_behavior
        </span>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,.tsv,.txt,.json,text/csv,text/plain,application/json"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
            e.target.value = "";
          }}
          hidden
        />
      </div>
      <label>
        <span>…or paste the list</span>
        <Textarea
          value={rawText}
          placeholder={"dbo.customer\ndbo.orders,modified_date,DeltaLoad\ninventory.items,updated_at"}
          onChange={(e) => {
            const value = e.target.value;
            setRawText(value);
            if (value.trim()) ingest(value, "pasted-list.txt");
            else setPending([]);
          }}
        />
      </label>
      {fileName ? (
        <p className="upload-note">
          Parsed <strong>{fileName}</strong> — {pending.length} object{pending.length === 1 ? "" : "s"} found.
        </p>
      ) : null}
      {warnings.map((warning, i) => (
        <small key={i} className="upload-warning">
          {warning}
        </small>
      ))}
      {pending.length > 0 ? (
        <div className="upload-preview">
          <div className="upload-preview-head">
            <strong>Preview</strong>
            <Badge variant="blue">{pending.length} rows</Badge>
          </div>
          <div className="upload-preview-body">
            {pending.slice(0, 8).map((row, i) => (
              <code key={i}>
                {[row.catalog, row.schema, row.object].filter(Boolean).join(".")}
                {row.watermarkColumn ? ` · ${row.watermarkColumn}` : ""}
                {row.loadingBehavior ? ` · ${row.loadingBehavior}` : ""}
              </code>
            ))}
            {pending.length > 8 ? <code>… {pending.length - 8} more</code> : null}
          </div>
          <Button size="sm" onClick={importPending} disabled={!pending.length}>
            <Database className="h-3.5 w-3.5" />
            Import {pending.length} object{pending.length === 1 ? "" : "s"}
          </Button>
        </div>
      ) : null}
    </>
  );
}

interface LinkedServiceEditorProps {
  title: string;
  ls: LinkedServiceDraft;
  connectors: string[];
  onChange: (next: LinkedServiceDraft) => void;
  errors: string[];
  /**
   * Optional: called with every row after the first when an uploaded file
   * has more than one data row, so the caller can bulk-create additional
   * saved connections (only meaningful on the Connections page — this editor
   * is also embedded in the Build wizard / standalone artifact form, which
   * edit a single linked service and have nowhere to put extra ones).
   */
  onBulkCreate?: (extra: ParsedLinkedServiceValues[]) => void;
}

const SYSTEM_IDENTITY: Pick<LinkedServiceDraft, "identityKind" | "credentialName" | "credentialResourceId"> = {
  identityKind: undefined,
  credentialName: undefined,
  credentialResourceId: undefined,
};

/**
 * §32.5 — system- vs user-assigned managed identity. "User" picks a registered identity
 * (`app.managed_identities`, team-scoped server-side) and snapshots its name + resource ID onto
 * the draft, so the generator emits a `credentials` artifact without ever querying the registry.
 */
function ManagedIdentityPicker({ ls, onChange }: { ls: LinkedServiceDraft; onChange: (ls: LinkedServiceDraft) => void }) {
  const [identities, setIdentities] = useState<ManagedIdentity[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [draftIdentity, setDraftIdentity] = useState({ name: "", resourceId: "", clientId: "" });
  const [registerError, setRegisterError] = useState<string | null>(null);
  const isUser = ls.identityKind === "user";

  async function registerIdentity() {
    setRegisterError(null);
    try {
      const created = await api.managedIdentities.create({
        name: draftIdentity.name.trim(),
        resourceId: draftIdentity.resourceId.trim(),
        clientId: draftIdentity.clientId.trim() || null,
      });
      setIdentities((rows) => [...(rows ?? []), created]);
      onChange({ ...ls, identityKind: "user", credentialName: created.name, credentialResourceId: created.resourceId });
      setRegistering(false);
      setDraftIdentity({ name: "", resourceId: "", clientId: "" });
    } catch (error) {
      setRegisterError(error instanceof Error ? error.message : "Couldn't register the identity.");
    }
  }

  useEffect(() => {
    if (!isUser || identities !== null) return;
    let cancelled = false;
    api.managedIdentities
      .list()
      .then((rows) => !cancelled && setIdentities(rows))
      .catch(() => {
        if (cancelled) return;
        setLoadError(true);
        setIdentities([]);
      });
    return () => {
      cancelled = true;
    };
  }, [isUser, identities]);

  const known = identities?.some((i) => i.name === ls.credentialName);
  return (
    <div className="field-grid">
      <div className="field-cell">
        <div className="field-label">
          <span>Managed identity</span>
          <span className="required-mark">*</span>
        </div>
        <Select
          value={isUser ? "user" : "system"}
          onChange={(e) =>
            onChange(e.target.value === "user" ? { ...ls, identityKind: "user" } : { ...ls, ...SYSTEM_IDENTITY })
          }
        >
          <option value="system">System-assigned (the factory&apos;s own identity)</option>
          <option value="user">User-assigned (via a credential)</option>
        </Select>
      </div>
      {isUser ? (
        <div className="field-cell">
          <div className="field-label">
            <span>Credential</span>
            <span className="required-mark">*</span>
          </div>
          <Select
            value={ls.credentialName ?? ""}
            onChange={(e) => {
              const picked = identities?.find((i) => i.name === e.target.value);
              onChange({
                ...ls,
                identityKind: "user",
                credentialName: picked?.name,
                credentialResourceId: picked?.resourceId,
              });
            }}
          >
            <option value="">{identities === null ? "Loading…" : "— select a registered identity —"}</option>
            {ls.credentialName && !known ? <option value={ls.credentialName}>{ls.credentialName}</option> : null}
            {(identities ?? []).map((i) => (
              <option key={i.id} value={i.name}>
                {i.name}
              </option>
            ))}
          </Select>
          <small>
            {loadError
              ? "Couldn't load managed identities — the backend is unreachable."
              : identities && identities.length === 0
                ? "No user-assigned identities are registered for your team yet — a team admin registers them first."
                : ls.credentialResourceId || "Generates a credential artifact referenced by this linked service."}
          </small>
          {registering ? (
            <div className="mt-2 space-y-2">
              <Input
                aria-label="Identity name"
                placeholder="Credential name (e.g. cred_etl_uami)"
                value={draftIdentity.name}
                onChange={(e) => setDraftIdentity({ ...draftIdentity, name: e.target.value })}
              />
              <Input
                aria-label="Identity resource ID"
                placeholder="/subscriptions/…/userAssignedIdentities/…"
                value={draftIdentity.resourceId}
                onChange={(e) => setDraftIdentity({ ...draftIdentity, resourceId: e.target.value })}
              />
              <Input
                aria-label="Identity client ID"
                placeholder="Client ID (optional)"
                value={draftIdentity.clientId}
                onChange={(e) => setDraftIdentity({ ...draftIdentity, clientId: e.target.value })}
              />
              {registerError ? <small className="text-red text-xs">{registerError}</small> : null}
              <div className="flex gap-2">
                <Button
                  size="sm"
                  disabled={!draftIdentity.name.trim() || !draftIdentity.resourceId.trim()}
                  onClick={registerIdentity}
                >
                  Register
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setRegistering(false)}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <Button size="sm" variant="ghost" className="mt-1" onClick={() => setRegistering(true)}>
              <Plus className="h-3.5 w-3.5" />
              Register new identity
            </Button>
          )}
        </div>
      ) : null}
    </div>
  );
}

export function LinkedServiceEditor({ title, ls, connectors, onChange, errors, onBulkCreate }: LinkedServiceEditorProps) {
  const schema = connectorSchema(ls.connectorType);
  const fields = visibleFields(schema, ls.authType);
  const required = new Set(requiredFields(schema, ls.authType).map((f) => f.key));
  const hasSecrets = fields.some((f) => f.isSecret);
  // Gap #27 — group fields instead of one flat grid: auth-specific/secret fields, core connection
  // fields (always-required), and optional "advanced" ones (collapsed by default).
  const authFields = fields.filter((f) => f.isSecret || (!f.required && !!f.requiredFor?.length));
  const connectionFields = fields.filter((f) => !authFields.includes(f) && required.has(f.key));
  const advancedFields = fields.filter((f) => !authFields.includes(f) && !required.has(f.key));
  // Inline required-field state only once the caller has reported validation errors — an untouched
  // form shouldn't open covered in red.
  const showInline = errors.length > 0;
  const isMissing = (f: ConnectionField) => required.has(f.key) && !(ls.fields[f.key] ?? "").trim();

  const [mode, setMode] = useState<"form" | "file">("form");
  const [pending, setPending] = useState<ParsedLinkedServiceValues[]>([]);
  const [uploadWarnings, setUploadWarnings] = useState<string[]>([]);
  const [fileName, setFileName] = useState("");
  const [rawText, setRawText] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function ingest(content: string, name: string) {
    const parsed = parseLinkedServiceFile(content, name);
    const warnings = [...parsed.warnings];
    if (parsed.values.length > 1 && !onBulkCreate) {
      warnings.push(
        `This file has ${parsed.values.length} rows — only the first will be applied here. Bulk-creating several connections at once is only available from the Connections page.`,
      );
    }
    setPending(parsed.values);
    setUploadWarnings(warnings);
    setFileName(name);
    setRawText(content);
  }

  async function handleFile(file: File) {
    const text = await file.text();
    ingest(text, file.name);
  }

  function applyPending() {
    const first = pending[0];
    if (!first) return;
    const nextConnectorType =
      first.connectorType && connectors.includes(first.connectorType) ? first.connectorType : ls.connectorType;
    const connectorChanged = nextConnectorType !== ls.connectorType;
    const nextSchema = connectorSchema(nextConnectorType);
    const fallbackAuth = connectorChanged ? nextSchema.defaultAuth : ls.authType;
    const nextAuthType =
      first.authType && nextSchema.authOptions.some((option) => option.id === first.authType)
        ? (first.authType as LinkedServiceDraft["authType"])
        : fallbackAuth;
    onChange({
      ...ls,
      name: first.name || ls.name,
      connectorType: nextConnectorType as LinkedServiceDraft["connectorType"],
      authType: nextAuthType,
      integrationRuntime: first.integrationRuntime || ls.integrationRuntime,
      keyVaultLinkedService: first.keyVaultLinkedService || ls.keyVaultLinkedService,
      fields: connectorChanged ? { ...first.fields } : { ...ls.fields, ...first.fields },
      parameterize: connectorChanged ? [] : ls.parameterize,
    });
    if (pending.length > 1 && onBulkCreate) onBulkCreate(pending.slice(1));
    setPending([]);
    setUploadWarnings([]);
    setRawText("");
    setFileName("");
    setMode("form");
  }

  function updateConnector(connectorType: string) {
    const nextSchema = connectorSchema(connectorType);
    let name = ls.name;
    // Keep generated names consistent when the connector changes.
    if (name.startsWith(`LS_${ls.connectorType}_`)) {
      name = `LS_${connectorType}_${name.slice(`LS_${ls.connectorType}_`.length)}`;
    }
    onChange({
      ...ls,
      connectorType: connectorType as LinkedServiceDraft["connectorType"],
      authType: nextSchema.defaultAuth,
      fields: {},
      parameterize: [],
      name,
      ...SYSTEM_IDENTITY,
    });
  }

  function toggleParameterize(key: string) {
    const next = ls.parameterize.includes(key)
      ? ls.parameterize.filter((k) => k !== key)
      : [...ls.parameterize, key];
    onChange({ ...ls, parameterize: next });
  }

  function renderField(field: ConnectionField) {
    const value = ls.fields[field.key] ?? "";
    const setValue = (next: string) =>
      onChange({ ...ls, fields: { ...ls.fields, [field.key]: next } });
    const requiredMark = required.has(field.key) ? <span className="required-mark">*</span> : null;
    const paramToggle = field.isSecret ? null : (
      <label className="param-flag" title="Render this value as an ADF linked-service parameter (@linkedService().key)">
        <input
          type="checkbox"
          checked={ls.parameterize.includes(field.key)}
          onChange={() => toggleParameterize(field.key)}
        />
        <span>Param</span>
      </label>
    );
    let control: React.ReactNode;
    if (field.type === "select") {
      control = (
        <Select value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="">—</option>
          {(field.options ?? []).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      );
    } else if (field.type === "boolean") {
      control = (
        <Select value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="">—</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </Select>
      );
    } else {
      control = (
        <Input
          type={field.type === "number" ? "number" : "text"}
          value={value}
          placeholder={field.placeholder}
          onChange={(e) => setValue(e.target.value)}
        />
      );
    }
    const invalid = showInline && isMissing(field);
    return (
      <div className={cn("field-cell", invalid && "is-invalid")} key={field.key}>
        <div className="field-label">
          {field.isSecret ? <KeyRound className="h-3 w-3" /> : null}
          <span>{field.label}</span>
          {requiredMark}
        </div>
        {control}
        {invalid ? <small className="text-red">{field.label} is required.</small> : null}
        {field.help ? <small>{field.help}</small> : null}
        {paramToggle}
      </div>
    );
  }

  const body = (
    <>
      <div className="field-grid">
        <div className="field-cell">
          <div className="field-label">
            <span>Linked service name</span>
            <span className="required-mark">*</span>
          </div>
          <Input value={ls.name} onChange={(e) => onChange({ ...ls, name: e.target.value })} />
        </div>
        <div className="field-cell">
          <div className="field-label">
            <span>Connector</span>
            <span className="required-mark">*</span>
          </div>
          <Select value={ls.connectorType} onChange={(e) => updateConnector(e.target.value)}>
            {!connectors.includes(ls.connectorType) ? (
              <option value={ls.connectorType}>{schema.displayName}</option>
            ) : null}
            {CONNECTOR_GROUPS.map((group) => {
              const options = group.connectors.filter((c) => connectors.includes(c));
              if (!options.length) return null;
              return (
                <optgroup key={group.category} label={CONNECTOR_GROUP_LABELS[group.category]}>
                  {options.map((connector) => (
                    <option key={connector} value={connector}>
                      {connectorSchema(connector).displayName}
                    </option>
                  ))}
                </optgroup>
              );
            })}
          </Select>
        </div>
        <div className="field-cell">
          <div className="field-label">
            <span>Authentication</span>
            <span className="required-mark">*</span>
          </div>
          <Select
            value={ls.authType}
            onChange={(e) => {
              const authType = e.target.value as LinkedServiceDraft["authType"];
              onChange({ ...ls, authType, ...(authType === "managed_identity" ? {} : SYSTEM_IDENTITY) });
            }}
          >
            {schema.authOptions.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </Select>
        </div>
        <div className="field-cell">
          <div className="field-label">
            <span>Integration runtime</span>
            <span className="required-mark">*</span>
          </div>
          <Input
            value={ls.integrationRuntime}
            onChange={(e) => onChange({ ...ls, integrationRuntime: e.target.value })}
          />
        </div>
      </div>

      <div className="mode-toggle" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "form"}
          className={cn("mode-toggle-btn", mode === "form" && "active")}
          onClick={() => setMode("form")}
        >
          <FileText className="h-3.5 w-3.5" />
          Fill form
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "file"}
          className={cn("mode-toggle-btn", mode === "file" && "active")}
          onClick={() => setMode("file")}
        >
          <Upload className="h-3.5 w-3.5" />
          Upload file
        </button>
      </div>

      {mode === "form" ? (
        <>
          {hasSecrets ? (
            <div className="field-cell">
              <div className="field-label">
                <KeyRound className="h-3 w-3" />
                <span>Key Vault linked service (stores the secret names below)</span>
                <span className="required-mark">*</span>
              </div>
              <Input
                value={ls.keyVaultLinkedService}
                onChange={(e) => onChange({ ...ls, keyVaultLinkedService: e.target.value })}
              />
              <small>Secret values never leave Key Vault; only secret names are stored here.</small>
            </div>
          ) : null}

          {connectionFields.length ? (
            <section className="field-section">
              <h4 className="field-section-title">Connection</h4>
              <div className="field-grid">{connectionFields.map((field) => renderField(field))}</div>
            </section>
          ) : null}
          {authFields.length ? (
            <section className="field-section">
              <h4 className="field-section-title">
                <KeyRound className="h-3 w-3" />
                Authentication · {schema.authOptions.find((o) => o.id === ls.authType)?.label ?? ls.authType}
              </h4>
              <div className="field-grid">{authFields.map((field) => renderField(field))}</div>
            </section>
          ) : null}
          {advancedFields.length ? (
            <details className="field-section" open={showInline && advancedFields.some(isMissing)}>
              <summary className="field-section-title">Advanced ({advancedFields.length} optional)</summary>
              <div className="field-grid">{advancedFields.map((field) => renderField(field))}</div>
            </details>
          ) : null}
          {ls.authType === "managed_identity" ? <ManagedIdentityPicker ls={ls} onChange={onChange} /> : null}
        </>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                downloadTextFile(
                  `linked_service_${ls.connectorType}_template.csv`,
                  buildLinkedServiceTemplate(ls.connectorType, ls.authType),
                )
              }
            >
              <Download className="h-3.5 w-3.5" />
              Download template
            </Button>
            <span className="text-xs text-muted">
              Columns match the {schema.displayName} connector and &quot;{ls.authType.replace(/_/g, " ")}&quot; authentication
              selected above — change those first if this is a different source system.
            </span>
          </div>
          <div
            className={cn("upload-zone", dragging && "dragging")}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const file = e.dataTransfer.files?.[0];
              if (file) handleFile(file);
            }}
            onClick={() => fileInputRef.current?.click()}
            role="button"
            tabIndex={0}
          >
            <Upload className="h-4 w-4" />
            <strong>Drop a file here or click to browse</strong>
            <span>
              .csv / .tsv / .txt / .json — header row + one row per linked service
              {onBulkCreate ? " (multiple rows bulk-create several connections)" : ""}
            </span>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.tsv,.txt,.json,text/csv,text/plain,application/json"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleFile(file);
                e.target.value = "";
              }}
              hidden
            />
          </div>
          <label>
            <span>…or paste the file contents</span>
            <Textarea
              value={rawText}
              placeholder={
                "name,connector_type,auth_type,integration_runtime,key_vault_linked_service,server,database\n" +
                "LS_erp_source,azure_sql_database,sql_authentication,AutoResolveIntegrationRuntime,LS_azure_key_vault,myserver.database.windows.net,mydatabase"
              }
              onChange={(e) => {
                const value = e.target.value;
                setRawText(value);
                if (value.trim()) ingest(value, fileName || "pasted-linked-service.csv");
                else {
                  setPending([]);
                  setUploadWarnings([]);
                }
              }}
            />
          </label>
          {fileName ? (
            <p className="upload-note">
              Parsed <strong>{fileName}</strong>
              {pending.length ? " — ready to apply below." : " — nothing usable was found."}
            </p>
          ) : null}
          {uploadWarnings.map((warning, i) => (
            <small key={i} className="upload-warning">
              {warning}
            </small>
          ))}
          {pending[0] ? (
            <div className="upload-preview">
              <div className="upload-preview-head">
                <strong>Preview{pending.length > 1 ? ` (row 1 of ${pending.length})` : ""}</strong>
                {pending[0].connectorType ? (
                  <Badge variant="blue">{connectorSchema(pending[0].connectorType).displayName}</Badge>
                ) : null}
                {pending.length > 1 && onBulkCreate ? (
                  <Badge variant="teal">+{pending.length - 1} more connection{pending.length - 1 === 1 ? "" : "s"}</Badge>
                ) : null}
              </div>
              <div className="upload-preview-body">
                <code>name: {pending[0].name || "—"}</code>
                <code>auth_type: {pending[0].authType || "—"}</code>
                <code>integration_runtime: {pending[0].integrationRuntime || "—"}</code>
                <code>key_vault_linked_service: {pending[0].keyVaultLinkedService || "—"}</code>
                <code>
                  fields: {Object.keys(pending[0].fields).length ? Object.keys(pending[0].fields).join(", ") : "—"}
                </code>
              </div>
              <Button size="sm" onClick={applyPending}>
                <Database className="h-3.5 w-3.5" />
                {pending.length > 1 && onBulkCreate ? `Apply to form + create ${pending.length - 1} more` : "Apply to form"}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </>
  );

  return (
    <div className="editor-block ls-editor" data-category={schema.category ?? "Generic"}>
      <div className="editor-head">
        <strong>{title}</strong>
        <span className="ls-category">{schema.category ?? "Generic"}</span>
        <Badge variant="teal">{schema.adfType}</Badge>
      </div>
      <p className="editor-hint">{schema.description ?? "Configure the connection parameters."}</p>
      {body}
      <ErrorList errors={errors} />
    </div>
  );
}

interface DatasetEditorProps {
  title: string;
  ds: DatasetDraft;
  ls: LinkedServiceDraft;
  fallbackName: string;
  onChange: (next: DatasetDraft) => void;
  errors: string[];
}

export function DatasetEditor({ title, ds, ls, fallbackName, onChange, errors }: DatasetEditorProps) {
  const schema = connectorSchema(ls.connectorType);
  const isFile = schema.kind === "file";
  const isObjectApi = schema.kind === "service" || schema.kind === "llm";
  const patch = (part: Partial<DatasetDraft>) => onChange({ ...ds, ...part });

  return (
    <div className="editor-block ls-editor ds-editor" data-category={schema.category ?? "Generic"}>
      <div className="editor-head">
        <strong>{title}</strong>
        <span className="ls-category">{schema.category ?? "Generic"}</span>
        <Badge variant="teal">{schema.datasetType}</Badge>
      </div>
      <p className="editor-hint">
        References linked service <code>{ls.name || "(unnamed)"}</code>.
        {isFile
          ? " Delimited-text format settings apply to file targets."
          : " Schema/table can be parameterized for the pattern."}
      </p>

      <section className="field-section">
      <h4 className="field-section-title">Identity</h4>
      <div className="field-grid">
        <div className="field-cell">
          <div className="field-label">
            <span>Dataset name</span>
          </div>
          <Input
            value={ds.name}
            placeholder={fallbackName}
            onChange={(e) => patch({ name: e.target.value })}
          />
        </div>
        <div className="field-cell">
          <div className="field-label">
            <span>Linked service reference</span>
          </div>
          <Input value={ls.name} disabled />
          <small>Bound to the {title.toLowerCase()} configured above.</small>
        </div>
      </div>
      </section>

      <label className="param-flag">
        <input
          type="checkbox"
          checked={ds.parameterized}
          onChange={(e) => patch({ parameterized: e.target.checked })}
        />
        <span>
          Parameterize dataset (recommended) — exposes
          {isFile ? " FolderPath/FileName" : isObjectApi ? " RelativeUrl" : " SchemaName/TableName"} as dataset parameters fed from the control table.
        </span>
      </label>

      <section className="field-section">
      <h4 className="field-section-title">{isFile ? "File format" : isObjectApi ? "Object" : "Location"}</h4>
      {isFile ? (
        <FileFormatFields ds={ds} patch={patch} />
      ) : isObjectApi ? (
        <div className="field-grid">
          <div className="field-cell">
            <div className="field-label"><span>Object / relative URL</span></div>
            <Input
              value={ds.table}
              placeholder={schema.kind === "llm" ? "e.g. chat/completions" : "e.g. Account or /incidents"}
              onChange={(e) => patch({ table: e.target.value })}
            />
            <small>Appended to the linked-service base URL; can also be supplied per row from the control table.</small>
          </div>
        </div>
      ) : (
        <div className="field-grid">
          <div className="field-cell">
            <div className="field-label"><span>Schema</span></div>
            <Input value={ds.schema} placeholder="e.g. dbo" onChange={(e) => patch({ schema: e.target.value })} />
          </div>
          <div className="field-cell">
            <div className="field-label"><span>Table</span></div>
            <Input value={ds.table} placeholder="e.g. customer" onChange={(e) => patch({ table: e.target.value })} />
          </div>
        </div>
      )}
      </section>

      <ErrorList errors={errors} />
    </div>
  );
}

function FileFormatFields({
  ds,
  patch,
}: {
  ds: DatasetDraft;
  patch: (part: Partial<DatasetDraft>) => void;
}) {
  return (
    <>
      <div className="field-grid">
        <div className="field-cell">
          <div className="field-label">
            <span>Folder path</span>
          </div>
          <Input
            value={ds.folderPath}
            placeholder="e.g. landing/customer/"
            onChange={(e) => patch({ folderPath: e.target.value })}
          />
        </div>
        <div className="field-cell">
          <div className="field-label">
            <span>File name</span>
          </div>
          <Input
            value={ds.fileName}
            placeholder="e.g. customer_delta.csv"
            onChange={(e) => patch({ fileName: e.target.value })}
          />
        </div>
      </div>
      <div className="field-grid">
        <div className="field-cell">
          <div className="field-label"><span>Column delimiter</span></div>
          <Input value={ds.format.columnDelimiter} onChange={(e) => patch({ format: { ...ds.format, columnDelimiter: e.target.value } })} />
        </div>
        <div className="field-cell">
          <div className="field-label"><span>Row delimiter</span></div>
          <Input value={ds.format.rowDelimiter} onChange={(e) => patch({ format: { ...ds.format, rowDelimiter: e.target.value } })} />
        </div>
        <div className="field-cell">
          <div className="field-label"><span>Quote char</span></div>
          <Input value={ds.format.quoteChar} onChange={(e) => patch({ format: { ...ds.format, quoteChar: e.target.value } })} />
        </div>
        <div className="field-cell">
          <div className="field-label"><span>Escape char</span></div>
          <Input value={ds.format.escapeChar} onChange={(e) => patch({ format: { ...ds.format, escapeChar: e.target.value } })} />
        </div>
        <div className="field-cell">
          <div className="field-label"><span>Encoding</span></div>
          <Input value={ds.format.encoding} placeholder="utf-8" onChange={(e) => patch({ format: { ...ds.format, encoding: e.target.value } })} />
        </div>
        <div className="field-cell">
          <div className="field-label"><span>Compression</span></div>
          <Select
            value={ds.format.compression}
            onChange={(e) => patch({ format: { ...ds.format, compression: e.target.value as DatasetDraft["format"]["compression"] } })}
          >
            <option value="none">none</option>
            <option value="gzip">gzip</option>
            <option value="bzip2">bzip2</option>
            <option value="deflate">deflate</option>
          </Select>
        </div>
        <div className="field-cell">
          <div className="field-label"><span>First row as header</span></div>
          <Select
            value={ds.format.firstRowAsHeader ? "true" : "false"}
            onChange={(e) => patch({ format: { ...ds.format, firstRowAsHeader: e.target.value === "true" } })}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </Select>
        </div>
      </div>
    </>
  );
}
