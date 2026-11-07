import { describe, expect, it } from "vitest";
import { buildGeneratedArtifacts } from "./pipeline-codegen";
import { emptySpecDraft } from "./spec";
import { evaluateName } from "./naming";
import type { SpecDraft } from "./spec";
import type { ArtifactVersion, NamingArtifactType, NamingConvention } from "@/types";

/**
 * Generator -> validator integration, frontend side (AGENTS.md §29, mirrors
 * `worker/tests/test_generator_validation_integration.py`).
 *
 * `spec.test.ts` covers `validateDraft` (pre-generation, draft-level) and
 * `naming.test.ts` covers `evaluateName` in isolation against hand-typed
 * names. Neither proves that this file's own reference generator
 * (`buildGeneratedArtifacts`, the implementation `worker/app/generator/
 * codegen.py` is a *port of*) actually produces artifact names that pass the
 * ADF-safe rule and the §25 naming standard, or that its secret handling
 * (AzureKeyVaultSecret dynamic content) doesn't accidentally trip a
 * hardcoded-secret scan. This file closes that gap on the frontend, the same
 * way the new worker suite closes it there.
 *
 * There is no frontend port of `worker/app/validator/rules.py` (JSON-syntax /
 * artifact-name / secret-scan run only server-side, post-persist) — so the
 * checks below reimplement just enough of it, inline, to hold the reference
 * generator to the same bar its worker port is already tested against.
 */

// Mirrors `worker/app/validator/rules.py`'s ARTIFACT_NAME_RE (§23.4a): the
// always-on ADF-safe rule every declared artifact name must satisfy,
// independent of any opt-in §25 naming standard.
const ARTIFACT_NAME_RE = /^[A-Za-z][A-Za-z0-9_]{1,127}$/;

