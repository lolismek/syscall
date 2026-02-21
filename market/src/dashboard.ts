import http from "node:http";

const PORT = parseInt(process.env.DASHBOARD_PORT || "3200", 10);
const API_URL = process.env.API_URL || "http://localhost:3100/api/status";

const HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Syscall Dashboard</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --yellow: #d29922; --red: #f85149;
    --orange: #db6d28; --purple: #bc8cff;
  }
  body { font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace; background: var(--bg); color: var(--text); font-size: 13px; line-height: 1.5; }
  .container { max-width: 1280px; margin: 0 auto; padding: 16px; }

  /* Header */
  .header { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px 20px; margin-bottom: 16px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  .header-left { flex: 1; min-width: 200px; }
  .project-name { font-size: 18px; font-weight: 600; color: var(--accent); }
  .project-desc { color: var(--muted); margin-top: 2px; font-size: 12px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .badge-planning { background: #d2992233; color: var(--yellow); }
  .badge-active { background: #3fb95033; color: var(--green); }
  .badge-completed { background: #58a6ff33; color: var(--accent); }
  .badge-pending { background: #8b949e33; color: var(--muted); }
  .badge-assigned, .badge-in_progress, .badge-submitted { background: #d2992233; color: var(--yellow); }
  .badge-accepted { background: #3fb95033; color: var(--green); }
  .badge-rejected, .badge-failed { background: #f8514933; color: var(--red); }
  .connection { display: flex; align-items: center; gap: 6px; font-size: 11px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; }
  .dot-ok { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot-err { background: var(--red); box-shadow: 0 0 6px var(--red); }

  /* Progress bar */
  .progress-bar-wrap { width: 200px; }
  .progress-label { font-size: 11px; color: var(--muted); margin-bottom: 4px; display: flex; justify-content: space-between; }
  .progress-track { height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
  .progress-fill { height: 100%; background: var(--green); border-radius: 3px; transition: width 0.5s ease; }

  /* Layout */
  .grid { display: grid; grid-template-columns: 1fr 260px; gap: 16px; }
  @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }

  /* Panels */
  .panel { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin-bottom: 16px; }
  .panel-title { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 12px; }

  /* Agents */
  .agents-grid { display: flex; gap: 10px; flex-wrap: wrap; }
  .agent-card { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px; min-width: 200px; flex: 1; }
  .agent-name { font-weight: 600; color: var(--accent); margin-bottom: 4px; }
  .agent-meta { font-size: 11px; color: var(--muted); }
  .cap-tag { display: inline-block; background: var(--border); color: var(--text); padding: 1px 6px; border-radius: 4px; font-size: 10px; margin: 2px 2px 0 0; }
  .empty-state { color: var(--muted); font-style: italic; font-size: 12px; padding: 8px 0; }

  /* Tasks */
  .task-group { margin-bottom: 14px; }
  .task-group-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; padding-bottom: 4px; border-bottom: 1px solid var(--border); }
  .task-row { display: flex; align-items: flex-start; gap: 8px; padding: 6px 8px; border-radius: 4px; margin-bottom: 2px; }
  .task-row:hover { background: #ffffff08; }
  .task-id { color: var(--muted); font-size: 11px; min-width: 60px; }
  .task-title { flex: 1; }
  .task-detail { font-size: 11px; color: var(--muted); }

  /* Sidebar checklist */
  .checklist-item { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 12px; }
  .check-icon { width: 16px; text-align: center; font-size: 13px; }
  .check-accepted { color: var(--green); }
  .check-pending { color: var(--muted); }
  .check-failed { color: var(--red); }
  .check-progress { color: var(--yellow); }
  .ratio { font-size: 14px; font-weight: 600; color: var(--text); margin-bottom: 10px; }

  /* Dependency graph */
  .dep-graph { position: relative; overflow-x: auto; min-height: 120px; padding: 8px 0; }
  .dep-graph svg { display: block; }
  .dep-node { cursor: default; }
  .dep-node rect { rx: 8; ry: 8; stroke-width: 2; }
  .dep-node text { font-family: inherit; fill: var(--text); }
  .dep-node .node-id { font-size: 10px; fill: var(--muted); }
  .dep-node .node-title { font-size: 11px; font-weight: 600; }
  .dep-node .node-status { font-size: 10px; }
  .dep-edge { fill: none; stroke-width: 2; }
  .dep-edge-broken { stroke-dasharray: 4 3; }
  .dep-legend { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px; font-size: 11px; color: var(--muted); }
  .dep-legend-item { display: flex; align-items: center; gap: 5px; }
  .dep-legend-swatch { width: 12px; height: 12px; border-radius: 3px; }
  .badge-blocked { background: #f8514922; color: var(--red); }
  .badge-available { background: #3fb95022; color: var(--green); }
  .broken-dep { color: var(--red); font-weight: 600; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="header-left">
      <div class="project-name" id="projectName">Connecting...</div>
      <div class="project-desc" id="projectDesc"></div>
    </div>
    <span class="badge badge-planning" id="projectBadge">\u2014</span>
    <div class="progress-bar-wrap">
      <div class="progress-label"><span>Progress</span><span id="progressPct">0%</span></div>
      <div class="progress-track"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
    </div>
    <div class="connection">
      <span class="dot dot-err" id="connDot"></span>
      <span id="connLabel">disconnected</span>
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
      <div class="dep-legend-item"><div class="dep-legend-swatch" style="background:#8b949e"></div>Pending</div>
      <div class="dep-legend-item"><div class="dep-legend-swatch" style="background:#d29922"></div>In Progress</div>
      <div class="dep-legend-item"><div class="dep-legend-swatch" style="background:#3fb950"></div>Accepted</div>
      <div class="dep-legend-item"><div class="dep-legend-swatch" style="background:#f85149"></div>Rejected / Failed</div>
      <div class="dep-legend-item"><div class="dep-legend-swatch" style="background:#f85149;border:1px dashed #f85149"></div>Broken Dep</div>
    </div>
  </div>

  <div class="grid">
    <div>
      <div class="panel">
        <div class="panel-title">Task Board</div>
        <div id="taskBoard"></div>
      </div>
    </div>
    <div>
      <div class="panel">
        <div class="panel-title">Progress</div>
        <div class="ratio" id="ratioText">0 / 0</div>
        <div id="checklist"></div>
      </div>
    </div>
  </div>
</div>

<script>
const API = "${API_URL}";
const STATUS_ORDER = ["in_progress", "assigned", "submitted", "pending", "accepted", "rejected", "failed"];
const STATUS_GROUP = {
  assigned: "In Progress", in_progress: "In Progress", submitted: "In Progress",
  pending: "Pending", accepted: "Completed", rejected: "Failed", failed: "Failed",
};
const GROUP_ORDER = ["In Progress", "Pending", "Completed", "Failed"];

function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
function relTime(iso) {
  const d = new Date(iso); const s = Math.floor((Date.now() - d) / 1000);
  if (s < 60) return s + "s ago"; if (s < 3600) return Math.floor(s/60) + "m ago";
  return Math.floor(s/3600) + "h ago";
}

function statusColor(s) {
  if (s === "accepted") return { fill: "#3fb95022", stroke: "#3fb950" };
  if (["rejected","failed"].includes(s)) return { fill: "#f8514922", stroke: "#f85149" };
  if (["assigned","in_progress","submitted"].includes(s)) return { fill: "#d2992222", stroke: "#d29922" };
  return { fill: "#8b949e22", stroke: "#8b949e" };
}

// Mirrors the backend getAvailableTasks logic so the dashboard shows the truth
function computeTaskAvailability(tasks) {
  const taskMap = {};
  tasks.forEach(t => taskMap[t.id] = t);
  const result = {}; // taskId -> { available: bool, blocked: bool, brokenDeps: string[] }
  tasks.forEach(t => {
    if (t.status !== "pending") {
      result[t.id] = { available: false, blocked: false, brokenDeps: [] };
      return;
    }
    const brokenDeps = [];
    let allMet = true;
    t.dependencies.forEach(depId => {
      const dep = taskMap[depId];
      if (!dep) { brokenDeps.push(depId); allMet = false; }
      else if (dep.status !== "accepted") { allMet = false; }
    });
    result[t.id] = {
      available: allMet && brokenDeps.length === 0,
      blocked: !allMet || brokenDeps.length > 0,
      brokenDeps,
    };
  });
  return result;
}

function renderDepGraph(tasks) {
  const container = document.getElementById("depGraph");
  if (!tasks || tasks.length === 0) { container.innerHTML = '<div class="empty-state">No tasks yet</div>'; return; }

  const availability = computeTaskAvailability(tasks);

  // Build adjacency & compute layers via topological sort (Kahn's)
  const taskMap = {};
  tasks.forEach(t => taskMap[t.id] = t);
  const inDeg = {};
  const children = {};
  tasks.forEach(t => { inDeg[t.id] = 0; children[t.id] = []; });
  tasks.forEach(t => {
    t.dependencies.forEach(dep => {
      if (taskMap[dep]) { children[dep].push(t.id); inDeg[t.id]++; }
      else { inDeg[t.id]++; }
    });
  });

  const layers = [];
  let queue = tasks.filter(t => inDeg[t.id] === 0).map(t => t.id);
  while (queue.length > 0) {
    layers.push([...queue]);
    const next = [];
    for (const id of queue) {
      for (const ch of children[id]) {
        inDeg[ch]--;
        if (inDeg[ch] === 0) next.push(ch);
      }
    }
    queue = next;
  }
  const placed = new Set(layers.flat());
  const missed = tasks.filter(t => !placed.has(t.id)).map(t => t.id);
  if (missed.length) layers.push(missed);

  // Track which layer each node is in
  const nodeLayer = {};
  layers.forEach((layer, li) => layer.forEach(id => { nodeLayer[id] = li; }));

  // Left-to-right layout — layers are columns
  const nodeW = 220, nodeH = 66, gapX = 80, gapY = 24;
  const marginX = 40, marginY = 30;
  const maxPerLayer = Math.max(...layers.map(l => l.length));
  const svgW = layers.length * (nodeW + gapX) - gapX + marginX * 2;
  const svgH = maxPerLayer * (nodeH + gapY) - gapY + marginY * 2;

  // Compute positions — layers go left to right, nodes centered vertically
  const pos = {};
  layers.forEach((layer, li) => {
    const x = marginX + li * (nodeW + gapX);
    const totalH = layer.length * nodeH + (layer.length - 1) * gapY;
    const startY = (svgH - totalH) / 2;
    layer.forEach((id, ni) => {
      pos[id] = { x, y: startY + ni * (nodeH + gapY) };
    });
  });

  // Build SVG
  let svg = '<svg width="' + svgW + '" height="' + svgH + '" xmlns="http://www.w3.org/2000/svg">';
  svg += '<defs>';
  svg += '<marker id="arrow" viewBox="0 0 10 8" refX="10" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,1 L8,4 L0,7" fill="none" stroke="#484f58" stroke-width="1.5"/></marker>';
  svg += '<marker id="arrow-green" viewBox="0 0 10 8" refX="10" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,1 L8,4 L0,7" fill="none" stroke="#3fb950" stroke-width="1.5"/></marker>';
  svg += '<marker id="arrow-red" viewBox="0 0 10 8" refX="10" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,1 L8,4 L0,7" fill="none" stroke="#f85149" stroke-width="1.5"/></marker>';
  svg += '</defs>';

  // Edges — from dependency (left) to dependent (right)
  tasks.forEach(t => {
    t.dependencies.forEach(dep => {
      if (!pos[t.id]) return;
      const depTask = taskMap[dep];
      if (depTask && pos[dep]) {
        const from = pos[dep], to = pos[t.id];
        const x1 = from.x + nodeW, y1 = from.y + nodeH / 2;
        const x2 = to.x, y2 = to.y + nodeH / 2;
        const accepted = depTask.status === "accepted";
        const edgeColor = accepted ? "#3fb950" : "#484f58";
        const markerRef = accepted ? "url(#arrow-green)" : "url(#arrow)";
        const opacity = accepted ? "0.6" : "0.4";

        const layerSpan = nodeLayer[t.id] - nodeLayer[dep];

        if (layerSpan <= 1) {
          // Adjacent layers — simple bezier, no nodes to cross
          const midX = (x1 + x2) / 2;
          svg += '<path class="dep-edge" d="M' + x1 + ',' + y1 + ' C' + midX + ',' + y1 + ' ' + midX + ',' + y2 + ' ' + x2 + ',' + y2 + '" stroke="' + edgeColor + '" opacity="' + opacity + '" marker-end="' + markerRef + '"/>';
        } else {
          // Multi-layer span — route around intermediate nodes
          const goUp = y2 <= y1;
          const edgeY = goUp ? Math.min(marginY / 2, Math.min(y1, y2) - 20) : Math.max(svgH - marginY / 2, Math.max(y1 + nodeH, y2 + nodeH) + 20);
          const outX = x1 + gapX * 0.3;
          const inX = x2 - gapX * 0.3;
          svg += '<path class="dep-edge" d="'
            + 'M' + x1 + ',' + y1
            + ' L' + outX + ',' + y1
            + ' Q' + (outX + 10) + ',' + y1 + ' ' + (outX + 10) + ',' + (y1 + (edgeY - y1) * 0.3)
            + ' L' + (outX + 10) + ',' + edgeY
            + ' L' + (inX - 10) + ',' + edgeY
            + ' L' + (inX - 10) + ',' + (y2 + (edgeY - y2) * 0.3)
            + ' Q' + (inX - 10) + ',' + y2 + ' ' + inX + ',' + y2
            + ' L' + x2 + ',' + y2
            + '" stroke="' + edgeColor + '" opacity="' + opacity + '" marker-end="' + markerRef + '"/>';
        }
      } else {
        // Broken dep
        const to = pos[t.id];
        const x2 = to.x, y2 = to.y + nodeH / 2;
        svg += '<text x="' + (x2 - 8) + '" y="' + (y2 + 4) + '" font-size="9" fill="#f85149" text-anchor="end">' + esc(dep) + '?</text>';
      }
    });
  });

  // Nodes
  tasks.forEach(t => {
    if (!pos[t.id]) return;
    const p = pos[t.id];
    const a = availability[t.id];
    const c = statusColor(t.status);
    const label = t.title;
    const agent = t.assignedTo ? t.assignedTo.split("-").slice(1, 2).join("") : "";

    let stroke = c.stroke;
    if (t.status === "pending" && a && a.brokenDeps.length > 0) stroke = "#f85149";

    svg += '<g class="dep-node" transform="translate(' + p.x + ',' + p.y + ')">';
    svg += '<rect width="' + nodeW + '" height="' + nodeH + '" fill="' + c.fill + '" stroke="' + stroke + '"' + (a && a.brokenDeps.length > 0 ? ' stroke-dasharray="4 2"' : '') + '/>';

    // Line 1: task ID + agent
    svg += '<text class="node-id" x="10" y="16">' + esc(t.id) + (agent ? "  \\u2192 " + esc(agent) : "") + '</text>';

    // Line 2: title — clip to node width
    svg += '<text class="node-title" x="10" y="34" clip-path="url(#nodeClip-' + t.id + ')">' + esc(label) + '</text>';
    svg += '<clipPath id="nodeClip-' + t.id + '"><rect x="0" y="20" width="' + (nodeW - 12) + '" height="20"/></clipPath>';

    // Line 3: status line
    let statusText = t.status;
    let statusFill = c.stroke;
    if (t.status === "pending" && a) {
      if (a.brokenDeps.length > 0) { statusText = "\\u26A0 broken deps"; statusFill = "#f85149"; }
      else if (a.blocked) { statusText = "\\u23F3 waiting on deps"; statusFill = "#8b949e"; }
      else { statusText = "\\u25CF ready"; statusFill = "#3fb950"; }
    } else if (t.status === "accepted") { statusText = "\\u2713 accepted"; }
    else if (t.status === "rejected") { statusText = "\\u2717 rejected"; }
    else if (t.status === "submitted") { statusText = "\\u2022 validating..."; }
    else if (t.status === "in_progress" || t.status === "assigned") { statusText = "\\u2022 " + t.status.replace("_", " "); }
    svg += '<text class="node-status" x="10" y="54" fill="' + statusFill + '">' + esc(statusText) + '</text>';

    svg += '</g>';
  });

  svg += '</svg>';
  container.innerHTML = svg;
}

function render(data) {
  // Header
  if (data.project) {
    document.getElementById("projectName").textContent = data.project.name;
    document.getElementById("projectDesc").textContent = data.project.description;
    const b = document.getElementById("projectBadge");
    b.textContent = data.project.status;
    b.className = "badge badge-" + data.project.status;
  }

  // Progress bar
  const p = data.progress;
  const pct = p.total ? Math.round((p.accepted / p.total) * 100) : 0;
  document.getElementById("progressPct").textContent = pct + "%";
  document.getElementById("progressFill").style.width = pct + "%";

  // Connection
  document.getElementById("connDot").className = "dot dot-ok";
  document.getElementById("connLabel").textContent = "live";

  // Agents
  const al = document.getElementById("agentsList");
  if (data.agents.length === 0) {
    al.innerHTML = '<div class="empty-state">No agents connected</div>';
  } else {
    al.innerHTML = data.agents.map(a => {
      const caps = a.capabilities.map(c => '<span class="cap-tag">' + esc(c) + '</span>').join("");
      const task = a.currentTaskId ? '<div class="agent-meta">Working on: ' + esc(a.currentTaskId) + '</div>' : '';
      return '<div class="agent-card"><div class="agent-name">' + esc(a.name) + '</div>'
        + '<div class="agent-meta">' + esc(a.id) + '</div>'
        + (caps ? '<div style="margin-top:4px">' + caps + '</div>' : '')
        + task
        + '<div class="agent-meta">Joined ' + relTime(a.joinedAt) + '</div></div>';
    }).join("");
  }

  // Compute availability to show accurate status
  const availability = computeTaskAvailability(data.tasks);
  const taskMap = {};
  data.tasks.forEach(t => taskMap[t.id] = t);

  // Task board grouped — split Pending into Available / Blocked
  const STATUS_GROUP_EX = {
    assigned: "In Progress", in_progress: "In Progress", submitted: "In Progress",
    pending: "Pending", accepted: "Completed", rejected: "Failed", failed: "Failed",
  };
  const GROUP_ORDER_EX = ["In Progress", "Available", "Blocked", "Completed", "Failed"];
  const groups = {};
  for (const t of data.tasks) {
    let g = STATUS_GROUP_EX[t.status] || "Pending";
    if (g === "Pending") {
      const a = availability[t.id];
      g = a && a.available ? "Available" : "Blocked";
    }
    (groups[g] = groups[g] || []).push(t);
  }
  const tb = document.getElementById("taskBoard");
  let html = "";
  for (const g of GROUP_ORDER_EX) {
    const items = groups[g];
    if (!items || items.length === 0) continue;
    html += '<div class="task-group"><div class="task-group-label">' + esc(g) + ' (' + items.length + ')</div>';
    for (const t of items) {
      const a = availability[t.id];
      // Show deps with status coloring
      let depsHtml = "";
      if (t.dependencies.length) {
        const depParts = t.dependencies.map(depId => {
          const dep = taskMap[depId];
          if (!dep) return '<span class="broken-dep">' + esc(depId) + ' (unknown!)</span>';
          if (dep.status === "accepted") return '<span style="color:var(--green)">' + esc(depId) + ' \\u2713</span>';
          return '<span style="color:var(--muted)">' + esc(depId) + ' (' + esc(dep.status) + ')</span>';
        });
        depsHtml = '<div class="task-detail">deps: ' + depParts.join(", ") + '</div>';
      }
      // Show broken deps warning
      let warning = "";
      if (a && a.brokenDeps.length > 0) {
        warning = '<div class="task-detail broken-dep">\\u26A0 broken dependency IDs: ' + a.brokenDeps.map(esc).join(", ") + '</div>';
      }
      const agent = t.assignedTo ? '<div class="task-detail">agent: ' + esc(t.assignedTo) + '</div>' : '';
      const branch = t.branch ? '<div class="task-detail">branch: ' + esc(t.branch) + '</div>' : '';
      // Show refined badge for pending tasks
      let badgeClass = "badge-" + t.status;
      let badgeText = t.status;
      if (t.status === "pending" && a) {
        if (a.brokenDeps.length > 0) { badgeClass = "badge-blocked"; badgeText = "broken deps"; }
        else if (a.blocked) { badgeClass = "badge-blocked"; badgeText = "blocked"; }
        else { badgeClass = "badge-available"; badgeText = "available"; }
      }
      html += '<div class="task-row"><span class="task-id">' + esc(t.id) + '</span>'
        + '<span class="badge ' + badgeClass + '">' + esc(badgeText) + '</span>'
        + '<div class="task-title">' + esc(t.title) + depsHtml + warning + agent + branch + '</div></div>';
    }
    html += '</div>';
  }
  if (!html) html = '<div class="empty-state">No tasks yet</div>';
  tb.innerHTML = html;

  // Dependency graph
  renderDepGraph(data.tasks);

  // Checklist sidebar
  document.getElementById("ratioText").textContent = p.accepted + " / " + p.total + " accepted";
  const cl = document.getElementById("checklist");
  cl.innerHTML = data.tasks.map(t => {
    let icon, cls;
    if (t.status === "accepted") { icon = "\\u2713"; cls = "check-accepted"; }
    else if (["rejected","failed"].includes(t.status)) { icon = "\\u2717"; cls = "check-failed"; }
    else if (["assigned","in_progress","submitted"].includes(t.status)) { icon = "\\u25CB"; cls = "check-progress"; }
    else { icon = "\\u25CB"; cls = "check-pending"; }
    return '<div class="checklist-item"><span class="check-icon ' + cls + '">' + icon + '</span>'
      + '<span>' + esc(t.id) + ': ' + esc(t.title) + '</span></div>';
  }).join("");
}

function showDisconnected() {
  document.getElementById("connDot").className = "dot dot-err";
  document.getElementById("connLabel").textContent = "disconnected";
}

async function poll() {
  try {
    const res = await fetch(API);
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    render(data);
  } catch {
    showDisconnected();
  }
}

poll();
setInterval(poll, 3000);
</script>
</body>
</html>`;

const server = http.createServer((_req, res) => {
  if (_req.url === "/" || _req.url === "/index.html") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(HTML);
  } else {
    res.writeHead(404);
    res.end("Not found");
  }
});

server.listen(PORT, () => {
  console.log(`Dashboard running at http://localhost:${PORT}`);
  console.log(`Polling API at ${API_URL}`);
});
