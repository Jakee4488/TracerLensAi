import type { Conversation } from "../types";
import type { Theme } from "../lib/theme";

interface Props {
  collapsed: boolean;
  signedIn: boolean;
  conversations: Conversation[];
  hasMore: boolean;
  onSelect: (chatId: string) => void;
  onLoadMore: () => void;
  onNewChat: () => void;
  // Settings
  theme: Theme;
  onToggleTheme: () => void;
  model: string;
  onModelChange: (value: string) => void;
  causal: boolean;
  onCausalChange: (value: boolean) => void;
  webSearch: boolean;
  onWebSearchChange: (value: boolean) => void;
}

export function Sidebar({
  collapsed,
  signedIn,
  conversations,
  hasMore,
  onSelect,
  onLoadMore,
  onNewChat,
  theme,
  onToggleTheme,
  model,
  onModelChange,
  causal,
  onCausalChange,
  webSearch,
  onWebSearchChange,
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

      <div className="sidebar-settings">
        <div className="history-title">Settings</div>
        <div className="settings-panel">
          <label className="sidebar-setting">
            <span className="sw-label">Model</span>
            <select
              className="model-select sidebar-select"
              id="model-select"
              value={model}
              onChange={(e) => onModelChange(e.target.value)}
            >
              <option value="gemini-2.5-flash">gemini-2.5-flash</option>
              <option value="gemini-2.5-pro">gemini-2.5-pro</option>
            </select>
          </label>

          <label className="switch-group sidebar-switch">
            <span className="sw-label">Causal</span>
            <span className="switch">
              <input
                type="checkbox"
                id="causal-toggle"
                checked={causal}
                onChange={(e) => onCausalChange(e.target.checked)}
              />
              <span className="track" />
            </span>
          </label>

          <label className="switch-group sidebar-switch" title="Add observation data from the web">
            <span className="sw-label">Web data</span>
            <span className="switch">
              <input
                type="checkbox"
                id="web-search-toggle"
                checked={webSearch}
                onChange={(e) => onWebSearchChange(e.target.checked)}
              />
              <span className="track" />
            </span>
          </label>
          
          <label className="switch-group sidebar-switch" title="Toggle dark/light theme">
            <span className="sw-label">Theme</span>
            <button className="icon-btn theme-btn" id="theme-toggle" onClick={onToggleTheme}>
              {theme === "light" ? "☀" : "☾"}
            </button>
          </label>
        </div>
      </div>
    </aside>
  );
}
