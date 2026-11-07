"use client";

import { LogOut, Menu } from "lucide-react";
import type { User } from "@/types";
import { ROLES, ROLE_LABELS } from "@/lib/state";
import { cn } from "@/lib/utils";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { VIEW_TITLES, sectionOf, type ViewId } from "./navigation";

interface TopbarProps {
  view: ViewId;
  workspaceName: string;
  user: User;
  onNavigate: (view: ViewId) => void;
  onToggleNav?: () => void;
  onRolePreview?: (role: User["role"]) => void;
  rolePreview?: boolean;
  /** Only set under AUTH_MODE=password (§27.2 extension) — absent under
   * mock/oidc/saml, where there's no real session to end. */
  onLogout?: () => Promise<void>;
}

export function Topbar({
  view,
  workspaceName,
  user,
  onToggleNav,
  onRolePreview,
  rolePreview,
  onLogout,
}: TopbarProps) {
  const title = VIEW_TITLES[view];
  const rawSection = sectionOf(view);
  const section = rawSection === title ? undefined : rawSection;

  return (
    <header className="topbar">
      {onToggleNav ? (
        <button className="topbar-menu" onClick={onToggleNav} aria-label="Toggle navigation">
          <Menu className="h-4 w-4" />
        </button>
      ) : null}

      <div className="breadcrumbs">
        <span>{workspaceName}</span>
        {section ? (
          <>
            <b>/</b>
            <span>{section}</span>
          </>
        ) : null}
        <b>/</b>
        <strong>{title}</strong>
      </div>

      <div className="ml-auto flex items-center gap-3">
        {onRolePreview ? (
          <Select
            value={user.role}
            onChange={(event) => onRolePreview(event.target.value as User["role"])}
            aria-label="Preview role"
            title="Preview role (RBAC)"
            className={cn("h-8 w-auto text-xs", rolePreview && "border-teal")}
          >
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {ROLE_LABELS[role]}
              </option>
            ))}
          </Select>
        ) : null}
        <span className="top-avatar">{initials(user.name)}</span>
        <span className="hidden flex-col leading-tight sm:flex">
          <span className="text-sm font-semibold text-ink">{user.name}</span>
          <span className="text-2xs font-medium text-faint">{ROLE_LABELS[user.role]}</span>
        </span>
        {onLogout ? (
          <Button variant="ghost" size="icon" onClick={() => void onLogout()} aria-label="Log out" title="Log out">
            <LogOut className="h-3.5 w-3.5" />
          </Button>
        ) : null}
      </div>
    </header>
  );
}

function initials(name: string): string {
  return name
    .split(" ")
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}
