import { useEffect, useLayoutEffect, useRef } from "react";
import { highlightCode, renderMarkdown } from "../lib/markdown";
import { hasCausalContent } from "./causal/CausalPanel";
import type { Stage } from "../lib/stages";
import type { CausalGraph as CausalGraphType, ChatMessage } from "../types";

function Markdown({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const html = renderMarkdown(text);
  useEffect(() => highlightCode(ref.current), [html]);
  
  const handleClick = (e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    if (target.classList.contains("node-citation")) {
      const nodeLabel = target.getAttribute("data-node");
      if (nodeLabel) {
        window.dispatchEvent(new CustomEvent("highlight-node", { detail: { id: nodeLabel } }));
      }
    }
  };
  
  return <div className="md-content" ref={ref} onClick={handleClick} dangerouslySetInnerHTML={{ __html: html }} />;
}

function AiMessage({ message, onSelect }: { message: ChatMessage; onSelect?: (msg: ChatMessage) => void }) {
  const report = message.report;
  const hasCausal = report && hasCausalContent(report);
  
  return (
    <div className="msg ai">
      <div className="avatar" />
      <div className="bubble">
        {hasCausal && (
          <div className="causal-summary-box">
            {onSelect && (
              <button className="view-details-btn" onClick={() => onSelect(message)}>
                ⚯ View Causal Details ➔
              </button>
            )}
          </div>
        )}
        <Markdown text={message.content || "No response received."} />
      </div>
    </div>
  );
}

function UserMessage({ message }: { message: ChatMessage }) {
  return (
    <div className="msg user">
      <div className="bubble">
        <p>{message.content}</p>
        {(message.attachments || []).map((name, i) => (
          <span className="bubble-attachment" key={`${name}-${i}`}>
            {"⎘ " + name}
          </span>
        ))}
      </div>
    </div>
  );
}

function PendingBubble({
  causal,
  stages,
}: {
  causal: boolean;
  stages: Stage[];
}) {
  return (
    <div className="msg ai">
      <div className="avatar" />
      <div className="bubble">
        {causal ? (
          <div className="causal-summary-box live-pulse">
            <div className="causal-summary-header">
              <span className="causal-spinner">⚯</span> Causal reasoning:{" "}
              <span className="active-step-label" key={stages.find(s => s.status === "active")?.id || "thinking"}>
                {stages.find(s => s.status === "active")?.label || "Thinking..."}
              </span>
            </div>
          </div>
        ) : (
          <span className="typing" aria-label="Agent is thinking">
            <i />
            <i />
            <i />
          </span>
        )}
      </div>
    </div>
  );
}

interface Props {
  messages: ChatMessage[];
  isSending: boolean;
  causal: boolean;
  stages: Stage[];
  liveGraph: CausalGraphType | null;
  onSelectMessage?: (message: ChatMessage) => void;
  onPromptClick?: (text: string) => void;
}

export function MessageList({ messages, isSending, causal, stages, liveGraph: _liveGraph, onSelectMessage, onPromptClick }: Props) {
  const areaRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const area = areaRef.current;
    if (area) area.scrollTop = area.scrollHeight;
  }, [messages, isSending, stages]);

  return (
    <div className="messages" id="messages-area" ref={areaRef}>
      <div className="messages-inner" id="messages-inner">
        {messages.length === 1 && messages[0].role === "greeting" && (
          <div className="empty-state">
            <div className="empty-state-greeting">
              <div className="avatar" />
              <p>{messages[0].content}</p>
            </div>
            <div className="prompt-cards">
              <button onClick={() => {
                if(onPromptClick) onPromptClick("Why does ice float?");
              }}>🧊 Why does ice float?</button>
              <button onClick={() => {
                if(onPromptClick) onPromptClick("Explain how water boils");
              }}>🔥 Explain how water boils</button>
            </div>
          </div>
        )}
        
        {messages.map((message) => {
          if (message.role === "greeting") return null; // handled in empty state
          if (message.role === "user") return <UserMessage key={message.key} message={message} />;
          if (message.role === "error") {
            return (
              <div className="msg ai" key={message.key}>
                <div className="avatar" />
                <div className="bubble error">
                  <p>{"Error: " + message.content}</p>
                </div>
              </div>
            );
          }
          return <AiMessage key={message.key} message={message} onSelect={onSelectMessage} />;
        })}
        {isSending && <PendingBubble causal={causal} stages={stages} />}
      </div>
    </div>
  );
}
