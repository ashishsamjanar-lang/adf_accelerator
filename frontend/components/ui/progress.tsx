import { cn } from "@/lib/utils";

interface ProgressProps {
  value: number; // 0..100
  tone?: "green" | "blue" | "amber";
  className?: string;
}

export function Progress({ value, tone = "green", className }: ProgressProps) {
  const clamped = Math.max(0, Math.min(100, value));
  const barColor =
    tone === "blue" ? "bg-blue" : tone === "amber" ? "bg-amber" : "bg-green";
  return (
    <div className={cn("h-1.5 overflow-hidden rounded-full bg-surface-3", className)}>
      <div className={cn("h-full rounded-full transition-all", barColor)} style={{ width: `${clamped}%` }} />
    </div>
  );
}