import { useCallback, useEffect, useRef, useState } from "react";
import { ChatHeader } from "./components/ChatHeader";
import { Composer } from "./components/Composer";
import { DropOverlay } from "./components/DropOverlay";
import { MessageList } from "./components/MessageList";
import { Sidebar } from "./components/Sidebar";
import { CausalPanel } from "./components/causal/CausalPanel";
import { useAttachments } from "./hooks/useAttachments";
import { useHistory } from "./hooks/useHistory";
import { useRunProgress } from "./hooks/useRunProgress";
import { analyzePrompt, fetchConversation, setTokenGetter } from "./lib/api";
import { hasCausalContent } from "./components/causal/CausalPanel";
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

const NARROW_VIEWPORT_QUERY = "(max-width: 900px)";
function isNarrowViewport(): boolean {
  return window.matchMedia(NARROW_VIEWPORT_QUERY).matches;
}

setTokenGetter(getIdToken);

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([greetingMessage()]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(isNarrowViewport);
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const [tokenTally, setTokenTally] = useState(0);
  const [model, setModel] = useState("gemini-2.5-flash");
  const [causal, setCausal] = useState(false);
  const [webSearch, setWebSearch] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null);

  const isSendingRef = useRef(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const chatIdRef = useRef<string | null>(null);
  const { attachments, handleFiles, remove, clear } = useAttachments();
  const history = useHistory();
  const run = useRunProgress();

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

  const stop = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
  }, []);

  const send = useCallback(async (overrideText?: string) => {
    const text = (typeof overrideText === "string" ? overrideText : input).trim();
    if (!text || isSendingRef.current) return;

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
    isSendingRef.current = true;
    setSelectedMessageId("live"); // Open the panel for the live run
    
    const abortController = new AbortController();
    abortControllerRef.current = abortController;

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
        { 
          signal: abortController.signal,
          onProgress: run.onProgress, 
          onGraph: run.onGraph 
        },
      );
      if (report.total_token_count) {
        setTokenTally((prev) => prev + report.total_token_count);
      }
      const failed = report.causal_status?.phase === "failed";
      
      const newMsgKey = nextMessageKey("ai");
      append({
        key: newMsgKey,
        role: "ai",
        content: report.response || "No response received.",
        report,
        stages: causal
          ? finalizeStages(run.stagesRef.current, run.elapsedRef.current, failed)
          : undefined,
      });
      setSelectedMessageId(newMsgKey);
      
      clear();
      void reloadHistory();
    } catch (error) {
      if ((error as Error).name !== "AbortError") {
        console.error(error);
        append({
          key: nextMessageKey("error"),
          role: "error",
          content: (error as Error).message,
        });
        setSelectedMessageId(null);
      }
    } finally {
      setIsSending(false);
      isSendingRef.current = false;
      abortControllerRef.current = null;
    }
  }, [input, attachments, causal, webSearch, model, append, clear, reloadHistory, run]);

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
                report: { response: msg.content || "", ...(msg.causal || {}) } as Report,
              },
        ),
      );
      setSelectedMessageId(null);
    } catch (e) {
      console.error("Failed to load conversation:", e);
    }
  }, []);

  const newChat = useCallback(() => {
    chatIdRef.current = null;
    setTokenTally(0);
    clear();
    setMessages([greetingMessage()]);
    setSelectedMessageId(null);
  }, [clear]);

  const selectedMsg = messages.find((m) => m.key === selectedMessageId);
  
  // Pass dummy report struct for "live" state, so CausalPanel can just render the liveGraph/stages
  const liveReport: Report | null = selectedMessageId === "live" ? {} as Report : null;
  const currentReport = selectedMessageId === "live" ? liveReport : selectedMsg?.report;
  
  // Right pane condition
  const showRightPane = (selectedMessageId === "live" && causal) || 
    (selectedMsg && (selectedMsg.stages?.length || (selectedMsg.report && hasCausalContent(selectedMsg.report))));

  return (
    <div className={`app-container ${showRightPane ? "has-right-pane" : ""}`}>
      <Sidebar
        collapsed={sidebarCollapsed}
        signedIn={!!user}
        conversations={history.conversations}
        hasMore={history.hasMore}
        onSelect={openConversation}
        onLoadMore={history.loadMore}
        onNewChat={newChat}
        theme={theme}
        onToggleTheme={toggleTheme}
        model={model}
        onModelChange={setModel}
        causal={causal}
        onCausalChange={setCausal}
        webSearch={webSearch}
        onWebSearchChange={setWebSearch}
      />

      <main className="chat-container">
        <ChatHeader
          onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
          tokenTally={tokenTally}
          user={user}
        />

        <MessageList
          messages={messages}
          isSending={isSending}
          causal={causal}
          stages={run.stages}
          liveGraph={run.graph}
          onSelectMessage={(msg) => setSelectedMessageId(msg.key)}
          onPromptClick={(text) => send(text)}
        />

        <Composer
          value={input}
          onChange={setInput}
          onSend={send}
          onStop={stop}
          isSending={isSending}
          disabled={isSending || (!input.trim() && attachments.length === 0)}
          attachments={attachments}
          onFiles={handleFiles}
          onRemoveAttachment={remove}
        />

        <DropOverlay onFiles={handleFiles} />
      </main>

      {showRightPane && (
        <aside className="causal-pane">
          <button className="close-pane-btn" onClick={() => setSelectedMessageId(null)}>×</button>
          <CausalPanel 
            report={currentReport!} 
            stages={selectedMessageId === "live" ? run.stages : selectedMsg?.stages} 
            liveGraph={selectedMessageId === "live" ? run.graph : undefined}
          />
        </aside>
      )}
    </div>
  );
}
