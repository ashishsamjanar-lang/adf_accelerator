import type { ComponentType, CSSProperties } from "react";
import {
  BookOpen,
  Boxes,
  Building2,
  CalendarClock,
  Cable,
  Factory,
  FileEdit,
  FileJson,
  FolderKanban,
  GitBranch,
  History,
  Hammer,
  LayoutDashboard,
  LayoutTemplate,
  Link2,
  ListChecks,
  ScrollText,
  Server,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Table2,
  Users,
  Workflow,
} from "lucide-react";

/**
 * Navigation model — two tier (AGENTS.md §3, §24.19, §26.10).
 *
 *   ┌── primary rail (icon only) : one entry per GROUP
 *   └── context panel            : the items inside the active group
 *
 * No horizontal nav, no Release/Deployment UI. Delivery is the unified
 * Build → Catalog → Onboarding → Drafts → Git surface from §24.
 */
export type ViewId =
  | "home"
  | "teams"
  | "projects"
  // Delivery
  | "pipelines"
  | "pipelines.create"
  | "delivery.catalog"
  | "delivery.onboarding"
  | "delivery.samples"
  | "pipelines.drafts"
  | "pipelines.git"
  | "pipelines.templates"
  | "pipelines.connections"
  // legacy artifact routes — still rendered, reached from inside Catalog
  | "artifacts.adf_pipelines"
  | "artifacts.datasets"
  | "artifacts.linked_services"
  | "artifacts.parameters"
  | "artifacts.triggers"
  // Administration
  | "admin"
  | "admin.project_settings"
  | "admin.standards"
  | "admin.users"
  | "admin.environments"
  | "admin.audit";

type Icon = ComponentType<{ className?: string; style?: CSSProperties }>;

export interface NavItem {
  id: ViewId;
  label: string;
  icon: Icon;
  hint?: string;
  /** Optional sub-heading rendered above this item when it differs from the item before it —
   * lets a long flat list (e.g. Delivery's 7 items) read as workflow stages instead of an
   * arbitrary stack. Groups with no `section` on any item render exactly as before. */
  section?: string;
}

export interface NavGroup {
  /** stable id, also the rail button's key */
  id: string;
  label: string;
  icon: Icon;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    id: "overview",
    label: "Overview",
    icon: LayoutDashboard,
    items: [{ id: "home", label: "Overview", icon: LayoutDashboard, hint: "Workspace summary" }],
  },
  {
    id: "workspace",
    label: "Workspace",
    icon: Building2,
    items: [
      { id: "teams", label: "Teams", icon: Users, hint: "People and membership" },
      { id: "projects", label: "Projects", icon: FolderKanban, hint: "Delivery workspaces" },
    ],
  },
  {
    id: "delivery",
    label: "Delivery",
    icon: Workflow,
    items: [
      { id: "pipelines.create", label: "Build", icon: Hammer, hint: "Author pipelines & artifacts", section: "Create & ship" },
      { id: "pipelines.drafts", label: "Drafts", icon: FileEdit, hint: "Work in progress", section: "Create & ship" },
      { id: "pipelines.git", label: "Git", icon: GitBranch, hint: "Commits & pull requests", section: "Create & ship" },
      { id: "delivery.onboarding", label: "Onboarding", icon: ListChecks, hint: "Source object intake", section: "Set up" },
      { id: "delivery.catalog", label: "Catalog", icon: Boxes, hint: "Everything in the repo", section: "Set up" },
      { id: "pipelines.connections", label: "Connections", icon: Cable, hint: "Saved linked services & datasets", section: "Set up" },
      { id: "delivery.samples", label: "Sample Guides", icon: BookOpen, hint: "Enterprise reference implementations", section: "Reference" },
    ],
  },
  {
    id: "admin",
    label: "Administration",
    icon: ShieldCheck,
    items: [
      { id: "admin.project_settings", label: "Project Settings", icon: Settings, hint: "Reusable defaults" },
      { id: "admin.standards", label: "Standards", icon: ScrollText, hint: "Naming & governance policy" },
      { id: "pipelines.templates", label: "Patterns", icon: LayoutTemplate, hint: "Pattern & template reference" },
      { id: "admin.users", label: "Users", icon: ShieldCheck, hint: "Roles & access" },
      { id: "admin.environments", label: "Environments", icon: Server, hint: "ADF resource targets" },
      { id: "admin.audit", label: "Audit log", icon: History, hint: "Append-only activity trail" },
    ],
  },
];

const ALL_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

/**
 * Icons for artifact types shown inside the Catalog and on the home dashboard —
 * kept here so every surface uses the same glyph for the same concept.
 */
export const ARTIFACT_ICONS = {
  pipeline: FileJson,
  dataset: Table2,
  linked_service: Link2,
  parameter: SlidersHorizontal,
  trigger: CalendarClock,
  connection: Cable,
  factory: Factory,
  // §32.5 — a Microsoft.DataFactory/factories/credentials resource (user-assigned managed
  // identity). Worker/backend-only today, no Build wizard UI generates one yet (§32.9 MI3).
  credential: ShieldCheck,
} as const;

/**
 * One icon per destination — the single source of truth so shortcuts, cards and
 * any other entry point stay visually consistent with the rail.
 */
export const NAV_ICONS: Partial<Record<ViewId, Icon>> = Object.assign(
  {},
  ...ALL_ITEMS.map((item) => ({ [item.id]: item.icon })),
);

export const VIEW_TITLES: Record<ViewId, string> = Object.assign(
  {} as Record<ViewId, string>,
  ...ALL_ITEMS.map((item) => ({ [item.id]: item.label })),
  {
    admin: "Administration",
    pipelines: "Pipelines",
    "pipelines.connections": "Connections",
    "artifacts.adf_pipelines": "ADF Pipelines",
    "artifacts.datasets": "Datasets",
    "artifacts.linked_services": "Linked Services",
    "artifacts.parameters": "Parameters",
    "artifacts.triggers": "Triggers",
  },
);

/** The group a view belongs to (for the rail's active state + breadcrumb). */
export function groupOf(view: ViewId): NavGroup | undefined {
  const direct = NAV_GROUPS.find((group) => group.items.some((item) => item.id === view));
  if (direct) return direct;
  // legacy / programmatic-only routes map onto a sensible group
  if (view === "pipelines" || view === "pipelines.connections" || view.startsWith("artifacts.")) {
    return NAV_GROUPS.find((group) => group.id === "delivery");
  }
  if (view === "admin" || view.startsWith("admin.")) {
    return NAV_GROUPS.find((group) => group.id === "admin");
  }
  return undefined;
}

export function sectionOf(view: ViewId): string | undefined {
  return groupOf(view)?.label;
}

export function parentOf(id: ViewId): ViewId | undefined {
  const parts = id.split(".");
  return parts.length > 1 ? (parts[0] as ViewId) : undefined;
}
