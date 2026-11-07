import { describe, expect, it } from "vitest";
import type { ArtifactVersion, NamingConvention } from "@/types";
import {
  advisoryChecks,
  artifactResourceName,
  branchMatchesPattern,
  buildPolicyDocument,
  findInlineSecrets,
  layoutChecks,
  missingRequiredTags,
  scanAdvisoryCompliance,
  scanConnectorCompliance,
  scanNamingCompliance,
  scanOnboardingCompliance,
  scanSecretCompliance,
} from "./compliance";
import type { OnboardingObjectEntry, PipelineDefinition } from "@/types";

function artifact(overrides: Partial<ArtifactVersion>): ArtifactVersion {
  return {
    id: "a1",
    pipelineId: "p1",
    pipelineName: "orders",
    fileName: "pipeline/PL_orders_master.json",
    fileType: "json",
    artifactType: "pipeline",
    layer: "pipeline",
    version: "v1.0.0",
    state: "generated",
    bytes: 10,
    ...overrides,
  } as ArtifactVersion;
}

const convention = {
  enforcement: "block",
  rules: { pipeline: { prefix: "pl_" } },
} as unknown as NamingConvention;

describe("compliance scan", () => {
  it("derives the ADF resource name from the file name", () => {
    expect(artifactResourceName("dataset/DS_x.json")).toBe("DS_x");
  });

  it("flags only nameable artifacts of the latest version in scope", () => {
    const result = scanNamingCompliance(
      [
        artifact({ id: "old", version: "v1.0.0" }),
        artifact({ id: "new", version: "v1.0.10" }),
        artifact({ id: "ok", version: "v1.0.10", fileName: "pipeline/pl_orders_child.json" }),
        artifact({ id: "sql", version: "v1.0.10", artifactType: "factory", fileName: "control/adf_control_table.sql" }),
        artifact({ id: "other", pipelineId: "p2" }),
      ],
      new Set(["p1"]),
      convention,
    );
    expect(result.scanned).toBe(2);
    expect(result.findings.map((f) => f.artifactId)).toEqual(["new"]);
    expect(result.findings[0].level).toBe("block");
  });

  it("reports nothing when enforcement is off", () => {
    const off = { ...convention, enforcement: "off" } as NamingConvention;
    expect(scanNamingCompliance([artifact({})], new Set(["p1"]), off).findings).toEqual([]);
  });
});

describe("policy export", () => {
  it("marks only naming and always-on rules as evaluated", () => {
    const doc = JSON.parse(
      buildPolicyDocument({ id: "p", name: "P" }, convention, [
        { id: "security.kv", category: "Security", rule: "KV", level: "block", alwaysOn: true },
        { id: "tagging.required", category: "Tagging", rule: "Tags", level: "off" },
      ]),
    );
    expect(doc.naming).toMatchObject({ evaluated: true, enforcement: "block", rules: { pipeline: { prefix: "pl_" } } });
    expect(doc.rules.map((r: { evaluated: boolean }) => r.evaluated)).toEqual([true, false]);
  });
});

describe("inline secret scan", () => {
  it("flags a literal password but not a Key Vault reference", () => {
    expect(findInlineSecrets('"connectionString": "Server=x;Password=hunter22;"')).toEqual(["Password in connection string"]);
    expect(findInlineSecrets('{"type":"AzureKeyVaultSecret","v":"Password=abc123"}')).toEqual([]);
  });

  it("scans only the latest version and counts artifacts without content as skipped", () => {
    const result = scanSecretCompliance(
      [
        artifact({ id: "old", version: "v1.0.0", content: "Password=leaked1" }),
        artifact({ id: "new", version: "v1.0.1", content: "{}" }),
        artifact({ id: "nocontent", version: "v1.0.1", fileName: "dataset/DS_x.json" }),
        artifact({ id: "leak", version: "v1.0.1", fileName: "linkedService/LS_x.json", content: "AccountKey=abcdefghijklmnopqrstuvwxyz0123" }),
      ],
      new Set(["p1"]),
    );
    expect(result.scanned).toBe(2);
    expect(result.skipped).toBe(1);
    expect(result.findings.map((f) => f.artifactId)).toEqual(["leak"]);
  });
});

