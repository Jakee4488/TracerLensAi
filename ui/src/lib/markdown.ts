import DOMPurify from "dompurify";
import hljs from "highlight.js";
import { marked } from "marked";

marked.setOptions({ gfm: true, breaks: true });

export function renderMarkdown(text: string): string {
  const raw = marked.parse(text ?? "");
  const html = typeof raw === "string" ? raw : "";
  return DOMPurify.sanitize(html);
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
