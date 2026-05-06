import { useState, useRef, useEffect } from 'react';
import './App.css';

// ── Avatars ───────────────────────────────────────────────────────────────────

const BotAvatar = () => (
  <div className="avatar bot-avatar">
    <svg viewBox="0 0 24 24" fill="none">
      <rect x="3" y="8" width="18" height="12" rx="3" fill="currentColor" opacity="0.15"/>
      <rect x="3" y="8" width="18" height="12" rx="3" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="9"  cy="14" r="1.5" fill="currentColor"/>
      <circle cx="15" cy="14" r="1.5" fill="currentColor"/>
      <path d="M9 4h6M12 4v4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <circle cx="12" cy="3.5" r="1" fill="currentColor"/>
    </svg>
  </div>
);

const UserAvatar = () => (
  <div className="avatar user-avatar">
    <svg viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M4 20c0-4 3.6-7 8-7s8 3 8 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  </div>
);

const TypingDots = () => (
  <div className="typing-dots">
    <span /><span /><span />
  </div>
);

// ── Helpers ───────────────────────────────────────────────────────────────────

const getTime = () =>
  new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

const genId = () => `session_${Date.now()}`;

const SUGGESTIONS = [
  'What is this document about?',
  'Summarize the key points',
  'Explain the main concepts',
];

const STORAGE_KEY = 'rag_sessions';

function loadSessions() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || []; }
  catch { return []; }
}

