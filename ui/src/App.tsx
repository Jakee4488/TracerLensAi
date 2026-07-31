import { useCallback, useEffect, useRef, useState } from "react";
import { ChatHeader } from "./components/ChatHeader";
import { Composer } from "./components/Composer";
import { DropOverlay } from "./components/DropOverlay";
import { MessageList } from "./components/MessageList";
import { Sidebar } from "./components/Sidebar";
import { useAttachments } from "./hooks/useAttachments";
import { useHistory } from "./hooks/useHistory";
import { useRunProgress } from "./hooks/useRunProgress";
import { analyzePrompt, fetchConversation, setTokenGetter } from "./lib/api";
import { finalizeStages } from "./lib/stages";
import { getIdToken, watchAuth, type User } from "./lib/firebase";
import { generateSessionId, nextMessageKey } from "./lib/ids";
import { applyTheme, currentTheme, type Theme } from "./lib/theme";
import type { ChatMessage, Report } from "./types";

const GREETING =
  "Hi — I'm the TracerLensAi Causal Agent. Ask a question or attach data, " +
  "and I'll trace the cause-and-effect graph behind it.";

function greetingMessage(): ChatMessage {
  return { key: nextMessageKey("greeting"), role: "greeting", content: GREETING };
}

setTokenGetter(getIdToken);

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([greetingMessage()]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const [tokenTally, setTokenTally] = useState(0);
  const [model, setModel] = useState("gemini-2.5-flash");
  const [causal, setCausal] = useState(false);
  const [webSearch, setWebSearch] = useState(false);
  const [user, setUser] = useState<User | null>(null);

  const chatIdRef = useRef<string | null>(null);
  const { attachments, handleFiles, remove, clear } = useAttachments();
  const history = useHistory();
  const run = useRunProgress();

  // History is per-user, so it reloads on sign-in and empties on sign-out.
  const { reload: reloadHistory, reset: resetHistory } = history;
  useEffect(
    () =>
      watchAuth((nextUser) => {
        setUser(nextUser);
        if (nextUser) void reloadHistory();
        else resetHistory();
      }),
    [reloadHistory, resetHistory],
  );

  const toggleTheme = useCallback(() => {
    setTheme((prev) => {
      const next: Theme = prev === "light" ? "dark" : "light";
      applyTheme(next);
      return next;
    });
  }, []);

  const append = useCallback((message: ChatMessage) => {
    setMessages((prev) => [...prev, message]);
  }, []);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || isSending) return;

    setInput("");
    if (!chatIdRef.current) chatIdRef.current = generateSessionId();

    const ready = attachments.filter((a) => a.status === "done");
    append({
      key: nextMessageKey("user"),
      role: "user",
      content: text,
      attachments: ready.map((a) => a.name),
    });

    run.reset();
    setIsSending(true);
    try {
      const report = await analyzePrompt(
        {
          prompt: text,
          causal_reasoning: causal,
          web_search: webSearch,
          model_name: model,
          chat_id: chatIdRef.current,
          attachments: ready.map((a) => a.id as string),
        },
        { onProgress: run.onProgress, onGraph: run.onGraph },
      );
      if (report.total_token_count) {
        setTokenTally((prev) => prev + report.total_token_count);
      }
      const failed = report.causal_status?.phase === "failed";
      append({
        key: nextMessageKey("ai"),
        role: "ai",
        content: report.response || "No response received.",
        report,
        // Read from the ref, not state: the last progress frame's setState has
        // not committed yet at this point in the same tick.
        stages: causal
          ? finalizeStages(run.stagesRef.current, run.elapsedRef.current, failed)
          : undefined,
      });
      clear();
      void reloadHistory();
    } catch (error) {
      console.error(error);
      append({
        key: nextMessageKey("error"),
        role: "error",
        content: (error as Error).message,
      });
    } finally {
      setIsSending(false);
    }
  }, [input, isSending, attachments, causal, webSearch, model, append, clear, reloadHistory, run]);

  const openConversation = useCallback(async (chatId: string) => {
    try {
      const conv = await fetchConversation(chatId);
      if (!conv) return;
      chatIdRef.current = conv.chat_id;
      setTokenTally(conv.total_tokens || 0);
      setMessages(
        (conv.messages || []).map((msg) =>
          msg.role === "user"
            ? {
                key: nextMessageKey("user"),
                role: "user" as const,
                content: msg.content || "",
                attachments: msg.attachments || [],
              }
            : {
                key: nextMessageKey("ai"),
                role: "ai" as const,
                content: msg.content || "",
                // Persisted causal_* fields use the same key names as a live
                // response, so spreading them replays the diagram and estimand
                // card without touching the render path.
                report: { response: msg.content || "", ...(msg.causal || {}) } as Report,
              },
        ),
      );
    } catch (e) {
      console.error("Failed to load conversation:", e);
    }
  }, []);

  const newChat = useCallback(() => {
    chatIdRef.current = null;
    setTokenTally(0);
    clear();
    setMessages([greetingMessage()]);
  }, [clear]);

  return (
    <div className="app-container">
      <Sidebar
        collapsed={sidebarCollapsed}
        signedIn={!!user}
        conversations={history.conversations}
        hasMore={history.hasMore}
        onSelect={openConversation}
        onLoadMore={history.loadMore}
        onNewChat={newChat}
      />

      <main className="chat-container">
        <ChatHeader
          theme={theme}
          onToggleTheme={toggleTheme}
          onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
          tokenTally={tokenTally}
          model={model}
          onModelChange={setModel}
          causal={causal}
          onCausalChange={setCausal}
          webSearch={webSearch}
          onWebSearchChange={setWebSearch}
          user={user}
        />

        <MessageList
          messages={messages}
          isSending={isSending}
          causal={causal}
          stages={run.stages}
          liveGraph={run.graph}
        />

        <Composer
          value={input}
          onChange={setInput}
          onSend={send}
          disabled={isSending}
          attachments={attachments}
          onFiles={handleFiles}
          onRemoveAttachment={remove}
        />

        <DropOverlay onFiles={handleFiles} />
      </main>
    </div>
  );
}
