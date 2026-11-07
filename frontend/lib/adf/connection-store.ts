"use client";

/**
 * Saved connection store.
 *
 * A "connection" is a fully-configured ADF linked service (connector +
 * authentication + integration runtime + Key Vault reference + every
 * connector-specific parameter) plus the object-onboarding and dataset
 * customization that go with it. Connections are authored once on the
 * Delivery > Connections screen and then reused by the Create Pipeline
 * wizard, which drops the saved linked service straight into the
 * source/target slots.
 *
 * Backed by the metastore's `connection_library` table (§24.17) via
 * `api.connections.*`, with this in-memory singleton mirrored to
 * localStorage as an instant, offline-tolerant cache — same "aggressively
 * try the API, fall back to local" shape as every other resource
 * (`lib/api/hooks.tsx`'s `useResource`). It exposes a `useSyncExternalStore`
 * hook so the Connections screen and the wizard stay in sync within a
 * session, and reconciles with the backend in the background: reads/writes
 * never block on the network, they just adopt the server's data/ids once a
 * call succeeds.
 */
import { useSyncExternalStore } from "react";
import { api } from "@/lib/api/client";
import {
  defaultDatasetDraft,
  defaultLinkedServiceDraft,
  type DatasetDraft,
  type LinkedServiceDraft,
} from "./connections";
import type { ConnectionRecord, SourceObjectEntry, SourceTargetType } from "@/types";

export type ConnectionRole = "source" | "target" | "both";

export interface SavedConnection {
  id: string;
  /** Human label; also used as the ADF linked service name. */
  name: string;
  role: ConnectionRole;
  description: string;
  /** Full connector-driven linked service definition. */
  linkedService: LinkedServiceDraft;
  /**
   * Tables / input-file onboarding for this connection: either typed in
   * manually ("form") or imported from an uploaded list file ("file").
   */
  objectMode: "form" | "file";
  objects: SourceObjectEntry[];
  /** Path / file / schema / table + file-format customization. */
  dataset: DatasetDraft;
  createdAt: string;
  updatedAt: string;
}

const STORAGE_KEY = "adf-forge:connections:v1";

const EMPTY: SavedConnection[] = [];

let connections: SavedConnection[] = [];
let hydrated = false;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

function persist() {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(connections));
  } catch {
    /* storage unavailable — session-only is acceptable for the demo */
  }
}

function hydrate() {
  if (hydrated || typeof window === "undefined") return;
  hydrated = true;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as SavedConnection[];
      if (Array.isArray(parsed)) connections = parsed;
    }
  } catch {
    connections = [];
  }
}

function subscribe(listener: () => void): () => void {
  hydrate();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): SavedConnection[] {
  hydrate();
  return connections;
}

function getServerSnapshot(): SavedConnection[] {
  return EMPTY;
}