describe("advisory compliance checks", () => {
  it("flags insecure linked-service transport settings like the worker", () => {
    const doc = { properties: { description: "d", typeProperties: { url: "http://x", enableSsl: false, authenticationType: "Anonymous", connectionString: "Server=a;Encrypt=False;TrustServerCertificate=True" } } };
    expect(advisoryChecks("linked_service", doc).filter((f) => f.ruleId === "security.transport").map((f) => f.message)).toEqual([
      "url uses plain HTTP",
      "enableSsl is false",
      "authenticationType is Anonymous",
      "connection string disables transport encryption (Encrypt=False)",
      "connection string skips server certificate validation (TrustServerCertificate=True)",
    ]);
  });

  it("flags Copy activities without retry, nested in containers", () => {
    const doc = { properties: { description: "d", annotations: ["finance"], activities: [
      { name: "CopyOk", type: "Copy", policy: { retry: 2 } },
      { name: "Route", type: "Switch", typeProperties: { cases: [{ activities: [{ name: "CopyNoRetry", type: "Copy", policy: { retry: 0 } }] }] } },
    ] } };
    expect(advisoryChecks("pipeline", doc).filter((f) => f.ruleId === "runtime.retry")).toEqual([{ ruleId: "runtime.retry", message: "Copy activity 'CopyNoRetry' has no retry policy (policy.retry < 1)" }]);
  });

  it("flags missing descriptions on pipeline/dataset/linked service only", () => {
    expect(advisoryChecks("dataset", { properties: { description: "  " } })).toEqual([{ ruleId: "docs.description", message: "Dataset has no description" }]);
    expect(advisoryChecks("trigger", { properties: {} })).toEqual([]);
  });

  it("scans latest JSON artifacts and counts content-less or unparseable ones as skipped", () => {
    const result = scanAdvisoryCompliance(
      [
        artifact({ id: "a", artifactType: "dataset", fileName: "dataset/DS_a.json", content: JSON.stringify({ properties: {} }) }),
        artifact({ id: "b", artifactType: "pipeline", fileName: "pipeline/PL_b.json", content: undefined }),
        artifact({ id: "c", artifactType: "linked_service", fileName: "linkedService/LS_c.json", content: "{not json" }),
        artifact({ id: "d", artifactType: "trigger", fileName: "trigger/TR_d.json", content: "{}" }),
      ],
      new Set(["p1"]),
    );
    expect(result.scanned).toBe(2);
    expect(result.skipped).toBe(2);
    expect(result.findings.map((f) => [f.artifactId, f.ruleId])).toEqual([["a", "docs.description"]]);
  });
});

describe("tagging / auth / parameterization advisory checks", () => {
  it("flags a pipeline without annotations", () => {
    expect(advisoryChecks("pipeline", { properties: { description: "d" } })).toEqual([{ ruleId: "tagging.required", message: "Pipeline has no annotations" }]);
    expect(advisoryChecks("pipeline", { properties: { description: "d", annotations: ["x"] } })).toEqual([]);
  });

  it("flags credential-based linked-service auth but not managed identity", () => {
    const sql = { properties: { description: "d", typeProperties: { authenticationType: "SQL" } } };
    const mi = { properties: { description: "d", typeProperties: { authenticationType: "ManagedIdentity" } } };
    expect(advisoryChecks("linked_service", sql)).toEqual([{ ruleId: "connection.auth", message: "authenticationType is SQL — prefer managed identity or service principal" }]);
    expect(advisoryChecks("linked_service", mi)).toEqual([]);
  });

  it("flags literal env-specific values but not expressions", () => {
    const ls = { properties: { description: "d", typeProperties: { server: "sql01.corp", url: "@linkedService().url", database: "sales" } } };
    const ds = { properties: { description: "d", typeProperties: { location: { container: "raw", folderPath: { type: "Expression", value: "@dataset().path" } } } } };
    expect(advisoryChecks("linked_service", ls)).toEqual([{ ruleId: "param.literals", message: "server is a literal — parameterize environment-specific values" }]);
    expect(advisoryChecks("dataset", ds).map((f) => f.message)).toEqual(["container is a literal — parameterize environment-specific values"]);
  });
});

