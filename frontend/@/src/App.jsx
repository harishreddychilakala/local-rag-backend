import { useState, useRef, useEffect } from 'react';
import './App.css';

// ── Avatars ───────────────────────────────────────────────────────────────────

const BotAvatar = () => (
  <div className="avatar bot-avatar">
    <svg viewBox="0 0 24 24" fill="none">
      <rect x="3" y="8" width="18" height="12" rx="3" fill="currentColor" opacity="0.15"/>
      <rect x="3" y="8" width="18" height="12" rx="3" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="9" cy="14" r="1.5" fill="currentColor"/>
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
  new Date().toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit'
  });

const genId = () => `session_${Date.now()}`;

const SUGGESTIONS = [
  'What is this document about?',
  'Summarize the key points',
  'Explain the main concepts',
];

const STORAGE_KEY = 'rag_sessions';

function loadSessions() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
  } catch {
    return [];
  }
}

function saveSessions(sessions) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {

  const [sessions, setSessions] = useState(loadSessions);
  const [activeId, setActiveId] = useState(sessions[0]?.id || null);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);

  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  // Active session
  const activeSession = sessions.find(s => s.id === activeId) || null;

  const messages = activeSession?.messages || [];

  // Save sessions
  useEffect(() => {
    saveSessions(sessions);
  }, [sessions]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: 'smooth'
    });
  }, [messages]);

  // ── Create New Chat ─────────────────────────────────────────────────────────

  const createNewChat = () => {

    const id = genId();

    const now = new Date().toLocaleDateString([], {
      month: 'short',
      day: 'numeric'
    });

    const newSession = {
      id,
      title: 'New Chat',
      date: now,
      messages: []
    };

    setSessions(prev => [newSession, ...prev]);

    setActiveId(id);

    setInput('');

    inputRef.current?.focus();
  };

  // ── Delete Session ──────────────────────────────────────────────────────────

  const deleteSession = (id) => {

    setSessions(prev => {

      const updated = prev.filter(s => s.id !== id);

      if (activeId === id) {
        setActiveId(updated[0]?.id || null);
      }

      return updated;
    });

    setConfirmDeleteId(null);
  };

  // ── Update Session ──────────────────────────────────────────────────────────

  const updateSession = (id, newMessages) => {

    setSessions(prev => prev.map(s => {

      if (s.id !== id) return s;

      const firstUser = newMessages.find(m => m.type === 'user');

      const title = firstUser
        ? firstUser.text.slice(0, 36) +
          (firstUser.text.length > 36 ? '…' : '')
        : s.title;

      return {
        ...s,
        title,
        messages: newMessages
      };
    }));
  };

  // ── Ask Question ────────────────────────────────────────────────────────────

  const askQuestion = async () => {

    const question = input.trim();

    if (!question || loading) return;

    let sessionId = activeId;

    // Create session if none exists
    if (!sessionId) {

      const id = genId();

      const now = new Date().toLocaleDateString([], {
        month: 'short',
        day: 'numeric'
      });

      const newSession = {
        id,
        title: 'New Chat',
        date: now,
        messages: []
      };

      setSessions(prev => [newSession, ...prev]);

      setActiveId(id);

      sessionId = id;
    }

    // User message
    const userMsg = {
      type: 'user',
      text: question,
      time: getTime()
    };

    // Thinking message
    const thinkingMsg = {
      type: 'bot',
      text: '',
      time: '',
      thinking: true
    };

    const nextMsgs = [...messages, userMsg, thinkingMsg];

    updateSession(sessionId, nextMsgs);

    setInput('');

    setLoading(true);

    try {

      const response = await fetch(
        "http://13.223.184.184:8000/ask",
        {
          method: "POST",

          headers: {
            "Content-Type": "application/json",
          },

          body: JSON.stringify({
            question: question
          }),
        }
      );

      const data = await response.json();

      const updatedMsgs = nextMsgs.map(msg =>

        msg.thinking
          ? {
              type: 'bot',
              text: data.answer || "No response received",
              time: getTime()
            }
          : msg
      );

      updateSession(sessionId, updatedMsgs);

    } catch (error) {

      console.error(error);

      const updatedMsgs = nextMsgs.map(msg =>

        msg.thinking
          ? {
              type: 'bot',
              text: '⚠️ Could not connect to backend.',
              time: getTime()
            }
          : msg
      );

      updateSession(sessionId, updatedMsgs);

    } finally {

      setLoading(false);

      inputRef.current?.focus();
    }
  };

  // ── Handle Enter Key ────────────────────────────────────────────────────────

  const handleKeyDown = (e) => {

    if (e.key === 'Enter' && !e.shiftKey) {

      e.preventDefault();

      askQuestion();
    }
  };

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="shell">

      {/* Sidebar */}
      <aside className={`sidebar ${sidebarOpen ? 'open' : 'closed'}`}>

        {/* Sidebar Header */}
        <div className="sidebar-header">

          <div className="brand">

            <div className="brand-icon">
              <svg viewBox="0 0 24 24" fill="none">
                <path d="M12 2L2 7l10 5 10-5-10-5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
                <path d="M2 17l10 5 10-5M2 12l10 5 10-5" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
              </svg>
            </div>

            {sidebarOpen && (
              <span className="brand-name">
                RAG Assistant
              </span>
            )}

          </div>

          <button
            className="icon-btn"
            onClick={() => setSidebarOpen(o => !o)}
          >
            ✕
          </button>

        </div>

        {/* New Chat */}
        <button
          className="new-chat-btn"
          onClick={createNewChat}
        >
          + New Chat
        </button>

        {/* Sessions */}
        {sidebarOpen && (

          <div className="session-list">

            {sessions.map(s => (

              <div
                key={s.id}
                className={`session-item ${s.id === activeId ? 'active' : ''}`}
                onClick={() => {
                  setActiveId(s.id);
                  setConfirmDeleteId(null);
                }}
              >

                <div className="session-meta">

                  <span className="session-title">
                    {s.title}
                  </span>

                  <span className="session-date">
                    {s.date}
                  </span>

                </div>

                {confirmDeleteId === s.id ? (

                  <div className="delete-confirm">

                    <button onClick={() => deleteSession(s.id)}>
                      Delete
                    </button>

                    <button onClick={() => setConfirmDeleteId(null)}>
                      Cancel
                    </button>

                  </div>

                ) : (

                  <button
                    className="delete-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmDeleteId(s.id);
                    }}
                  >
                    🗑
                  </button>

                )}
              </div>
            ))}
          </div>
        )}

        {/* Footer */}
        {sidebarOpen && (

          <div className="sidebar-footer">

            <span className="status-badge">

              <span className="status-dot" />

              Connected

            </span>

          </div>
        )}
      </aside>

      {/* Main */}
      <div className="main">

        {/* Topbar */}
        <header className="topbar">

          <span className="topbar-title">
            {activeSession
              ? activeSession.title
              : 'RAG Assistant'}
          </span>

        </header>

        {/* Chat Window */}
        <div className="chat-window">

          {messages.length === 0 && (

            <div className="empty-state">

              <h2>How can I help you?</h2>

              <p>
                Ask anything about your documents.
              </p>

              <div className="suggestions">

                {SUGGESTIONS.map(s => (

                  <button
                    key={s}
                    className="suggestion-chip"
                    onClick={() => {
                      setInput(s);
                      inputRef.current?.focus();
                    }}
                  >
                    {s}
                  </button>

                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (

            <div
              key={i}
              className={`message-group ${msg.type}`}
            >

              {msg.type === 'bot' && <BotAvatar />}

              <div className="message-content">

                <div className={`bubble ${msg.thinking ? 'thinking-bubble' : ''}`}>

                  {msg.thinking
                    ? <TypingDots />
                    : msg.text}

                </div>

                {!msg.thinking && (
                  <span className="timestamp">
                    {msg.time}
                  </span>
                )}

              </div>

              {msg.type === 'user' && <UserAvatar />}

            </div>
          ))}

          <div ref={bottomRef} />

        </div>

        {/* Input */}
        <div className="input-area">

          <div className="input-bar">

            <input
              ref={inputRef}
              type="text"
              placeholder="Ask a question about your documents..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading}
            />

            <button
              className="send-btn"
              onClick={askQuestion}
              disabled={loading || !input.trim()}
            >
              ➤
            </button>

          </div>

          <p className="input-hint">
            Press Enter to send
          </p>

        </div>
      </div>
    </div>
  );
}