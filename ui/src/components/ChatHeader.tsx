import { signIn, signOutUser, type User } from "../lib/firebase";
import type { Theme } from "../lib/theme";

interface Props {
  theme: Theme;
  onToggleTheme: () => void;
  onToggleSidebar: () => void;
  tokenTally: number;
  model: string;
  onModelChange: (value: string) => void;
  causal: boolean;
  onCausalChange: (value: boolean) => void;
  webSearch: boolean;
  onWebSearchChange: (value: boolean) => void;
  user: User | null;
}

export function ChatHeader({
  theme,
  onToggleTheme,
  onToggleSidebar,
  tokenTally,
  model,
  onModelChange,
  causal,
  onCausalChange,
  webSearch,
  onWebSearchChange,
  user,
}: Props) {
  return (
    <header className="chat-header">
      <button
        className="menu-btn"
        id="toggle-sidebar"
        aria-label="Toggle sidebar"
        onClick={onToggleSidebar}
      >
        ☰
      </button>
      <div className="chat-title">
        Causal Agent <span className="status-dot" aria-hidden="true" />
      </div>
      <span className="token-badge" id="token-tally-badge">
        {`${tokenTally.toLocaleString()} tokens used`}
      </span>

      <div className="header-controls">
        <select
          className="model-select"
          id="model-select"
          aria-label="Model"
          value={model}
          onChange={(e) => onModelChange(e.target.value)}
        >
          <option value="gemini-2.5-flash">gemini-2.5-flash</option>
          <option value="gemini-2.5-pro">gemini-2.5-pro</option>
        </select>

        <label className="switch-group">
          <span className="sw-label">Causal</span>
          <span className="switch">
            <input
              type="checkbox"
              id="causal-toggle"
              aria-label="Causal reasoning"
              checked={causal}
              onChange={(e) => onCausalChange(e.target.checked)}
            />
            <span className="track" />
          </span>
        </label>

        <label className="switch-group" title="Add observation data from the web">
          <span className="sw-label">Web data</span>
          <span className="switch">
            <input
              type="checkbox"
              id="web-search-toggle"
              aria-label="Add observation data from the web"
              checked={webSearch}
              onChange={(e) => onWebSearchChange(e.target.checked)}
            />
            <span className="track" />
          </span>
        </label>

        <button className="icon-btn" id="theme-toggle" aria-label="Toggle theme" onClick={onToggleTheme}>
          {theme === "light" ? "☀" : "☾"}
        </button>

        <button
          className="signin-btn"
          id="sign-in-btn"
          style={user ? { display: "none" } : undefined}
          onClick={signIn}
        >
          Sign in with Google
        </button>
        <div className="user-chip" id="user-chip" style={user ? { display: "flex" } : { display: "none" }}>
          <img id="user-avatar" alt="" referrerPolicy="no-referrer" src={user?.photoURL || ""} />
          <span id="user-name">{user?.displayName || user?.email || ""}</span>
          <button id="sign-out-btn" onClick={signOutUser}>
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
