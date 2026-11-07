import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { ColumnMappingsEditor } from "./column-mappings-editor";
import { SourceObjectsEditor } from "./connection-editors";
import type { ColumnMapping } from "@/lib/adf/spec";
import type { SourceObjectEntry } from "@/types";

afterEach(cleanup);

function Harness() {
  const [value, setValue] = useState<ColumnMapping[] | undefined>(undefined);
  return (
    <>
      <ColumnMappingsEditor value={value ?? []} onChange={setValue} />
      <output data-testid="state">{JSON.stringify(value ?? null)}</output>
    </>
  );
}

const state = () => JSON.parse(screen.getByTestId("state").textContent ?? "null") as ColumnMapping[] | null;

describe("ColumnMappingsEditor (schema-drift slice)", () => {
  it("adds, edits, and removes mappings — removing the last one clears the field", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: /Add column mapping/ }));
    fireEvent.change(screen.getByLabelText("Source column 1"), { target: { value: "CUST_NM" } });
    fireEvent.change(screen.getByLabelText("Sink column 1"), { target: { value: "customer_name" } });
    expect(state()).toEqual([{ source: "CUST_NM", sink: "customer_name" }]);
    fireEvent.click(screen.getByRole("button", { name: "Remove mapping 1" }));
    expect(state()).toBeNull();
  });
});

describe("SourceObjectsEditor per-object column mappings", () => {
  function ObjectsHarness({ showColumnMappings }: { showColumnMappings: boolean }) {
    const [rows, setRows] = useState<SourceObjectEntry[]>([{ object: "dbo.customer" }]);
    return (
      <>
        <SourceObjectsEditor
          mode="form"
          rows={rows}
          errors={[]}
          onChange={(_, next) => setRows(next)}
          showColumnMappings={showColumnMappings}
        />
        <output data-testid="rows">{JSON.stringify(rows)}</output>
      </>
    );
  }

  it("edits one object's mappings under Show all columns", () => {
    render(<ObjectsHarness showColumnMappings />);
    fireEvent.click(screen.getByRole("button", { name: "Show all columns" }));
    fireEvent.click(screen.getByRole("button", { name: /Add column mapping/ }));
    fireEvent.change(screen.getByLabelText("Object 1 Source column 1"), { target: { value: "CUST_NM" } });
    fireEvent.change(screen.getByLabelText("Object 1 Sink column 1"), { target: { value: "customer_name" } });
    const rows = JSON.parse(screen.getByTestId("rows").textContent ?? "[]") as SourceObjectEntry[];
    expect(rows[0].columnMappings).toEqual([{ source: "CUST_NM", sink: "customer_name" }]);
  });

  it("stays hidden unless the host opts in", () => {
    render(<ObjectsHarness showColumnMappings={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Show all columns" }));
    expect(screen.queryByRole("button", { name: /Add column mapping/ })).toBeNull();
  });
});
