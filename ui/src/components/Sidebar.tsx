import type { Conversation } from "../types";

interface Props {
  collapsed: boolean;
  signedIn: boolean;
  conversations: Conversation[];
  hasMore: boolean;
  onSelect: (chatId: string) => void;
  onLoadMore: () => void;
  onNewChat: () => void;
}

export function Sidebar({
  collapsed,
  signedIn,
  conversations,
  hasMore,
  onSelect,
  onLoadMore,
  onNewChat,
}: Props) {
  return (
    <aside className={collapsed ? "sidebar collapsed" : "sidebar"} id="sidebar">
      <div className="brand">
        <span className="logo-mark" aria-hidden="true">
          <svg width="30" height="30" viewBox="0 0 30 30" fill="none">
            <defs>
              <linearGradient id="brand-grad" x1="0" y1="0" x2="30" y2="30">
                <stop offset="0" stopColor="#22d3ee" />
                <stop offset="1" stopColor="#8b5cf6" />
              </linearGradient>
            </defs>
            <circle cx="15" cy="15" r="11" stroke="url(#brand-grad)" strokeWidth="2.4" />
            <circle cx="15" cy="15" r="4" fill="url(#brand-grad)" />
            <path
              d="M15 1.5v6M15 22.5v6M1.5 15h6M22.5 15h6"
              stroke="url(#brand-grad)"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
        </span>
        <span className="brand-name">
          TracerLens<span className="brand-ai">Ai</span>
        </span>
      </div>
      <div className="tracer-line" aria-hidden="true" />

      <button className="new-chat-btn" id="new-chat-btn" onClick={onNewChat}>
        <span className="plus">+</span> New chat
      </button>

      <nav className="history" aria-label="Recent workflows">
        <div className="history-title">Recent workflows</div>
        <div id="history-items">
          {!signedIn ? (
            <div className="history-item hint" id="history-empty-hint">
              Sign in to see your saved workflows
            </div>
          ) : conversations.length === 0 ? (
            <div className="history-item hint">No saved workflows yet</div>
          ) : (
            <>
              {conversations.map((conv) => (
                <div
                  className="history-item"
                  key={conv.chat_id}
                  title={conv.title}
                  onClick={() => onSelect(conv.chat_id)}
                >
                  {conv.title}
                </div>
              ))}
              {hasMore && (
                <div className="history-item hint history-load-more" onClick={onLoadMore}>
                  Load older
                </div>
              )}
            </>
          )}
        </div>
      </nav>

      <div className="sidebar-foot">
        <span>TracerLensAi</span>
        <span>causal agent</span>
      </div>
    </aside>
  );
}
