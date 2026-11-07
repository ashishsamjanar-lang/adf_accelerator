import type { SelectHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "flex h-9 w-full appearance-none rounded-md border border-line bg-surface-2 px-3 pr-8 text-sm text-ink outline-none transition-colors focus:border-teal focus:ring-2 focus:ring-teal/15 disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
}