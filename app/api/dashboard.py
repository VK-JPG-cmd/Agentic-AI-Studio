"""AG02 Demo Dashboard HTML UI — 'THE AGENT WITH AN UNDO BUTTON' Control Plane."""

from __future__ import annotations

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AG02 // THE AGENT WITH AN UNDO BUTTON — Transactional Saga Control Plane</title>
  <style>
    :root {
      --bg-base: #070b14;
      --bg-panel: #0e1525;
      --bg-panel-elevated: #141e33;
      --border-subtle: #1e2d4a;
      --border-highlight: #38bdf8;
      --text-primary: #f1f5f9;
      --text-secondary: #94a3b8;
      --text-muted: #64748b;
      --accent-cyan: #06b6d4;
      --accent-blue: #3b82f6;
      --accent-emerald: #10b981;
      --accent-amber: #f59e0b;
      --accent-rose: #f43f5e;
      --accent-purple: #a855f7;
      --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      --font-mono: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      background: radial-gradient(circle at top right, #0f1c36 0%, var(--bg-base) 55%);
      color: var(--text-primary);
      font-family: var(--font-sans);
      min-height: 100vh;
      line-height: 1.5;
      padding-bottom: 48px;
    }

    /* Top Infrastructure Header */
    .topbar {
      background: rgba(14, 21, 37, 0.92);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border-subtle);
      padding: 16px 28px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 50;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 14px;
    }

    .brand-badge {
      background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue));
      color: #030712;
      font-family: var(--font-mono);
      font-weight: 800;
      font-size: 0.82rem;
      padding: 6px 10px;
      border-radius: 6px;
      letter-spacing: 0.08em;
    }

    .brand-title h1 {
      font-size: 1.15rem;
      font-weight: 800;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: #ffffff;
    }

    .brand-title p {
      font-size: 0.78rem;
      color: var(--text-secondary);
      font-family: var(--font-mono);
    }

    .system-pills {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }

    .pill {
      background: var(--bg-panel-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: 999px;
      padding: 6px 13px;
      font-size: 0.76rem;
      font-family: var(--font-mono);
      color: var(--text-secondary);
      display: inline-flex;
      align-items: center;
      gap: 7px;
    }

    .dot-online {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--accent-emerald);
      box-shadow: 0 0 8px var(--accent-emerald);
    }

    /* Main Grid Layout */
    .container {
      max-width: 1520px;
      margin: 24px auto;
      padding: 0 24px;
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 20px;
    }

    .panel {
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      border-radius: 10px;
      padding: 20px;
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.35);
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--border-subtle);
      padding-bottom: 10px;
    }

    .panel-title {
      display: flex;
      align-items: center;
      gap: 9px;
      font-size: 0.85rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: #e2e8f0;
    }

    .section-num {
      background: #1e293b;
      border: 1px solid #334155;
      color: var(--accent-cyan);
      font-family: var(--font-mono);
      font-size: 0.72rem;
      padding: 2px 7px;
      border-radius: 4px;
    }

    .col-12 { grid-column: span 12; }
    .col-8 { grid-column: span 8; }
    .col-6 { grid-column: span 6; }
    .col-4 { grid-column: span 4; }

    @media (max-width: 1100px) {
      .col-8, .col-6, .col-4 { grid-column: span 12; }
    }

    /* Controls & Buttons */
    .request-row {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }

    .request-input {
      flex: 1;
      min-width: 280px;
      background: #060a12;
      border: 1px solid #283a5e;
      color: var(--text-primary);
      padding: 12px 16px;
      border-radius: 8px;
      font-size: 0.95rem;
      font-family: var(--font-sans);
      outline: none;
      transition: border-color 0.15s;
    }

    .request-input:focus {
      border-color: var(--accent-cyan);
    }

    .btn {
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 10px 16px;
      font-size: 0.83rem;
      font-weight: 600;
      font-family: var(--font-sans);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: all 0.15s ease;
    }

    .btn-primary {
      background: linear-gradient(135deg, #0284c7, #2563eb);
      color: #ffffff;
      border-color: #38bdf8;
    }
    .btn-primary:hover { filter: brightness(1.12); }

    .btn-secondary {
      background: var(--bg-panel-elevated);
      color: var(--text-primary);
      border-color: #334155;
    }
    .btn-secondary:hover { border-color: var(--accent-cyan); }

    .btn-undo {
      background: linear-gradient(135deg, #d97706, #b45309);
      color: #fff;
      border-color: #fbbf24;
    }
    .btn-undo:hover { filter: brightness(1.12); }

    .btn-danger {
      background: linear-gradient(135deg, #e11d48, #be123c);
      color: #fff;
      border-color: #fb7185;
    }
    .btn-danger:hover { filter: brightness(1.12); }

    .btn-success {
      background: linear-gradient(135deg, #059669, #047857);
      color: #fff;
      border-color: #34d399;
    }

    .presets-bar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }

    .preset-chip {
      background: #0b1322;
      border: 1px solid #243554;
      color: var(--text-secondary);
      padding: 5px 11px;
      border-radius: 6px;
      font-size: 0.75rem;
      cursor: pointer;
      font-family: var(--font-mono);
    }
    .preset-chip:hover {
      color: #fff;
      border-color: var(--accent-cyan);
    }

    /* Step Cards & Badges */
    .step-list {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .step-card {
      background: var(--bg-panel-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: 8px;
      padding: 12px 15px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }

    .step-main {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .step-idx {
      font-family: var(--font-mono);
      font-size: 0.76rem;
      font-weight: 700;
      color: var(--accent-cyan);
      background: rgba(6, 182, 212, 0.12);
      border: 1px solid rgba(6, 182, 212, 0.35);
      padding: 4px 8px;
      border-radius: 5px;
    }

    .step-title {
      font-weight: 600;
      font-size: 0.92rem;
      color: #f8fafc;
    }

    .step-sub {
      font-family: var(--font-mono);
      font-size: 0.74rem;
      color: var(--text-secondary);
    }

    .badge-group {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }

    .badge {
      font-family: var(--font-mono);
      font-size: 0.72rem;
      font-weight: 600;
      padding: 3px 9px;
      border-radius: 5px;
      border: 1px solid transparent;
    }

    .badge-reversible {
      background: rgba(16, 185, 129, 0.12);
      color: #34d399;
      border-color: rgba(16, 185, 129, 0.35);
    }

    .badge-irreversible {
      background: rgba(244, 63, 94, 0.15);
      color: #fb7185;
      border-color: rgba(244, 63, 94, 0.4);
    }

    .badge-comp {
      background: rgba(59, 130, 246, 0.12);
      color: #60a5fa;
      border-color: rgba(59, 130, 246, 0.35);
    }

    .badge-approval {
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
      border-color: rgba(245, 158, 11, 0.4);
    }

    .badge-status-COMPLETED {
      background: rgba(16, 185, 129, 0.16);
      color: #34d399;
      border-color: rgba(16, 185, 129, 0.45);
    }

    .badge-status-FAILED, .badge-status-ROLLBACK_FAILED {
      background: rgba(244, 63, 94, 0.18);
      color: #fda4af;
      border-color: rgba(244, 63, 94, 0.5);
    }

    .badge-status-COMPENSATED {
      background: rgba(168, 85, 247, 0.16);
      color: #c084fc;
      border-color: rgba(168, 85, 247, 0.45);
    }

    .badge-status-WAITING_FOR_APPROVAL, .badge-status-PARTIAL_ROLLBACK {
      background: rgba(245, 158, 11, 0.18);
      color: #fcd34d;
      border-color: rgba(245, 158, 11, 0.5);
    }

    /* World State Matrix */
    .world-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }

    .world-box {
      background: #080d19;
      border: 1px solid var(--border-subtle);
      border-radius: 8px;
      padding: 14px;
    }

    .world-box h4 {
      font-family: var(--font-mono);
      font-size: 0.76rem;
      text-transform: uppercase;
      color: var(--text-secondary);
      margin-bottom: 10px;
      letter-spacing: 0.05em;
    }

    .metric-line {
      display: flex;
      justify-content: space-between;
      font-family: var(--font-mono);
      font-size: 0.84rem;
      padding: 4px 0;
      border-bottom: 1px dashed #19253d;
    }

    .restored-banner {
      padding: 12px 16px;
      border-radius: 8px;
      font-family: var(--font-mono);
      font-weight: 700;
      font-size: 0.9rem;
      text-align: center;
      letter-spacing: 0.04em;
    }

    .restored-ok {
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid #10b981;
      color: #34d399;
    }

    .restored-warn {
      background: rgba(245, 158, 11, 0.15);
      border: 1px solid #f59e0b;
      color: #fcd34d;
    }

    .restored-danger {
      background: rgba(244, 63, 94, 0.18);
      border: 1px solid #f43f5e;
      color: #fda4af;
    }

    /* Fault Injection Radio Grid */
    .fault-options {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 8px;
    }

    .fault-option {
      background: var(--bg-panel-elevated);
      border: 1px solid var(--border-subtle);
      padding: 9px 12px;
      border-radius: 6px;
      font-family: var(--font-mono);
      font-size: 0.78rem;
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
    }

    .fault-option input {
      accent-color: var(--accent-rose);
    }

    /* Approval Banner */
    .approval-box {
      background: rgba(245, 158, 11, 0.1);
      border: 2px solid var(--accent-amber);
      border-radius: 10px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    /* Transaction Table */
    .log-table-wrap {
      overflow-x: auto;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-family: var(--font-mono);
      font-size: 0.78rem;
    }

    th, td {
      text-align: left;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border-subtle);
    }

    th {
      color: var(--text-secondary);
      text-transform: uppercase;
      font-size: 0.72rem;
      background: #090f1d;
    }

    .decision-meta-bar {
      background: #090f1d;
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 9px 12px;
      font-family: var(--font-mono);
      font-size: 0.75rem;
      color: var(--text-secondary);
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 10px;
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <span class="brand-badge">AG02 // SAGA ENGINE</span>
      <div class="brand-title">
        <h1>THE AGENT WITH AN UNDO BUTTON</h1>
        <p>Transactional Execution Layer &bull; Deterministic Compensation &bull; SQLite Crash Recovery</p>
      </div>
    </div>
    <div class="system-pills">
      <div class="pill"><span class="dot-online"></span> TransactionManager: ACTIVE</div>
      <div class="pill">CoT Exposure: <strong>DISABLED (STRICT)</strong></div>
      <div class="pill" id="active-tx-pill">Active TX: none</div>
    </div>
  </header>

  <main class="container">
    <!-- SECTION 1: USER REQUEST -->
    <section class="panel col-8" id="section-user-request">
      <div class="panel-header">
        <div class="panel-title">
          <span class="section-num">01</span> USER REQUEST
        </div>
        <span style="font-family: var(--font-mono); font-size: 0.74rem; color: var(--text-secondary);">
          LLM Planner &rarr; Policy Validator &rarr; Saga Engine
        </span>
      </div>
      <div class="request-row">
        <input
          id="user-goal-input"
          class="request-input"
          type="text"
          value="Book a hotel, charge payment, create a support ticket."
          placeholder="Enter transactional agent workflow goal..."
        />
        <button class="btn btn-secondary" onclick="generatePlanOnly()">1. Generate Plan</button>
        <button class="btn btn-primary" onclick="executeWorkflow()">2. Execute Saga Transaction</button>
        <button class="btn btn-undo" onclick="triggerManualUndo()">Undo Button &#x21A9;&#xFE0F;</button>
        <button class="btn btn-secondary" onclick="resetWorld()">Reset World</button>
      </div>
      <div class="presets-bar">
        <span style="font-size: 0.75rem; color: var(--text-muted); font-family: var(--font-mono);">PRESETS:</span>
        <button class="preset-chip" onclick="setPreset('Book a hotel, charge payment, create a support ticket.', 0, false)">
          3-Step: Booking + Payment + Ticket
        </button>
        <button class="preset-chip" onclick="setPreset('Book a hotel, charge payment, create a support ticket.', 3, false)">
          Fail at Step 3 &rarr; Auto Rollback
        </button>
        <button class="preset-chip" onclick="setPreset('Book a hotel, charge payment, create a support ticket, and send confirmation email.', 0, false)">
          4-Step with Irreversible Email (Approval Gate)
        </button>
      </div>
      <div class="decision-meta-bar" id="decision-metadata-bar">
        <span>Planner: <strong>AG02StructuredPlanner</strong></span>
        <span>Validation: <strong>READY</strong></span>
        <span>Chain-of-Thought: <strong>REDACTED / NEVER STORED</strong></span>
      </div>
    </section>

    <!-- SECTION 6 & 7: FAILURE INJECTION PANEL & CRASH SIMULATION -->
    <section class="panel col-4" id="section-fault-injection">
      <div class="panel-header">
        <div class="panel-title">
          <span class="section-num">06</span> FAILURE INJECTION PANEL
        </div>
        <span style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--accent-rose);">DETERMINISTIC</span>
      </div>
      <div class="fault-options">
        <label class="fault-option">
          <input type="radio" name="fail_step" value="0" checked /> No Step Failure
        </label>
        <label class="fault-option">
          <input type="radio" name="fail_step" value="1" /> Fail at step 1
        </label>
        <label class="fault-option">
          <input type="radio" name="fail_step" value="2" /> Fail at step 2
        </label>
        <label class="fault-option">
          <input type="radio" name="fail_step" value="3" /> Fail at step 3
        </label>
        <label class="fault-option">
          <input type="radio" name="fail_step" value="4" /> Fail at step 4
        </label>
        <label class="fault-option">
          <input type="checkbox" id="fail-during-rollback" /> Fail during rollback
        </label>
      </div>

      <div class="panel-header" style="margin-top: 8px;">
        <div class="panel-title">
          <span class="section-num">07</span> CRASH SIMULATION &amp; RECOVERY
        </div>
      </div>
      <div style="display: flex; gap: 8px; flex-wrap: wrap;">
        <button class="btn btn-danger" onclick="simulateCrash()">Simulate Crash</button>
        <button class="btn btn-secondary" onclick="recoverAfterCrash('rollback')">Recover: Rollback</button>
        <button class="btn btn-secondary" onclick="recoverAfterCrash('resume')">Recover: Resume</button>
      </div>
      <div id="crash-status-note" style="font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-secondary);">
        SQLite WAL Persistence: Ready for mid-flight crash recovery.
      </div>
    </section>

    <!-- SECTION 9: IRREVERSIBLE ACTION APPROVAL GATE -->
    <section class="panel col-12" id="section-irreversible-approval">
      <div class="panel-header">
        <div class="panel-title">
          <span class="section-num">09</span> IRREVERSIBLE ACTION &amp; HUMAN-IN-THE-LOOP APPROVAL GATE
        </div>
        <span id="approval-gate-badge" class="badge badge-comp">STANDBY</span>
      </div>
      <div id="approval-dialog-container" class="approval-box">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;">
          <div>
            <div style="font-family: var(--font-mono); font-weight: 700; color: #fbbf24; font-size: 0.88rem;">
              &#x26A0;&#xFE0F; APPROVAL_REQUIRED // Tool: <span id="approval-tool-name">send_email</span>
            </div>
            <div style="font-size: 0.84rem; color: var(--text-primary); margin-top: 4px;" id="approval-reason-text">
              Reason: This action cannot be undone. Irreversible steps are placed LAST and gated on explicit approval.
            </div>
            <div style="font-family: var(--font-mono); font-size: 0.74rem; color: var(--text-secondary); margin-top: 4px;" id="approval-step-meta">
              Step ID: awaiting irreversible step | Reversible: false | Compensation: None
            </div>
          </div>
          <div style="display: flex; gap: 10px;">
            <button class="btn btn-success" id="btn-approve-step" onclick="decideApproval(true)">
              Approve &amp; Execute send_email
            </button>
            <button class="btn btn-danger" id="btn-deny-step" onclick="decideApproval(false)">
              Deny &amp; Rollback Reversible Steps
            </button>
          </div>
        </div>
      </div>
    </section>

    <!-- SECTION 2: GENERATED PLAN -->
    <section class="panel col-4" id="section-generated-plan">
      <div class="panel-header">
        <div class="panel-title">
          <span class="section-num">02</span> GENERATED PLAN
        </div>
        <span style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-secondary);">
          Tool Metadata &amp; Contracts
        </span>
      </div>
      <div class="step-list" id="generated-plan-list">
        <div class="step-card">
          <div>
            <div class="step-title">Step 1 &rarr; Create Booking</div>
            <div class="step-sub">tool: create_booking | compensation: cancel_booking</div>
          </div>
          <div class="badge-group">
            <span class="badge badge-reversible">Reversible</span>
            <span class="badge badge-comp">Undo: cancel_booking</span>
            <span class="badge">Approval: No</span>
          </div>
        </div>
        <div class="step-card">
          <div>
            <div class="step-title">Step 2 &rarr; Charge Payment</div>
            <div class="step-sub">tool: charge_payment | compensation: refund_payment</div>
          </div>
          <div class="badge-group">
            <span class="badge badge-reversible">Reversible</span>
            <span class="badge badge-comp">Undo: refund_payment</span>
            <span class="badge">Approval: No</span>
          </div>
        </div>
        <div class="step-card">
          <div>
            <div class="step-title">Step 3 &rarr; Create Ticket</div>
            <div class="step-sub">tool: create_ticket | compensation: delete_ticket</div>
          </div>
          <div class="badge-group">
            <span class="badge badge-reversible">Reversible</span>
            <span class="badge badge-comp">Undo: delete_ticket</span>
            <span class="badge">Approval: No</span>
          </div>
        </div>
      </div>
    </section>

    <!-- SECTION 3: LIVE EXECUTION TIMELINE -->
    <section class="panel col-4" id="section-execution-timeline">
      <div class="panel-header">
        <div class="panel-title">
          <span class="section-num">03</span> LIVE EXECUTION TIMELINE
        </div>
        <span style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--accent-cyan);">
          FORWARD SAGA
        </span>
      </div>
      <div class="step-list" id="execution-timeline-list">
        <div class="step-card">
          <span class="step-title">Create Booking</span>
          <span class="badge badge-status-COMPLETED">&#x2705; READY</span>
        </div>
        <div class="step-card">
          <span class="step-title">Charge Payment</span>
          <span class="badge badge-status-COMPLETED">&#x2705; READY</span>
        </div>
        <div class="step-card">
          <span class="step-title">Create Ticket</span>
          <span class="badge badge-status-COMPLETED">&#x2705; READY</span>
        </div>
      </div>
    </section>

    <!-- SECTION 4: AUTOMATIC ROLLBACK -->
    <section class="panel col-4" id="section-automatic-rollback">
      <div class="panel-header">
        <div class="panel-title">
          <span class="section-num">04</span> AUTOMATIC ROLLBACK
        </div>
        <span style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--accent-purple);">
          STRICT REVERSE ORDER &#x21A9;&#xFE0F;
        </span>
      </div>
      <div class="step-list" id="rollback-timeline-list">
        <div class="step-card">
          <span class="step-title">Delete Ticket</span>
          <span class="badge badge-comp">&#x21A9;&#xFE0F; delete_ticket</span>
        </div>
        <div class="step-card">
          <span class="step-title">Refund Payment</span>
          <span class="badge badge-comp">&#x21A9;&#xFE0F; refund_payment</span>
        </div>
        <div class="step-card">
          <span class="step-title">Cancel Booking</span>
          <span class="badge badge-comp">&#x21A9;&#xFE0F; cancel_booking</span>
        </div>
      </div>
    </section>

    <!-- SECTION 5: WORLD STATE -->
    <section class="panel col-12" id="section-world-state">
      <div class="panel-header">
        <div class="panel-title">
          <span class="section-num">05</span> WORLD STATE (BEFORE / DURING / AFTER ROLLBACK)
        </div>
        <span style="font-family: var(--font-mono); font-size: 0.75rem; color: var(--accent-emerald);">
          Deterministic MockWorld Snapshot Verification
        </span>
      </div>
      <div class="world-grid">
        <div class="world-box">
          <h4>Before Execution</h4>
          <div class="metric-line"><span>Bookings:</span><strong id="wb-bookings">0</strong></div>
          <div class="metric-line"><span>Payments:</span><strong id="wb-payments">0</strong></div>
          <div class="metric-line"><span>Tickets:</span><strong id="wb-tickets">0</strong></div>
          <div class="metric-line"><span>Emails:</span><strong id="wb-emails">0</strong></div>
        </div>
        <div class="world-box">
          <h4>During Execution (Peak)</h4>
          <div class="metric-line"><span>Bookings:</span><strong id="wd-bookings">0</strong></div>
          <div class="metric-line"><span>Payments:</span><strong id="wd-payments">0</strong></div>
          <div class="metric-line"><span>Tickets:</span><strong id="wd-tickets">0</strong></div>
          <div class="metric-line"><span>Emails:</span><strong id="wd-emails">0</strong></div>
        </div>
        <div class="world-box">
          <h4>After Rollback / Final</h4>
          <div class="metric-line"><span>Bookings:</span><strong id="wa-bookings">0</strong></div>
          <div class="metric-line"><span>Payments:</span><strong id="wa-payments">0</strong></div>
          <div class="metric-line"><span>Tickets:</span><strong id="wa-tickets">0</strong></div>
          <div class="metric-line"><span>Emails:</span><strong id="wa-emails">0</strong></div>
        </div>
      </div>
      <div id="world-restored-banner" class="restored-banner restored-ok">
        WORLD FULLY RESTORED &#x2705;
      </div>
    </section>

    <!-- SECTION 8: TRANSACTION LOG -->
    <section class="panel col-12" id="section-transaction-log">
      <div class="panel-header">
        <div class="panel-title">
          <span class="section-num">08</span> DURABLE TRANSACTION LOG (SQLITE AUDIT TRAIL)
        </div>
        <span id="tx-log-summary" style="font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-secondary);">
          Transaction ID: —
        </span>
      </div>
      <div class="log-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Transaction ID</th>
              <th>Step #</th>
              <th>Step ID</th>
              <th>Tool Name</th>
              <th>Execution Status</th>
              <th>Compensation Tool</th>
              <th>Compensation Status</th>
              <th>Timestamp (UTC)</th>
            </tr>
          </thead>
          <tbody id="transaction-log-tbody">
            <tr>
              <td colspan="8" style="color: var(--text-muted); text-align: center;">
                Execute a workflow or click "Simulate Crash" to inspect durable SQLite step records.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>

  <script>
    let currentTransactionId = null;
    let currentApprovalId = null;

    const TOOL_LABELS = {
      create_booking: "Create Booking",
      cancel_booking: "Cancel Booking",
      charge_payment: "Charge Payment",
      refund_payment: "Refund Payment",
      create_ticket: "Create Ticket",
      delete_ticket: "Delete Ticket",
      send_email: "Send Email",
      send_notification: "Send Notification",
      book_flight: "Book Flight",
      reserve_hotel: "Reserve Hotel",
    };

    function prettyTool(name) {
      return TOOL_LABELS[name] || name;
    }

    function getSelectedFailStep() {
      const checked = document.querySelector('input[name="fail_step"]:checked');
      return checked ? parseInt(checked.value, 10) : 0;
    }

    function setPreset(goalText, failStep, failRollback) {
      document.getElementById('user-goal-input').value = goalText;
      const radios = document.querySelectorAll('input[name="fail_step"]');
      radios.forEach(r => { r.checked = (parseInt(r.value, 10) === failStep); });
      document.getElementById('fail-during-rollback').checked = Boolean(failRollback);
      generatePlanOnly();
    }

    function renderPlanSteps(steps, decisionMeta) {
      const container = document.getElementById('generated-plan-list');
      container.innerHTML = '';
      steps.forEach((step, idx) => {
        const stepNum = idx + 1;
        const toolName = step.tool_name || step.tool;
        const isRev = step.is_reversible !== undefined ? step.is_reversible : !step.irreversible;
        const compTool = step.compensation_tool || 'None (Irreversible)';
        const reqAppr = Boolean(step.requires_approval || step.approval_required);

        const card = document.createElement('div');
        card.className = 'step-card';
        card.innerHTML = `
          <div>
            <div class="step-title">Step ${stepNum} &rarr; ${prettyTool(toolName)}</div>
            <div class="step-sub">tool: ${toolName} | compensation: ${compTool}</div>
          </div>
          <div class="badge-group">
            <span class="badge ${isRev ? 'badge-reversible' : 'badge-irreversible'}">
              ${isRev ? 'Reversible' : 'Irreversible'}
            </span>
            <span class="badge badge-comp">Undo: ${compTool}</span>
            <span class="badge ${reqAppr ? 'badge-approval' : ''}">
              Approval: ${reqAppr ? 'REQUIRED &#x26A0;&#xFE0F;' : 'Auto'}
            </span>
          </div>
        `;
        container.appendChild(card);
      });

      if (decisionMeta && Object.keys(decisionMeta).length > 0) {
        const metaBar = document.getElementById('decision-metadata-bar');
        metaBar.innerHTML = `
          <span>Planner: <strong>${decisionMeta.planner || 'AG02StructuredPlanner'}</strong></span>
          <span>Selected Tools: <strong>${(decisionMeta.selected_tools || []).join(' &rarr; ')}</strong></span>
          <span>Validation: <strong>${decisionMeta.validation_status || 'VALIDATED'}</strong></span>
          <span>Chain-of-Thought: <strong>STRIPPED / HIDDEN</strong></span>
        `;
      }
    }

    function renderTransactionResult(data) {
      const tx = data.transaction;
      const metrics = data.world_metrics;
      currentTransactionId = tx.transaction_id;
      document.getElementById('active-tx-pill').textContent = `Active TX: ${tx.transaction_id} (${tx.status})`;

      // Section 2: Generated Plan
      renderPlanSteps(tx.steps, tx.decision_metadata);

      // Section 3: Live Execution Timeline
      const execList = document.getElementById('execution-timeline-list');
      execList.innerHTML = '';
      tx.steps.forEach((step, idx) => {
        let icon = '&#x23F3;';
        if (step.status === 'COMPLETED' || step.status === 'COMPENSATED') icon = '&#x2705;';
        else if (step.status === 'FAILED' || step.status === 'ROLLBACK_FAILED') icon = '&#x274C; FAILED';
        else if (step.status === 'WAITING_FOR_APPROVAL') icon = '&#x26A0;&#xFE0F; WAITING_FOR_APPROVAL';
        else if (step.status === 'PENDING') icon = '&#x23F8;&#xFE0F; NOT EXECUTED';

        const card = document.createElement('div');
        card.className = 'step-card';
        card.innerHTML = `
          <div>
            <div class="step-title">${prettyTool(step.tool_name)}</div>
            <div class="step-sub">${step.step_id} &bull; attempts: ${step.attempt_count}</div>
          </div>
          <span class="badge badge-status-${step.status}">${icon} (${step.status})</span>
        `;
        execList.appendChild(card);
      });

      // Section 4: Automatic Rollback
      const rbList = document.getElementById('rollback-timeline-list');
      rbList.innerHTML = '';
      if (tx.compensation_history && tx.compensation_history.length > 0) {
        tx.compensation_history.forEach(comp => {
          const compName = comp.compensation_tool || `undo_${comp.tool_name}`;
          const card = document.createElement('div');
          card.className = 'step-card';
          card.innerHTML = `
            <div>
              <div class="step-title">${prettyTool(compName)}</div>
              <div class="step-sub">Undoing step ${comp.step_index + 1} (${comp.tool_name})</div>
            </div>
            <span class="badge badge-status-${comp.status}">&#x21A9;&#xFE0F; ${comp.status}</span>
          `;
          rbList.appendChild(card);
        });
      } else {
        rbList.innerHTML = `
          <div class="step-card">
            <span class="step-sub">No rollback triggered yet (or no reversible side effects to undo).</span>
          </div>
        `;
      }

      // Section 5: World State (Before / During / After)
      if (metrics) {
        document.getElementById('wb-bookings').textContent = metrics.before.bookings;
        document.getElementById('wb-payments').textContent = metrics.before.payments;
        document.getElementById('wb-tickets').textContent = metrics.before.tickets;
        document.getElementById('wb-emails').textContent = metrics.before.emails;

        document.getElementById('wd-bookings').textContent = metrics.during.bookings;
        document.getElementById('wd-payments').textContent = metrics.during.payments;
        document.getElementById('wd-tickets').textContent = metrics.during.tickets;
        document.getElementById('wd-emails').textContent = metrics.during.emails;

        document.getElementById('wa-bookings').textContent = metrics.after.bookings;
        document.getElementById('wa-payments').textContent = metrics.after.payments;
        document.getElementById('wa-tickets').textContent = metrics.after.tickets;
        document.getElementById('wa-emails').textContent = metrics.after.emails;

        const banner = document.getElementById('world-restored-banner');
        if (tx.status === 'COMPENSATED' || (tx.world_restored && tx.restoration_status === 'FULLY_RESTORED')) {
          banner.className = 'restored-banner restored-ok';
          banner.innerHTML = 'WORLD FULLY RESTORED &#x2705;';
        } else if (tx.status === 'PARTIAL_ROLLBACK') {
          banner.className = 'restored-banner restored-warn';
          banner.innerHTML = 'PARTIAL_ROLLBACK &#x26A0;&#xFE0F; — Reversible steps undone; irreversible side effect cannot be undone';
        } else if (tx.status === 'ROLLBACK_FAILED') {
          banner.className = 'restored-banner restored-danger';
          banner.innerHTML = 'ROLLBACK_FAILED &#x1F6A8; — Manual intervention required';
        } else if (tx.status === 'WAITING_FOR_APPROVAL') {
          banner.className = 'restored-banner restored-warn';
          banner.innerHTML = 'PAUSED AT APPROVAL GATE &#x23F8;&#xFE0F; — Awaiting explicit user approval for irreversible tool';
        } else if (tx.status === 'RUNNING') {
          banner.className = 'restored-banner restored-danger';
          banner.innerHTML = 'PROCESS CRASH DETECTED &#x1F4A5; — Transaction incomplete in SQLite; run Crash Recovery';
        } else {
          banner.className = 'restored-banner restored-ok';
          banner.innerHTML = 'TRANSACTION COMMITTED &#x2705; (Click Undo Button &#x21A9;&#xFE0F; to roll back)';
        }
      }

      // Section 9: Irreversible Action Approval Dialog
      const approvalBadge = document.getElementById('approval-gate-badge');
      const waitingStep = tx.steps.find(s => s.status === 'WAITING_FOR_APPROVAL');
      if (tx.status === 'WAITING_FOR_APPROVAL' && waitingStep) {
        currentApprovalId = waitingStep.approval_id;
        approvalBadge.textContent = 'WAITING_FOR_APPROVAL';
        approvalBadge.className = 'badge badge-approval';
        document.getElementById('approval-tool-name').textContent = waitingStep.tool_name;
        document.getElementById('approval-reason-text').textContent =
          (tx.approval_request && tx.approval_request.reason) || 'This action cannot be undone.';
        document.getElementById('approval-step-meta').textContent =
          `Step ID: ${waitingStep.step_id} | Approval ID: ${waitingStep.approval_id} | Irreversible: ${waitingStep.irreversible}`;
      } else {
        currentApprovalId = null;
        approvalBadge.textContent = 'STANDBY';
        approvalBadge.className = 'badge badge-comp';
      }

      // Section 8: Transaction Log Table
      document.getElementById('tx-log-summary').textContent =
        `Transaction ID: ${tx.transaction_id} | Status: ${tx.status} | Restoration: ${tx.restoration_status}`;
      const tbody = document.getElementById('transaction-log-tbody');
      tbody.innerHTML = '';
      tx.steps.forEach(step => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${tx.transaction_id}</td>
          <td>#${step.step_number}</td>
          <td>${step.step_id}</td>
          <td>${step.tool_name}</td>
          <td><span class="badge badge-status-${step.status}">${step.status}</span></td>
          <td>${step.compensation_tool || '—'}</td>
          <td>${step.compensation_status}</td>
          <td>${step.updated_at}</td>
        `;
        tbody.appendChild(tr);
      });
    }

    async function generatePlanOnly() {
      const goal = document.getElementById('user-goal-input').value;
      const res = await fetch('/agent/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal })
      });
      const data = await res.json();
      if (data.plan) {
        renderPlanSteps(data.plan.steps, data.plan.decision_metadata);
      }
    }

    async function executeWorkflow() {
      const goal = document.getElementById('user-goal-input').value;
      const failStep = getSelectedFailStep();
      const failRollback = document.getElementById('fail-during-rollback').checked;
      const res = await fetch('/demo/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          goal,
          fail_at_step: failStep > 0 ? failStep : null,
          fail_during_rollback: failRollback
        })
      });
      const data = await res.json();
      renderTransactionResult(data);
    }

    async function triggerManualUndo() {
      if (!currentTransactionId) return;
      const res = await fetch(`/transactions/${currentTransactionId}/undo`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Manual Undo Button clicked in AG02 Demo Dashboard' })
      });
      if (res.ok) {
        const data = await res.json();
        renderTransactionResult(data);
      }
    }

    async function decideApproval(approved) {
      if (!currentApprovalId) return;
      const res = await fetch(`/approvals/${currentApprovalId}/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          approved,
          reason: approved ? 'User approved irreversible action in UI' : 'User denied irreversible action in UI'
        })
      });
      if (res.ok) {
        const data = await res.json();
        renderTransactionResult(data);
      }
    }

    async function simulateCrash() {
      const goal = document.getElementById('user-goal-input').value;
      const res = await fetch('/demo/simulate-crash', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal, crash_after_step: 2 })
      });
      const data = await res.json();
      document.getElementById('crash-status-note').innerHTML =
        `<strong style="color: #fda4af;">CRASHED after Step 2!</strong> TX <code>${data.transaction.transaction_id}</code> persisted in SQLite as RUNNING. Click Recover below.`;
      renderTransactionResult(data);
    }

    async function recoverAfterCrash(strategy) {
      const res = await fetch('/demo/recover', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          transaction_id: currentTransactionId,
          strategy
        })
      });
      if (res.ok) {
        const data = await res.json();
        document.getElementById('crash-status-note').innerHTML =
          `<strong style="color: #34d399;">RECOVERED (${strategy.toUpperCase()})!</strong> No committed side effects were duplicated.`;
        renderTransactionResult(data);
      }
    }

    async function resetWorld() {
      await fetch('/world/reset', { method: 'POST' });
      document.getElementById('wb-bookings').textContent = '0';
      document.getElementById('wb-payments').textContent = '0';
      document.getElementById('wb-tickets').textContent = '0';
      document.getElementById('wb-emails').textContent = '0';
      document.getElementById('wd-bookings').textContent = '0';
      document.getElementById('wd-payments').textContent = '0';
      document.getElementById('wd-tickets').textContent = '0';
      document.getElementById('wd-emails').textContent = '0';
      document.getElementById('wa-bookings').textContent = '0';
      document.getElementById('wa-payments').textContent = '0';
      document.getElementById('wa-tickets').textContent = '0';
      document.getElementById('wa-emails').textContent = '0';
      document.getElementById('world-restored-banner').className = 'restored-banner restored-ok';
      document.getElementById('world-restored-banner').innerHTML = 'WORLD FULLY RESTORED &#x2705;';
      generatePlanOnly();
    }
  </script>
</body>
</html>
"""
