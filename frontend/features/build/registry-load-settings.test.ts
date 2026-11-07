import { describe, expect, it } from "vitest";
import { registryLoadSettings } from "@/features/build/registry-object-picker";
import { emptySpecDraft } from "@/lib/adf/spec";
import type { RegistryObject } from "@/types";

const obj = (over: Partial<RegistryObject>): RegistryObject =>
  ({ writeBehavior: "append", loadingBehavior: "FullLoad", businessKeys: [], watermarkColumn: null, watermarkColumnType: null, ...over }) as RegistryObject;

describe("registryLoadSettings (E2E-06)", () => {
  it("carries FullLoad + business keys; merge keys the layer load, not the Bronze Copy", () => {
    const patch = registryLoadSettings(obj({ writeBehavior: "merge", businessKeys: ["customer_id", "region"] }), emptySpecDraft());
    expect(patch).toMatchObject({ layerKeyColumns: "customer_id, region", loadTypeMode: "full", fullLoad: true });
    expect(patch.writeBehavior).toBeUndefined(); // the Bronze landing Copy keeps the form's write behavior
    expect(patch.upsertKeyColumns).toBeUndefined(); // RUN-14: never the Bronze Copy's upsert key
  });

  it("carries a DeltaLoad watermark column and type", () => {
    const prev = { ...emptySpecDraft(), loadTypeMode: "full" as const };
    const patch = registryLoadSettings(obj({ loadingBehavior: "DeltaLoad", watermarkColumn: "updated_at", watermarkColumnType: "bigint" }), prev);
    expect(patch).toMatchObject({ loadTypeMode: "incremental", watermarkEnabled: true });
    expect(patch.watermarkColumns).toEqual([{ name: "updated_at", type: "bigint", order: 1 }]);
    expect(patch.layerKeyColumns).toBeUndefined();
  });
});
