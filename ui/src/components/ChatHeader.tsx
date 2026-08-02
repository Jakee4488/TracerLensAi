import { signIn, signOutUser, type User } from "../lib/firebase";

interface Props {
  onToggleSidebar: () => void;
  tokenTally: number;
  user: User | null;
}

export function ChatHeader({
  onToggleSidebar,
  tokenTally,
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
