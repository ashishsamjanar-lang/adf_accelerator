"use client";

import type { InputHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "onChange"> {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  hint?: string;
  disabled?: boolean;
}

/**
 * Reusable checkbox component with label and hint support.
 * Uses the form-check design system from globals.css.
 */
export function Checkbox({ checked, onChange, label, hint, disabled, className, ...props }: CheckboxProps) {
  return (
    <label className={cn("form-check", className)}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        {...props}
      />
      <span className="form-check-body">
        <span className="form-check-label">{label}</span>
        {hint ? <small>{hint}</small> : null}
      </span>
    </label>
  );
}
