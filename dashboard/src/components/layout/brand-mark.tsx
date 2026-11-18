import { cn } from "@/lib/utils";

/** Releasi "R" mark, same geometry as releasi.co. Inherits currentColor. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" aria-hidden="true" className={cn("h-6 w-6", className)}>
      <path d="M6 9h16a10 10 0 0 1 0 20H6V9Zm8 7v6h8a3 3 0 0 0 0-6h-8Z" fill="currentColor" fillRule="evenodd" />
      <path d="m22 25 12 12H23L11 25Z" fill="currentColor" />
    </svg>
  );
}

export function Wordmark({ className, markClassName }: { className?: string; markClassName?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 font-bold tracking-[-0.06em]", className)}>
      <BrandMark className={markClassName} />
      <span>Releasi</span>
    </span>
  );
}
