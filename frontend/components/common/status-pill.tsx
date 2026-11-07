import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/format";

interface StatusPillProps {
  tone: Tone;
  children: ReactNode;
  className?: string;
}

/** Small status pill aligned to the existing design system. */
export function StatusPill({ tone, children, className }: StatusPillProps) {
  return (
    <span className={cn("status-pill", tone, className)}>
      <i />
      {children}
    </span>
  );
}