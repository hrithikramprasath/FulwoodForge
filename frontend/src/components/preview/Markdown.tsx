import { useMemo } from "react";
import { renderSafeInlineMarkdown, renderSafeMarkdown } from "@/lib/sanitize";
import { cn } from "@/lib/utils";

/*
 * The only components allowed to place model-written Markdown in the DOM. Both
 * go through lib/sanitize.ts (marked, KaTeX, DOMPurify): REQ-SEC-01 / VULN-01.
 */

/** Block Markdown and KaTeX maths. */
export function Md({
  children,
  className,
}: {
  children: string;
  className?: string;
}) {
  const html = useMemo(() => renderSafeMarkdown(children), [children]);
  return (
    <div
      className={cn("markdown text-[13.5px] leading-relaxed", className)}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

/** Inline variant for short fields (labels, objectives, definitions). */
export function Mdi({
  children,
  className,
}: {
  children: string;
  className?: string;
}) {
  const html = useMemo(() => renderSafeInlineMarkdown(children), [children]);
  return (
    <span className={className} dangerouslySetInnerHTML={{ __html: html }} />
  );
}
