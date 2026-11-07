/**
 * §26.3 Map step — helpers for named, reusable header mapping profiles for the Bronze onboarding
 * upload. Profiles are stored server-side per project (`api.onboarding.mappingProfiles`, §26.7
 * `/mapping-profiles`, since 2026-09-26 — previously browser-local only).
 */
import type { OnboardingHeaderMap } from "@/lib/adf/onboarding";
import type { OnboardingMappingProfile } from "@/types";

/** The part of a profile that applies to this file — only headers the file actually has. */
export function profileMapFor(profile: Pick<OnboardingMappingProfile, "headerMap">, headers: string[]): OnboardingHeaderMap {
  const present = new Set(headers);
  return Object.fromEntries(Object.entries(profile.headerMap).filter(([header]) => present.has(header)));
}
