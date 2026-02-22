export function getStyles(port: number): string {
  return `
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #09090b;
    --surface: rgba(24, 24, 27, 0.55);
    --surface-solid: #18181b;
    --surface-2: rgba(39, 39, 42, 0.5);
    --border: #3f3f46;
    --border-subtle: rgba(63, 63, 70, 0.5);
    --text: #fafafa;
    --text-secondary: #a1a1aa;
    --muted: #71717a;
    --accent: #6366f1;
    --accent-hover: #818cf8;
    --accent-dim: #6366f122;
    --green: #22c55e;
    --green-dim: #22c55e22;
    --yellow: #eab308;
    --yellow-dim: #eab30822;
    --red: #ef4444;
    --red-dim: #ef444422;
    --orange: #f97316;
    --purple: #a855f7;
    --purple-dim: #a855f722;
    --radius: 0px;
    --radius-sm: 0px;
    --radius-xs: 0px;
    --shadow: none;
    --shadow-lg: 0 4px 12px rgba(0,0,0,0.3);
    --transition: all 0.2s ease;
    --glass: blur(12px) saturate(1.2);
  }

  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--text);
    font-size: 14px;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }

  .container { max-width: 1400px; margin: 0 auto; padding: 20px 24px; position: relative; z-index: 1; }

  code, .mono {
    font-family: 'JetBrains Mono', 'SF Mono', monospace;
    font-size: 0.9em;
  }

  /* ===== Hero Header ===== */
  .hero {
    background: var(--surface);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    padding: 20px 24px 0;
    margin-bottom: 20px;
  }
  .hero-top {
    display: flex;
    align-items: center;
    gap: 16px;
    padding-bottom: 16px;
  }
  .hero-brand {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .hero-logo {
    width: 36px;
    height: 36px;
    background: var(--accent);
    border-radius: var(--radius-sm);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    font-weight: 700;
    color: white;
  }
  .hero-title {
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.02em;
  }
  .hero-subtitle {
    font-size: 13px;
    color: var(--muted);
    margin-top: -2px;
  }
  .connection {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--muted);
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .dot-ok { background: var(--green); box-shadow: 0 0 8px var(--green); }
  .dot-err { background: var(--red); box-shadow: 0 0 8px var(--red); }

  /* Tab bar */
  .tab-bar {
    display: flex;
    gap: 0;
    border-top: 1px solid var(--border-subtle);
  }
  .tab {
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 500;
    color: var(--muted);
    cursor: pointer;
    border-bottom: 2px solid transparent;
    transition: var(--transition);
    background: none;
    border-top: none;
    border-left: none;
    border-right: none;
    font-family: inherit;
  }
  .tab:hover { color: var(--text-secondary); }
  .tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  /* Breadcrumb nav (project detail) */
  .breadcrumb {
    display: flex;
    align-items: center;
    gap: 8px;
    padding-bottom: 16px;
  }
  .breadcrumb-link {
    color: var(--accent);
    cursor: pointer;
    font-size: 13px;
    background: none;
    border: none;
    font-family: inherit;
    padding: 0;
    font-weight: 500;
  }
  .breadcrumb-link:hover { color: var(--accent-hover); }
  .breadcrumb-sep { color: var(--muted); font-size: 12px; }
  .breadcrumb-current { color: var(--text); font-size: 13px; font-weight: 500; }

  /* ===== Panels ===== */
  .panel {
    background: var(--surface);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    padding: 20px;
    margin-bottom: 16px;
    transition: var(--transition);
  }
  .panel-title {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--muted);
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  /* ===== Badges ===== */
  .badge {
    display: inline-flex;
    align-items: center;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .badge-planning { background: var(--yellow-dim); color: var(--yellow); }
  .badge-recruiting { background: var(--purple-dim); color: var(--purple); }
  .badge-active { background: var(--green-dim); color: var(--green); }
  .badge-completed { background: var(--accent-dim); color: var(--accent); }
  .badge-stopped { background: var(--red-dim); color: var(--red); }
  .badge-pending { background: #3f3f4644; color: var(--muted); }
  .badge-assigned, .badge-in_progress, .badge-submitted { background: var(--yellow-dim); color: var(--yellow); }
  .badge-accepted { background: var(--green-dim); color: var(--green); }
  .badge-rejected, .badge-failed { background: var(--red-dim); color: var(--red); }
  .badge-blocked { background: var(--red-dim); color: var(--red); }
  .badge-available { background: var(--green-dim); color: var(--green); }

  /* ===== Buttons ===== */
  .btn {
    background: var(--accent);
    color: white;
    border: none;
    border-radius: var(--radius-sm);
    padding: 10px 20px;
    font-family: inherit;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
    transition: var(--transition);
  }
  .btn:hover { background: var(--accent-hover); transform: translateY(-1px); }
  .btn:active { transform: translateY(0); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .btn-ghost {
    background: transparent;
    color: var(--accent);
    border: 1px solid var(--border);
  }
  .btn-ghost:hover { border-color: var(--accent); background: var(--accent-dim); }
  .btn-danger {
    background: transparent;
    color: var(--red);
    border: 1px solid var(--red);
    padding: 6px 14px;
    font-size: 12px;
  }
  .btn-danger:hover { background: var(--red-dim); }
  .btn-sm { padding: 6px 12px; font-size: 12px; border-radius: var(--radius-xs); }

  /* ===== Forms ===== */
  .create-form { display: flex; gap: 10px; margin-bottom: 12px; align-items: flex-start; }
  .create-input {
    flex: 1;
    background: rgba(9, 9, 11, 0.6);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 10px 16px;
    font-family: inherit;
    font-size: 14px;
    outline: none;
    transition: var(--transition);
    min-height: 72px;
    resize: vertical;
    line-height: 1.5;
  }
  .create-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-dim); }
  .create-input::placeholder { color: var(--muted); }

  .advanced-toggle {
    font-size: 12px;
    color: var(--muted);
    cursor: pointer;
    background: none;
    border: none;
    font-family: inherit;
    padding: 0;
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .advanced-toggle:hover { color: var(--text-secondary); }
  .advanced-options {
    display: none;
    gap: 24px;
    margin-top: 4px;
    margin-bottom: 4px;
    padding: 14px 16px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    flex-wrap: wrap;
    align-items: center;
  }
  .advanced-options.open { display: flex; }
  .create-option {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    color: var(--text-secondary);
  }
  .create-option label { white-space: nowrap; font-size: 12px; color: var(--muted); }
  .create-option input[type="number"] {
    width: 72px;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: var(--radius-xs);
    padding: 6px 10px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    outline: none;
    -moz-appearance: textfield;
  }
  .create-option input[type="number"]::-webkit-outer-spin-button,
  .create-option input[type="number"]::-webkit-inner-spin-button {
    -webkit-appearance: none;
    margin: 0;
  }
  .create-option input[type="number"]:focus { border-color: var(--accent); }
  .create-status { font-size: 13px; margin-top: 4px; min-height: 0; }
  .create-status.error { color: var(--red); }
  .create-status.ok { color: var(--green); }

  /* ===== Project Cards ===== */
  .projects-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 12px; }
  .project-card {
    background: rgba(9, 9, 11, 0.5);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    padding: 20px;
    cursor: pointer;
    transition: var(--transition);
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .project-card:hover {
    border-color: var(--accent);
    background: rgba(9, 9, 11, 0.65);
  }
  .project-card-left { flex: 1; min-width: 0; }
  .project-card-name {
    font-size: 15px;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .project-card-desc {
    color: var(--muted);
    font-size: 13px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .project-card-meta {
    font-size: 12px;
    color: var(--muted);
    margin-top: 6px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
  }
  .project-card-right { flex-shrink: 0; }

  /* SVG progress ring */
  .progress-ring { transform: rotate(-90deg); }
  .progress-ring-bg { fill: none; stroke: var(--border); stroke-width: 4; }
  .progress-ring-fill { fill: none; stroke: var(--green); stroke-width: 4; stroke-linecap: round; transition: stroke-dashoffset 0.5s ease; }
  .progress-ring-text {
    font-family: 'JetBrains Mono', monospace;
    font-size: 14px;
    font-weight: 600;
    fill: var(--text);
  }

  .github-link { font-size: 12px; color: var(--muted); text-decoration: none; }
  .github-link:hover { color: var(--accent); }

  /* ===== View switching ===== */
  .view { display: none; }
  .view.active { display: block; }

  /* ===== For Agents Tab ===== */
  .guide-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  @media (max-width: 900px) { .guide-grid { grid-template-columns: 1fr; } }

  .guide-card {
    background: rgba(9, 9, 11, 0.5);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    padding: 20px;
  }
  .guide-card h3 {
    font-size: 15px;
    font-weight: 600;
    margin-bottom: 16px;
    color: var(--text);
  }
  .step {
    display: flex;
    gap: 12px;
    margin-bottom: 16px;
  }
  .step:last-child { margin-bottom: 0; }
  .step-num {
    width: 28px;
    height: 28px;
    background: var(--accent-dim);
    color: var(--accent);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    font-weight: 600;
    flex-shrink: 0;
  }
  .step-content { flex: 1; }
  .step-title { font-weight: 600; font-size: 13px; margin-bottom: 4px; }
  .step-desc { font-size: 13px; color: var(--text-secondary); }

  .code-block {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: var(--radius-xs);
    padding: 10px 14px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: var(--text);
    margin-top: 8px;
    position: relative;
    overflow-x: auto;
    cursor: pointer;
    transition: var(--transition);
    word-break: break-all;
  }
  .code-block:hover { border-color: var(--accent); }
  .code-block::after {
    content: 'click to copy';
    position: absolute;
    right: 8px;
    top: 50%;
    transform: translateY(-50%);
    font-size: 10px;
    color: var(--muted);
    opacity: 0;
    transition: var(--transition);
  }
  .code-block:hover::after { opacity: 1; }
  .code-block.copied::after { content: 'copied!'; color: var(--green); opacity: 1; }

  .tools-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .tools-table th {
    text-align: left;
    padding: 8px 12px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
  }
  .tools-table td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--border-subtle);
    color: var(--text-secondary);
  }
  .tools-table td:first-child {
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: var(--accent);
    white-space: nowrap;
  }
  .tools-table tr:last-child td { border-bottom: none; }

  /* ===== Integration Header & Tabs ===== */
  .integration-header {
    background: var(--surface);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    padding: 24px;
    margin-bottom: 0;
  }
  .integration-header h2 {
    font-size: 18px;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin-bottom: 6px;
  }
  .integration-header > p {
    font-size: 13px;
    color: var(--text-secondary);
    margin-bottom: 16px;
  }
  .endpoint-row {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .endpoint-label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--muted);
    white-space: nowrap;
  }

  .integration-tabs {
    display: flex;
    gap: 0;
    background: var(--surface);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    border: 1px solid var(--border-subtle);
    border-top: none;
    border-radius: 0 0 var(--radius) var(--radius);
    margin-bottom: 16px;
    overflow-x: auto;
  }
  .integration-tab {
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 500;
    color: var(--muted);
    cursor: pointer;
    border: none;
    border-bottom: 2px solid transparent;
    background: none;
    font-family: inherit;
    transition: var(--transition);
    white-space: nowrap;
  }
  .integration-tab:hover { color: var(--text-secondary); }
  .integration-tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  .integration-panel { display: none; }
  .integration-panel.active { display: block; }

  .integration-badge {
    display: inline-block;
    background: var(--green-dim);
    color: var(--green);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 3px 10px;
    border-radius: 20px;
    margin-bottom: 12px;
  }
  .integration-desc {
    font-size: 13px;
    color: var(--text-secondary);
    margin-bottom: 16px;
    line-height: 1.5;
  }
  .step-alt {
    font-size: 12px;
    color: var(--muted);
    margin-top: 10px;
  }
  .code-block-multi {
    white-space: pre;
    word-break: normal;
    line-height: 1.5;
  }

  .workflow-list { display: flex; flex-direction: column; gap: 12px; }
  .workflow-item {
    display: flex;
    gap: 10px;
    font-size: 13px;
    color: var(--text-secondary);
    line-height: 1.5;
  }
  .workflow-item strong { color: var(--text); }
  .workflow-icon {
    color: var(--accent);
    font-size: 14px;
    flex-shrink: 0;
    margin-top: 1px;
  }

  .coming-soon-banner {
    background: var(--accent-dim);
    border: 1px solid var(--accent);
    border-radius: var(--radius);
    padding: 16px 24px;
    text-align: center;
    margin-top: 16px;
  }
  .coming-soon-banner h4 {
    font-size: 14px;
    font-weight: 600;
    color: var(--accent);
    margin-bottom: 4px;
  }
  .coming-soon-banner p {
    font-size: 13px;
    color: var(--muted);
  }

  /* Live projects mini cards */
  .live-project-card {
    background: rgba(24, 24, 27, 0.5);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 12px 16px;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .live-project-name { font-weight: 500; font-size: 13px; }
  .live-project-stats { font-size: 12px; color: var(--muted); font-family: 'JetBrains Mono', monospace; }

  /* ===== Project Detail ===== */
  .detail-header {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 20px;
    flex-wrap: wrap;
  }
  .detail-header-left { flex: 1; min-width: 200px; }
  .detail-header-name {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin-bottom: 4px;
  }
  .detail-header-desc {
    color: var(--muted);
    font-size: 13px;
  }

  .progress-bar-wrap { min-width: 200px; }
  .progress-label {
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 6px;
    display: flex;
    justify-content: space-between;
  }
  .progress-track {
    height: 6px;
    background: var(--border);
    border-radius: 3px;
    overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    background: var(--green);
    border-radius: 3px;
    transition: width 0.5s ease;
  }

  /* Recruiting banner */
  .recruiting-banner {
    background: var(--purple-dim);
    border: 1px solid #a855f744;
    border-radius: var(--radius);
    padding: 16px 24px;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 20px;
    flex-wrap: wrap;
    animation: pulse-border 2s ease-in-out infinite;
  }
  @keyframes pulse-border {
    0%, 100% { border-color: #a855f744; }
    50% { border-color: #a855f788; }
  }
  .recruiting-timer {
    font-size: 32px;
    font-weight: 700;
    color: var(--purple);
    font-family: 'JetBrains Mono', monospace;
    font-variant-numeric: tabular-nums;
    min-width: 80px;
  }
  .recruiting-info { flex: 1; }
  .recruiting-info-title { font-weight: 600; color: var(--purple); margin-bottom: 2px; }
  .recruiting-info-sub { font-size: 13px; color: var(--text-secondary); }

  /* ===== Agents ===== */
  .agents-grid { display: flex; gap: 10px; flex-wrap: wrap; }
  .agent-card {
    background: rgba(9, 9, 11, 0.5);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 14px 16px;
    min-width: 220px;
    flex: 1;
    transition: var(--transition);
  }
  .agent-card:hover { border-color: var(--border); }
  .agent-header { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
  .agent-avatar {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    font-weight: 600;
    color: white;
  }
  .agent-name { font-weight: 600; font-size: 14px; }
  .agent-meta { font-size: 12px; color: var(--muted); margin-top: 2px; font-family: 'JetBrains Mono', monospace; font-size: 11px; }
  .cap-tag {
    display: inline-block;
    background: var(--surface-2);
    color: var(--text-secondary);
    padding: 2px 8px;
    border-radius: 20px;
    font-size: 11px;
    margin: 3px 3px 0 0;
  }
  .empty-state { color: var(--muted); font-style: italic; font-size: 13px; padding: 12px 0; }

  /* ===== Kanban Task Board ===== */
  .kanban {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 12px;
  }
  .kanban-column {
    background: rgba(9, 9, 11, 0.45);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 12px;
    min-height: 100px;
  }
  .kanban-column-title {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--muted);
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .kanban-column-count {
    background: var(--surface-2);
    color: var(--muted);
    padding: 1px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 500;
  }
  .kanban-card {
    background: rgba(24, 24, 27, 0.5);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 10px 12px;
    margin-bottom: 8px;
    transition: var(--transition);
  }
  .kanban-card:last-child { margin-bottom: 0; }
  .kanban-card:hover { border-color: var(--border); }
  .kanban-card-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }
  .kanban-card-id {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: var(--muted);
  }
  .kanban-card-title { font-size: 13px; font-weight: 500; }
  .kanban-card-meta { font-size: 11px; color: var(--muted); margin-top: 4px; }
  .kanban-card-meta a { color: var(--accent); text-decoration: none; }

  /* ===== Grid Layout ===== */
  .detail-grid {
    display: grid;
    grid-template-columns: 1fr 280px;
    gap: 16px;
  }
  @media (max-width: 900px) { .detail-grid { grid-template-columns: 1fr; } }

  /* ===== Dep Graph ===== */
  .dep-graph { position: relative; overflow-x: auto; min-height: 120px; padding: 8px 0; }
  .dep-graph svg { display: block; }
  .dep-node { cursor: default; }
  .dep-node rect { rx: 10; ry: 10; stroke-width: 2; }
  .dep-node text { font-family: 'Inter', sans-serif; fill: var(--text); }
  .dep-node .node-id { font-size: 10px; fill: var(--muted); font-family: 'JetBrains Mono', monospace; }
  .dep-node .node-title { font-size: 11px; font-weight: 600; }
  .dep-node .node-status { font-size: 10px; }
  .dep-edge { fill: none; stroke-width: 2; }
  .dep-legend {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    margin-top: 12px;
    font-size: 12px;
    color: var(--muted);
  }
  .dep-legend-item { display: flex; align-items: center; gap: 6px; }
  .dep-legend-swatch { width: 14px; height: 14px; border-radius: 4px; }

  /* ===== Progress sidebar ===== */
  .progress-sidebar-ring { display: flex; justify-content: center; margin-bottom: 16px; }
  .checklist-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 0;
    font-size: 13px;
  }
  .check-icon { width: 18px; text-align: center; font-size: 14px; }
  .check-accepted { color: var(--green); }
  .check-pending { color: var(--muted); }
  .check-failed { color: var(--red); }
  .check-progress { color: var(--yellow); }
  .ratio {
    font-size: 15px;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 12px;
    text-align: center;
  }

  /* ===== Nia Activity Log ===== */
  .nia-log { max-height: 320px; overflow-y: auto; }
  .nia-event {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 6px 10px;
    border-radius: var(--radius-xs);
    margin-bottom: 2px;
    font-size: 12px;
    transition: var(--transition);
  }
  .nia-event:hover { background: #ffffff06; }
  .nia-time { color: var(--muted); font-size: 11px; min-width: 55px; flex-shrink: 0; font-family: 'JetBrains Mono', monospace; }
  .nia-badge {
    display: inline-block;
    padding: 1px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    min-width: 70px;
    text-align: center;
    flex-shrink: 0;
  }
  .nia-badge-index_repo { background: var(--accent-dim); color: var(--accent); }
  .nia-badge-index_docs { background: var(--accent-dim); color: var(--accent); }
  .nia-badge-search_project { background: var(--purple-dim); color: var(--purple); }
  .nia-badge-search_general { background: var(--yellow-dim); color: var(--yellow); }
  .nia-badge-search_web { background: #f9731622; color: var(--orange); }
  .nia-badge-lookup { background: var(--purple-dim); color: var(--purple); }
  .nia-badge-web_research { background: #f9731622; color: var(--orange); }
  .nia-source { font-size: 10px; padding: 1px 6px; border-radius: 3px; flex-shrink: 0; }
  .nia-source-orchestrator { background: var(--green-dim); color: var(--green); }
  .nia-source-agent { background: var(--yellow-dim); color: var(--yellow); }
  .nia-detail { flex: 1; color: var(--text-secondary); word-break: break-word; }
  .nia-status { font-size: 11px; min-width: 14px; flex-shrink: 0; }
  .nia-status-started { color: var(--yellow); }
  .nia-status-success { color: var(--green); }
  .nia-status-error { color: var(--red); }
  .nia-status-bg-error { color: var(--muted); }
  .nia-duration { font-size: 11px; color: var(--muted); min-width: 45px; text-align: right; flex-shrink: 0; font-family: 'JetBrains Mono', monospace; }
  .nia-header-row { display: flex; align-items: center; gap: 10px; }
  .nia-count { font-size: 11px; color: var(--muted); font-weight: 400; }
  .nia-powered { font-size: 11px; color: var(--muted); font-weight: 400; letter-spacing: 0; text-transform: none; }

  .broken-dep { color: var(--red); font-weight: 600; }

  /* ===== Ambient Background ===== */
  .ambient-bg {
    position: fixed;
    inset: 0;
    pointer-events: none;
    overflow: hidden;
    z-index: 0;
  }
  .ambient-bg video {
    position: absolute;
    top: 50%;
    left: 50%;
    min-width: 100%;
    min-height: 100%;
    transform: translate(-50%, -50%);
    object-fit: cover;
    opacity: 0.25;
  }

  /* ===== Animations ===== */
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .fade-in { animation: fadeIn 0.3s ease forwards; }

  /* ===== Planning / Skeleton states ===== */
  @keyframes shimmer {
    0% { background-position: -400px 0; }
    100% { background-position: 400px 0; }
  }
  .skeleton {
    background: linear-gradient(90deg, var(--surface-2) 25%, #3f3f4640 50%, var(--surface-2) 75%);
    background-size: 800px 100%;
    animation: shimmer 1.8s ease-in-out infinite;
    border-radius: 6px;
  }
  .skeleton-line {
    height: 14px;
    margin-bottom: 10px;
    border-radius: 4px;
  }
  .skeleton-line.short { width: 40%; }
  .skeleton-line.medium { width: 65%; }
  .skeleton-line.long { width: 90%; }
  .planning-banner {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 20px 24px;
    background: var(--surface);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    border: 1px solid var(--yellow-dim);
    border-radius: var(--radius);
    margin-bottom: 20px;
  }
  .planning-spinner {
    width: 28px;
    height: 28px;
    border: 3px solid var(--yellow-dim);
    border-top-color: var(--yellow);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
  .planning-banner-text {
    font-size: 14px;
    color: var(--text-secondary);
  }
  .planning-banner-text strong {
    color: var(--yellow);
    font-weight: 600;
  }
  .skeleton-card {
    background: var(--surface);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    padding: 16px;
    margin-bottom: 10px;
  }

  /* ===== Evolution ===== */
  .badge-evolution { background: var(--purple-dim); color: var(--purple); }
  .evolution-card { cursor: pointer; }
  .evolution-card:hover { border-color: var(--purple) !important; background: rgba(9, 9, 11, 0.65); }
  .evo-metric { display: flex; flex-direction: column; align-items: center; justify-content: center; min-width: 56px; }
  .evo-metric-value {
    font-family: 'JetBrains Mono', monospace;
    font-size: 16px;
    font-weight: 700;
    color: var(--purple);
  }
  .evo-metric-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .evo-kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
  }
  .evo-kpi {
    background: rgba(9, 9, 11, 0.5);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 14px 16px;
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }
  .evo-kpi-value {
    font-family: 'JetBrains Mono', monospace;
    font-size: 18px;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 4px;
    word-break: break-word;
  }
  .evo-kpi-label {
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .evo-charts-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  @media (max-width: 900px) { .evo-charts-row { grid-template-columns: 1fr; } }
  .evo-chart-wrap { position: relative; }
  .evo-chart-wrap canvas {
    width: 100%;
    height: auto;
    background: rgba(9, 9, 11, 0.4);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
  }
  .evo-chart-label {
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 8px;
    font-weight: 500;
  }
  .evo-leaderboard table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  .evo-leaderboard th {
    text-align: left;
    padding: 8px 10px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
  }
  .evo-leaderboard td {
    padding: 6px 10px;
    border-bottom: 1px solid var(--border-subtle);
    color: var(--text-secondary);
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
  }
  .evo-leaderboard tr:last-child td { border-bottom: none; }
  .evo-leaderboard td:first-child { color: var(--muted); max-width: 100px; overflow: hidden; text-overflow: ellipsis; }
  .evo-islands-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 12px;
  }
  @media (max-width: 900px) { .evo-islands-grid { grid-template-columns: 1fr; } }
  .evo-island-card {
    background: rgba(9, 9, 11, 0.5);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 12px 16px;
  }
  .evo-island-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 4px;
  }
  .evo-island-name { font-weight: 600; font-size: 13px; }
  .evo-island-meta { font-size: 11px; color: var(--muted); margin-bottom: 8px; }
  .evo-island-card canvas {
    width: 100%;
    height: auto;
    background: rgba(9, 9, 11, 0.4);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
  }
  `;
}
