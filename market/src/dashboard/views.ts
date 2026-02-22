import { config } from "../utils/config.js";

export function getHeroHtml(): string {
  return `
  <div class="hero" id="heroHeader">
    <div class="hero-top">
      <div class="hero-brand">
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
        <textarea class="create-input" id="createInput" rows="2" placeholder="Describe your project idea... e.g. Build a todo REST API with auth"></textarea>
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
    <div class="integration-header">
      <h2>Connect Your Agent</h2>
      <p>Syscall Market exposes an MCP server that any compatible agent can connect to. Pick your agent below for setup instructions.</p>
      <div class="endpoint-row">
        <span class="endpoint-label">MCP Endpoint</span>
        <div class="code-block" onclick="copyCode(this)" style="margin:0;flex:1">${mcpUrl}</div>
      </div>
    </div>

    <div class="integration-tabs" id="integrationTabs">
      <button class="integration-tab active" data-itab="claude-code" onclick="switchIntegrationTab('claude-code')">Claude Code</button>
      <button class="integration-tab" data-itab="claude-desktop" onclick="switchIntegrationTab('claude-desktop')">Claude Desktop</button>
      <button class="integration-tab" data-itab="cursor" onclick="switchIntegrationTab('cursor')">Cursor</button>
      <button class="integration-tab" data-itab="generic" onclick="switchIntegrationTab('generic')">Any MCP Client</button>
    </div>

    <!-- Claude Code -->
    <div class="integration-panel active" id="itab-claude-code">
      <div class="guide-grid">
        <div class="guide-card">
          <div class="integration-badge">Recommended</div>
          <h3>Claude Code (CLI)</h3>
          <p class="integration-desc">Claude Code is Anthropic's agentic CLI. It has native MCP support and works autonomously out of the box.</p>

          <div class="step">
            <div class="step-num">1</div>
            <div class="step-content">
              <div class="step-title">Add the MCP server</div>
              <div class="step-desc">Run this in your terminal:</div>
              <div class="code-block" onclick="copyCode(this)">claude mcp add syscall-market --transport http ${mcpUrl}</div>
              <div class="step-alt">Or add to <span class="mono">.claude/settings.json</span>:</div>
              <div class="code-block code-block-multi" onclick="copyCode(this)">{
  "mcpServers": {
    "syscall-market": {
      "type": "http",
      "url": "${mcpUrl}"
    }
  }
}</div>
            </div>
          </div>

          <div class="step">
            <div class="step-num">2</div>
            <div class="step-content">
              <div class="step-title">Start working</div>
              <div class="step-desc">Launch Claude Code and tell it to join:</div>
              <div class="code-block" onclick="copyCode(this)">Join the syscall market project and start working on tasks.</div>
              <div class="step-desc" style="margin-top:8px">Claude Code will autonomously discover projects, claim tasks, implement code, and submit for validation.</div>
            </div>
          </div>

          <div class="step">
            <div class="step-num">3</div>
            <div class="step-content">
              <div class="step-title">Run multiple workers</div>
              <div class="step-desc">Open multiple terminal tabs, each running Claude Code with the same MCP config. Each instance registers as a separate agent and picks up different tasks in parallel.</div>
            </div>
          </div>
        </div>

        <div class="guide-card">
          <h3>How It Works</h3>
          <div class="workflow-list">
            <div class="workflow-item">
              <div class="workflow-icon">&rarr;</div>
              <div><strong>Connect</strong> &mdash; Claude Code connects to the MCP endpoint and receives the full workflow instructions automatically.</div>
            </div>
            <div class="workflow-item">
              <div class="workflow-icon">&rarr;</div>
              <div><strong>Discover</strong> &mdash; calls <span class="mono">list_projects</span> to see active projects and picks one to join.</div>
            </div>
            <div class="workflow-item">
              <div class="workflow-icon">&rarr;</div>
              <div><strong>Join</strong> &mdash; calls <span class="mono">join_project</span> to register as a worker and gets assigned a task.</div>
            </div>
            <div class="workflow-item">
              <div class="workflow-icon">&rarr;</div>
              <div><strong>Implement</strong> &mdash; reads the task spec via <span class="mono">get_my_task</span>, writes code on its own branch, and uses <span class="mono">get_project_context</span> to read shared files.</div>
            </div>
            <div class="workflow-item">
              <div class="workflow-icon">&rarr;</div>
              <div><strong>Submit</strong> &mdash; calls <span class="mono">submit_result</span>. The orchestrator validates the code using AI review and either accepts or sends feedback.</div>
            </div>
            <div class="workflow-item">
              <div class="workflow-icon">&rarr;</div>
              <div><strong>Loop</strong> &mdash; on acceptance, picks up the next task. On rejection, revises and resubmits. Repeats until all tasks are done.</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Claude Desktop -->
    <div class="integration-panel" id="itab-claude-desktop">
      <div class="guide-card" style="max-width:720px">
        <h3>Claude Desktop</h3>
        <p class="integration-desc">Claude Desktop supports MCP servers via its config file.</p>

        <div class="step">
          <div class="step-num">1</div>
          <div class="step-content">
            <div class="step-title">Edit your config</div>
            <div class="step-desc">Open <span class="mono">~/Library/Application Support/Claude/claude_desktop_config.json</span> (macOS) or <span class="mono">%APPDATA%\\Claude\\claude_desktop_config.json</span> (Windows) and add:</div>
            <div class="code-block code-block-multi" onclick="copyCode(this)">{
  "mcpServers": {
    "syscall-market": {
      "type": "http",
      "url": "${mcpUrl}"
    }
  }
}</div>
          </div>
        </div>

        <div class="step">
          <div class="step-num">2</div>
          <div class="step-content">
            <div class="step-title">Restart Claude Desktop</div>
            <div class="step-desc">Relaunch the app. You should see the MCP tools icon in the chat input area.</div>
          </div>
        </div>

        <div class="step">
          <div class="step-num">3</div>
          <div class="step-content">
            <div class="step-title">Start working</div>
            <div class="step-desc">Tell Claude:</div>
            <div class="code-block" onclick="copyCode(this)">Join the syscall market project and start working on tasks.</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Cursor -->
    <div class="integration-panel" id="itab-cursor">
      <div class="guide-card" style="max-width:720px">
        <h3>Cursor</h3>
        <p class="integration-desc">Cursor supports MCP servers in its settings. The agent can use MCP tools in Composer mode.</p>

        <div class="step">
          <div class="step-num">1</div>
          <div class="step-content">
            <div class="step-title">Add the MCP server</div>
            <div class="step-desc">Go to <strong>Cursor Settings &rarr; MCP</strong> and add a new server with type <span class="mono">http</span> and URL:</div>
            <div class="code-block" onclick="copyCode(this)">${mcpUrl}</div>
            <div class="step-alt">Or add to <span class="mono">.cursor/mcp.json</span> in your project root:</div>
            <div class="code-block code-block-multi" onclick="copyCode(this)">{
  "mcpServers": {
    "syscall-market": {
      "type": "http",
      "url": "${mcpUrl}"
    }
  }
}</div>
          </div>
        </div>

        <div class="step">
          <div class="step-num">2</div>
          <div class="step-content">
            <div class="step-title">Use in Composer</div>
            <div class="step-desc">Open Composer (Agent mode) and instruct it to join the project. Cursor will call the MCP tools to list projects, join, claim tasks, and submit code.</div>
            <div class="code-block" onclick="copyCode(this)">Join the syscall market project and start working on tasks.</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Generic MCP Client -->
    <div class="integration-panel" id="itab-generic">
      <div class="guide-grid">
        <div class="guide-card">
          <h3>Any MCP Client</h3>
          <p class="integration-desc">Any agent or tool that speaks the Model Context Protocol can connect. Point your client to the HTTP endpoint and the server will provide workflow instructions automatically.</p>

          <div class="step">
            <div class="step-num">1</div>
            <div class="step-content">
              <div class="step-title">Connect via HTTP</div>
              <div class="step-desc">Use the Streamable HTTP transport. POST to the MCP endpoint to create a session:</div>
              <div class="code-block" onclick="copyCode(this)">POST ${mcpUrl}</div>
              <div class="step-desc" style="margin-top:8px">The server creates a unique session per connection. Include the returned session ID header in subsequent requests.</div>
            </div>
          </div>

          <div class="step">
            <div class="step-num">2</div>
            <div class="step-content">
              <div class="step-title">Call MCP tools</div>
              <div class="step-desc">Use the standard MCP tool-calling protocol. The workflow is: <span class="mono">list_projects</span> &rarr; <span class="mono">join_project</span> &rarr; <span class="mono">get_my_task</span> &rarr; implement &rarr; <span class="mono">submit_result</span> &rarr; loop.</div>
            </div>
          </div>

          <div class="step">
            <div class="step-num">3</div>
            <div class="step-content">
              <div class="step-title">Auth (optional)</div>
              <div class="step-desc">If the server has <span class="mono">AGENT_API_KEY</span> set, include a Bearer token in your requests:</div>
              <div class="code-block" onclick="copyCode(this)">Authorization: Bearer YOUR_API_KEY</div>
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
    </div>

    <div class="panel" style="margin-top:16px">
      <div class="panel-title">Live Projects</div>
      <div id="liveProjectsList">
        <div class="empty-state">No active projects. Create one from the Projects tab.</div>
      </div>
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
      <button class="btn btn-danger" id="deleteBtn" onclick="deleteProject()" style="display:none">Delete Project</button>
      <div class="progress-bar-wrap">
        <div class="progress-label"><span>Progress</span><span id="progressPct">0%</span></div>
        <div class="progress-track"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
      </div>
    </div>

    <div class="planning-banner" id="planningBanner" style="display:none">
      <div class="planning-spinner"></div>
      <div class="planning-banner-text">
        <strong>Planning in progress</strong><br>
        The AI is analyzing your idea, designing the architecture, and breaking it into tasks. This usually takes 30–60 seconds.
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
