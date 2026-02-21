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
  .dep-graph { position: relative; overflow-x: auto; min-height: 120px; }
  .dep-graph svg { display: block; }
  .dep-node { cursor: default; }
  .dep-node rect { rx: 6; ry: 6; stroke-width: 1.5; }
  .dep-node text { font-family: inherit; font-size: 11px; fill: var(--text); }
  .dep-node .node-id { font-size: 10px; fill: var(--muted); }
  .dep-edge { fill: none; stroke-width: 1.5; marker-end: url(#arrow); }
  .dep-legend { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px; font-size: 11px; color: var(--muted); }
  .dep-legend-item { display: flex; align-items: center; gap: 5px; }
  .dep-legend-swatch { width: 12px; height: 12px; border-radius: 3px; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="header-left">
      <div class="project-name" id="projectName">Connecting...</div>
      <div class="project-desc" id="projectDesc"></div>
    </div>
    <span class="badge badge-planning" id="projectBadge">—</span>
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

function renderDepGraph(tasks) {
  const container = document.getElementById("depGraph");
  if (!tasks || tasks.length === 0) { container.innerHTML = '<div class="empty-state">No tasks yet</div>'; return; }

  // Build adjacency & compute layers via topological sort (Kahn's)
  const taskMap = {};
  tasks.forEach(t => taskMap[t.id] = t);
  const inDeg = {};
  const children = {};
  tasks.forEach(t => { inDeg[t.id] = 0; children[t.id] = []; });
  tasks.forEach(t => {
    t.dependencies.forEach(dep => {
      if (taskMap[dep]) { children[dep].push(t.id); inDeg[t.id]++; }
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
  // Catch any nodes missed (cycles)
  const placed = new Set(layers.flat());
  const missed = tasks.filter(t => !placed.has(t.id)).map(t => t.id);
  if (missed.length) layers.push(missed);

  // Layout params
  const nodeW = 160, nodeH = 50, padX = 40, padY = 30;
  const maxPerLayer = Math.max(...layers.map(l => l.length));
  const svgW = layers.length * (nodeW + padX) + padX;
  const svgH = maxPerLayer * (nodeH + padY) + padY;

  // Compute positions
  const pos = {};
  layers.forEach((layer, li) => {
    const x = padX + li * (nodeW + padX);
    const totalH = layer.length * nodeH + (layer.length - 1) * padY;
    const startY = (svgH - totalH) / 2;
    layer.forEach((id, ni) => {
      pos[id] = { x, y: startY + ni * (nodeH + padY) };
    });
  });

  // Build SVG
  let svg = '<svg width="' + svgW + '" height="' + svgH + '" xmlns="http://www.w3.org/2000/svg">';
  svg += '<defs><marker id="arrow" viewBox="0 0 10 6" refX="10" refY="3" markerWidth="8" markerHeight="6" orient="auto"><path d="M0,0 L10,3 L0,6 Z" fill="#8b949e"/></marker></defs>';

  // Edges
  tasks.forEach(t => {
    t.dependencies.forEach(dep => {
      if (!pos[dep] || !pos[t.id]) return;
      const from = pos[dep], to = pos[t.id];
      const x1 = from.x + nodeW, y1 = from.y + nodeH / 2;
      const x2 = to.x, y2 = to.y + nodeH / 2;
      const cx1 = x1 + (x2 - x1) * 0.5, cx2 = x2 - (x2 - x1) * 0.5;
      // Color edge by dependency status
      const depTask = taskMap[dep];
      const edgeColor = depTask && depTask.status === "accepted" ? "#3fb95088" : "#8b949e55";
      svg += '<path class="dep-edge" d="M' + x1 + ',' + y1 + ' C' + cx1 + ',' + y1 + ' ' + cx2 + ',' + y2 + ' ' + x2 + ',' + y2 + '" stroke="' + edgeColor + '"/>';
    });
  });

  // Nodes
  tasks.forEach(t => {
    if (!pos[t.id]) return;
    const p = pos[t.id], c = statusColor(t.status);
    const label = t.title.length > 20 ? t.title.slice(0, 19) + "\\u2026" : t.title;
    const agent = t.assignedTo ? t.assignedTo.split("-").slice(1, 2).join("") : "";
    svg += '<g class="dep-node" transform="translate(' + p.x + ',' + p.y + ')">';
    svg += '<rect width="' + nodeW + '" height="' + nodeH + '" fill="' + c.fill + '" stroke="' + c.stroke + '"/>';
    svg += '<text class="node-id" x="8" y="16">' + esc(t.id) + (agent ? " \\u2022 " + esc(agent) : "") + '</text>';
    svg += '<text x="8" y="34">' + esc(label) + '</text>';
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

  // Task board grouped
  const groups = {};
  for (const t of data.tasks) {
    const g = STATUS_GROUP[t.status] || "Pending";
    (groups[g] = groups[g] || []).push(t);
  }
  const tb = document.getElementById("taskBoard");
  let html = "";
  for (const g of GROUP_ORDER) {
    const items = groups[g];
    if (!items || items.length === 0) continue;
    html += '<div class="task-group"><div class="task-group-label">' + esc(g) + ' (' + items.length + ')</div>';
    for (const t of items) {
      const deps = t.dependencies.length ? '<div class="task-detail">deps: ' + t.dependencies.map(esc).join(", ") + '</div>' : '';
      const agent = t.assignedTo ? '<div class="task-detail">agent: ' + esc(t.assignedTo) + '</div>' : '';
      const branch = t.branch ? '<div class="task-detail">branch: ' + esc(t.branch) + '</div>' : '';
      html += '<div class="task-row"><span class="task-id">' + esc(t.id) + '</span>'
        + '<span class="badge badge-' + t.status + '">' + esc(t.status) + '</span>'
        + '<div class="task-title">' + esc(t.title) + deps + agent + branch + '</div></div>';
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
