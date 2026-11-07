import { cn } from "@/lib/utils";

interface SeparatorProps {
  className?: string;
  orientation?: "horizontal" | "vertical";
}

/**
 * Visual separator component for dividing content sections.
 */
export function Separator({ className, orientation = "horizontal" }: SeparatorProps) {
  return (
    <div
      className={cn(
        "bg-line",
        orientation === "horizontal" ? "h-px w-full" : "h-full w-px",
        className
      )}
    />
  );
}
