import type { ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./button";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  eyebrow?: string;
  children: ReactNode;
  wide?: boolean;
}

/**
 * Lightweight shadcn-style dialog (no Radix dependency in this scaffold).
 * Rendered inside the app shell with a fixed overlay; closes on backdrop click or Esc.
 */
export function Dialog({ open, onClose, title, description, eyebrow, children, wide }: DialogProps) {
  if (!open) return null;
  return (
    <div
      className={cn("fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-[#1e1b3bcc] p-5")}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className={cn(
          "max-h-[92vh] w-full overflow-y-auto rounded-lg bg-white p-7 shadow-[0_25px_75px_#1e1b3b40] animate-fade-in",
          wide ? "max-w-4xl" : "max-w-[570px]",
        )}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="mb-4 flex items-start justify-between gap-5">
          <div>
            {eyebrow ? <span className="text-2xs font-extrabold uppercase tracking-[1.25px] text-teal">{eyebrow}</span> : null}
            <h2 className="m-0 mt-1.5 text-xl font-bold tracking-tight text-ink">{title}</h2>
            {description ? <p className="mb-0 mt-1 text-xs text-muted">{description}</p> : null}
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close dialog" className="shrink-0 rounded-full">
            <X className="h-4 w-4" />
          </Button>
        </div>
        {children}
      </section>
    </div>
  );
}