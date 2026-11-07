"use client";

import { Fragment, useEffect, useState } from "react";
import { ChevronsUpDown, HelpCircle, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { ROLE_LABELS } from "@/lib/state";
import type { DevIdentity } from "@/lib/api/hooks";
import type { User } from "@/types";
import { NAV_GROUPS, groupOf, type ViewId } from "./navigation";
import { Select } from "@/components/ui/select";
import { UserGuideDialog } from "./user-guide-dialog";

interface SidebarProps {
  activeView: ViewId;
  onNavigate: (view: ViewId) => void;
  user: User;
  workspaceName: string;
  /** Real per-request dev identity switch (mock auth only) — distinct from the client-only role preview in the topbar. */
  devIdentity?: DevIdentity;
  mobileOpen?: boolean;
}

export function Sidebar({
  activeView,
  onNavigate,
  user,
  workspaceName,
  devIdentity,
  mobileOpen,
}: SidebarProps) {
  const activeGroupId = groupOf(activeView)?.id ?? NAV_GROUPS[0].id;
  const [openGroupId, setOpenGroupId] = useState(activeGroupId);
  const [guideOpen, setGuideOpen] = useState(false);

  // Follow navigation, but let the user peek another group without navigating.
  useEffect(() => {
    setOpenGroupId(activeGroupId);
  }, [activeGroupId]);

  const openGroup = NAV_GROUPS.find((group) => group.id === openGroupId) ?? NAV_GROUPS[0];

  return (
    <>
      <nav className="rail" aria-label="Primary">
        <button
          className="rail-brand"
          onClick={() => onNavigate("home")}
          aria-label="ADF Forge — Overview"
        >
          <span className="brand-mark">AF</span>
        </button>

        <div className="rail-groups">
          {NAV_GROUPS.map((group) => {
            const Icon = group.icon;
            const isActive = group.id === activeGroupId;
            const isOpen = group.id === openGroupId;
            return (
              <button
                key={group.id}
                className={cn("rail-item", isActive && "active", isOpen && !isActive && "peek")}
                onClick={() => {
                  setOpenGroupId(group.id);
                  if (group.items.length === 1) onNavigate(group.items[0].id);
                }}
                aria-current={isActive ? "true" : undefined}
                title={group.label}
              >
                <Icon className="h-[19px] w-[19px]" />
                <span className="rail-tip">{group.label}</span>
              </button>
            );
          })}
        </div>

        <div className="rail-foot">
          <span className="rail-avatar" title={`${user.name} · ${ROLE_LABELS[user.role]}`}>
            {initials(user.name)}
          </span>
        </div>
      </nav>

      <aside className={cn("context-panel", mobileOpen && "open")} aria-label={openGroup.label}>
        <div className="context-head">
          <span className="brand-word">
            ADF<span>Forge</span>
          </span>
        </div>

        <div className="workspace-switcher">
          <span className="eyebrow">Workspace</span>
          <button className="workspace-button" type="button">
            <span className="workspace-dot" />
            <span className="truncate">{workspaceName}</span>
            <ChevronsUpDown className="chevron h-3.5 w-3.5" />
          </button>
        </div>

        <div className="sidebar-cta-wrap">
          <button type="button" className="sidebar-cta" onClick={() => onNavigate("pipelines.create")}>
            <Plus className="h-4 w-4" />
            New pipeline
          </button>
        </div>

        <div className="context-scroll">
          <span className="context-label">{openGroup.label}</span>
          <div className="context-nav">
            {openGroup.items.map((item, index) => {
              const Icon = item.icon;
              const active = activeView === item.id;
              const showSectionLabel = !!item.section && item.section !== openGroup.items[index - 1]?.section;
              return (
                <Fragment key={item.id}>
                  {showSectionLabel ? <span className="nav-section-label">{item.section}</span> : null}
                  <button
                    className={cn("nav-item", active && "active")}
                    onClick={() => onNavigate(item.id)}
                    aria-current={active ? "page" : undefined}
                  >
                    <span className="nav-icon">
                      <Icon className="h-[18px] w-[18px]" />
                    </span>
                    <span className="nav-item-text">
                      {item.label}
                      {item.hint ? <small>{item.hint}</small> : null}
                    </span>
                  </button>
                </Fragment>
              );
            })}
          </div>
        </div>

        <div className="context-foot">
          {devIdentity?.available ? (
            <div className="role-preview">
              <span className="eyebrow">Acting as (mock auth)</span>
              <Select
                value={devIdentity.currentUserId ?? ""}
                disabled={devIdentity.switching}
                onChange={(event) => {
                  const id = event.target.value;
                  if (id) void devIdentity.switchTo(id);
                }}
                aria-label="Acting as (dev user switcher)"
                className={cn("h-8 text-xs", devIdentity.currentUserId && "border-teal")}
              >
                <option value="" disabled>
                  {devIdentity.switching ? "Switching…" : "Select a user…"}
                </option>
                {devIdentity.users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name} — {ROLE_LABELS[u.role]}
                  </option>
                ))}
              </Select>
              {devIdentity.error ? <small className="text-red">{devIdentity.error}</small> : null}
            </div>
          ) : null}
          <button type="button" className="nav-item" onClick={() => setGuideOpen(true)}>
            <span className="nav-icon nav-icon-guide">
              <HelpCircle className="h-[18px] w-[18px]" />
            </span>
            <span className="nav-item-text">
              User guide
              <small>What every screen does</small>
            </span>
          </button>
        </div>
      </aside>

      <UserGuideDialog open={guideOpen} onClose={() => setGuideOpen(false)} />
    </>
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
