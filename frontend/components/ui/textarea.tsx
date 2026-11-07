import type { TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "min-h-20 w-full rounded-md border border-line bg-surface-2 px-3 py-2 text-sm leading-6 text-ink outline-none transition-colors placeholder:text-faint focus:border-teal focus:ring-2 focus:ring-teal/15 disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}