/** Reactive list of saved connections. */
export function useConnections(): SavedConnection[] {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/** Connections that can fill a given side of a copy pipeline. */
export function useConnectionsForRole(role: "source" | "target"): SavedConnection[] {
  return useConnections().filter((c) => c.role === role || c.role === "both");
}

export function getConnection(id: string): SavedConnection | undefined {
  hydrate();
  return connections.find((c) => c.id === id);
}

export function upsertConnection(next: SavedConnection): void {
  hydrate();
  const now = new Date().toISOString();
  const existing = connections.findIndex((c) => c.id === next.id);
  const record: SavedConnection = { ...next, name: next.name.trim() || next.linkedService.name, updatedAt: now };
  if (existing >= 0) {
    connections = connections.map((c, i) => (i === existing ? record : c));
  } else {
    connections = [{ ...record, createdAt: now }, ...connections];
  }
  persist();
  emit();
  void pushConnection(record);
}

export function deleteConnection(id: string): void {
  hydrate();
  connections = connections.filter((c) => c.id !== id);
  persist();
  emit();
  void removeRemoteConnection(id);
}

// --- Metastore sync (§24.17) --------------------------------------------
//
// Best-effort only: every call below is wrapped so a failure (no backend,
// offline, network hiccup) never throws into the caller or reverts the
// optimistic local update — the localStorage mirror above already made the
// UI correct; this just reconciles the shared, team-visible copy when it can.

/** Server ids are `conn-<12 hex chars>` (`new_id("conn")`); a not-yet-synced
 * local id is `conn-<base36 timestamp>-<counter>` (`makeId()`) — never a
 * hex-only suffix — so this tells "needs create" from "needs update". */
const SERVER_ID_RE = /^conn-[0-9a-f]{12}$/;

let activeProjectId: string | null = null;

/** Point the store at a project's backend connection library and pull its
 * current rows. Call once when a screen that manages connections mounts
 * (there is no global "current project" selector yet — same precedent as
 * `project-settings-view.tsx` defaulting to the first project). */
export function connectToProject(projectId: string): void {
  activeProjectId = projectId;
  void pullFromServer(projectId);
}

function recordToConnection(record: ConnectionRecord): SavedConnection {
  const draft = (record.draft ?? {}) as Partial<{
    linkedService: LinkedServiceDraft;
    objectMode: "form" | "file";
    objects: SourceObjectEntry[];
    dataset: DatasetDraft;
  }>;
  return {
    id: record.id,
    name: record.name,
    role: record.role,
    description: record.description,
    linkedService: draft.linkedService ?? defaultLinkedServiceDraft("source", "sql_server"),
    objectMode: draft.objectMode ?? "form",
    objects: draft.objects ?? [],
    dataset: draft.dataset ?? defaultDatasetDraft("source", "sql_server"),
    createdAt: record.createdAt ?? "",
    updatedAt: record.updatedAt ?? "",
  };
}

function connectionPayload(connection: SavedConnection) {
  return {
    name: connection.name,
    role: connection.role,
    connectorType: connection.linkedService.connectorType,
    description: connection.description,
    draft: {
      linkedService: connection.linkedService,
      objectMode: connection.objectMode,
      objects: connection.objects,
      dataset: connection.dataset,
    },
  };
}

/** Pull is authoritative when it succeeds: the backend is the shared,
 * multi-user source of truth, so a reachable server replaces the local
 * mirror outright rather than merging (unreachable leaves the local cache
 * untouched, per the module header). */
async function pullFromServer(projectId: string): Promise<void> {
  try {
    const records = await api.connections.list(projectId);
    connections = records.map(recordToConnection);
    persist();
    emit();
  } catch {
    // Offline or no backend yet — keep whatever localStorage already has.
  }
}

async function pushConnection(connection: SavedConnection): Promise<void> {
  if (!activeProjectId) return;
  try {
    if (SERVER_ID_RE.test(connection.id)) {
      await api.connections.update(connection.id, connectionPayload(connection));
      return;
    }
    const created = await api.connections.create({ projectId: activeProjectId, ...connectionPayload(connection) });
    // Adopt the server-assigned id so the next edit PUTs instead of re-creating.
    connections = connections.map((c) => (c.id === connection.id ? { ...c, id: created.id } : c));
    persist();
    emit();
  } catch {
    // Offline — local copy stands; the next successful pull/push reconciles it.
  }
}

async function removeRemoteConnection(id: string): Promise<void> {
  if (!activeProjectId || !SERVER_ID_RE.test(id)) return;
  try {
    await api.connections.remove(id);
  } catch {
    // Offline — nothing to reconcile locally, it's already gone from the local list.
  }
}

let counter = 0;
function makeId(): string {
  counter += 1;
  return `conn-${Date.now().toString(36)}-${counter}`;
}

/** A blank connection prefilled from the connector registry. */
export function newConnection(role: ConnectionRole = "source"): SavedConnection {
  const connector: SourceTargetType = role === "target" ? "azure_data_lake_storage_gen2" : "sql_server";
  const kind = role === "target" ? "target" : "source";
  const ls = defaultLinkedServiceDraft(kind, connector);
  return {
    id: makeId(),
    name: ls.name,
    role,
    description: "",
    linkedService: ls,
    objectMode: "form",
    objects: [],
    dataset: defaultDatasetDraft(kind, connector),
    createdAt: "",
    updatedAt: "",
  };
}

/** Deep-ish clone so wizard edits never mutate the stored record. */
export function cloneLinkedService(ls: LinkedServiceDraft): LinkedServiceDraft {
  return {
    ...ls,
    fields: { ...ls.fields },
    parameterize: [...ls.parameterize],
  };
}

export function cloneDataset(ds: DatasetDraft): DatasetDraft {
  return { ...ds, format: { ...ds.format } };
}
