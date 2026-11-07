import { describe, expect, it } from "vitest";
import {
  ALL_STATES,
  can,
  isFailureState,
  isProgressable,
  LIFECYCLE_STEPS,
  ROLES,
  stateLabel,
  stateProgress,
} from "./state";

// AGENTS.md §10 (lifecycle) + §11 (RBAC) — the frontend's own copy of the
// state machine and permission matrix the UI uses to decide what to render
// and offer; had no test at all before this file.

describe("stateLabel / stateProgress", () => {
  it("labels and orders every non-failure lifecycle step", () => {
    expect(stateLabel("DRAFT")).toBe("Draft");
    expect(stateProgress("DRAFT")).toBe(1);
    expect(stateLabel("PR_CREATED")).toBe("PR created");
    expect(stateProgress("PR_CREATED")).toBe(LIFECYCLE_STEPS.length);
  });

  it("labels a failure state via FAILURE_STATES, not the step list", () => {
    expect(stateLabel("GIT_PUSH_FAILED")).toBe("Git push failed");
    expect(stateProgress("GIT_PUSH_FAILED")).toBe(0);
  });
});

describe("isFailureState", () => {
  it("is true for every *_FAILED state", () => {
    expect(isFailureState("GENERATION_FAILED")).toBe(true);
    expect(isFailureState("VALIDATION_FAILED")).toBe(true);
    expect(isFailureState("GIT_PUSH_FAILED")).toBe(true);
    expect(isFailureState("PR_CREATION_FAILED")).toBe(true);
  });

  it("is false for a normal lifecycle state", () => {
    expect(isFailureState("GENERATED")).toBe(false);
  });
});

describe("isProgressable", () => {
  it("is false for DRAFT", () => {
    expect(isProgressable("DRAFT")).toBe(false);
  });

  it("is false for a failure state", () => {
    expect(isProgressable("GENERATION_FAILED")).toBe(false);
  });

  it("is true for a real, non-draft lifecycle state", () => {
    expect(isProgressable("GENERATED")).toBe(true);
    expect(isProgressable("PUSHED")).toBe(true);
  });
});

describe("ALL_STATES", () => {
  it("includes every lifecycle step and every failure state exactly once", () => {
    expect(new Set(ALL_STATES).size).toBe(ALL_STATES.length);
    expect(ALL_STATES).toContain("DRAFT");
    expect(ALL_STATES).toContain("GIT_PUSH_FAILED");
  });
});

describe("can (RBAC matrix)", () => {
  it("grants PLATFORM_ADMIN every permission, including platform admin", () => {
    expect(can("PLATFORM_ADMIN", "admin:platform")).toBe(true);
    expect(can("PLATFORM_ADMIN", "pipeline:push")).toBe(true);
  });

  it("denies TEAM_ADMIN the platform-only permission", () => {
    expect(can("TEAM_ADMIN", "admin:platform")).toBe(false);
    expect(can("TEAM_ADMIN", "admin:team")).toBe(true);
  });

  it("lets DEVELOPER create/generate/validate/push but not administer", () => {
    expect(can("DEVELOPER", "pipeline:create")).toBe(true);
    expect(can("DEVELOPER", "pipeline:push")).toBe(true);
    expect(can("DEVELOPER", "admin:team")).toBe(false);
  });

  it("denies VIEWER every permission", () => {
    for (const permission of ["pipeline:create", "pipeline:generate", "pipeline:validate", "pipeline:push", "admin:team", "admin:platform"] as const) {
      expect(can("VIEWER", permission)).toBe(false);
    }
  });

  it("covers every role in ROLES", () => {
    expect(ROLES).toEqual(["PLATFORM_ADMIN", "TEAM_ADMIN", "DEVELOPER", "VIEWER"]);
  });
});
