import { describe, expect, it } from "vitest";
import {
  buildLinkedServiceTemplate,
  buildSourceObjectsTemplate,
  connectorSchema,
  parseLinkedServiceFile,
  parseTablesFile,
} from "./connections";

// Dummy/regression coverage for the two new "Upload file" round trips added
// alongside a downloadable template: source objects (table/file onboarding,
// used by Connections + Build + Onboarding) and linked service / source
// system creation (Connections, Build, standalone artifact form). Each
// template is asserted to parse back cleanly with the same module's parser,
// since a template that the app itself can't re-import would be worse than
// no template at all.

describe("buildSourceObjectsTemplate + parseTablesFile round trip", () => {
  it("produces a template that parses back with no warnings", () => {
    const csv = buildSourceObjectsTemplate();
    const { objects, warnings } = parseTablesFile(csv, "source_objects_template.csv");
    expect(warnings).toEqual([]);
    expect(objects).toEqual([
      { object: "customer", catalog: undefined, schema: "dbo", qualifierOverride: undefined, caseOverride: undefined, watermarkColumn: undefined, loadingBehavior: "FullLoad" },
      { object: "ORDERS", catalog: "MYDB", schema: "SALES", qualifierOverride: undefined, caseOverride: undefined, watermarkColumn: "MODIFIED_DATE", loadingBehavior: "DeltaLoad" },
      { object: "BusinessPartner", catalog: undefined, schema: undefined, qualifierOverride: undefined, caseOverride: undefined, watermarkColumn: undefined, loadingBehavior: undefined },
    ]);
  });

  it("ignores '#' documentation lines that precede the header row (regression: header used to only be detected at literal line 0)", () => {
    const content = [
      "# some documentation",
      "# more documentation",
      "object,watermark_column,loading_behavior",
      "dbo.customer,,FullLoad",
    ].join("\n");
    const { objects, warnings } = parseTablesFile(content, "objects.csv");
    expect(warnings).toEqual([]);
    expect(objects).toEqual([{ object: "dbo.customer", watermarkColumn: undefined, loadingBehavior: "FullLoad" }]);
  });

  it("still detects a header with no leading comments (no regression on the common case)", () => {
    const { objects } = parseTablesFile("object\ndbo.customer", "objects.csv");
    expect(objects).toEqual([{ object: "dbo.customer" }]);
  });

  it("maps columns by header name, not position (regression: a header row used to be detected then ignored, falling back to positional cells)", () => {
    // Deliberately out of the template's own column order — a real spreadsheet
    // user might reorder columns, and header-based mapping must not care.
    const content = ["loading_behavior,object,watermark_column", "DeltaLoad,dbo.orders,modified_date"].join("\n");
    const { objects, warnings } = parseTablesFile(content, "objects.csv");
    expect(warnings).toEqual([]);
    expect(objects).toEqual([{ object: "dbo.orders", watermarkColumn: "modified_date", loadingBehavior: "DeltaLoad" }]);
  });

  it("reads catalog/schema/qualifier/case-override columns from a header row", () => {
    const content = [
      "object,catalog,schema,qualifier_override,case_override,watermark_column,loading_behavior",
      "ORDERS,MYDB,SALES,\"MYDB.SALES.ORDERS\",as-typed,MODIFIED_DATE,DeltaLoad",
    ].join("\n");
    const { objects, warnings } = parseTablesFile(content, "objects.csv");
    expect(warnings).toEqual([]);
    expect(objects).toEqual([
      {
        object: "ORDERS",
        catalog: "MYDB",
        schema: "SALES",
        qualifierOverride: "MYDB.SALES.ORDERS",
        caseOverride: "as-typed",
        watermarkColumn: "MODIFIED_DATE",
        loadingBehavior: "DeltaLoad",
      },
    ]);
  });

  it("falls back to minimal positional cells (object, watermark_column, loading_behavior) when the file has no header row", () => {
    const { objects, warnings } = parseTablesFile("dbo.customer,,FullLoad\ndbo.orders,modified_date,DeltaLoad", "objects.csv");
    expect(warnings).toEqual([]);
    expect(objects).toEqual([
      { object: "dbo.customer", watermarkColumn: undefined, loadingBehavior: "FullLoad" },
      { object: "dbo.orders", watermarkColumn: "modified_date", loadingBehavior: "DeltaLoad" },
    ]);
  });
});

