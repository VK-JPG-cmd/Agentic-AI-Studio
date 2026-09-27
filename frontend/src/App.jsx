import React, { useState, useEffect, useRef } from 'react'
import './App.css'

function renderFormattedContent(text) {
  if (!text) return ''
  const urlRegex = /(https?:\/\/[^\s<>"'()]+)/g
  const parts = String(text).split(urlRegex)
  if (parts.length === 1) return text

  return parts.map((part, i) => {
    if (part.match(urlRegex)) {
      return (
        <a
          key={i}
          href={part}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-url-link"
        >
          {part}
        </a>
      )
    }
    return part
  })
}

export default function App() {
  const [messages, setMessages] = useState([])
  const [inputQuery, setInputQuery] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [activeTimeline, setActiveTimeline] = useState(null)
  const [activeTab, setActiveTab] = useState('timeline') // 'timeline' | 'inspector'
  const [healthStatus, setHealthStatus] = useState(null)
  const [errorBanner, setErrorBanner] = useState(null)

  const chatEndRef = useRef(null)

  // Fetch health and service configuration on mount
  useEffect(() => {
    fetchHealth()
  }, [])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isLoading])

  const fetchHealth = async () => {
    try {
      const res = await fetch('/health')
      if (res.ok) {
        const data = await res.json()
        setHealthStatus(data)
      } else {
        setHealthStatus({ status: 'degraded' })
      }
    } catch {
      // Fallback if direct proxy or backend is starting up
      try {
        const directRes = await fetch('http://localhost:8000/health')
        if (directRes.ok) {
          const directData = await directRes.json()
          setHealthStatus(directData)
        }
      } catch {
        setHealthStatus({ status: 'offline' })
      }
    }
  }

  const handleSend = async (queryToSend) => {
    const text = (queryToSend || inputQuery).trim()
    if (!text || isLoading) return

    setErrorBanner(null)
    setInputQuery('')

    // Append user message to chat stream
    const userMessage = {
      id: `usr_${Date.now()}`,
      role: 'user',
      query: text,
      timestamp: new Date().toLocaleTimeString(),
    }

    setMessages((prev) => [...prev, userMessage])
    setIsLoading(true)

    const startTime = performance.now()

    try {
      // Call existing FastAPI backend
      let response = null
      try {
        response = await fetch('/agent/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: text }),
        })
      } catch {
        // Fallback directly to localhost:8000 if running without proxy
        response = await fetch('http://localhost:8000/agent/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: text }),
        })
      }

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}))
        throw new Error(errData.detail || `Server responded with status ${response.status}`)
      }

      const agentData = await response.json()
      const totalDuration = Math.round(performance.now() - startTime)

      // Open browser tabs directly on the client side if requested by agent tools
      const browserToolResults = (agentData.tool_results || []).filter(
        (tr) => (tr.tool_name === 'browser' || tr.tool_name === 'open_tab') && tr.success && tr.output?.url
      )
      let popupBlocked = false
      const openedUrls = []

      browserToolResults.forEach((tr) => {
        const target = tr.output.url === 'about:blank' ? 'about:blank' : tr.output.url
        openedUrls.push(target)
        try {
          const win = window.open(target, '_blank', 'noopener,noreferrer')
          if (!win || win.closed || typeof win.closed === 'undefined') {
            popupBlocked = true
          }
        } catch (e) {
          popupBlocked = true
          console.warn('Popup blocked:', e)
        }
      })

      const agentMessage = {
        id: `agent_${Date.now()}`,
        role: 'agent',
        response: agentData.response,
        run_id: agentData.run_id,
        session_id: agentData.session_id,
        status: agentData.status,
        iterations: agentData.iterations,
        steps: agentData.steps || [],
        tool_calls: agentData.tool_calls || [],
        tool_results: agentData.tool_results || [],
        openedUrls: openedUrls,
        popupBlocked: popupBlocked,
        duration: totalDuration,
        timestamp: new Date().toLocaleTimeString(),
        raw: agentData,
      }

      setMessages((prev) => [...prev, agentMessage])
      setActiveTimeline(agentMessage)
    } catch (err) {
      setErrorBanner(err.message || 'Failed to process request. Ensure backend is running.')
    } finally {
      setIsLoading(false)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const examplePrompts = [
    {
      title: 'Chennai Weather > 30°C',
      prompt: 'Find the weather in Chennai and calculate whether the temperature is above 30°C.',
    },
    {
      title: 'Math: 25 * 4',
      prompt: 'Calculate 25 * 4 and tell me the result.',
    },
    {
      title: 'Chennai Weather',
      prompt: 'What is the weather in Chennai?',
    },
    {
      title: 'Python Age in 2026',
      prompt: 'Search for python release year and calculate its age in 2026.',
    },
    {
      title: 'FastAPI Knowledge',
      prompt: 'Tell me about FastAPI',
    },
    {
      title: 'Open Browser Tab',
      prompt: 'Open a new tab in my browser',
    },
    {
      title: 'Undo & Saga Engine',
      prompt: 'How does the AG02 transactional engine with Undo Button and Saga compensation work?',
    },
  ]

  return (
    <div className="app-container">
      {/* Top Header */}
      <header className="app-header">
        <div className="brand-section">
          <div className="brand-logo" aria-label="Logo">
            <svg viewBox="0 0 24 24">
              <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
            </svg>
          </div>
          <div className="brand-text">
            <h1>Agentic AI Studio</h1>
          </div>
          <span className="brand-badge">Autonomous Loop</span>
        </div>

        <div className="header-status">
          <div className="status-pill">
            <div
              className={`status-indicator ${
                healthStatus?.status === 'healthy' ? 'online' : 'offline'
              }`}
            />
            <span>
              {healthStatus?.status === 'healthy'
                ? 'Backend Ready'
                : healthStatus
                ? 'Degraded'
                : 'Connecting...'}
            </span>
          </div>
          {healthStatus?.llm_model && (
            <div className="status-pill" title="Active Model">
              <span>Model: {healthStatus.llm_model.split('/').pop()}</span>
            </div>
          )}
          {healthStatus?.tools_available && (
            <div className="status-pill" title="Registered Tools">
              <span>Tools: {healthStatus.tools_available.length} active</span>
            </div>
          )}
          <a
            href="/dashboard"
            target="_blank"
            rel="noopener noreferrer"
            className="status-pill saga-link-pill"
            title="Open AG02 Saga & Undo Control Plane Dashboard"
          >
            <span>🔄 Undo & Saga Control Plane ↗</span>
          </a>
        </div>
      </header>

      {/* Main Workspace */}
      <main className="workspace-grid">
        {/* Left Column: Chat Area */}
        <section className="glass-panel chat-section">
          <div className="chat-header">
            <div className="chat-title">
              <span>Conversation Stream</span>
            </div>
            {messages.length > 0 && (
              <button
                className="view-timeline-btn"
                onClick={() => setMessages([])}
                style={{ marginLeft: 'auto' }}
              >
                Clear History
              </button>
            )}
          </div>

          <div className="chat-history">
            {errorBanner && (
              <div className="error-banner animate-fade-in">
                <span>⚠️ {errorBanner}</span>
              </div>
            )}

            {messages.length === 0 && (
              <div className="welcome-card animate-fade-in">
                <h2>Welcome to Agentic AI</h2>
                <p>
                  Experience an autonomous agent capable of multi-step planning, tool
                  execution, and real-time reflection with SQLite persistence and safe guardrails.
                </p>

                <div className="quick-prompts-label">Select a demo task to run:</div>
                <div className="quick-prompts-grid">
                  {examplePrompts.map((ex, idx) => (
                    <button
                      key={idx}
                      className="prompt-chip"
                      onClick={() => handleSend(ex.prompt)}
                      disabled={isLoading}
                    >
                      <span>⚡</span>
                      <span>{ex.title}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg) => (
              <div key={msg.id} className={`message-bubble ${msg.role}`}>
                <div className="message-meta">
                  <span>{msg.role === 'user' ? '👤 You' : '🤖 Autonomous Agent'}</span>
                  <span>•</span>
                  <span>{msg.timestamp}</span>
                </div>
                <div className="message-body">
                  {msg.role === 'user' ? (
                    msg.query
                  ) : (
                    <>
                      <div className="agent-text-content">
                        {renderFormattedContent(msg.response)}
                      </div>

                      {msg.openedUrls && msg.openedUrls.length > 0 && (
                        <div className="action-executed-card">
                          <div className="action-card-header">
                            <span className="action-title">🌐 Browser Action Executed</span>
                            {msg.popupBlocked && (
                              <span className="popup-warn-badge">⚠️ Popup Blocked</span>
                            )}
                          </div>
                          <div className="action-card-body">
                            {msg.openedUrls.map((url, uIdx) => (
                              <div key={uIdx} className="action-url-row">
                                <span className="action-url-text">{url}</span>
                                <a
                                  href={url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="action-open-btn"
                                >
                                  <span>Open URL</span>
                                  <span>↗</span>
                                </a>
                              </div>
                            ))}
                            {msg.popupBlocked && (
                              <p className="popup-notice">
                                Your browser blocked the automatic new tab. Click the <strong>Open URL ↗</strong> button above to open the page.
                              </p>
                            )}
                          </div>
                        </div>
                      )}
                    </>
                  )}

                  {msg.role === 'agent' && (
                    <div className="agent-stats-bar">
                      <div className="stat-item">
                        <span>Run:</span>
                        <span className="stat-tag">{msg.run_id || 'run_local'}</span>
                      </div>
                      <div className="stat-item">
                        <span>Tools:</span>
                        <span className="stat-tag">{msg.tool_calls.length}</span>
                      </div>
                      <div className="stat-item">
                        <span>Duration:</span>
                        <span className="stat-tag">{msg.duration}ms</span>
                      </div>
                      <button
                        className="view-timeline-btn"
                        onClick={() => {
                          setActiveTimeline(msg)
                          setActiveTab('timeline')
                        }}
                      >
                        Inspect Timeline ➔
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}

            {isLoading && (
              <div className="message-bubble agent animate-fade-in">
                <div className="message-meta">
                  <span>🤖 Autonomous Agent</span>
                  <span>• Thinking...</span>
                </div>
                <div className="message-body" style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  <div
                    className="animate-spin"
                    style={{
                      width: '18px',
                      height: '18px',
                      border: '2px solid rgba(255,255,255,0.2)',
                      borderTopColor: 'var(--accent-cyan)',
                      borderRadius: '50%',
                    }}
                  />
                  <span>Evaluating goal, planning actions, and executing tools...</span>
                </div>
              </div>
            )}

            <div ref={chatEndRef} />
          </div>

          {/* Input Bar */}
          <div className="chat-input-container">
            <div className="input-box-wrapper">
              <textarea
                id="agent-user-input"
                className="chat-textarea"
                placeholder="Ask the agent a goal (e.g. 'Find the weather in Chennai and check if above 30°C')..."
                value={inputQuery}
                onChange={(e) => setInputQuery(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={1}
                disabled={isLoading}
              />
              <button
                id="agent-send-button"
                className="send-button"
                onClick={() => handleSend()}
                disabled={isLoading || !inputQuery.trim()}
              >
                {isLoading ? (
                  <span>Running...</span>
                ) : (
                  <>
                    <span>Send</span>
                    <span>➔</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </section>

        {/* Right Column: Tool Execution Timeline */}
        <section className="glass-panel timeline-section">
          <div className="timeline-header">
            <div className="chat-title">
              <span>Tool Execution Timeline</span>
            </div>

            <div className="tab-group">
              <button
                className={`tab-btn ${activeTab === 'timeline' ? 'active' : ''}`}
                onClick={() => setActiveTab('timeline')}
              >
                Flow Stepper
              </button>
              <button
                className={`tab-btn ${activeTab === 'inspector' ? 'active' : ''}`}
                onClick={() => setActiveTab('inspector')}
              >
                State JSON
              </button>
            </div>
          </div>

          <div className="timeline-content">
            {!activeTimeline ? (
              <div className="empty-timeline">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M12 6v6m0 0v6m0-6h6m-6 0H6" strokeLinecap="round" strokeLinejoin="round" />
                  <circle cx="12" cy="12" r="9" />
                </svg>
                <p>Run a task to inspect the live execution timeline.</p>
                <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  Visualizes decisions, tool dispatches, observations, and final answers.
                </span>
              </div>
            ) : activeTab === 'timeline' ? (
              <div className="timeline-flow animate-fade-in">
                {/* 1. Initial User Request Node */}
                <div className="timeline-step">
                  <div className="step-header">
                    <span className="step-type-badge final">
                      <span>👤</span> USER REQUEST
                    </span>
                    <span className="step-timestamp">{activeTimeline.timestamp}</span>
                  </div>
                  <div className="step-body">
                    <strong>Goal:</strong> "{activeTimeline.raw?.user_goal || messages.find(m => m.role === 'user')?.query || 'User task'}"
                  </div>
                </div>

                <div className="step-connector">↓</div>

                {/* 2. Structured Steps: THINK/DECISION, TOOL_CALL, TOOL_RESULT, FINAL */}
                {activeTimeline.steps && activeTimeline.steps.length > 0 ? (
                  activeTimeline.steps.map((st, index) => {
                    const type = st.step_type
                    let badgeClass = 'decision'
                    let icon = '🧠'

                    if (type === 'TOOL_CALL') {
                      badgeClass = 'tool_call'
                      icon = '⚡'
                    } else if (type === 'TOOL_RESULT') {
                      badgeClass = 'tool_result'
                      icon = '📦'
                    } else if (type === 'FINAL') {
                      badgeClass = 'final'
                      icon = '🎯'
                    }

                    return (
                      <React.Fragment key={st.step_id || index}>
                        <div className="timeline-step">
                          <div className="step-header">
                            <span className={`step-type-badge ${badgeClass}`}>
                              <span>{icon}</span> {type}
                            </span>
                            <span className="step-timestamp">
                              {st.timestamp ? new Date(st.timestamp).toLocaleTimeString() : ''}
                            </span>
                          </div>

                          <div className="step-body">
                            {type === 'THINK/DECISION' && (
                              <div>
                                <span style={{ color: 'var(--accent-purple)', fontWeight: 600 }}>Decision: </span>
                                <span>{st.content}</span>
                              </div>
                            )}

                            {type === 'TOOL_CALL' && (
                              <div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.3rem' }}>
                                  <span style={{ color: 'var(--accent-amber)', fontWeight: 600 }}>
                                    Tool: {st.metadata?.tool_name || st.content?.match(/^Calling\s+(\w+)/)?.[1] || 'unspecified'}
                                  </span>
                                  <span className="stat-tag" style={{ fontSize: '0.7rem' }}>
                                    ID: {st.metadata?.call_id}
                                  </span>
                                </div>
                                <div className="step-code-box">
                                  {JSON.stringify(st.metadata?.arguments || {}, null, 2)}
                                </div>
                              </div>
                            )}

                            {type === 'TOOL_RESULT' && (
                              <div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.3rem' }}>
                                  <span style={{ color: st.metadata?.success !== false ? 'var(--accent-emerald)' : 'var(--accent-rose)', fontWeight: 600 }}>
                                    Status: {st.metadata?.success !== false ? '✓ Success' : '✗ Failed'}
                                  </span>
                                  {st.metadata?.tool_name && (
                                    <span className="stat-tag" style={{ fontSize: '0.7rem' }}>
                                      {st.metadata.tool_name}
                                    </span>
                                  )}
                                  {(st.metadata?.tool_name === 'browser' || st.metadata?.tool_name === 'open_tab') && (
                                    <a
                                      href={(() => {
                                        try {
                                          const parsed = JSON.parse(st.content)
                                          return parsed.url || '#'
                                        } catch {
                                          return '#'
                                        }
                                      })()}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="timeline-open-link"
                                      style={{ marginLeft: 'auto', fontSize: '0.75rem' }}
                                    >
                                      Open in Browser ↗
                                    </a>
                                  )}
                                </div>
                                <div className="step-code-box" style={{ color: '#a7f3d0' }}>
                                  {st.content}
                                </div>
                              </div>
                            )}

                            {type === 'FINAL' && (
                              <div>
                                <span style={{ color: 'var(--accent-cyan)', fontWeight: 600 }}>Synthesized Response: </span>
                                <span>{st.content}</span>
                              </div>
                            )}
                          </div>
                        </div>

                        {index < activeTimeline.steps.length - 1 && (
                          <div className="step-connector">↓</div>
                        )}
                      </React.Fragment>
                    )
                  })
                ) : (
                  /* Fallback display if steps array was not present */
                  <div className="timeline-step">
                    <div className="step-header">
                      <span className="step-type-badge final">FINAL ANSWER</span>
                    </div>
                    <div className="step-body">{activeTimeline.response}</div>
                  </div>
                )}
              </div>
            ) : (
              /* Tab 2: Raw State JSON Inspector */
              <pre className="json-viewer animate-fade-in">
                {JSON.stringify(activeTimeline.raw, null, 2)}
              </pre>
            )}
          </div>
        </section>
      </main>
    </div>
  )
}
