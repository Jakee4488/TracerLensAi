import type { CausalGraph, Conversation, Report } from "../types";
import { getAnonId } from "./ids";
import { readSse } from "./sse";

export interface PromptBody {
  prompt: string;
  causal_reasoning: boolean;
  web_search: boolean;
  model_name: string;
  chat_id: string;
  attachments?: string[];
}

export interface ProgressFrame {
  stage?: string;
  message?: string;
  step?: string;
  elapsed_ms: number;
  index?: number;
  total?: number;
}

interface AnalyzeHandlers {
  signal?: AbortSignal;
  onProgress?: (frame: ProgressFrame) => void;
  onGraph?: (graph: CausalGraph | null) => void;
}

interface HistoryPage {
  conversations: Conversation[];
  next_cursor: string | null;
}

interface ConversationMessage {
  role: "user" | "ai";
  content?: string;
  attachments?: string[];
  causal?: Partial<Report>;
}

export interface ConversationPayload {
  chat_id: string;
  title?: string;
  total_tokens: number;
  messages: ConversationMessage[];
}

interface UploadPayload {
  file_id: string;
}

type TokenGetter = () => Promise<string | null>;

let tokenGetter: TokenGetter = async () => null;

export function setTokenGetter(getter: TokenGetter): void {
  tokenGetter = getter;
}

function apiBase(): string {
  const value = (window as typeof window & { TRACERLENS_API_BASE?: string }).TRACERLENS_API_BASE;
  return value ? value.replace(/\/$/, "") : "";
}

async function authHeaders(contentType?: string): Promise<HeadersInit> {
  const headers: Record<string, string> = { "X-Anon-Id": getAnonId() };
  if (contentType) headers["Content-Type"] = contentType;
  const token = await tokenGetter();
  if (token) headers.Authorization = "Bearer " + token;
  return headers;
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(`${apiBase()}${path}`, init);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) message = payload.detail;
    } catch {
      // Keep status message fallback.
    }
    throw new Error(message);
  }
  return response;
}

export async function analyzePrompt(body: PromptBody, handlers: AnalyzeHandlers = {}): Promise<Report> {
  const response = await request("/analyze-prompt", {
    method: "POST",
    headers: await authHeaders("application/json"),
    body: JSON.stringify(body),
    signal: handlers.signal,
  });

  if (!response.headers.get("content-type")?.includes("text/event-stream")) {
    return (await response.json()) as Report;
  }

  let done: Report | null = null;
  for await (const frame of readSse(response)) {
    if (frame.event === "progress") {
      handlers.onProgress?.(JSON.parse(frame.data) as ProgressFrame);
    } else if (frame.event === "graph") {
      handlers.onGraph?.(JSON.parse(frame.data) as CausalGraph | null);
    } else if (frame.event === "done") {
      done = JSON.parse(frame.data) as Report;
    } else if (frame.event === "error") {
      const payload = JSON.parse(frame.data) as { error?: string; detail?: string };
      throw new Error(payload.error || payload.detail || "Streaming error");
    }
  }

  if (!done) throw new Error("Missing terminal response from server");
  return done;
}

export async function uploadFile(file: File): Promise<UploadPayload> {
  const form = new FormData();
  form.append("file", file);
  const response = await request("/upload", {
    method: "POST",
    headers: await authHeaders(),
    body: form,
  });
  return (await response.json()) as UploadPayload;
}

export async function fetchHistory(cursor?: string): Promise<HistoryPage | null> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  const response = await request(`/history${query}`, { headers: await authHeaders() });
  return (await response.json()) as HistoryPage;
}

export async function fetchConversation(chatId: string): Promise<ConversationPayload | null> {
  const response = await request(`/history/${encodeURIComponent(chatId)}`, {
    headers: await authHeaders(),
  });
  return (await response.json()) as ConversationPayload;
}
