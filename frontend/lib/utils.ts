import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui-style className merge helper. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}