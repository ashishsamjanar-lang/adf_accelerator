import { describe, expect, it } from "vitest";
import { profileMapFor } from "./mapping-profiles";

describe("onboarding mapping profiles (§26.3)", () => {
  it("applies only the headers present in the file", () => {
    const profile = { headerMap: { TABNAME: "object_key", SYS: "source_system" } };
    expect(profileMapFor(profile, ["TABNAME", "other"])).toEqual({ TABNAME: "object_key" });
  });
});
