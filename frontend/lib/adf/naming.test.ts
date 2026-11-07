import { describe, expect, it } from "vitest";
import {
  buildExpectedName,
  describeRule,
  evaluateName,
  isConfigured,
  ruleFor,
} from "./naming";
import type { NamingConvention } from "@/types";

// Reference implementation for §25.3 — `backend/app/standards.py` and
// `worker/app/validator/naming.py` are vendored ports that must stay in
// parity with this file (see this module's own header + DEVELOPMENT_NOTES.md). No test
// of any kind covered this file before (the frontend had no test runner at
// all); these cases mirror the ones added to the two Python ports so the
// three copies can be checked against the same expectations.

/** `*Pattern` fields are required-but-documentation-only (§25.4) — fill them
 * with empty strings so every test can supply just the parts it cares about. */
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

describe("isConfigured", () => {
  it("is false with no rule", () => {
    expect(isConfigured(undefined)).toBe(false);
    expect(isConfigured({})).toBe(false);
  });

  it("is false when case is explicitly none and nothing else is set", () => {
    expect(isConfigured({ case: "none" })).toBe(false);
  });

  it("is true when any single dimension is set", () => {
    expect(isConfigured({ prefix: "PL_" })).toBe(true);
    expect(isConfigured({ suffix: "_v1" })).toBe(true);
    expect(isConfigured({ maxLength: 64 })).toBe(true);
    expect(isConfigured({ case: "snake" })).toBe(true);
  });
});

describe("buildExpectedName", () => {
  it("wraps the base with prefix and suffix", () => {
    expect(buildExpectedName("customer", { prefix: "PL_", suffix: "_v1" })).toBe("PL_customer_v1");
  });

  it("returns the base unchanged with no rule", () => {
    expect(buildExpectedName("customer", undefined)).toBe("customer");
  });
});

describe("ruleFor", () => {
  it("looks up the rule for the given artifact type", () => {
    const convention = mkConvention({ enforcement: "block", rules: { pipeline: { prefix: "PL_" } } });
    expect(ruleFor(convention, "pipeline")).toEqual({ prefix: "PL_" });
    expect(ruleFor(convention, "dataset")).toBeUndefined();
  });

  it("returns undefined with no convention", () => {
    expect(ruleFor(undefined, "pipeline")).toBeUndefined();
  });
});

describe("describeRule", () => {
  it("reports no rule configured", () => {
    expect(describeRule(undefined)).toBe("No naming rule configured for this artifact type.");
  });

  it("summarizes every configured dimension", () => {
    expect(describeRule({ prefix: "PL_", suffix: "_v1", case: "snake", maxLength: 64 })).toBe(
      'Requires prefix "PL_", suffix "_v1", snake case, max 64 characters.',
    );
  });
});

describe("evaluateName", () => {
  it("returns null when enforcement is off, even with a configured rule", () => {
    const convention = mkConvention({ enforcement: "off", rules: { pipeline: { prefix: "PL_" } } });
    expect(evaluateName("customer_load", "pipeline", convention)).toBeNull();
  });

  it("returns null with no convention at all", () => {
    expect(evaluateName("customer_load", "pipeline", undefined)).toBeNull();
  });

  it("returns null when no rule is configured for the type", () => {
    const convention = mkConvention({ enforcement: "block", rules: { dataset: { prefix: "DS_" } } });
    expect(evaluateName("customer_load", "pipeline", convention)).toBeNull();
  });

  it("flags a missing prefix as a block violation under block enforcement", () => {
    const convention = mkConvention({ enforcement: "block", rules: { pipeline: { prefix: "PL_" } } });
    const violation = evaluateName("customer_load", "pipeline", convention);
    expect(violation).not.toBeNull();
    expect(violation?.level).toBe("block");
    expect(violation?.message).toContain('must start with "PL_"');
  });

  it("flags the same violation as warn under warn enforcement", () => {
    const convention = mkConvention({ enforcement: "warn", rules: { pipeline: { prefix: "PL_" } } });
    expect(evaluateName("customer_load", "pipeline", convention)?.level).toBe("warn");
  });

  it("passes a compliant name", () => {
    const convention = mkConvention({ enforcement: "block", rules: { pipeline: { prefix: "PL_" } } });
    expect(evaluateName("PL_customer_load", "pipeline", convention)).toBeNull();
  });

  it("checks the suffix rule", () => {
    const convention = mkConvention({ enforcement: "block", rules: { dataset: { suffix: "_ds" } } });
    expect(evaluateName("customer", "dataset", convention)?.message).toContain('must end with "_ds"');
    expect(evaluateName("customer_ds", "dataset", convention)).toBeNull();
  });

  it("checks the maxLength rule", () => {
    const convention = mkConvention({ enforcement: "block", rules: { trigger: { maxLength: 5 } } });
    expect(evaluateName("too_long_name", "trigger", convention)?.message).toContain("at most 5 characters");
    expect(evaluateName("short", "trigger", convention)).toBeNull();
  });

  it("checks snake_case", () => {
    const convention = mkConvention({ enforcement: "block", rules: { linkedService: { case: "snake" } } });
    expect(evaluateName("ls_sql_server", "linkedService", convention)).toBeNull();
    expect(evaluateName("LS_SqlServer", "linkedService", convention)?.message).toContain("must be snake case");
  });

  it("checks PascalCase ignoring prefix/suffix when computing the body", () => {
    const convention = mkConvention({
      enforcement: "block",
      rules: { pipeline: { prefix: "PL_", suffix: "_v1", case: "pascal" } },
    });
    expect(evaluateName("PL_CustomerLoad_v1", "pipeline", convention)).toBeNull();
    expect(evaluateName("PL_customerLoad_v1", "pipeline", convention)?.message).toContain("must be pascal case");
  });

  it("treats a blank name as nothing to report, not a violation", () => {
    const convention = mkConvention({ enforcement: "block", rules: { pipeline: { prefix: "PL_" } } });
    expect(evaluateName("   ", "pipeline", convention)).toBeNull();
    expect(evaluateName("", "pipeline", convention)).toBeNull();
  });
});
