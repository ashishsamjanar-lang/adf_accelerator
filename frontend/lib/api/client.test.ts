import { afterEach, describe, expect, it, vi } from "vitest";
import { api, getLastApiActivityAt, SESSION_EXPIRED_EVENT } from "./client";

// Dummy coverage for the connection-library + template/parse client methods
// added alongside the metastore-backed connection store (§24.17) — asserts
// each hits the expected URL/method/body, not real network behavior.

function mockFetchOnce(body: unknown, init: Partial<Response> = {}) {
  const response = {
    ok: true,
    status: 200,
    json: async () => body,
    ...init,
  } as Response;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api.connections", () => {
  it("lists connections scoped to a project", async () => {
    mockFetchOnce([{ id: "conn-1" }]);
    await api.connections.list("proj-a");
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/v1/connections?project_id=proj-a");
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("creates a connection with the given payload", async () => {
    mockFetchOnce({ id: "conn-1" });
    await api.connections.create({
      projectId: "proj-a",
      name: "ERP",
      role: "source",
      connectorType: "sql_server",
      description: "",
      draft: { linkedService: {} },
    });
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/v1/connections");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toMatchObject({ projectId: "proj-a", name: "ERP" });
  });

  it("updates a connection by id", async () => {
    mockFetchOnce({ id: "conn-1" });
    await api.connections.update("conn-1", { name: "Renamed" });
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/v1/connections/conn-1");
    expect(init.method).toBe("PUT");
  });

  it("deletes a connection by id", async () => {
    mockFetchOnce(undefined, { status: 204 });
    await api.connections.remove("conn-1");
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/v1/connections/conn-1");
    expect(init.method).toBe("DELETE");
  });
});

describe("api.connectorTemplates", () => {
  it("builds the source-objects template download URL", () => {
    expect(api.connectorTemplates.sourceObjectsTemplateUrl()).toBe("/api/v1/connectors/templates/source-objects");
  });

  it("builds the linked-service template URL with an optional auth_type", () => {
    expect(api.connectorTemplates.linkedServiceTemplateUrl("azure_sql_database")).toBe(
      "/api/v1/connectors/azure_sql_database/linked-service-template",
    );
    expect(api.connectorTemplates.linkedServiceTemplateUrl("azure_sql_database", "sql_authentication")).toBe(
      "/api/v1/connectors/azure_sql_database/linked-service-template?auth_type=sql_authentication",
    );
  });

  it("posts content + fileName to parse source objects", async () => {
    mockFetchOnce({ objects: [], warnings: [] });
    await api.connectorTemplates.parseSourceObjects("object\ndbo.customer", "objects.csv");
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/v1/connectors/templates/source-objects/parse");
    expect(JSON.parse(init.body as string)).toEqual({ content: "object\ndbo.customer", fileName: "objects.csv" });
  });

  it("posts content + fileName to parse a linked service file", async () => {
    mockFetchOnce({ values: null, warnings: [] });
    await api.connectorTemplates.parseLinkedService("name,connector_type\nx,sql_server", "ls.csv");
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/v1/connectors/linked-service/parse");
    expect(JSON.parse(init.body as string).fileName).toBe("ls.csv");
  });
});

describe("session expiry (§27 session UX)", () => {
  it("fires SESSION_EXPIRED_EVENT on a 401 from a non-auth endpoint, not from /auth/*", async () => {
    const listener = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, listener);
    try {
      mockFetchOnce({}, { ok: false, status: 401 });
      await expect(api.connections.list("proj-a")).rejects.toThrow();
      expect(listener).toHaveBeenCalledTimes(1);
      mockFetchOnce({}, { ok: false, status: 401 });
      await expect(api.auth.me()).rejects.toThrow();
      expect(listener).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(SESSION_EXPIRED_EVENT, listener);
    }
  });

  it("records the time of the last successful response, not a failed one", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-09-25T10:00:00Z"));
      mockFetchOnce({ authMode: "password", idleTimeoutMinutes: 30 });
      await api.auth.config();
      expect(getLastApiActivityAt()).toBe(Date.parse("2026-09-25T10:00:00Z"));
      vi.setSystemTime(new Date("2026-09-25T10:05:00Z"));
      mockFetchOnce({}, { ok: false, status: 500 });
      await expect(api.auth.me()).rejects.toThrow();
      expect(getLastApiActivityAt()).toBe(Date.parse("2026-09-25T10:00:00Z"));
    } finally {
      vi.useRealTimers();
    }
  });
});
