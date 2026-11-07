import { describe, expect, it } from "vitest";
import {
  buildSafeQualifierToken,
  checkQualifierCollision,
  checkSapExtractionSupport,
  getQualifierProfile,
  resolveQualifiedName,
  sanitizeForAdfName,
} from "./qualifier";

// Root-cause coverage for the naming-convention-edge-cases fix: the old
// `pipeline-codegen.ts`/`spec.ts` behavior blindly split on "." and defaulted
// to "dbo" — these cases are exactly what that got wrong.

describe("getQualifierProfile", () => {
  it("defaults unknown/undefined connectors to 2-part schema.object", () => {
    expect(getQualifierProfile(undefined)).toEqual({ parts: ["schema", "object"], caseConvention: "insensitive", objectKind: "table" });
  });

  it("derives file connectors from the registry kind", () => {
    expect(getQualifierProfile("azure_blob_storage").objectKind).toBe("file");
  });

  it("derives SaaS/service connectors from the registry kind", () => {
    expect(getQualifierProfile("salesforce").parts).toEqual(["object"]);
  });
});

describe("resolveQualifiedName — 2-part relational (unchanged behavior)", () => {
  it("splits schema.table for a plain SQL Server object", () => {
    const resolved = resolveQualifiedName({ object: "dbo.customer" }, getQualifierProfile("sql_server"));
    expect(resolved).toMatchObject({ schema: "dbo", object: "customer", forQuery: "dbo.customer" });
  });

  it("falls back to no schema when only a bare table is given", () => {
    const resolved = resolveQualifiedName({ object: "customer" }, getQualifierProfile("sql_server"));
    expect(resolved.schema).toBeUndefined();
    expect(resolved.object).toBe("customer");
  });
});

describe("resolveQualifiedName — 3-part sources", () => {
  it("resolves catalog.schema.table for SAP HANA instead of mis-joining into schema", () => {
    const resolved = resolveQualifiedName({ object: "PRD.SAPABAP1.MARA" }, getQualifierProfile("sap_hana"));
    expect(resolved.catalog).toBe("PRD");
    expect(resolved.schema).toBe("SAPABAP1");
    expect(resolved.object).toBe("MARA");
    expect(resolved.forQuery).toBe("PRD.SAPABAP1.MARA");
  });

  it("accepts structured catalog/schema fields directly, bypassing string parsing", () => {
    const resolved = resolveQualifiedName({ catalog: "prd", schema: "sapabap1", object: "mara" }, getQualifierProfile("sap_hana"));
    // Case-conformed to SAP HANA's uppercase convention even though typed lowercase.
    expect(resolved).toMatchObject({ catalog: "PRD", schema: "SAPABAP1", object: "MARA" });
  });

  it("folds extra leading dot-segments into catalog rather than guessing", () => {
    const resolved = resolveQualifiedName({ object: "a.b.c.table" }, getQualifierProfile("sap_hana"));
    expect(resolved).toMatchObject({ catalog: "A.B", schema: "C", object: "TABLE" });
  });
});

describe("resolveQualifiedName — schema-less SaaS/OData sources", () => {
  it("never forces a 'dbo' schema onto a SAP ECC OData entity", () => {
    const resolved = resolveQualifiedName({ object: "API_SALES_ORDER_SRV.A_SalesOrder" }, getQualifierProfile("sap_ecc"));
    expect(resolved.schema).toBeUndefined();
    expect(resolved.catalog).toBeUndefined();
    // The dot is part of the OData service path, not a schema separator — must survive intact.
    expect(resolved.object).toBe("API_SALES_ORDER_SRV.A_SalesOrder");
  });

  it("does not split a Salesforce object name that happens to contain a dot", () => {
    const resolved = resolveQualifiedName({ object: "Account.Contact" }, getQualifierProfile("salesforce"));
    expect(resolved.schema).toBeUndefined();
    expect(resolved.forQuery).toBe("Account.Contact");
  });
});

describe("resolveQualifiedName — automatic case conformance", () => {
  it("upper-cases an object onboarded in lowercase for an Oracle source", () => {
    const resolved = resolveQualifiedName({ object: "mara" }, getQualifierProfile("oracle"));
    // No schema given, so only the object segment is folded — still correctly upper-cased.
    expect(resolved.forQuery).toBe("MARA");
  });

  it("lower-cases an object onboarded in mixed case for a Postgres source", () => {
    const resolved = resolveQualifiedName({ object: "Public.Customer" }, getQualifierProfile("postgresql"));
    expect(resolved.forQuery).toBe("public.customer");
  });

  it("skips case-folding when caseOverride is 'as-typed'", () => {
    const resolved = resolveQualifiedName({ object: "Sapabap1.Mara", caseOverride: "as-typed" }, getQualifierProfile("sap_hana"));
    expect(resolved.forQuery).toBe("Sapabap1.Mara");
  });

  it("bypasses parsing and case-folding entirely with qualifierOverride", () => {
    const resolved = resolveQualifiedName({ object: "ignored", qualifierOverride: '"Weird Case".Table' }, getQualifierProfile("sap_hana"));
    expect(resolved.forQuery).toBe('"Weird Case".Table');
  });
});

