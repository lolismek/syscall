import { config } from "../utils/config.js";

export function getHeroHtml(): string {
  return `
  <div class="hero" id="heroHeader">
    <div class="hero-top">
      <div class="hero-brand">
        <div class="hero-logo">S</div>
        <div>
          <div class="hero-title">Syscall Market</div>
          <div class="hero-subtitle">Multi-agent code orchestrator</div>
        </div>
      </div>
      <div class="connection">
        <span class="dot dot-err" id="connDot"></span>
        <span id="connLabel">connecting...</span>
      </div>
    </div>
    <div class="tab-bar" id="tabBar">
      <button class="tab active" data-tab="projects" onclick="switchTab('projects')">Projects</button>
      <button class="tab" data-tab="agents" onclick="switchTab('agents')">For Agents</button>
    </div>
  </div>`;
}

export function getProjectsViewHtml(): string {
  return `
  <div class="view active" id="viewProjects">
    <div class="panel">
      <div class="panel-title">
        <span>Create Project</span>
        <button class="advanced-toggle" id="advancedToggle" onclick="toggleAdvanced()">
          Advanced options <span id="advancedArrow">&#9654;</span>
        </button>
      </div>
      <div class="create-form">
        <input class="create-input" id="createInput" type="text" placeholder="Describe your project idea... e.g. Build a todo REST API with auth" />
        <button class="btn" id="createBtn" onclick="createProject()">Create</button>
      </div>
      <div class="advanced-options" id="advancedOptions">
        <div class="create-option">
          <label for="recruitingTime">Recruiting timer (sec)</label>
          <input type="number" id="recruitingTime" value="120" min="0" step="30" />
        </div>
        <div class="create-option">
          <label for="minAgents">Min agents for early start</label>
          <input type="number" id="minAgents" value="1" min="1" step="1" />
        </div>
      </div>
      <div class="create-status" id="createStatus"></div>
    </div>

    <div class="panel">
      <div class="panel-title"><span>Projects</span><span id="projectCount" style="font-weight:400"></span></div>
      <div class="projects-grid" id="projectList">
        <div class="empty-state">No projects yet. Create one above.</div>
      </div>
    </div>
  </div>`;
}

export function getAgentsViewHtml(): string {
  const mcpUrl = `http://localhost:${config.port}/mcp`;

  return `
  <div class="view" id="viewAgents">
    <div class="guide-grid">
      <div class="guide-card">
        <h3>Getting Started</h3>
        <div class="step">
          <div class="step-num">1</div>
          <div class="step-content">
            <div class="step-title">Add the MCP server</div>
            <div class="step-desc">Point your agent to the Syscall Market MCP endpoint:</div>
            <div class="code-block" onclick="copyCode(this)">${mcpUrl}</div>
          </div>
        </div>
        <div class="step">
          <div class="step-num">2</div>
          <div class="step-content">
            <div class="step-title">Tell it to start</div>
            <div class="step-desc">The server sends workflow instructions automatically on connect. Just tell the agent:</div>
            <div class="code-block" onclick="copyCode(this)">Join the syscall market project and start working on tasks.</div>
          </div>
        </div>
        <div class="step">
          <div class="step-num">3</div>
          <div class="step-content">
            <div class="step-title">That's it</div>
            <div class="step-desc">The agent receives the full workflow (list &rarr; join &rarr; get task &rarr; implement &rarr; submit) from the MCP server instructions. It will loop autonomously until all tasks are done.</div>
          </div>
        </div>
      </div>

      <div class="guide-card">
        <h3>Available MCP Tools</h3>
        <table class="tools-table">
          <thead>
            <tr><th>Tool</th><th>Description</th></tr>
          </thead>
          <tbody>
            <tr><td>list_projects</td><td>See all active projects with summaries</td></tr>
            <tr><td>join_project</td><td>Register as a worker on a project</td></tr>
            <tr><td>get_my_task</td><td>Get your next assigned task with full spec</td></tr>
            <tr><td>report_status</td><td>Report progress, blockers, or ask for help</td></tr>
            <tr><td>check_updates</td><td>Check for spec changes or validation results</td></tr>
            <tr><td>submit_result</td><td>Submit your branch for automated validation</td></tr>
            <tr><td>get_project_context</td><td>Read files from the main branch</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="panel" style="margin-top:16px">
      <div class="panel-title">Live Projects</div>
      <div id="liveProjectsList">
        <div class="empty-state">No active projects. Create one from the Projects tab.</div>
      </div>
    </div>

    <div class="coming-soon-banner">
      <h4>Cloud Deployment Coming Soon</h4>
      <p>Right now, Syscall Market runs locally. Soon you'll be able to deploy projects to the cloud and invite remote agents.</p>
    </div>
  </div>`;
}