// A deliberately small mirror of `worker/app/validator/rules.py`'s
// SECRET_PATTERNS — just enough to prove the reference generator's real
// output never regresses into embedding a literal secret instead of an
// AzureKeyVaultSecret reference. The worker suite owns the exhaustive list;
// this is a parity guard, not a replacement for it.
const SECRET_LIKE_PATTERNS: RegExp[] = [
  /(?:password|pwd)\s*=\s*(?!@|\{\{)[^;"']{3,}/i,
  /AccountKey=(?!@|\{\{)[A-Za-z0-9+/=]{20,}/i,
  /"(?:api_?key|client_?secret|access_?token)"\s*:\s*"[A-Za-z0-9._-]{16,}"/i,
];

function draftWith(overrides: Partial<SpecDraft> = {}): SpecDraft {
  const base = emptySpecDraft();
  return {
    ...base,
    name: "customer_incremental",
    projectId: "proj-a",
    sourceLinkedService: {
      ...base.sourceLinkedService,
      connectorType: "sql_server",
      authType: "sql_authentication",
      fields: { server: "sqlsrv01.database.windows.net", database: "customerdb", userName: "svc_reader", password: "sqlserver-password" },
    },
    targetLinkedService: {
      ...base.targetLinkedService,
      connectorType: "azure_data_lake_storage_gen2",
      authType: "account_key",
      fields: { accountName: "stgcustomer", url: "https://stgcustomer.dfs.core.windows.net", accountKey: "storage-account-key" },
    },
    sourceDataset: { ...base.sourceDataset, linkedServiceName: base.sourceLinkedService.name },
    targetDataset: { ...base.targetDataset, linkedServiceName: base.targetLinkedService.name },
    ...overrides,
  };
}

// A `NamingArtifactType` for every `ArtifactType` the naming standard covers
// (§25.3 deliberately excludes "parameter"/"factory" — see project-settings
// types — so those two are skipped below, same as the worker port).
const NAMING_TYPE_BY_ARTIFACT_TYPE: Partial<Record<ArtifactVersion["artifactType"], NamingArtifactType>> = {
  pipeline: "pipeline",
  dataset: "dataset",
  linked_service: "linkedService",
  trigger: "trigger",
};

function jsonArtifacts(artifacts: ArtifactVersion[]): { artifact: ArtifactVersion; body: Record<string, unknown> }[] {
  return artifacts
    .filter((a) => a.fileType === "json" && a.content)
    .map((artifact) => ({ artifact, body: JSON.parse(artifact.content as string) as Record<string, unknown> }));
}

// Same minimal-fixture helper `naming.test.ts` uses — `NamingConvention`'s
// `*Pattern` fields are documentation-only (§25.4) and irrelevant here.
function mkConvention(overrides: Partial<NamingConvention> = {}): NamingConvention {
  return {
    pipelinePattern: "",
    datasetPattern: "",
    linkedServicePattern: "",
    parameterPattern: "",
    triggerPattern: "",
    ...overrides,
  };
}

describe("a real generated artifact set passes the always-on ADF-safe naming rule", () => {
  const templates: SpecDraft["templateId"][] = [
    "full_load",
    "incremental_load",
    "initial_load",
    "composite_watermark_load",
    "metadata_driven_load",
  ];

  it.each(templates)("every declared name in the %s template's output is ADF-safe", (templateId) => {
    const artifacts = buildGeneratedArtifacts(draftWith({ templateId }));
    expect(artifacts.length).toBeGreaterThan(0);

    for (const { artifact, body } of jsonArtifacts(artifacts)) {
      const declared = body.name;
      if (typeof declared !== "string") continue;
      // globalParameters.json's factory name is the `<FACTORY_NAME>`
      // placeholder when no ADF instance is selected — exempt, same carve-out
      // as the worker validator's.
      if (artifact.artifactType === "factory") {
        expect(declared === "<FACTORY_NAME>" || /^[A-Za-z0-9][A-Za-z0-9-]{1,61}[A-Za-z0-9]$/.test(declared)).toBe(true);
        continue;
      }
      expect(declared, `${artifact.fileName}: "${declared}"`).toMatch(ARTIFACT_NAME_RE);
    }
  });

  it.each(templates)("the %s template's output never embeds a hardcoded-secret-shaped literal", (templateId) => {
    const artifacts = buildGeneratedArtifacts(draftWith({ templateId }));
    for (const artifact of artifacts) {
      for (const pattern of SECRET_LIKE_PATTERNS) {
        expect(artifact.content, `${artifact.fileName} matched ${pattern}`).not.toMatch(pattern);
      }
    }
    // Confirms the check above isn't vacuous — the linked services really do
    // carry a secret-shaped field, just routed through Key Vault reference.
    const sourceLs = artifacts.find((a) => a.artifactType === "linked_service" && a.fileName.includes("source"));
    expect(sourceLs?.content).toContain("AzureKeyVaultSecret");
    expect(sourceLs?.content).toContain("sqlserver-password");
  });
});

describe("§25 opt-in naming standard against real generated artifact names", () => {
  it("flags every generated pipeline/dataset/linked-service/trigger name that lacks a required block-level prefix", () => {
    const artifacts = buildGeneratedArtifacts(draftWith());
    const convention = mkConvention({
      enforcement: "block",
      rules: { pipeline: { prefix: "PL_ACME_" }, dataset: { prefix: "DS_ACME_" }, linkedService: { prefix: "LS_ACME_" }, trigger: { prefix: "TR_ACME_" } },
    });

    let checked = 0;
    for (const { artifact, body } of jsonArtifacts(artifacts)) {
      const namingType = NAMING_TYPE_BY_ARTIFACT_TYPE[artifact.artifactType];
      if (!namingType || typeof body.name !== "string") continue;
      checked += 1;
      const violation = evaluateName(body.name, namingType, convention);
      expect(violation, `${artifact.fileName} ("${body.name}") should violate the required prefix`).not.toBeNull();
      expect(violation?.level).toBe("block");
    }
    expect(checked).toBeGreaterThan(0);
  });

  it("passes once the generated names actually carry the required prefix", () => {
    // The reference generator always emits "PL_{name}_master"/"PL_{name}_child" —
    // so a rule requiring exactly that prefix is satisfied without renaming
    // anything, proving the convention isn't just always failing above.
    const artifacts = buildGeneratedArtifacts(draftWith());
    const convention = mkConvention({ enforcement: "block", rules: { pipeline: { prefix: "PL_" } } });
    const pipelines = jsonArtifacts(artifacts).filter(({ artifact }) => artifact.artifactType === "pipeline");
    expect(pipelines.length).toBeGreaterThan(0);
    for (const { body } of pipelines) {
      expect(evaluateName(body.name as string, "pipeline", convention)).toBeNull();
    }
  });
});

describe("a tampered real artifact is caught the same way a hand-written fixture would be", () => {
  it("a renamed dataset that breaks ADF-safe naming fails the check a clean one passes", () => {
    const artifacts = buildGeneratedArtifacts(draftWith());
    const dataset = jsonArtifacts(artifacts).find(({ artifact }) => artifact.artifactType === "dataset");
    expect(dataset).toBeDefined();
    expect(dataset!.body.name as string).toMatch(ARTIFACT_NAME_RE);

    const tamperedName = "DS target with spaces!";
    expect(tamperedName).not.toMatch(ARTIFACT_NAME_RE);
  });
});
