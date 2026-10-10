import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Joins class names; of two Tailwind classes for one property the later one wins. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
