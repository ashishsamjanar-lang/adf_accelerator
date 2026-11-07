"use client";

import { cn } from "@/lib/utils";

interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  hint?: string;
  disabled?: boolean;
  className?: string;
}

/**
 * Toggle switch component for boolean settings.
 * Provides a visual on/off toggle with optional label and hint.
 */
export function Switch({ checked, onChange, label, hint, disabled, className }: SwitchProps) {
  return (
    <label className={cn("flex items-start gap-3 cursor-pointer", disabled && "opacity-50 cursor-not-allowed", className)}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className={cn(
          "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors",
          checked ? "bg-teal" : "bg-line"
        )}
      >
        <span
          className={cn(
            "inline-block h-4 w-4 transform rounded-full bg-white transition-transform shadow-sm",
            checked ? "translate-x-4" : "translate-x-0.5"
          )}
        />
      </button>
      <span className="flex flex-col gap-0.5">
        {label ? <span className="text-xs font-extrabold text-ink">{label}</span> : null}
        {hint ? <small className="text-2xs font-normal text-muted">{hint}</small> : null}
      </span>
    </label>
  );
}
