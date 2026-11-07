import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { readDataModularWorkbook } from "./sttm-data-modular";
import { buildSttmTemplate } from "./sttm";

const FIXTURES = resolve(__dirname, "../../../backend/tests/fixtures/data_modular");

function bytes(name: string): ArrayBuffer {
  const buf = readFileSync(resolve(FIXTURES, name));
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
}

describe("readDataModularWorkbook (gaps.md DMT-11 preview)", () => {
  it("reads Section 1 and the Section 2 mapping rows of a data_moduler export", async () => {
    const preview = await readDataModularWorkbook(bytes("core_customer.xlsx"));
    expect(preview).not.toBeNull();
    expect(preview!.attributes.length).toBeGreaterThan(0);
    expect(preview!.rows.length).toBeGreaterThan(0);
    const first = preview!.rows[0];
    expect(first).toMatchObject({ recordType: "DATA", sourceObject: "raw_customer", sourceColumn: "cust_id", targetColumn: "customer_id" });
    expect(first.row).toBe(69);
  });

  it("returns null for a legacy-layout workbook", async () => {
    const legacy = await buildSttmTemplate("silver");
    expect(await readDataModularWorkbook(legacy)).toBeNull();
  });
});
