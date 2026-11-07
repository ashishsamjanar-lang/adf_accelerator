import type { HTMLAttributes, ReactNode } from "react";
import { AlertCircle, AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { cn } from "@/lib/utils";

type AlertVariant = "info" | "success" | "warning" | "error";

const CONFIG: Record<AlertVariant, { icon: typeof Info; className: string; iconClass: string }> = {
  info: { icon: Info, className: "border-teal-100 bg-teal-50", iconClass: "text-teal" },
  success: { icon: CheckCircle2, className: "border-green/25 bg-green-bg", iconClass: "text-green" },
  warning: { icon: AlertTriangle, className: "border-amber/25 bg-amber-bg", iconClass: "text-amber" },
  error: { icon: AlertCircle, className: "border-red/25 bg-red-bg", iconClass: "text-red" },
};

interface AlertProps extends HTMLAttributes<HTMLDivElement> {
  variant?: AlertVariant;
  title?: string;
  description?: string;
  action?: React.ReactNode;
  icon?: ReactNode;
}

export function Alert({ variant = "info", title, description, className, action, icon, ...props }: AlertProps) {
  const { icon: DefaultIcon, className: boxClass, iconClass } = CONFIG[variant];
  return (
    <div
      role={variant === "error" ? "alert" : variant === "warning" ? "alert" : "status"}
      className={cn("flex items-start gap-3 rounded-md border p-3.5 text-xs", boxClass, className)}
      {...props}
    >
      {icon ? icon : <DefaultIcon className={cn("mt-0.5 h-4 w-4 shrink-0", iconClass)} />}
      <div className="min-w-0 flex-1">
        {title ? <p className="m-0 font-bold text-ink">{title}</p> : null}
        {description ? <p className="m-0 mt-0.5 leading-5 text-muted">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}