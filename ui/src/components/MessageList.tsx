import { useEffect, useLayoutEffect, useRef } from "react";
import { highlightCode, renderMarkdown } from "../lib/markdown";
import { CausalPanel, hasCausalContent } from "./causal/CausalPanel";
import { CausalGraph } from "./causal/CausalGraph";
import { WorkflowTimeline } from "./causal/WorkflowTimeline";
import type { Stage } from "../lib/stages";
import type { CausalGraph as CausalGraphType, ChatMessage } from "../types";

function Markdown({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const html = renderMarkdown(text);
  // Highlighting mutates the rendered DOM, so it runs after commit. The HTML is
  // DOMPurify output — the one place model text reaches innerHTML.
  useEffect(() => highlightCode(ref.current), [html]);
  return <div className="md-content" ref={ref} dangerouslySetInnerHTML={{ __html: html }} />;
}

function AiMessage({ message }: { message: ChatMessage }) {
  const report = message.report;
  return (
    <div className="msg ai">
      <div className="avatar" />
      <div className="bubble">
        <Markdown text={message.content || "No response received."} />
        {report && hasCausalContent(report) && (
          <CausalPanel report={report} stages={message.stages} />
        )}
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

/**
 * In-flight bubble.
 *
 * With causal reasoning on there is a nine-stage pipeline to show, so the
 * timeline replaces the dots — and the DAG joins it as soon as the pipeline
 * emits one, mid-run. With the toggle off there is no pipeline, so the
 * original three-dot indicator stands.
 */
function PendingBubble({
  causal,
  stages,
  graph,
}: {
  causal: boolean;
  stages: Stage[];
  graph: CausalGraphType | null;
}) {
  return (
    <div className="msg ai">
      <div className="avatar" />
      <div className="bubble">
        {causal ? (
          <div className="causal-panel">
            <div className="causal-head">⚯ Causal reasoning</div>
            <WorkflowTimeline stages={stages} />
            {graph && graph.nodes?.length > 0 && <CausalGraph graph={graph} />}
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
}

export function MessageList({ messages, isSending, causal, stages, liveGraph }: Props) {
  const areaRef = useRef<HTMLDivElement>(null);

  // Layout effect so the scroll lands before paint, not a frame after it.
  // `stages` is a dependency too: the timeline grows as the run proceeds, and
  // without it the view stops following once the bubble is already on screen.
  useLayoutEffect(() => {
    const area = areaRef.current;
    if (area) area.scrollTop = area.scrollHeight;
  }, [messages, isSending, stages]);

  return (
    <div className="messages" id="messages-area" ref={areaRef}>
      <div className="messages-inner" id="messages-inner">
        {messages.map((message) => {
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
          if (message.role === "greeting") {
            return (
              <div className="msg ai" key={message.key}>
                <div className="avatar" />
                <div className="bubble">
                  <p>{message.content}</p>
                </div>
              </div>
            );
          }
          return <AiMessage key={message.key} message={message} />;
        })}
        {isSending && <PendingBubble causal={causal} stages={stages} graph={liveGraph} />}
      </div>
    </div>
  );
}