function saveSessions(sessions) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [sessions,          setSessions]          = useState(loadSessions);
  const [activeId,          setActiveId]          = useState(sessions[0]?.id || null);
  const [input,             setInput]             = useState('');
  const [loading,           setLoading]           = useState(false);
  const [sidebarOpen,       setSidebarOpen]       = useState(true);
  const [confirmDeleteId,   setConfirmDeleteId]   = useState(null);

  const bottomRef = useRef(null);
  const inputRef  = useRef(null);

  // Active session's messages
  const activeSession = sessions.find(s => s.id === activeId) || null;
  const messages      = activeSession?.messages || [];

  // Persist to localStorage on every sessions change
  useEffect(() => { saveSessions(sessions); }, [sessions]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // ── Session helpers ──────────────────────────────────────────────────────────

  const createNewChat = () => {
    const id  = genId();
    const now = new Date().toLocaleDateString([], { month: 'short', day: 'numeric' });
    const newSession = { id, title: 'New Chat', date: now, messages: [] };
    setSessions(prev => [newSession, ...prev]);
    setActiveId(id);
    setInput('');
    inputRef.current?.focus();
  };

  const deleteSession = (id) => {
    setSessions(prev => {
      const updated = prev.filter(s => s.id !== id);
      if (activeId === id) setActiveId(updated[0]?.id || null);
      return updated;
    });
    setConfirmDeleteId(null);
  };

  const updateSession = (id, newMessages) => {
    setSessions(prev => prev.map(s => {
      if (s.id !== id) return s;
      // Use first user message as session title
      const firstUser = newMessages.find(m => m.type === 'user');
      const title = firstUser
        ? firstUser.text.slice(0, 36) + (firstUser.text.length > 36 ? '…' : '')
        : s.title;
      return { ...s, title, messages: newMessages };
    }));
  };

  // ── Ask logic ────────────────────────────────────────────────────────────────

  const askQuestion = async () => {
    const question = input.trim();
    if (!question || loading) return;

    // Create a session on-the-fly if none is active
    let sessionId = activeId;
    if (!sessionId) {
      const id  = genId();
      const now = new Date().toLocaleDateString([], { month: 'short', day: 'numeric' });
      setSessions(prev => [{ id, title: 'New Chat', date: now, messages: [] }, ...prev]);
      setActiveId(id);
      sessionId = id;
    }

    const userMsg    = { type: 'user', text: question, time: getTime() };
    const thinkingMsg = { type: 'bot',  text: '',       time: '', thinking: true };
    const nextMsgs   = [...messages, userMsg, thinkingMsg];

    updateSession(sessionId, nextMsgs);
    setInput('');
    setLoading(true);

    try {
      const res = await fetch(`http://13.223.184.184:8000/ask?q=${encodeURIComponent(question)}`);
      const data = await res.json();
      const resolved = nextMsgs.map(m =>
        m.thinking ? { type: 'bot', text: data.answer, time: getTime() } : m
      );
      updateSession(sessionId, resolved);
    } catch (err) {
      console.error(err);
      const resolved = nextMsgs.map(m =>
        m.thinking
          ? { type: 'bot', text: '⚠️ Could not reach the backend. Make sure it is running on port 8000.', time: getTime() }
          : m
      );
      updateSession(sessionId, resolved);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); askQuestion(); }
  };

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <div className="shell">

      {/* ── Sidebar ── */}
      <aside className={`sidebar ${sidebarOpen ? 'open' : 'closed'}`}>

        {/* Sidebar header */}
        <div className="sidebar-header">
          <div className="brand">
            <div className="brand-icon">
              <svg viewBox="0 0 24 24" fill="none">
                <path d="M12 2L2 7l10 5 10-5-10-5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
                <path d="M2 17l10 5 10-5M2 12l10 5 10-5" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
              </svg>
            </div>
            {sidebarOpen && <span className="brand-name">RAG Assistant</span>}
          </div>
          <button className="icon-btn" onClick={() => setSidebarOpen(o => !o)} title="Toggle sidebar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              {sidebarOpen
                ? <path d="M18 6L6 18M6 6l12 12"/>
                : <path d="M3 6h18M3 12h18M3 18h18"/>}
            </svg>
          </button>
        </div>

        {/* New chat button */}
        <button className="new-chat-btn" onClick={createNewChat}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M12 5v14M5 12h14"/>
          </svg>
          {sidebarOpen && <span>New Chat</span>}
        </button>

        {/* Session list */}
        {sidebarOpen && (
          <div className="session-list">
            {sessions.length === 0 && (
              <p className="no-sessions">No chats yet</p>
            )}
            {sessions.map(s => (
              <div
                key={s.id}
                className={`session-item ${s.id === activeId ? 'active' : ''}`}
                onClick={() => { setActiveId(s.id); setConfirmDeleteId(null); }}
              >
                <div className="session-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                    <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
                  </svg>
                </div>
                <div className="session-meta">
                  <span className="session-title">{s.title}</span>
                  <span className="session-date">{s.date}</span>
                </div>

                {/* Delete button / confirm */}
                {confirmDeleteId === s.id ? (
                  <div className="delete-confirm" onClick={e => e.stopPropagation()}>
                    <button className="confirm-yes" onClick={() => deleteSession(s.id)}>Delete</button>
                    <button className="confirm-no"  onClick={() => setConfirmDeleteId(null)}>Cancel</button>
                  </div>
                ) : (
                  <button
                    className="delete-btn"
                    title="Delete"
                    onClick={e => { e.stopPropagation(); setConfirmDeleteId(s.id); }}
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                      <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>
                    </svg>
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Sidebar footer */}
        {sidebarOpen && (
          <div className="sidebar-footer">
            <span className="status-badge">
              <span className="status-dot"/>
              Connected
            </span>
          </div>
        )}
      </aside>

      {/* ── Main chat area ── */}
      <div className="main">

        {/* Top bar */}
        <header className="topbar">
          {!sidebarOpen && (
            <button className="icon-btn" onClick={() => setSidebarOpen(true)} title="Open sidebar">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <path d="M3 6h18M3 12h18M3 18h18"/>
              </svg>
            </button>
          )}
          <span className="topbar-title">
            {activeSession ? activeSession.title : 'RAG Assistant'}
          </span>
          <span className="topbar-count">
            {messages.filter(m => !m.thinking).length > 0 &&
              `${Math.floor(messages.filter(m => !m.thinking).length / 2)} Q&A`}
          </span>
        </header>

        {/* Chat window */}
        <div className="chat-window">
          {messages.length === 0 && (
            <div className="empty-state">
              <div className="empty-icon">
                <svg viewBox="0 0 24 24" fill="none">
                  <path d="M12 2L2 7l10 5 10-5-10-5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
                  <path d="M2 17l10 5 10-5M2 12l10 5 10-5" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
                </svg>
              </div>
              <h2>How can I help you?</h2>
              <p>Ask anything about your documents. I'll find the most relevant answers.</p>
              <div className="suggestions">
                {SUGGESTIONS.map(s => (
                  <button key={s} className="suggestion-chip"
                    onClick={() => { setInput(s); inputRef.current?.focus(); }}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`message-group ${msg.type}`}>
              {msg.type === 'bot' && <BotAvatar />}
              <div className="message-content">
                <div className={`bubble${msg.thinking ? ' thinking-bubble' : ''}`}>
                  {msg.thinking ? <TypingDots /> : msg.text}
                </div>
                {!msg.thinking && <span className="timestamp">{msg.time}</span>}
              </div>
              {msg.type === 'user' && <UserAvatar />}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {/* Input area */}
        <div className="input-area">
          <div className="input-bar">
            <input
              ref={inputRef}
              type="text"
              placeholder="Ask a question about your documents..."
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading}
              autoFocus
            />
            <button
              className="send-btn"
              onClick={askQuestion}
              disabled={loading || !input.trim()}
              title="Send (Enter)"
            >
              <svg viewBox="0 0 24 24" fill="none">
                <path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z"
                  stroke="currentColor" strokeWidth="2"
                  strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          </div>
          <p className="input-hint">Press <kbd>Enter</kbd> to send</p>
        </div>
      </div>
    </div>
  );
}
