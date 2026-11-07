/**
 * Naming-standard evaluator (AGENTS.md §25.3 "naming" category, §25.4 tokens,
 * §25.6 evaluation points). This is the **reference implementation** —
 * `backend/app/standards.py` and `worker/app/validator/naming.py` are ports
 * that must stay in parity, the same way `pipeline-codegen.ts` and
 * `worker/app/generator/codegen.py` do (see DEVELOPMENT_NOTES.md's generator note).
 *
 * Scope: the enforceable subset of §25.3's naming rule — prefix, suffix,
 * case and max length per artifact type. The freeform `*Pattern` strings on
 * `NamingConvention` remain documentation only; a full token-template ->
 * regex compiler (§25.4) is future work, not required to make prefixes and
 * suffixes real.
 *
 * Nothing here is enforced unless `NamingConvention.enforcement` is set to
 * "warn" or "block" (default "off" — §25.1's "opt-in" contract) *and* a rule
 * exists for the artifact type being checked.
 */
import type { NamingArtifactType, NamingConvention, NamingRule } from "@/types";

export interface NamingViolation {
  level: "warn" | "block";
  message: string;
}

const CASE_PATTERNS: Record<NonNullable<NamingRule["case"]>, RegExp> = {
  snake: /^[a-z][a-z0-9_]*$/,
  pascal: /^[A-Z][A-Za-z0-9]*$/,
  camel: /^[a-z][A-Za-z0-9]*$/,
  upper: /^[A-Z][A-Z0-9_]*$/,
  lower: /^[a-z][a-z0-9]*$/,
  none: /^.*$/,
};

export function ruleFor(convention: NamingConvention | undefined, type: NamingArtifactType): NamingRule | undefined {
  return convention?.rules?.[type];
}

/** Whether a rule actually constrains anything (an empty `{}` config is a no-op). */
export function isConfigured(rule: NamingRule | undefined): boolean {
  return !!rule && !!(rule.prefix || rule.suffix || rule.maxLength || (rule.case && rule.case !== "none"));
}

/** Build an example compliant name by wrapping a base with the rule's prefix/suffix. */
export function buildExpectedName(base: string, rule: NamingRule | undefined): string {
  return `${rule?.prefix ?? ""}${base}${rule?.suffix ?? ""}`;
}

/** Human-readable summary of a rule, for inline hints and the Standards page. */
export function describeRule(rule: NamingRule | undefined): string {
  if (!isConfigured(rule)) return "No naming rule configured for this artifact type.";
  const parts: string[] = [];
  if (rule?.prefix) parts.push(`prefix "${rule.prefix}"`);
  if (rule?.suffix) parts.push(`suffix "${rule.suffix}"`);
  if (rule?.case && rule.case !== "none") parts.push(`${rule.case} case`);
  if (rule?.maxLength) parts.push(`max ${rule.maxLength} characters`);
  return `Requires ${parts.join(", ")}.`;
}

/**
 * Evaluate a candidate name against the project's naming convention for one
 * artifact type. Returns `null` when the name is compliant, no rule is
 * configured for the type, or enforcement is "off" — i.e. "nothing to
 * report", not "definitely valid" (§25.1 default-permissive contract).
 */
export function evaluateName(
  name: string,
  type: NamingArtifactType,
  convention: NamingConvention | undefined,
): NamingViolation | null {
  const enforcement = convention?.enforcement ?? "off";
  if (enforcement === "off") return null;
  const rule = ruleFor(convention, type);
  if (!isConfigured(rule)) return null;

  const trimmed = (name ?? "").trim();
  if (!trimmed) return null; // required-field validation is a separate concern

  const problems: string[] = [];
  const prefix = rule?.prefix ?? "";
  const suffix = rule?.suffix ?? "";
  if (prefix && !trimmed.startsWith(prefix)) problems.push(`must start with "${prefix}"`);
  if (suffix && !trimmed.endsWith(suffix)) problems.push(`must end with "${suffix}"`);
  if (rule?.maxLength && trimmed.length > rule.maxLength) problems.push(`must be at most ${rule.maxLength} characters`);
  if (rule?.case && rule.case !== "none") {
    const bodyEnd = suffix && trimmed.endsWith(suffix) ? trimmed.length - suffix.length : trimmed.length;
    const body = trimmed.slice(prefix.length, Math.max(prefix.length, bodyEnd));
    if (body && !CASE_PATTERNS[rule.case].test(body)) problems.push(`must be ${rule.case} case`);
  }
  if (problems.length === 0) return null;

  return {
    level: enforcement === "block" ? "block" : "warn",
    message: `"${trimmed}" ${problems.join(", ")} — expected something like "${buildExpectedName("name", rule)}".`,
  };
}