describe("sanitizeForAdfName", () => {
  it("replaces every ADF-illegal character with underscore", () => {
    expect(sanitizeForAdfName("/BIC/AZCUSTOMER")).toBe("BIC_AZCUSTOMER");
    expect(sanitizeForAdfName("sales order-2024")).toBe("sales_order_2024");
    expect(sanitizeForAdfName("a.b+c?d/e<f>g*h%i&j:k\\l")).toBe("a_b_c_d_e_f_g_h_i_j_k_l");
  });

  it("never returns an empty string", () => {
    expect(sanitizeForAdfName("///")).toBe("object");
  });

  it("preserves a legitimate double underscore not touching any illegal character (SF-01)", () => {
    // Regression: Salesforce's literal `__c`/`__r` custom-field/relationship
    // suffix must survive unchanged — a blind "collapse every underscore
    // run" pass previously turned `Renewal_Date__c` into `Renewal_Date_c`,
    // which could silently collide with an unrelated object sharing the
    // same base name.
    expect(sanitizeForAdfName("Renewal_Date__c")).toBe("Renewal_Date__c");
    expect(sanitizeForAdfName("Owner__r")).toBe("Owner__r");
  });

  it("still collapses an illegal character touching a literal underscore", () => {
    expect(sanitizeForAdfName("sales_-order")).toBe("sales_order");
    expect(sanitizeForAdfName("a_/b")).toBe("a_b");
  });
});

describe("checkQualifierCollision", () => {
  it("flags two different objects that sanitize to the same token", () => {
    const collisions = checkQualifierCollision([
      { key: "curated.policy", token: "curated_policy" },
      { key: "curated-policy", token: "curated_policy" },
      { key: "curated.claim", token: "curated_claim" },
    ]);
    expect(collisions).toEqual([{ token: "curated_policy", keys: ["curated.policy", "curated-policy"] }]);
  });

  it("reports nothing when every token is unique", () => {
    expect(checkQualifierCollision([{ key: "a", token: "a" }, { key: "b", token: "b" }])).toEqual([]);
  });

  it("does NOT fold case for an 'insensitive' connector profile (SAP-05b — documents a real gap)", () => {
    // A connector's "insensitive" case convention only means
    // resolveQualifiedName() doesn't *transform* case (unlike "upper"/
    // "lower", which do) — it does not mean two objects differing only by
    // case are treated as the same object for collision purposes.
    // `Y_Report` and `y_report` on SQL Server resolve to different tokens
    // and so silently generate two separate artifacts for what the source
    // considers one table.
    const profile = getQualifierProfile("sql_server");
    expect(profile.caseConvention).toBe("insensitive");
    const resolvedA = resolveQualifiedName({ object: "Y_Report" }, profile);
    const resolvedB = resolveQualifiedName({ object: "y_report" }, profile);
    const tokenA = buildSafeQualifierToken(resolvedA);
    const tokenB = buildSafeQualifierToken(resolvedB);
    expect(tokenA).not.toBe(tokenB);
    expect(
      checkQualifierCollision([
        { key: "Y_Report", token: tokenA },
        { key: "y_report", token: tokenB },
      ]),
    ).toEqual([]);
  });
});

describe("checkSapExtractionSupport", () => {
  it("blocks a BW-namespaced object read through a non-BW connector", () => {
    const issue = checkSapExtractionSupport("sap_hana", "/BIC/AZCUSTOMER");
    expect(issue?.level).toBe("error");
  });

  it("allows a BW-namespaced object through the sap_bw connector itself", () => {
    expect(checkSapExtractionSupport("sap_bw", "/BIC/AZCUSTOMER")).toBeNull();
  });

  it("warns (not blocks) on a well-known pool/cluster table name", () => {
    const issue = checkSapExtractionSupport("sap_hana", "BSEG");
    expect(issue?.level).toBe("warning");
  });

  it("is silent for an ordinary transparent table", () => {
    expect(checkSapExtractionSupport("sap_hana", "MARA")).toBeNull();
  });
});

describe("buildSafeQualifierToken", () => {
  it("joins sanitized segments with underscores", () => {
    const resolved = resolveQualifiedName({ object: "PRD./BIC/AZ.MARA" }, getQualifierProfile("sap_hana"));
    expect(buildSafeQualifierToken(resolved)).toBe("PRD_BIC_AZ_MARA");
  });
});
