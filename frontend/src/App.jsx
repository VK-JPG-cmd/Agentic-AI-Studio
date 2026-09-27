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
  // Navigation Mode: 'agent' (Autonomous ReAct Chat) | 'saga' (AG02 Saga & Undo Studio)
  const [activeMode, setActiveMode] = useState('agent')

  // --- Autonomous Agent State ---
  const [messages, setMessages] = useState([])
  const [inputQuery, setInputQuery] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [activeTimeline, setActiveTimeline] = useState(null)
  const [activeTab, setActiveTab] = useState('timeline') // 'timeline' | 'inspector'
  const [healthStatus, setHealthStatus] = useState(null)
  const [errorBanner, setErrorBanner] = useState(null)

  // --- AG02 Transactional Saga State ---
  const [worldState, setWorldState] = useState(null)
  const [worldCounts, setWorldCounts] = useState({ bookings: 0, payments: 0, tickets: 0, emails: 0 })
  const [worldIsClean, setWorldIsClean] = useState(true)
  const [currentTx, setCurrentTx] = useState(null)
  const [worldMetrics, setWorldMetrics] = useState(null)
  const [isSagaRunning, setIsSagaRunning] = useState(false)
  const [sagaError, setSagaError] = useState(null)
  const [pendingApproval, setPendingApproval] = useState(null)
  const [sagaTab, setSagaTab] = useState('stepper') // 'stepper' | 'metrics' | 'log'
  const [faultStep, setFaultStep] = useState(null)
  const [failRollback, setFailRollback] = useState(false)
  const [sagaGoal, setSagaGoal] = useState('Book a hotel, charge payment, create a support ticket.')

  const chatEndRef = useRef(null)

  // Fetch health and service configuration on mount
  useEffect(() => {
    fetchHealth()
    fetchWorldState()
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

  // --- Saga API Handlers ---
  const fetchWorldState = async () => {
    try {
      let res = await fetch('/ag02/world')
      if (!res.ok) res = await fetch('http://localhost:8000/ag02/world')
      if (res.ok) {
        const data = await res.json()
        setWorldState(data.state)
        setWorldCounts(data.counts || { bookings: 0, payments: 0, tickets: 0, emails: 0 })
        setWorldIsClean(data.is_clean)
      }
    } catch (e) {
      console.warn('Failed to fetch world state:', e)
    }
  }

  const handleResetWorld = async () => {
    try {
      let res = await fetch('/ag02/world/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      if (!res.ok) {
        res = await fetch('http://localhost:8000/ag02/world/reset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        })
      }
      if (res.ok) {
        const data = await res.json()
        setWorldState(data.state)
        setWorldCounts({ bookings: 0, payments: 0, tickets: 0, emails: 0 })
        setWorldIsClean(true)
        setCurrentTx(null)
        setWorldMetrics(null)
        setPendingApproval(null)
        setSagaError(null)
      }
    } catch (e) {
      setSagaError('Failed to reset world: ' + e.message)
    }
  }

  const handleRunSaga = async (goalOverride, faultOverride, rollbackOverride) => {
    const goalToRun = (goalOverride || sagaGoal).trim()
    if (!goalToRun || isSagaRunning) return
    setIsSagaRunning(true)
    setSagaError(null)
    setPendingApproval(null)

    const payload = {
      goal: goalToRun,
      fail_at_step: faultOverride !== undefined ? faultOverride : faultStep,
      fail_during_rollback: rollbackOverride !== undefined ? rollbackOverride : failRollback,
    }

    try {
      let res = await fetch('/ag02/demo/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        res = await fetch('http://localhost:8000/ag02/demo/execute', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail?.message || err.detail || `Server error: ${res.status}`)
      }
      const data = await res.json()
      setCurrentTx(data.transaction)
      setWorldMetrics(data.world_metrics)
      setWorldState(data.world_state)
      await fetchWorldState()

      if (data.transaction?.status === 'APPROVAL_REQUIRED') {
        fetchPendingApprovals(data.transaction.transaction_id)
      }
    } catch (e) {
      setSagaError(e.message)
    } finally {
      setIsSagaRunning(false)
    }
  }

  const fetchPendingApprovals = async (txId) => {
    try {
      let res = await fetch(`/ag02/approvals?transaction_id=${txId}`)
      if (!res.ok) res = await fetch(`http://localhost:8000/ag02/approvals?transaction_id=${txId}`)
      if (res.ok) {
        const data = await res.json()
        if (data.approvals && data.approvals.length > 0) {
          setPendingApproval(data.approvals[0])
        }
      }
    } catch (e) {
      console.warn('Failed to fetch approvals:', e)
    }
  }

  const handleUndo = async (txId) => {
    if (!txId || isSagaRunning) return
    setIsSagaRunning(true)
    setSagaError(null)
    try {
      let res = await fetch(`/ag02/transactions/${txId}/undo`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Manual Undo Button triggered from React Studio' }),
      })
      if (!res.ok) {
        res = await fetch(`http://localhost:8000/ag02/transactions/${txId}/undo`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: 'Manual Undo Button triggered from React Studio' }),
        })
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `Undo failed: ${res.status}`)
      }
      const data = await res.json()
      setCurrentTx(data.transaction)
      setWorldMetrics(data.world_metrics)
      setWorldState(data.world_state)
      await fetchWorldState()
    } catch (e) {
      setSagaError('Undo error: ' + e.message)
    } finally {
      setIsSagaRunning(false)
    }
  }

  const handleApprovalDecision = async (approvalId, approved) => {
    if (!approvalId || isSagaRunning) return
    setIsSagaRunning(true)
    setSagaError(null)
    try {
      let res = await fetch(`/ag02/approvals/${approvalId}/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          approved,
          reason: approved ? 'Approved by operator' : 'Denied by operator',
        }),
      })
      if (!res.ok) {
        res = await fetch(`http://localhost:8000/ag02/approvals/${approvalId}/decide`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            approved,
            reason: approved ? 'Approved by operator' : 'Denied by operator',
          }),
        })
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `Approval resolution failed: ${res.status}`)
      }
      const data = await res.json()
      setCurrentTx(data.transaction)
      setWorldMetrics(data.world_metrics)
      setWorldState(data.world_state)
      setPendingApproval(null)
      await fetchWorldState()
    } catch (e) {
      setSagaError('Approval decision error: ' + e.message)
    } finally {
      setIsSagaRunning(false)
    }
  }

  const handleSimulateCrash = async () => {
    if (isSagaRunning) return
    setIsSagaRunning(true)
    setSagaError(null)
    try {
      let res = await fetch('/ag02/demo/simulate-crash', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal: sagaGoal, crash_after_step: 2 }),
      })
      if (!res.ok) {
        res = await fetch('http://localhost:8000/ag02/demo/simulate-crash', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ goal: sagaGoal, crash_after_step: 2 }),
        })
      }
      const data = await res.json()
      setCurrentTx(data.transaction)
      setWorldMetrics(data.world_metrics)
      setWorldState(data.world_state)
      await fetchWorldState()
    } catch (e) {
      setSagaError('Crash simulation error: ' + e.message)
    } finally {
      setIsSagaRunning(false)
    }
  }

  const handleRecover = async (txId) => {
    if (isSagaRunning) return
    setIsSagaRunning(true)
    setSagaError(null)
    try {
      let res = await fetch('/ag02/demo/recover', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transaction_id: txId, strategy: 'rollback' }),
      })
      if (!res.ok) {
        res = await fetch('http://localhost:8000/ag02/demo/recover', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ transaction_id: txId, strategy: 'rollback' }),
        })
      }
      const data = await res.json()
      setCurrentTx(data.transaction)
      setWorldMetrics(data.world_metrics)
      setWorldState(data.world_state)
      await fetchWorldState()
    } catch (e) {
      setSagaError('Recovery error: ' + e.message)
    } finally {
      setIsSagaRunning(false)
    }
  }

  // --- Autonomous Agent ReAct Handler ---
  const handleSend = async (queryToSend) => {
    const text = (queryToSend || inputQuery).trim()
    if (!text || isLoading) return

    setErrorBanner(null)
    setInputQuery('')

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
      let response = null
      try {
        response = await fetch('/agent/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: text }),
        })
      } catch {
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
      title: 'Open Browser Tab',
      prompt: 'Open a new tab in my browser',
    },
    {
      title: 'FastAPI Knowledge',
      prompt: 'Tell me about FastAPI',
    },
  ]

  const sagaScenarios = [
    {
      title: '🌴 Complete Travel Booking',
      desc: 'Book hotel, charge payment, issue ticket (Reversible)',
      goal: 'Book a hotel, charge payment, create a support ticket.',
      fault: null,
      failRollback: false,
    },
    {
      title: '⚡ Fail at Step 2 (Auto Rollback)',
      desc: 'Inject payment failure to trigger automatic reverse compensation',
      goal: 'Book a hotel, charge payment, create a support ticket.',
      fault: 2,
      failRollback: false,
    },
    {
      title: '📧 Approval Interlock Gate',
      desc: 'Include irreversible email step requiring human approval',
      goal: 'Book a flight, charge payment, and send confirmation email.',
      fault: null,
      failRollback: false,
    },
    {
      title: '💥 Simulate Crash & Recover',
      desc: 'Crash process mid-flight and recover cleanly from SQLite durable log',
      action: 'crash',
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
          <span className="brand-badge">
            {activeMode === 'agent' ? 'Autonomous Loop' : 'Saga & Undo'}
          </span>
        </div>

        {/* Mode Navigation Switcher */}
        <div className="mode-segmented-control">
          <button
            className={`mode-segment-btn ${activeMode === 'agent' ? 'active' : ''}`}
            onClick={() => setActiveMode('agent')}
          >
            <span>💬 Autonomous Agent</span>
          </button>
          <button
            className={`mode-segment-btn ${activeMode === 'saga' ? 'active' : ''}`}
            onClick={() => {
              setActiveMode('saga')
              fetchWorldState()
            }}
          >
            <span>🔄 Saga & Undo Studio</span>
          </button>
        </div>

        <div className="header-status">
          {activeMode === 'agent' ? (
            <>
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
            </>
          ) : (
            <>
              <div className="status-pill" title="World State Integrity">
                <div className={`status-indicator ${worldIsClean ? 'online' : 'warning'}`} />
                <span>{worldIsClean ? 'World State: Pristine' : 'World State: Active'}</span>
              </div>
              <button
                className="status-pill reset-pill-btn"
                onClick={handleResetWorld}
                title="Reset mock world back to zero state"
              >
                <span>🧹 Reset World</span>
              </button>
            </>
          )}
        </div>
      </header>

      {/* Mode 1: Autonomous Agent Chat & Tool Stepper */}
      {activeMode === 'agent' ? (
        <main className="workspace-grid animate-fade-in">
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
                    Experience an autonomous agent capable of multi-step planning, tool execution,
                    and real-time reflection with SQLite persistence and safe guardrails.
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
                        <div>{renderFormattedContent(msg.response)}</div>

                        {/* Direct Browser Window Link Badge */}
                        {msg.openedUrls && msg.openedUrls.length > 0 && (
                          <div className="action-card-container animate-fade-in">
                            <div className="action-card-header">
                              <span className="action-icon">🌐</span>
                              <span className="action-title">
                                {msg.popupBlocked ? 'Pop-up Blocked by Browser' : 'Tab Opened in Browser'}
                              </span>
                            </div>
                            {msg.openedUrls.map((targetUrl, idx) => (
                              <div key={idx} className="action-url-item">
                                <span className="action-url-text">{targetUrl}</span>
                                <a
                                  href={targetUrl}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="action-open-btn"
                                >
                                  Open Now ↗
                                </a>
                              </div>
                            ))}
                            {msg.popupBlocked && (
                              <p className="popup-notice">
                                Your browser blocked automatic pop-ups. Click the button above to view the link.
                              </p>
                            )}
                          </div>
                        )}
                      </>
                    )}
                  </div>

                  {msg.role === 'agent' && (
                    <div className="message-footer">
                      <div className="stat-item">
                        <span>Steps:</span>
                        <span className="stat-tag">{msg.steps.length}</span>
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
              ))}

              {isLoading && (
                <div className="message-bubble agent animate-fade-in">
                  <div className="message-meta">
                    <span>🤖 Autonomous Agent</span>
                    <span>• Thinking...</span>
                  </div>
                  <div
                    className="message-body"
                    style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}
                  >
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
                  Step Execution
                </button>
                <button
                  className={`tab-btn ${activeTab === 'inspector' ? 'active' : ''}`}
                  onClick={() => setActiveTab('inspector')}
                >
                  Raw State JSON
                </button>
              </div>
            </div>

            <div className="timeline-content">
              {!activeTimeline ? (
                <div className="timeline-empty">
                  <div className="empty-icon">⚡</div>
                  <p>No active trace</p>
                  <span>Execute a query to inspect live tool calls, decisions, and observations</span>
                </div>
              ) : activeTab === 'timeline' ? (
                <div className="timeline-stepper animate-fade-in">
                  {/* Step 0: User Request */}
                  <div className="timeline-step">
                    <div className="step-header">
                      <span className="step-type-badge user">USER REQUEST</span>
                    </div>
                    <div className="step-body">"{activeTimeline.raw?.user_goal}"</div>
                  </div>

                  <div className="step-connector">↓</div>

                  {activeTimeline.steps && activeTimeline.steps.length > 0 ? (
                    activeTimeline.steps.map((step, index) => {
                      const typeClass = (step.step_type || 'think').toLowerCase().replace('/', '-')
                      const isToolCall = step.step_type === 'TOOL_CALL'
                      const isToolResult = step.step_type === 'TOOL_RESULT'
                      const isFinal = step.step_type === 'FINAL'

                      return (
                        <React.Fragment key={step.step_id || index}>
                          <div className="timeline-step">
                            <div className="step-header">
                              <span className={`step-type-badge ${typeClass}`}>
                                {step.step_type}
                              </span>
                              {step.tool_name && (
                                <span className="step-tool-name">{step.tool_name}</span>
                              )}
                              {step.timestamp && (
                                <span className="step-timestamp">
                                  {new Date(step.timestamp).toLocaleTimeString()}
                                </span>
                              )}
                            </div>

                            <div className="step-body">
                              {step.content && (
                                <div className="step-content-text">
                                  {renderFormattedContent(step.content)}
                                </div>
                              )}

                              {isToolCall && step.tool_input && (
                                <div className="step-details-card">
                                  <div className="step-details-label">Tool Input:</div>
                                  <pre className="step-details-code">
                                    {JSON.stringify(step.tool_input, null, 2)}
                                  </pre>
                                </div>
                              )}

                              {isToolResult && step.tool_output && (
                                <div className="step-details-card">
                                  <div className="step-details-label">Observation:</div>
                                  <pre className="step-details-code">
                                    {JSON.stringify(step.tool_output, null, 2)}
                                  </pre>
                                </div>
                              )}

                              {isFinal && (
                                <div className="final-answer-box">
                                  {renderFormattedContent(step.content || activeTimeline.response)}
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
                    <div className="timeline-step">
                      <div className="step-header">
                        <span className="step-type-badge final">FINAL ANSWER</span>
                      </div>
                      <div className="step-body">{activeTimeline.response}</div>
                    </div>
                  )}
                </div>
              ) : (
                <pre className="json-viewer animate-fade-in">
                  {JSON.stringify(activeTimeline.raw, null, 2)}
                </pre>
              )}
            </div>
          </section>
        </main>
      ) : (
        /* Mode 2: AG02 Transactional Saga & Undo Studio */
        <main className="saga-workspace animate-fade-in">
          {/* Top World State Ribbon */}
          <div className="world-ribbon glass-panel">
            <div className="world-ribbon-title">
              <div className="world-icon-badge">🌍</div>
              <div>
                <h3>Deterministic Mock World State</h3>
                <span className="world-sub">
                  Live tracking of side effects across execution & reverse compensations
                </span>
              </div>
            </div>

            <div className="world-metrics-grid">
              <div className="world-metric-card">
                <span className="metric-icon">✈️</span>
                <div className="metric-info">
                  <span className="metric-num">{worldCounts.bookings}</span>
                  <span className="metric-lbl">Flight / Hotel Bookings</span>
                </div>
              </div>
              <div className="world-metric-card">
                <span className="metric-icon">💳</span>
                <div className="metric-info">
                  <span className="metric-num">{worldCounts.payments}</span>
                  <span className="metric-lbl">Payment Charges</span>
                </div>
              </div>
              <div className="world-metric-card">
                <span className="metric-icon">🎫</span>
                <div className="metric-info">
                  <span className="metric-num">{worldCounts.tickets}</span>
                  <span className="metric-lbl">Support Tickets</span>
                </div>
              </div>
              <div className="world-metric-card">
                <span className="metric-icon">📧</span>
                <div className="metric-info">
                  <span className="metric-num">{worldCounts.emails}</span>
                  <span className="metric-lbl">Emails Dispatched</span>
                </div>
              </div>
              <div className="world-metric-card state-badge-card">
                <span className="metric-icon">{worldIsClean ? '🛡️' : '⚡'}</span>
                <div className="metric-info">
                  <span className={`metric-status ${worldIsClean ? 'clean' : 'modified'}`}>
                    {worldIsClean ? 'Pristine / Restored' : 'Active Side Effects'}
                  </span>
                  <span className="metric-lbl">Integrity Status</span>
                </div>
              </div>
            </div>
          </div>

          {/* Saga 2-Column Grid */}
          <div className="workspace-grid saga-grid">
            {/* Left Column: Transaction Launcher, Presets, Undo Hero Button */}
            <section className="glass-panel saga-left-panel">
              <div className="panel-title-bar">
                <span>Transactional Workflow Engine</span>
                {currentTx && (
                  <span className={`tx-status-badge ${currentTx.status?.toLowerCase()}`}>
                    {currentTx.status}
                  </span>
                )}
              </div>

              {sagaError && (
                <div className="error-banner animate-fade-in">
                  <span>⚠️ {sagaError}</span>
                </div>
              )}

              {/* Pending Approval Modal/Card */}
              {pendingApproval && (
                <div className="approval-card animate-fade-in">
                  <div className="approval-header">
                    <span>🛑 Irreversible Action Authorization</span>
                    <span className="approval-tag">Human-in-the-Loop</span>
                  </div>
                  <p>
                    Tool <strong>{pendingApproval.tool_name}</strong> is non-compensable and
                    requires explicit approval to execute.
                  </p>
                  <div className="approval-actions">
                    <button
                      className="btn-approve"
                      onClick={() => handleApprovalDecision(pendingApproval.approval_id, true)}
                      disabled={isSagaRunning}
                    >
                      ✅ Approve & Dispatch
                    </button>
                    <button
                      className="btn-reject"
                      onClick={() => handleApprovalDecision(pendingApproval.approval_id, false)}
                      disabled={isSagaRunning}
                    >
                      ❌ Reject & Rollback
                    </button>
                  </div>
                </div>
              )}

              {/* HERO UNDO BUTTON CARD */}
              {currentTx && (
                <div
                  className={`hero-undo-card animate-fade-in ${
                    currentTx.status === 'COMPENSATED' ? 'restored' : ''
                  }`}
                >
                  <div className="undo-card-top">
                    <div>
                      <h4>The Agent With An Undo Button</h4>
                      <p className="undo-card-tx-meta">
                        Transaction: <code>{currentTx.transaction_id}</code> • Steps:{' '}
                        {currentTx.steps?.length || 0}
                      </p>
                    </div>
                    {currentTx.world_restored && (
                      <span className="restored-badge">✨ WORLD FULLY RESTORED</span>
                    )}
                  </div>

                  <button
                    className="hero-undo-button"
                    onClick={() => handleUndo(currentTx.transaction_id)}
                    disabled={
                      isSagaRunning ||
                      currentTx.status === 'COMPENSATED' ||
                      currentTx.status === 'ROLLBACK_FAILED'
                    }
                  >
                    <span className="undo-btn-icon">⏪</span>
                    <div className="undo-btn-content">
                      <span className="undo-btn-title">
                        {currentTx.status === 'COMPENSATED'
                          ? 'TRANSACTION COMPENSATED'
                          : 'UNDO TRANSACTION'}
                      </span>
                      <span className="undo-btn-sub">
                        {currentTx.status === 'COMPENSATED'
                          ? 'All state mutations were reverted in reverse order'
                          : 'Trigger exact compensating actions & restore world state'}
                      </span>
                    </div>
                  </button>

                  {/* Crash recovery button if transaction crashed */}
                  {currentTx.status === 'CRASHED' && (
                    <button
                      className="hero-recover-button"
                      onClick={() => handleRecover(currentTx.transaction_id)}
                      disabled={isSagaRunning}
                    >
                      <span>🔄 Resume & Recover from SQLite Durable Log</span>
                    </button>
                  )}
                </div>
              )}

              {/* Scenario Presets */}
              <div className="presets-container">
                <label className="section-label">Select a Transaction Scenario:</label>
                <div className="scenario-grid">
                  {sagaScenarios.map((sc, idx) => (
                    <button
                      key={idx}
                      className="scenario-chip"
                      onClick={() => {
                        if (sc.action === 'crash') {
                          handleSimulateCrash()
                        } else {
                          setSagaGoal(sc.goal)
                          setFaultStep(sc.fault)
                          setFailRollback(sc.failRollback)
                          handleRunSaga(sc.goal, sc.fault, sc.failRollback)
                        }
                      }}
                      disabled={isSagaRunning}
                    >
                      <span className="sc-title">{sc.title}</span>
                      <span className="sc-desc">{sc.desc}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Fault Injection Panel */}
              <div className="fault-config-box">
                <label className="section-label">Fault Injection & Rollback Controls:</label>
                <div className="fault-pills">
                  <button
                    className={`fault-pill ${faultStep === null ? 'active' : ''}`}
                    onClick={() => setFaultStep(null)}
                  >
                    No Fault
                  </button>
                  <button
                    className={`fault-pill ${faultStep === 1 ? 'active' : ''}`}
                    onClick={() => setFaultStep(1)}
                  >
                    Fail Step 1
                  </button>
                  <button
                    className={`fault-pill ${faultStep === 2 ? 'active' : ''}`}
                    onClick={() => setFaultStep(2)}
                  >
                    Fail Step 2 (Payment)
                  </button>
                  <button
                    className={`fault-pill ${faultStep === 3 ? 'active' : ''}`}
                    onClick={() => setFaultStep(3)}
                  >
                    Fail Step 3
                  </button>
                  <button
                    className={`fault-pill ${failRollback ? 'active warning' : ''}`}
                    onClick={() => setFailRollback(!failRollback)}
                  >
                    {failRollback ? '⚠️ Fail Rollback: ON' : 'Fail During Rollback'}
                  </button>
                </div>
              </div>

              {/* Custom Goal Input */}
              <div className="saga-input-box">
                <textarea
                  className="chat-textarea"
                  value={sagaGoal}
                  onChange={(e) => setSagaGoal(e.target.value)}
                  placeholder="Enter custom transactional goal..."
                  rows={2}
                  disabled={isSagaRunning}
                />
                <button
                  className="send-button saga-run-btn"
                  onClick={() => handleRunSaga()}
                  disabled={isSagaRunning || !sagaGoal.trim()}
                >
                  {isSagaRunning ? 'Executing...' : 'Run Transaction ➔'}
                </button>
              </div>
            </section>

            {/* Right Column: Saga Stepper & Verification Matrix */}
            <section className="glass-panel saga-right-panel">
              <div className="timeline-header">
                <div className="tab-group">
                  <button
                    className={`tab-btn ${sagaTab === 'stepper' ? 'active' : ''}`}
                    onClick={() => setSagaTab('stepper')}
                  >
                    Execution Stepper ({currentTx?.steps?.length || 0})
                  </button>
                  <button
                    className={`tab-btn ${sagaTab === 'metrics' ? 'active' : ''}`}
                    onClick={() => setSagaTab('metrics')}
                  >
                    State Matrix
                  </button>
                  <button
                    className={`tab-btn ${sagaTab === 'log' ? 'active' : ''}`}
                    onClick={() => setSagaTab('log')}
                  >
                    Durable Log
                  </button>
                </div>
              </div>

              <div className="timeline-content">
                {sagaTab === 'stepper' && (
                  <div className="saga-steps-list">
                    {!currentTx || !currentTx.steps || currentTx.steps.length === 0 ? (
                      <div className="timeline-empty">
                        <div className="empty-icon">🔄</div>
                        <p>No active transaction</p>
                        <span>
                          Choose a preset scenario on the left to run forward tools and test the Undo button
                        </span>
                      </div>
                    ) : (
                      currentTx.steps.map((st, idx) => (
                        <div
                          key={idx}
                          className={`saga-step-card status-${st.status?.toLowerCase()}`}
                        >
                          <div className="step-card-header">
                            <span className="step-index">Step {idx + 1}</span>
                            <span className="step-tool-name">{st.tool_name}</span>
                            <span className={`step-badge ${st.status?.toLowerCase()}`}>
                              {st.status}
                            </span>
                          </div>

                          <div className="step-card-body">
                            <div className="step-info-row">
                              <span className="info-key">Compensation Tool:</span>
                              <span className="info-val">
                                {st.compensation_tool || (st.is_reversible ? 'None' : '❌ Irreversible')}
                              </span>
                            </div>
                            <div className="step-info-row">
                              <span className="info-key">Reversibility:</span>
                              <span
                                className={`info-val ${
                                  st.is_reversible ? 'text-reversible' : 'text-irreversible'
                                }`}
                              >
                                {st.is_reversible ? '✅ Reversible' : '⚠️ Irreversible (Requires Approval)'}
                              </span>
                            </div>
                            {st.error && (
                              <div className="step-error-box">
                                <span>⚠️ Error: {st.error}</span>
                              </div>
                            )}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                )}

                {sagaTab === 'metrics' && (
                  <div className="matrix-container">
                    {!worldMetrics ? (
                      <div className="timeline-empty">
                        <div className="empty-icon">📊</div>
                        <p>Run a transaction to view Before / During / After world state metrics</p>
                      </div>
                    ) : (
                      <div className="matrix-table-card">
                        <table className="matrix-table">
                          <thead>
                            <tr>
                              <th>Resource Entity</th>
                              <th>Before Exec</th>
                              <th>Peak (During)</th>
                              <th>After / Restored</th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr>
                              <td>✈️ / 🏨 Bookings</td>
                              <td>{worldMetrics.before?.bookings ?? 0}</td>
                              <td>{worldMetrics.during?.bookings ?? 0}</td>
                              <td>{worldMetrics.after?.bookings ?? 0}</td>
                            </tr>
                            <tr>
                              <td>💳 Payment Charges</td>
                              <td>{worldMetrics.before?.payments ?? 0}</td>
                              <td>{worldMetrics.during?.payments ?? 0}</td>
                              <td>{worldMetrics.after?.payments ?? 0}</td>
                            </tr>
                            <tr>
                              <td>🎫 Support Tickets</td>
                              <td>{worldMetrics.before?.tickets ?? 0}</td>
                              <td>{worldMetrics.during?.tickets ?? 0}</td>
                              <td>{worldMetrics.after?.tickets ?? 0}</td>
                            </tr>
                            <tr>
                              <td>📧 Emails Sent</td>
                              <td>{worldMetrics.before?.emails ?? 0}</td>
                              <td>{worldMetrics.during?.emails ?? 0}</td>
                              <td>{worldMetrics.after?.emails ?? 0}</td>
                            </tr>
                          </tbody>
                        </table>

                        <div className="matrix-summary">
                          <span>Status:</span>
                          <span
                            className={`summary-status ${
                              worldMetrics.world_restored ? 'clean' : ''
                            }`}
                          >
                            {worldMetrics.world_restored
                              ? '✨ WORLD FULLY RESTORED (All Compensations Succeeded)'
                              : worldMetrics.restoration_status || 'Modified State'}
                          </span>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {sagaTab === 'log' && (
                  <div className="raw-json-inspector">
                    <pre>
                      {JSON.stringify(
                        {
                          current_transaction: currentTx,
                          world_metrics: worldMetrics,
                          world_state: worldState,
                        },
                        null,
                        2
                      )}
                    </pre>
                  </div>
                )}
              </div>
            </section>
          </div>
        </main>
      )}
    </div>
  )
}
