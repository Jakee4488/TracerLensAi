import DOMPurify from "dompurify";
import hljs from "highlight.js";
import { marked } from "marked";

marked.setOptions({ gfm: true, breaks: true });

export function renderMarkdown(text: string): string {
  const processed = text ?? "";
  const raw = marked.parse(processed);
  const html = typeof raw === "string" ? raw : "";
  let finalHtml = DOMPurify.sanitize(html);

  // Transform AI's causal citations into superscript badges AFTER sanitize
  // so DOMPurify doesn't strip the data attributes, and marked doesn't escape them.
  finalHtml = finalHtml.replace(
    /\[Node:\s*([^\]]+)\]/gi,
    (match, label) => {
      const safeLabel = label.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
      return `<sup class="node-citation" data-node="${safeLabel}" title="Causal Node: ${safeLabel}">⚯ ${safeLabel}</sup>`;
    }
  );

  return finalHtml;
}

export function highlightCode(root: HTMLElement | null): void {
  if (!root) return;
  for (const node of root.querySelectorAll("pre code")) {
    hljs.highlightElement(node as HTMLElement);
  }
}

export function fmtNum(value: number | null | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}
