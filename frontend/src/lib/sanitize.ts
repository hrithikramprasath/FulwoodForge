import DOMPurify, { type Config } from "dompurify";
import { marked } from "marked";
import katex from "katex";

/*
 * The only path from model-written Markdown/LaTeX to the DOM (REQ-SEC-01 /
 * VULN-01). Pipeline: protect code, lift math into placeholders, marked.parse,
 * KaTeX render, DOMPurify. Components must never hand marked output to
 * dangerouslySetInnerHTML directly.
 */

const PURIFY_CONFIG: Config = {
  // svg is included beside html and mathMl because KaTeX draws tall radicals and
  // stretchy arrows as inline <svg>; DOMPurify's svg profile stays script-free.
  USE_PROFILES: { html: true, mathMl: true, svg: true },
  FORBID_TAGS: ["script", "iframe", "object", "embed", "form", "style"],
  FORBID_ATTR: ["onerror", "onload", "onclick", "onmouseover", "onfocus"],
};

let hookInstalled = false;
function ensureLinkHook(): void {
  if (hookInstalled) return;
  hookInstalled = true;
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.hasAttribute("href")) {
      node.setAttribute("rel", "noopener noreferrer");
      node.setAttribute("target", "_blank");
    }
  });
}

// Code spans/fences first so math inside backticks is left alone, then $$..$$, then $..$.
const MATH_PATTERN =
  /(```[\s\S]*?```|`[^`\n]+`)|\$\$([\s\S]+?)\$\$|\$([^\s$](?:[^$\n]*?[^\s$])?)\$/g;

interface MathSegment {
  tex: string;
  display: boolean;
}

function extractMath(source: string): {
  text: string;
  segments: MathSegment[];
} {
  const segments: MathSegment[] = [];
  const text = source.replace(
    MATH_PATTERN,
    (
      match: string,
      code: string | undefined,
      block: string | undefined,
      inline: string | undefined,
    ) => {
      if (code) return match;
      const tex = (block ?? inline ?? "").trim();
      const display = block !== undefined;
      segments.push({ tex, display });
      return display
        ? `\n\nFFMATH${segments.length - 1}END\n\n`
        : `FFMATH${segments.length - 1}END`;
    },
  );
  return { text, segments };
}

function renderMath({ tex, display }: MathSegment): string {
  return katex.renderToString(tex, {
    displayMode: display,
    throwOnError: false,
    strict: "ignore",
    trust: false,
    output: "htmlAndMathml",
  });
}

function renderRawHtml(markdown: string, inline: boolean): string {
  const { text, segments } = extractMath(markdown);
  const rawHtml: string = inline
    ? marked.parseInline(text, { async: false })
    : marked.parse(text, { async: false });
  const withMath = rawHtml.replace(/FFMATH(\d+)END/g, (_m, index: string) => {
    const segment = segments[Number(index)];
    return segment ? renderMath(segment) : "";
  });
  return withMath;
}

/** Block Markdown + KaTeX, sanitised. */
export function renderSafeMarkdown(markdown: string): string {
  ensureLinkHook();
  return DOMPurify.sanitize(renderRawHtml(markdown, false), PURIFY_CONFIG);
}

/** Inline Markdown + KaTeX (no wrapping paragraph) for short fields, sanitised. */
export function renderSafeInlineMarkdown(markdown: string): string {
  ensureLinkHook();
  return DOMPurify.sanitize(renderRawHtml(markdown, true), PURIFY_CONFIG);
}
