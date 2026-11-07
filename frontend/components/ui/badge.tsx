import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-2xs font-extrabold leading-4",
  {
    variants: {
      variant: {
        default: "bg-muted/20 text-muted",
        green: "bg-green-bg text-green",
        blue: "bg-blue-bg text-blue",
        amber: "bg-amber-bg text-amber",
        red: "bg-red-bg text-red",
        teal: "bg-mint text-teal-dark",
        forge: "bg-forge-bg text-teal-dark",
        purple: "bg-purple-bg text-purple",
        gray: "bg-muted/20 text-muted",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { badgeVariants };