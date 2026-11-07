import type { AcceleratorData } from "@/lib/api/data";
import type { User } from "@/types";
import type { ViewId } from "@/components/shell/navigation";

/** Shared props for all feature views. */
export interface FeatureProps {
  data: AcceleratorData;
  user: User;
  /**
   * `params.editPipelineId`, when present, is consumed only by the `pipelines.create` (Build) route
   * — reopens that pipeline in the wizard's edit mode (append objects / rename master+child) instead
   * of a blank create-mode draft. `params.catalogQuery`, when present, is consumed only by the
   * `delivery.catalog` route — seeds its search box so every artifact type for one pipeline (pipeline,
   * dataset, linked service, trigger, parameter/control-table SQL) lands in view together, instead of
   * the type-scoped `artifacts.*` routes which only ever show one `ArtifactType` at a time (e.g. just
   * the 2 `pipeline`-type `PL_*_master.json`/`PL_*_child.json` rows — see Drafts' "Review artifacts").
   * Every existing `onNavigate("someView")` call stays valid; the optional second argument only
   * matters to callers that need it (Pipelines' "Edit", Drafts' "Continue setup"/"Review artifacts",
   * Build's own "add to existing pipeline" picker). `params.onboardingPrefill` (Onboarding's per-row
   * "Build pipeline") opens a create-mode Build on that project with those objects preselected.
   */
  onNavigate: (view: ViewId, params?: NavParams) => void;
}

export interface NavParams {
  editPipelineId?: string;
  catalogQuery?: string;
  onboardingPrefill?: OnboardingPrefill;
}

/** §26.5 "generate pipeline" — Bronze onboarding objects to preselect in a new Build. */
export interface OnboardingPrefill {
  projectId: string;
  objectKeys: string[];
}