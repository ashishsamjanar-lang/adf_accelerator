/**
 * Generator-parity test harness (AGENTS.md §23.12 gap #20, §29 target design).
 *
 * Prints `buildGeneratedArtifacts(draft)` as normalized JSON for a given
 * SpecDraft fixture file, so `worker/tests/test_generator_parity.py` can diff
 * this — the *reference* generator (`frontend/lib/adf/pipeline-codegen.ts`) —
 * against the worker's Python port (`worker/app/generator/codegen.py`) for
 * an identical input. This script is a test fixture only; nothing in the app
 * imports it.
 *
 * Usage: tsx scripts/export-generated-artifacts.ts <fixture.json>
 */
import { readFileSync } from "node:fs";
import { emptySpecDraft, type SpecDraft } from "../lib/adf/spec";
import { defaultLinkedServiceDraft } from "../lib/adf/connections";
import { buildGeneratedArtifacts } from "../lib/adf/pipeline-codegen";

const fixturePath = process.argv[2];
if (!fixturePath) {
  console.error("Usage: tsx scripts/export-generated-artifacts.ts <fixture.json>");
  process.exit(1);
}

const overrides = JSON.parse(readFileSync(fixturePath, "utf-8")) as Partial<SpecDraft>;
const base = emptySpecDraft();
// Mirror the worker's SpecDraft.from_dict: a fixture that overrides sourceType/targetType without an
// explicit linked service gets that connector's default LS, not emptySpecDraft()'s sql_server/ADLS one.
const draft: SpecDraft = {
  ...base,
  ...overrides,
  sourceLinkedService: overrides.sourceLinkedService ?? defaultLinkedServiceDraft("source", overrides.sourceType ?? base.sourceType),
  targetLinkedService: overrides.targetLinkedService ?? defaultLinkedServiceDraft("target", overrides.targetType ?? base.targetType),
};

const artifacts = buildGeneratedArtifacts(draft)
  .map((a) => ({
    fileName: a.fileName,
    fileType: a.fileType,
    artifactType: a.artifactType,
    layer: a.layer,
    content: a.content,
  }))
  .sort((a, b) => a.fileName.localeCompare(b.fileName));

process.stdout.write(JSON.stringify(artifacts, null, 2));