describe("buildLinkedServiceTemplate + parseLinkedServiceFile round trip", () => {
  it("round-trips a database connector (Azure SQL Database, SQL authentication), bulk-creating one connection per row", () => {
    const csv = buildLinkedServiceTemplate("azure_sql_database", "sql_authentication");
    const { values, warnings } = parseLinkedServiceFile(csv, "linked_service_template.csv");
    expect(warnings).toEqual([]);
    expect(values).toHaveLength(2);
    expect(values[0]).toMatchObject({
      name: "LS_azure_sql_database_source",
      connectorType: "azure_sql_database",
      authType: "sql_authentication",
      integrationRuntime: "AutoResolveIntegrationRuntime",
      keyVaultLinkedService: "LS_azure_key_vault",
    });
    expect(values[0].fields.server).toBe("myserver.database.windows.net");
    expect(values[0].fields.database).toBe("mydatabase");
    expect(values[0].fields.userName).toBe("sqladmin");
    // Secret fields never carry a value in the template.
    expect(values[0].fields.password).toBeUndefined();
    expect(values[1].name).toBe("LS_azure_sql_database_source_2");
  });

  it("round-trips a file connector (Azure Blob Storage) as a JSON object", () => {
    const csv = buildLinkedServiceTemplate("azure_blob_storage", connectorSchema("azure_blob_storage").defaultAuth);
    // Convert the generated CSV's first data row into an equivalent JSON object,
    // exactly as a user hand-authoring JSON instead of CSV would.
    const lines = csv.split(/\r?\n/).filter((l) => l && !l.startsWith("#"));
    const [header, row] = lines.map((l) => l.split(","));
    const obj: Record<string, string> = {};
    header.forEach((key, i) => (obj[key] = row[i] ?? ""));
    const { values, warnings } = parseLinkedServiceFile(JSON.stringify(obj), "linked_service.json");
    expect(warnings).toEqual([]);
    expect(values).toHaveLength(1);
    expect(values[0].connectorType).toBe("azure_blob_storage");
    expect(values[0].name).toBe("LS_azure_blob_storage_source");
  });

  it("round-trips a JSON array of objects, bulk-creating one connection per entry", () => {
    const content = JSON.stringify([
      { name: "LS_one", connector_type: "azure_sql_database", auth_type: "sql_authentication" },
      { name: "LS_two", connector_type: "azure_sql_database", auth_type: "sql_authentication" },
    ]);
    const { values, warnings } = parseLinkedServiceFile(content, "linked_services.json");
    expect(warnings).toEqual([]);
    expect(values.map((v) => v.name)).toEqual(["LS_one", "LS_two"]);
  });

  it("bulk-creates one connection per CSV data row (regression: used to keep only the first row)", () => {
    const content = [
      "name,connector_type,auth_type,integration_runtime,key_vault_linked_service",
      "LS_one,azure_sql_database,sql_authentication,AutoResolveIntegrationRuntime,LS_azure_key_vault",
      "LS_two,azure_sql_database,sql_authentication,AutoResolveIntegrationRuntime,LS_azure_key_vault",
      "LS_three,azure_sql_database,sql_authentication,AutoResolveIntegrationRuntime,LS_azure_key_vault",
    ].join("\n");
    const { values, warnings } = parseLinkedServiceFile(content, "linked_services.csv");
    expect(warnings).toEqual([]);
    expect(values.map((v) => v.name)).toEqual(["LS_one", "LS_two", "LS_three"]);
  });

  it("warns on a column that isn't a field on the named connector", () => {
    const content = [
      "name,connector_type,auth_type,not_a_real_field",
      "LS_x,azure_sql_database,sql_authentication,whatever",
    ].join("\n");
    const { values, warnings } = parseLinkedServiceFile(content, "linked_service.csv");
    expect(values[0].fields.not_a_real_field).toBeUndefined();
    expect(warnings.some((w) => w.includes("not_a_real_field"))).toBe(true);
  });

  it("reports a failure for a file with no data row", () => {
    const { values, warnings } = parseLinkedServiceFile("name,connector_type", "empty.csv");
    expect(values).toEqual([]);
    expect(warnings.length).toBeGreaterThan(0);
  });

  it("reports a failure for unparsable JSON", () => {
    const { values, warnings } = parseLinkedServiceFile("{not json", "linked_service.json");
    expect(values).toEqual([]);
    expect(warnings[0]).toMatch(/Could not parse/);
  });
});
