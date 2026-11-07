"use client";

import { useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

interface Tab {
  id: string;
  label: string;
  icon?: ReactNode;
}

interface TabsProps {
  tabs: Tab[];
  defaultTab?: string;
  onChange?: (tabId: string) => void;
  className?: string;
}

/**
 * Tab navigation component for organizing content into sections.
 * Supports icons and active state indication.
 */
export function Tabs({ tabs, defaultTab, onChange, className }: TabsProps) {
  const [activeTab, setActiveTab] = useState(defaultTab ?? tabs[0]?.id);

  const handleTabChange = (tabId: string) => {
    setActiveTab(tabId);
    onChange?.(tabId);
  };

  return (
    <div className={cn("flex gap-1 border-b border-line", className)}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          onClick={() => handleTabChange(tab.id)}
          className={cn(
            "flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold transition-colors",
            activeTab === tab.id
              ? "border-teal text-teal"
              : "border-transparent text-muted hover:text-ink"
          )}
        >
          {tab.icon}
          {tab.label}
        </button>
      ))}
    </div>
  );
}

interface TabPanelProps {
  children: ReactNode;
  className?: string;
}

/**
 * Container for tab content. Use with Tabs component.
 */
export function TabPanel({ children, className }: TabPanelProps) {
  return <div className={cn("py-4", className)}>{children}</div>;
}