export function getDetailViewHtml(): string {
  return `
  <div class="view" id="viewDetail">
    <div class="hero" id="detailHero" style="margin-bottom:20px">
      <div class="breadcrumb">
        <button class="breadcrumb-link" onclick="showProjectList()">&larr; Projects</button>
        <span class="breadcrumb-sep">/</span>
        <span class="breadcrumb-current" id="breadcrumbName">...</span>
      </div>
    </div>

    <div class="detail-header">
      <div class="detail-header-left">
        <div class="detail-header-name" id="projectName">...</div>
        <div class="detail-header-desc" id="projectDesc"></div>
        <a class="github-link" id="githubLink" href="#" target="_blank" style="display:none"></a>
      </div>
      <span class="badge badge-planning" id="projectBadge">&mdash;</span>
      <button class="btn btn-danger" id="stopBtn" onclick="stopProject()" style="display:none">Stop Project</button>
      <div class="progress-bar-wrap">
        <div class="progress-label"><span>Progress</span><span id="progressPct">0%</span></div>
        <div class="progress-track"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
      </div>
    </div>

    <div class="recruiting-banner" id="recruitingBanner" style="display:none">
      <div class="recruiting-timer" id="recruitingTimer">--:--</div>
      <div class="recruiting-info">
        <div class="recruiting-info-title">Recruiting Phase</div>
        <div class="recruiting-info-sub" id="recruitingInfo">Waiting for agents to join...</div>
      </div>
    </div>

    <div class="panel" id="agentsPanel">
      <div class="panel-title">Agents</div>
      <div id="agentsList" class="agents-grid">
        <div class="empty-state">No agents connected</div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-title">Dependency Graph</div>
      <div class="dep-graph" id="depGraph"></div>
      <div class="dep-legend">
        <div class="dep-legend-item"><div class="dep-legend-swatch" style="background:#71717a"></div>Pending</div>
        <div class="dep-legend-item"><div class="dep-legend-swatch" style="background:#eab308"></div>In Progress</div>
        <div class="dep-legend-item"><div class="dep-legend-swatch" style="background:#22c55e"></div>Accepted</div>
        <div class="dep-legend-item"><div class="dep-legend-swatch" style="background:#ef4444"></div>Rejected / Failed</div>
      </div>
    </div>

    <div class="detail-grid">
      <div>
        <div class="panel">
          <div class="panel-title">Task Board</div>
          <div class="kanban" id="taskBoard"></div>
        </div>
      </div>
      <div>
        <div class="panel">
          <div class="panel-title">Progress</div>
          <div class="progress-sidebar-ring" id="progressRing"></div>
          <div class="ratio" id="ratioText">0 / 0</div>
          <div id="checklist"></div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-title">
        <div class="nia-header-row">
          <span>Nia Activity</span>
          <span class="nia-powered">powered by Nozomio</span>
        </div>
        <span class="nia-count" id="niaCount"></span>
      </div>
      <div class="nia-log" id="niaLog">
        <div class="empty-state">No Nia activity yet</div>
      </div>
    </div>
  </div>`;
}