describe("onboarding compliance scan", () => {
  const base: OnboardingObjectEntry = {
    object_key: "crm.orders", enabled: true, owner: "ana", source_system: "crm", source_type: "table", source_object: "orders",
    target_type: "table", target_object: "orders", target_file_format: "parquet", loading_behavior: "FullLoad",
    data_classification: "confidential",
  };

  it("reports missing fields and DeltaLoad watermark gaps on enabled objects only", () => {
    const result = scanOnboardingCompliance([
      base,
      { ...base, object_key: "crm.a", owner: " ", target_object: undefined, data_classification: "internal" },
      { ...base, object_key: "crm.b", loading_behavior: "DeltaLoad", watermark_column: "updated_at", data_classification: "internal" },
      { ...base, object_key: "crm.off", enabled: false, owner: undefined },
    ]);
    expect(result.scanned).toBe(3);
    expect(result.findings).toEqual([
      { objectKey: "crm.a", ruleId: "onboarding.fields", message: "Missing owner, target_object/target_path" },
      { objectKey: "crm.b", ruleId: "onboarding.watermark", message: "DeltaLoad object missing watermark_column_type" },
    ]);
  });
});

describe("remaining §25 rules", () => {
  it("security.pii flags an enabled onboarded object without data_classification", () => {
    const obj = {
      object_key: "crm.x", enabled: true, owner: "ana", source_system: "crm", source_type: "table", source_object: "x",
      target_type: "table", target_object: "x", target_file_format: "parquet", loading_behavior: "FullLoad",
    } as OnboardingObjectEntry;
    expect(scanOnboardingCompliance([obj]).findings).toEqual([{ objectKey: "crm.x", ruleId: "security.pii", message: "No data_classification set" }]);
  });

  it("structure.layout checks the folder and the resource-name/file-name match", () => {
    expect(layoutChecks("pipeline", "PL_a.json", { name: "PL_a" })).toEqual([]);
    expect(layoutChecks("dataset", "pipeline/DS_a.json", { name: "DS_b" })).toEqual([
      "Stored under 'pipeline/' — ADF expects 'dataset/'",
      "Resource name 'DS_b' doesn't match file name 'DS_a.json'",
    ]);
    expect(layoutChecks("factory", "globalParameters.json", { name: "factory" })).toEqual([]);
  });

  it("audit.columns flags Copy activities missing the layer's audit columns, ignoring delete-staging copies", () => {
    const doc = {
      properties: {
        description: "d",
        annotations: ["x"],
        folder: { name: "metadata-driven/silver" },
        activities: [
          { name: "CopyA", type: "Copy", policy: { retry: 1 }, typeProperties: { source: { typeProperties: { additionalColumns: [] } } } },
          { name: "StageSourceKeys", type: "Copy", policy: { retry: 1 } },
        ],
      },
    };
    const findings = advisoryChecks("pipeline", doc, { auditColumns: { silver: [{ name: "_run_id", value: "@pipeline().RunId" }] } });
    expect(findings).toEqual([{ ruleId: "audit.columns", message: "Copy activity 'CopyA' is missing audit column(s) _run_id" }]);
  });

  it("connection.connectors reports connectors outside a configured allow-list", () => {
    const p = { name: "orders", spec: { source: { type: "oracle" }, target: { type: "azure_sql_database" } } } as unknown as PipelineDefinition;
    expect(scanConnectorCompliance([p], []).configured).toBe(false);
    expect(scanConnectorCompliance([p], ["azure_sql_database"]).findings).toEqual([{ pipelineName: "orders", message: "Source connector 'oracle' is not on the allow-list" }]);
  });

  it("git.branch matches {tokens} as one segment and everything else literally", () => {
    expect(branchMatchesPattern("feature/orders-20260926", "feature/{pipeline-name}-{timestamp}")).toBe(true);
    expect(branchMatchesPattern("hotfix/orders", "feature/{pipeline-name}-{timestamp}")).toBe(false);
    expect(branchMatchesPattern("anything", "")).toBe(true);
  });
});

describe("required tags (gaps.md GOV-05/WD-06)", () => {
  it("matches key and key:value annotations case-insensitively", () => {
    expect(missingRequiredTags(["data-domain:hr", "PII"], ["data-domain", "pii", "source-system"])).toEqual(["source-system"]);
  });

  it("reports missing required keys on a pipeline instead of the bare no-annotations check", () => {
    const doc = { properties: { annotations: ["pii"], description: "x", activities: [] } };
    const findings = advisoryChecks("pipeline", doc, { requiredTagKeys: ["pii", "data-domain"] }).filter((f) => f.ruleId === "tagging.required");
    expect(findings.map((f) => f.message)).toEqual(["Pipeline is missing required tag(s) data-domain"]);
    expect(advisoryChecks("pipeline", { properties: { annotations: [], description: "x" } }).some((f) => f.ruleId === "tagging.required")).toBe(true);
  });
});
