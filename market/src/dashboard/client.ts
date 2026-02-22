export function getClientScript(): string {
  return `
const API_BASE = "";
let selectedProjectId = null;
let selectedEvolutionRunId = null;
let isLiveEvolution = false;
let currentView = "projects"; // "projects" | "agents" | "detail" | "evolution"

// Client-side recruiting timer state
let recruitingTargetTime = null; // Date.getTime() of recruitingUntil
let recruitingTimerInterval = null;

function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
function relTime(iso) {
  const d = new Date(iso); const s = Math.floor((Date.now() - d) / 1000);
  if (s < 60) return s + "s ago"; if (s < 3600) return Math.floor(s/60) + "m ago";
  return Math.floor(s/3600) + "h ago";
}

function statusColor(s) {
  if (s === "accepted") return { fill: "#22c55e22", stroke: "#22c55e" };
  if (["rejected","failed"].includes(s)) return { fill: "#ef444422", stroke: "#ef4444" };
  if (["assigned","in_progress","submitted"].includes(s)) return { fill: "#eab30822", stroke: "#eab308" };
  return { fill: "#71717a22", stroke: "#71717a" };
}

function hashColor(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) hash = str.charCodeAt(i) + ((hash << 5) - hash);
  const colors = ["#6366f1","#a855f7","#ec4899","#f97316","#eab308","#22c55e","#06b6d4","#3b82f6"];
  return colors[Math.abs(hash) % colors.length];
}

function copyCode(el) {
  navigator.clipboard.writeText(el.textContent.trim());
  el.classList.add("copied");
  setTimeout(() => el.classList.remove("copied"), 1500);
}

// ---------- Integration tab switching ----------

function switchIntegrationTab(tab) {
  document.querySelectorAll(".integration-tab").forEach(t => t.classList.toggle("active", t.dataset.itab === tab));
  document.querySelectorAll(".integration-panel").forEach(p => p.classList.toggle("active", p.id === "itab-" + tab));
}

// ---------- Client-side recruiting timer ----------

function startRecruitingTimer(recruitingUntilIso, connectedAgents, minAgents) {
  recruitingTargetTime = new Date(recruitingUntilIso).getTime();
  if (recruitingTimerInterval) clearInterval(recruitingTimerInterval);
  // Store latest agent info for display
  window._recruitAgents = connectedAgents;
  window._recruitMinAgents = minAgents;
  updateRecruitingDisplay();
  recruitingTimerInterval = setInterval(updateRecruitingDisplay, 1000);
}

function stopRecruitingTimer() {
  recruitingTargetTime = null;
  if (recruitingTimerInterval) { clearInterval(recruitingTimerInterval); recruitingTimerInterval = null; }
}

function updateRecruitingDisplay() {
  var timerEl = document.getElementById("recruitingTimer");
  var infoEl = document.getElementById("recruitingInfo");
  if (!timerEl || !recruitingTargetTime) return;
  var remainMs = Math.max(0, recruitingTargetTime - Date.now());
  var remainSec = Math.ceil(remainMs / 1000);
  var mm = String(Math.floor(remainSec / 60)).padStart(2, "0");
  var ss = String(remainSec % 60).padStart(2, "0");
  timerEl.textContent = mm + ":" + ss;
  var agents = window._recruitAgents || 0;
  var minA = window._recruitMinAgents || 1;
  infoEl.textContent = agents + " agent(s) connected \\u2014 need " + minA + " to start early. Timer: " + mm + ":" + ss;
}

// ---------- Tab / View switching ----------

function switchTab(tab) {
  currentView = tab;
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === tab));
  document.getElementById("viewProjects").classList.toggle("active", tab === "projects");
  document.getElementById("viewAgents").classList.toggle("active", tab === "agents");
  document.getElementById("viewDetail").classList.remove("active");
  document.getElementById("tabBar").style.display = "";
  if (tab === "projects") fetchProjects();
  if (tab === "agents") fetchLiveProjects();
}

function showProjectList() {
  selectedProjectId = null;
  selectedEvolutionRunId = null;
  isLiveEvolution = false;
  stopRecruitingTimer();
  switchTab("projects");
}

function selectProject(id) {
  selectedProjectId = id;
  selectedEvolutionRunId = null;
  currentView = "detail";
  document.getElementById("viewProjects").classList.remove("active");
  document.getElementById("viewAgents").classList.remove("active");
  document.getElementById("viewDetail").classList.add("active");
  document.getElementById("tabBar").style.display = "none";
  pollDetail();
}

// ---------- Advanced options toggle ----------

function toggleAdvanced() {
  const el = document.getElementById("advancedOptions");
  const arrow = document.getElementById("advancedArrow");
  el.classList.toggle("open");
  arrow.innerHTML = el.classList.contains("open") ? "&#9660;" : "&#9654;";
}

// ---------- Project creation ----------

async function createProject() {
  const input = document.getElementById("createInput");
  const btn = document.getElementById("createBtn");
  const status = document.getElementById("createStatus");
  const idea = input.value.trim();
  if (!idea) { status.textContent = "Please enter a project idea."; status.className = "create-status error"; return; }

  const recruitingDurationSeconds = parseInt(document.getElementById("recruitingTime").value, 10) || 0;
  const minAgents = parseInt(document.getElementById("minAgents").value, 10) || 1;
  const useEvolution = document.getElementById("useEvolution").checked;

  btn.disabled = true;
  status.textContent = useEvolution
    ? "Starting evolution algorithm..."
    : "Creating project... (planning via LLM, may take 30-60s)";
  status.className = "create-status";

  try {
    const res = await fetch(API_BASE + "/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idea, recruitingDurationSeconds, minAgents, useEvolution }),
    });
    const data = await res.json();
    if (!res.ok) {
      status.textContent = data.error || "Failed to create project";
      status.className = "create-status error";
      return;
    }
    status.textContent = "";
    input.value = "";

    if (data.evolutionRunId) {
      // Evolution mode — navigate to live evolution view
      selectEvolutionRun(data.evolutionRunId, true);
      return;
    }
    // Navigate to the project immediately — it shows a planning skeleton
    selectProject(data.projectId);
  } catch (err) {
    status.textContent = "Network error: " + err.message;
    status.className = "create-status error";
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("createInput").addEventListener("keydown", function(e) {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) createProject();
});

async function stopProject() {
  if (!selectedProjectId) return;
  if (!confirm("Stop this project? This is permanent and cannot be undone.")) return;
  const btn = document.getElementById("stopBtn");
  btn.disabled = true;
  try {
    const res = await fetch(API_BASE + "/api/projects/" + encodeURIComponent(selectedProjectId) + "/stop", { method: "POST" });
    if (!res.ok) { const d = await res.json(); alert(d.error || "Failed"); return; }
    pollDetail();
  } catch (err) { alert("Network error: " + err.message); }
  finally { btn.disabled = false; }
}

async function stopProjectById(id) {
  if (!confirm("Stop this project? This is permanent.")) return;
  try {
    const res = await fetch(API_BASE + "/api/projects/" + encodeURIComponent(id) + "/stop", { method: "POST" });
    if (!res.ok) { const d = await res.json(); alert(d.error || "Failed"); return; }
    fetchProjects();
  } catch (err) { alert("Network error: " + err.message); }
}

async function deleteProject() {
  if (!selectedProjectId) return;
  if (!confirm("Delete this project? All data (tasks, agents, workspace) will be permanently removed.")) return;
  var btn = document.getElementById("deleteBtn");
  btn.disabled = true;
  try {
    var res = await fetch(API_BASE + "/api/projects/" + encodeURIComponent(selectedProjectId) + "", { method: "DELETE" });
    if (!res.ok) { var d = await res.json(); alert(d.error || "Failed"); return; }
    selectedProjectId = null;
    showView("list");
    fetchProjects();
  } catch (err) { alert("Network error: " + err.message); }
  finally { btn.disabled = false; }
}

async function deleteProjectById(id) {
  if (!confirm("Delete this project? All data will be permanently removed.")) return;
  try {
    var res = await fetch(API_BASE + "/api/projects/" + encodeURIComponent(id) + "", { method: "DELETE" });
    if (!res.ok) { var d = await res.json(); alert(d.error || "Failed"); return; }
    fetchProjects();
  } catch (err) { alert("Network error: " + err.message); }
}

// ---------- SVG progress ring ----------

function renderProgressRing(pct, size, stroke) {
  size = size || 80;
  stroke = stroke || 4;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c - (pct / 100) * c;
  return '<div style="position:relative;width:' + size + 'px;height:' + size + 'px">'
    + '<svg width="' + size + '" height="' + size + '" class="progress-ring">'
    + '<circle class="progress-ring-bg" cx="' + size/2 + '" cy="' + size/2 + '" r="' + r + '"/>'
    + '<circle class="progress-ring-fill" cx="' + size/2 + '" cy="' + size/2 + '" r="' + r + '" stroke-dasharray="' + c + '" stroke-dashoffset="' + offset + '"/>'
    + '</svg>'
    + '<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center"><span class="progress-ring-text" style="font-size:' + (size > 60 ? 16 : 12) + 'px">' + pct + '%</span></div>'
    + '</div>';
}

// ---------- Project list ----------

async function fetchProjects() {
  try {
    const res = await fetch(API_BASE + "/api/projects");
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();

    document.getElementById("connDot").className = "dot dot-ok";
    document.getElementById("connLabel").textContent = "connected";

    const list = data.projects || [];
    document.getElementById("projectCount").textContent = list.length ? list.length + " total" : "";

    if (list.length === 0) {
      document.getElementById("projectList").innerHTML = '';
      fetchEvolutionCards();
      return;
    }

    // Also fetch evolution runs and append
    fetchEvolutionCards();

    document.getElementById("projectList").innerHTML = list.map(function(p) {
      const pct = p.taskCount ? Math.round((p.accepted / p.taskCount) * 100) : 0;
      const ghHtml = p.githubUrl ? '<div class="project-card-meta" style="margin-top:4px"><a class="github-link" href="' + esc(p.githubUrl) + '" target="_blank" onclick="event.stopPropagation()">' + esc(p.githubUrl) + '</a></div>' : '';
      const stopBtnHtml = (p.status !== "stopped" && p.status !== "completed" && p.status !== "planning")
        ? ' <button class="btn btn-danger btn-sm" style="margin-left:4px" onclick="event.stopPropagation();stopProjectById(\\'' + esc(p.id) + '\\')">Stop</button>'
        : '';
      const deleteBtnHtml = (p.status === "stopped" || p.status === "completed")
        ? ' <button class="btn btn-danger btn-sm" style="margin-left:4px" onclick="event.stopPropagation();deleteProjectById(\\'' + esc(p.id) + '\\')">Delete</button>'
        : '';
      const ringSize = 56;
      const r = (ringSize - 4) / 2;
      const c = 2 * Math.PI * r;
      const offset = c - (pct / 100) * c;
      const ring = '<div style="position:relative;width:' + ringSize + 'px;height:' + ringSize + 'px">'
        + '<svg width="' + ringSize + '" height="' + ringSize + '" style="transform:rotate(-90deg)">'
        + '<circle cx="' + ringSize/2 + '" cy="' + ringSize/2 + '" r="' + r + '" fill="none" stroke="#3f3f46" stroke-width="3"/>'
        + '<circle cx="' + ringSize/2 + '" cy="' + ringSize/2 + '" r="' + r + '" fill="none" stroke="#22c55e" stroke-width="3" stroke-linecap="round" stroke-dasharray="' + c + '" stroke-dashoffset="' + offset + '" style="transition:stroke-dashoffset 0.5s"/>'
        + '</svg>'
        + '<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-family:JetBrains Mono,monospace;font-size:12px;font-weight:600">' + pct + '%</div>'
        + '</div>';
      return '<div class="project-card" onclick="selectProject(\\'' + esc(p.id) + '\\')">'
        + '<div class="project-card-left">'
        + '<div class="project-card-name">' + esc(p.name || p.id) + ' <span class="badge badge-' + esc(p.status) + '">' + esc(p.status) + '</span>' + stopBtnHtml + deleteBtnHtml + '</div>'
        + '<div class="project-card-desc">' + esc(p.description || "") + '</div>'
        + '<div class="project-card-meta">' + esc(p.id) + ' &middot; ' + relTime(p.createdAt) + '</div>'
        + ghHtml
        + '</div>'
        + '<div class="project-card-right">' + ring + '</div>'
        + '</div>';
    }).join("");
  } catch(e) {
    document.getElementById("connDot").className = "dot dot-err";
    document.getElementById("connLabel").textContent = "disconnected";
  }
}

// ---------- Live projects (For Agents tab) ----------

async function fetchLiveProjects() {
  try {
    const res = await fetch(API_BASE + "/api/projects");
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    const list = (data.projects || []).filter(function(p) { return p.status !== "stopped"; });
    const el = document.getElementById("liveProjectsList");
    if (list.length === 0) {
      el.innerHTML = '<div class="empty-state">No active projects. Create one from the Projects tab.</div>';
      return;
    }
    el.innerHTML = list.map(function(p) {
      const available = p.taskCount - p.accepted - p.inProgress;
      return '<div class="live-project-card">'
        + '<div><div class="live-project-name">' + esc(p.name || p.id) + '</div>'
        + '<div style="font-size:12px;color:#a1a1aa">' + esc(p.description || "") + '</div></div>'
        + '<div class="live-project-stats">' + p.taskCount + ' tasks &middot; ' + Math.max(0, available) + ' available</div>'
        + '</div>';
    }).join("");
  } catch(e) {}
}

// ---------- Task availability ----------

function computeTaskAvailability(tasks) {
  const taskMap = {};
  tasks.forEach(function(t) { taskMap[t.id] = t; });
  const result = {};
  tasks.forEach(function(t) {
    if (t.status !== "pending") {
      result[t.id] = { available: false, blocked: false, brokenDeps: [] };
      return;
    }
    const brokenDeps = [];
    let allMet = true;
    t.dependencies.forEach(function(depId) {
      const dep = taskMap[depId];
      if (!dep) { brokenDeps.push(depId); allMet = false; }
      else if (dep.status !== "accepted") { allMet = false; }
    });
    result[t.id] = {
      available: allMet && brokenDeps.length === 0,
      blocked: !allMet || brokenDeps.length > 0,
      brokenDeps: brokenDeps,
    };
  });
  return result;
}

// ---------- Dependency graph ----------

function renderDepGraph(tasks) {
  const container = document.getElementById("depGraph");
  if (!tasks || tasks.length === 0) { container.innerHTML = '<div class="empty-state">No tasks yet</div>'; return; }

  const availability = computeTaskAvailability(tasks);
  const taskMap = {};
  tasks.forEach(function(t) { taskMap[t.id] = t; });
  const inDeg = {};
  const children = {};
  tasks.forEach(function(t) { inDeg[t.id] = 0; children[t.id] = []; });
  tasks.forEach(function(t) {
    t.dependencies.forEach(function(dep) {
      if (taskMap[dep]) { children[dep].push(t.id); inDeg[t.id]++; }
      else { inDeg[t.id]++; }
    });
  });

  var layers = [];
  var queue = tasks.filter(function(t) { return inDeg[t.id] === 0; }).map(function(t) { return t.id; });
  while (queue.length > 0) {
    layers.push(queue.slice());
    var next = [];
    for (var qi = 0; qi < queue.length; qi++) {
      var id = queue[qi];
      for (var ci = 0; ci < children[id].length; ci++) {
        var ch = children[id][ci];
        inDeg[ch]--;
        if (inDeg[ch] === 0) next.push(ch);
      }
    }
    queue = next;
  }
  var placed = {};
  layers.forEach(function(layer) { layer.forEach(function(id) { placed[id] = true; }); });
  var missed = tasks.filter(function(t) { return !placed[t.id]; }).map(function(t) { return t.id; });
  if (missed.length) layers.push(missed);

  var nodeW = 220, nodeH = 66, gapX = 80, gapY = 24;
  var marginX = 40, marginY = 30;
  var maxPerLayer = Math.max.apply(null, layers.map(function(l) { return l.length; }));
  var svgW = layers.length * (nodeW + gapX) - gapX + marginX * 2;
  var svgH = maxPerLayer * (nodeH + gapY) - gapY + marginY * 2;
  // Ensure minimum height
  if (svgH < 150) svgH = 150;

  var pos = {};
  layers.forEach(function(layer, li) {
    var x = marginX + li * (nodeW + gapX);
    var totalH = layer.length * nodeH + (layer.length - 1) * gapY;
    var startY = (svgH - totalH) / 2;
    layer.forEach(function(id, ni) {
      pos[id] = { x: x, y: startY + ni * (nodeH + gapY) };
    });
  });

  // Build node center-Y lookup for edge avoidance
  var nodeCenters = {};
  tasks.forEach(function(t) {
    if (pos[t.id]) nodeCenters[t.id] = pos[t.id].y + nodeH / 2;
  });

  var svg = '<svg width="' + svgW + '" height="' + svgH + '" xmlns="http://www.w3.org/2000/svg">';
  svg += '<defs>';
  svg += '<marker id="arrow" viewBox="0 0 10 8" refX="10" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,1 L8,4 L0,7" fill="none" stroke="#3f3f46" stroke-width="1.5"/></marker>';
  svg += '<marker id="arrow-green" viewBox="0 0 10 8" refX="10" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,1 L8,4 L0,7" fill="none" stroke="#22c55e" stroke-width="1.5"/></marker>';
  svg += '<marker id="arrow-red" viewBox="0 0 10 8" refX="10" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,1 L8,4 L0,7" fill="none" stroke="#ef4444" stroke-width="1.5"/></marker>';
  svg += '</defs>';

  // Draw edges (behind nodes)
  tasks.forEach(function(t) {
    t.dependencies.forEach(function(dep) {
      if (!pos[t.id]) return;
      var depTask = taskMap[dep];
      if (depTask && pos[dep]) {
        var from = pos[dep], to = pos[t.id];
        var x1 = from.x + nodeW, y1 = from.y + nodeH / 2;
        var x2 = to.x, y2 = to.y + nodeH / 2;
        var accepted = depTask.status === "accepted";
        var edgeColor = accepted ? "#22c55e" : "#3f3f46";
        var markerRef = accepted ? "url(#arrow-green)" : "url(#arrow)";
        var opacity = accepted ? "0.7" : "0.4";

        // Always use a smooth cubic bezier — works for any layer span
        // Control points: pull horizontally from source, then horizontally into target
        var dx = x2 - x1;
        var cpOffset = Math.max(dx * 0.4, 40); // at least 40px pull
        var cx1 = x1 + cpOffset;
        var cy1 = y1;
        var cx2 = x2 - cpOffset;
        var cy2 = y2;

        svg += '<path class="dep-edge" d="M' + x1 + ',' + y1 + ' C' + cx1 + ',' + cy1 + ' ' + cx2 + ',' + cy2 + ' ' + x2 + ',' + y2 + '" stroke="' + edgeColor + '" opacity="' + opacity + '" marker-end="' + markerRef + '"/>';
      } else {
        // Broken dep — show label
        var to2 = pos[t.id];
        if (to2) {
          var x2b = to2.x, y2b = to2.y + nodeH / 2;
          svg += '<text x="' + (x2b - 8) + '" y="' + (y2b + 4) + '" font-size="9" fill="#ef4444" text-anchor="end">' + esc(dep) + '?</text>';
        }
      }
    });
  });

  // Draw nodes (on top of edges)
  tasks.forEach(function(t) {
    if (!pos[t.id]) return;
    var p = pos[t.id];
    var a = availability[t.id];
    var c = statusColor(t.status);
    var label = t.title;
    var agent = t.assignedTo ? t.assignedTo.split("-").slice(1, 2).join("") : "";
    var stroke = c.stroke;
    if (t.status === "pending" && a && a.brokenDeps.length > 0) stroke = "#ef4444";

    svg += '<g class="dep-node" transform="translate(' + p.x + ',' + p.y + ')">';
    svg += '<rect width="' + nodeW + '" height="' + nodeH + '" fill="' + c.fill + '" stroke="' + stroke + '"' + (a && a.brokenDeps.length > 0 ? ' stroke-dasharray="4 2"' : '') + '/>';
    svg += '<text class="node-id" x="10" y="16">' + esc(t.id) + (agent ? "  \\u2192 " + esc(agent) : "") + '</text>';
    svg += '<text class="node-title" x="10" y="34" clip-path="url(#nodeClip-' + t.id + ')">' + esc(label) + '</text>';
    svg += '<clipPath id="nodeClip-' + t.id + '"><rect x="0" y="20" width="' + (nodeW - 12) + '" height="20"/></clipPath>';

    var statusText = t.status;
    var statusFill = c.stroke;
    if (t.status === "pending" && a) {
      if (a.brokenDeps.length > 0) { statusText = "\\u26A0 broken deps"; statusFill = "#ef4444"; }
      else if (a.blocked) { statusText = "\\u23F3 waiting on deps"; statusFill = "#71717a"; }
      else { statusText = "\\u25CF ready"; statusFill = "#22c55e"; }
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

// ---------- Detail view render ----------

function renderPlanningSkeleton() {
  var banner = document.getElementById("planningBanner");
  if (banner) banner.style.display = "flex";

  document.getElementById("depGraph").innerHTML =
    '<div class="skeleton-card"><div class="skeleton skeleton-line long"></div>'
    + '<div class="skeleton skeleton-line medium"></div>'
    + '<div class="skeleton skeleton-line short"></div></div>';

  document.getElementById("taskBoard").innerHTML =
    '<div class="kanban-column"><div class="kanban-column-title"><span>Tasks</span></div>'
    + '<div class="skeleton-card"><div class="skeleton skeleton-line long"></div><div class="skeleton skeleton-line medium"></div></div>'
    + '<div class="skeleton-card"><div class="skeleton skeleton-line medium"></div><div class="skeleton skeleton-line short"></div></div>'
    + '<div class="skeleton-card"><div class="skeleton skeleton-line long"></div><div class="skeleton skeleton-line short"></div></div>'
    + '</div>';

  document.getElementById("progressRing").innerHTML = renderProgressRing(0, 90, 5);
  document.getElementById("ratioText").textContent = "Planning...";
  document.getElementById("progressPct").textContent = "—";
  document.getElementById("progressFill").style.width = "0%";

  var al = document.getElementById("agentsList");
  al.innerHTML = '<div class="empty-state">Agents can join after planning completes</div>';
}

function renderDetail(data) {
  if (data.project) {
    document.getElementById("breadcrumbName").textContent = data.project.name;
    document.getElementById("projectName").textContent = data.project.name;
    document.getElementById("projectDesc").textContent = data.project.description;
    var b = document.getElementById("projectBadge");
    b.textContent = data.project.status;
    b.className = "badge badge-" + data.project.status;
    var stopBtn = document.getElementById("stopBtn");
    stopBtn.style.display = (data.project.status !== "stopped" && data.project.status !== "completed" && data.project.status !== "planning") ? "inline-block" : "none";
    var deleteBtn = document.getElementById("deleteBtn");
    deleteBtn.style.display = (data.project.status === "stopped" || data.project.status === "completed") ? "inline-block" : "none";
    var ghLink = document.getElementById("githubLink");
    if (data.project.githubUrl) {
      ghLink.href = data.project.githubUrl;
      ghLink.textContent = data.project.githubUrl;
      ghLink.style.display = "inline";
    } else {
      ghLink.style.display = "none";
    }
  }

  // Planning state — show skeleton UI and return early
  var planningBanner = document.getElementById("planningBanner");
  if (data.project && data.project.status === "planning") {
    if (planningBanner) planningBanner.style.display = "flex";
    renderPlanningSkeleton();
    document.getElementById("connDot").className = "dot dot-ok";
    document.getElementById("connLabel").textContent = "connected";
    return;
  } else {
    if (planningBanner) planningBanner.style.display = "none";
  }

  // Recruiting banner — update from server and start client-side timer
  var rb = document.getElementById("recruitingBanner");
  if (data.project.status === "recruiting" && data.project.recruitingUntil) {
    rb.style.display = "flex";
    startRecruitingTimer(
      data.project.recruitingUntil,
      data.project.connectedAgents || 0,
      data.project.minAgents || 1
    );
  } else {
    rb.style.display = "none";
    stopRecruitingTimer();
  }

  var p = data.progress;
  var pct = p.total ? Math.round((p.accepted / p.total) * 100) : 0;
  document.getElementById("progressPct").textContent = pct + "%";
  document.getElementById("progressFill").style.width = pct + "%";

  document.getElementById("connDot").className = "dot dot-ok";
  document.getElementById("connLabel").textContent = "connected";

  // Progress ring
  document.getElementById("progressRing").innerHTML = renderProgressRing(pct, 90, 5);

  // Agents
  var al = document.getElementById("agentsList");
  if (data.agents.length === 0) {
    al.innerHTML = '<div class="empty-state">No agents connected</div>';
  } else {
    al.innerHTML = data.agents.map(function(a) {
      var caps = a.capabilities.map(function(c) { return '<span class="cap-tag">' + esc(c) + '</span>'; }).join("");
      var task = a.currentTaskId ? '<div class="agent-meta">Working on: ' + esc(a.currentTaskId) + '</div>' : '';
      var initial = (a.name || "?")[0].toUpperCase();
      var color = hashColor(a.name || a.id);
      return '<div class="agent-card"><div class="agent-header">'
        + '<div class="agent-avatar" style="background:' + color + '">' + initial + '</div>'
        + '<div><div class="agent-name">' + esc(a.name) + '</div>'
        + '<div class="agent-meta">' + relTime(a.joinedAt) + '</div></div></div>'
        + (caps ? '<div style="margin-top:6px">' + caps + '</div>' : '')
        + task + '</div>';
    }).join("");
  }

  var availability = computeTaskAvailability(data.tasks);
  var taskMap = {};
  data.tasks.forEach(function(t) { taskMap[t.id] = t; });

  // Kanban columns
  var columns = {
    "Available": [],
    "In Progress": [],
    "Completed": [],
    "Failed": [],
  };
  for (var i = 0; i < data.tasks.length; i++) {
    var t = data.tasks[i];
    if (t.status === "accepted") { columns["Completed"].push(t); }
    else if (["rejected","failed"].includes(t.status)) { columns["Failed"].push(t); }
    else if (["assigned","in_progress","submitted"].includes(t.status)) { columns["In Progress"].push(t); }
    else { columns["Available"].push(t); }
  }

  var colOrder = ["Available", "In Progress", "Completed", "Failed"];
  var tb = document.getElementById("taskBoard");
  var kanbanHtml = "";
  for (var ci = 0; ci < colOrder.length; ci++) {
    var colName = colOrder[ci];
    var items = columns[colName];
    kanbanHtml += '<div class="kanban-column">';
    kanbanHtml += '<div class="kanban-column-title"><span>' + colName + '</span><span class="kanban-column-count">' + items.length + '</span></div>';
    if (items.length === 0) {
      kanbanHtml += '<div class="empty-state" style="text-align:center;padding:20px 0">None</div>';
    }
    for (var ti = 0; ti < items.length; ti++) {
      var task = items[ti];
      var a2 = availability[task.id];
      var badgeClass = "badge-" + task.status;
      var badgeText = task.status;
      if (task.status === "pending" && a2) {
        if (a2.brokenDeps.length > 0) { badgeClass = "badge-blocked"; badgeText = "broken deps"; }
        else if (a2.blocked) { badgeClass = "badge-blocked"; badgeText = "blocked"; }
        else { badgeClass = "badge-available"; badgeText = "ready"; }
      }
      var meta = "";
      if (task.assignedTo) meta += '<div class="kanban-card-meta">Agent: ' + esc(task.assignedTo.split("-").slice(1,2).join("")) + '</div>';
      if (task.dependencies.length) {
        var depParts = task.dependencies.map(function(depId) {
          var dep = taskMap[depId];
          if (!dep) return '<span class="broken-dep">' + esc(depId) + '?</span>';
          if (dep.status === "accepted") return '<span style="color:#22c55e">' + esc(depId) + ' \\u2713</span>';
          return '<span style="color:#71717a">' + esc(depId) + '</span>';
        });
        meta += '<div class="kanban-card-meta">Deps: ' + depParts.join(", ") + '</div>';
      }
      kanbanHtml += '<div class="kanban-card">'
        + '<div class="kanban-card-header"><span class="kanban-card-id">' + esc(task.id) + '</span><span class="badge ' + badgeClass + '" style="font-size:10px;padding:1px 6px">' + esc(badgeText) + '</span></div>'
        + '<div class="kanban-card-title">' + esc(task.title) + '</div>'
        + meta + '</div>';
    }
    kanbanHtml += '</div>';
  }
  tb.innerHTML = kanbanHtml;

  renderDepGraph(data.tasks);

  document.getElementById("ratioText").textContent = p.accepted + " / " + p.total + " accepted";
  var cl = document.getElementById("checklist");

  // Nia Activity Log
  var niaLog = document.getElementById("niaLog");
  var niaEvents = data.niaEvents || [];
  var niaCountEl = document.getElementById("niaCount");
  if (niaEvents.length === 0) {
    niaLog.innerHTML = '<div class="empty-state">No Nia activity yet</div>';
    niaCountEl.textContent = "";
  } else {
    var finished = niaEvents.filter(function(e) { return e.status !== "started"; });
    niaCountEl.textContent = finished.length + " events";
    var statusIcon = { started: "\\u25CB", success: "\\u2713", error: "\\u2717" };

    // Whether an event type is a background (non-critical) operation
    function isBackgroundOp(type) {
      return type === "index_repo" || type === "index_docs";
    }

    function describeEvent(e) {
      var agentName = e.agentId ? e.agentId.split("-").slice(1, 2).join("") : null;
      var who = agentName || "orchestrator";
      var shortDetail = e.detail.length > 60 ? e.detail.slice(0, 60) + "\\u2026" : e.detail;

      if (e.type === "index_repo") {
        var repo = e.detail.split(":")[0].split("/").pop() || e.detail;
        if (e.status === "error") return "Background: failed to index repo " + repo + " (non-critical)";
        return "Background: indexed project repo " + repo;
      }
      if (e.type === "index_docs") {
        if (e.status === "error") return "Background: failed to index docs (non-critical)";
        return "Background: indexed docs " + shortDetail;
      }
      if (e.type === "search_project" || e.type === "search_general") {
        var words = e.detail.split(/\\s+/).slice(0, 5).join(" ");
        return who + " searched for \\u201c" + words + (e.detail.split(/\\s+/).length > 5 ? "\\u2026" : "") + "\\u201d";
      }
      if (e.type === "search_web") {
        var words2 = e.detail.split(/\\s+/).slice(0, 6).join(" ");
        return who + " researched \\u201c" + words2 + (e.detail.split(/\\s+/).length > 6 ? "\\u2026" : "") + "\\u201d";
      }
      if (e.type === "web_research") return who + " researched \\u201c" + shortDetail + "\\u201d";
      if (e.type === "lookup") return who + " looked up \\u201c" + shortDetail + "\\u201d";
      return shortDetail;
    }

    var typeLabel = { index_repo: "INDEX", index_docs: "INDEX", search_project: "SEARCH", search_general: "SEARCH", search_web: "RESEARCH", web_research: "RESEARCH", lookup: "LOOKUP" };
    var sorted = finished.slice().reverse();
    niaLog.innerHTML = sorted.map(function(e) {
      var time = new Date(e.timestamp);
      var hh = String(time.getHours()).padStart(2, "0");
      var mmm = String(time.getMinutes()).padStart(2, "0");
      var sss = String(time.getSeconds()).padStart(2, "0");
      var timeStr = hh + ":" + mmm + ":" + sss;
      var badge = typeLabel[e.type] || e.type;
      var dur = e.durationMs != null ? (e.durationMs / 1000).toFixed(1) + "s" : "";
      var desc = describeEvent(e);
      var bg = isBackgroundOp(e.type);
      // For background ops that error, use a muted style instead of alarming red
      var statusCls = "nia-status-" + e.status;
      if (bg && e.status === "error") statusCls = "nia-status-bg-error";
      var icon = statusIcon[e.status] || "";
      if (bg && e.status === "error") icon = "\\u2014"; // em dash instead of X for background errors
      var opacityStyle = bg ? ' style="opacity:0.65"' : '';
      return '<div class="nia-event"' + opacityStyle + '>'
        + '<span class="nia-time">' + timeStr + '</span>'
        + '<span class="nia-badge nia-badge-' + esc(e.type) + '">' + esc(badge) + '</span>'
        + '<span class="nia-status ' + statusCls + '">' + icon + '</span>'
        + '<span class="nia-detail">' + esc(desc) + '</span>'
        + '<span class="nia-duration">' + esc(dur) + '</span>'
        + '</div>';
    }).join("");
  }

  cl.innerHTML = data.tasks.map(function(t) {
    var icon, cls;
    if (t.status === "accepted") { icon = "\\u2713"; cls = "check-accepted"; }
    else if (["rejected","failed"].includes(t.status)) { icon = "\\u2717"; cls = "check-failed"; }
    else if (["assigned","in_progress","submitted"].includes(t.status)) { icon = "\\u25CB"; cls = "check-progress"; }
    else { icon = "\\u25CB"; cls = "check-pending"; }
    return '<div class="checklist-item"><span class="check-icon ' + cls + '">' + icon + '</span>'
      + '<span>' + esc(t.id) + ': ' + esc(t.title) + '</span></div>';
  }).join("");
}

// ---------- Evolution ----------

async function fetchEvolutionCards() {
  try {
    var res = await fetch(API_BASE + "/api/evolution-runs");
    if (!res.ok) return;
    var data = await res.json();
    var runs = data.runs || [];
    if (runs.length === 0) return;

    var grid = document.getElementById("projectList");
    var html = runs.map(function(r) {
      var bestFitness = r.best_full_fitness != null ? r.best_full_fitness : r.best_quick_fitness;
      var fitnessHtml = '<div class="evo-metric"><div class="evo-metric-value">' + (bestFitness != null ? fmtNum(bestFitness, 1) : '\\u2014') + '</div><div class="evo-metric-label">fitness</div></div>';
      var liveTag = r.live ? '<span class="live-indicator"></span>' : '';
      var onClickFn = r.live ? "selectEvolutionRun(\\'" + esc(r.id) + "\\', true)" : "selectEvolutionRun(\\'" + esc(r.id) + "\\')";
      return '<div class="project-card evolution-card" onclick="' + onClickFn + '">'
        + '<div class="project-card-left">'
        + '<div class="project-card-name">' + esc(r.name) + ' <span class="badge badge-evolution">evolution</span>' + liveTag + '</div>'
        + '<div class="project-card-desc">' + esc(r.description) + '</div>'
        + '<div class="project-card-meta">' + r.candidate_count + ' candidates &middot; ' + r.latest_iteration + ' iterations</div>'
        + '</div>'
        + '<div class="project-card-right">' + fitnessHtml + '</div>'
        + '</div>';
    }).join("");
    grid.insertAdjacentHTML("beforeend", html);
  } catch(e) {}
}

function selectEvolutionRun(id, live) {
  window.location.href = '/evo/?run=' + encodeURIComponent(id);
}

function fmtNum(v, decimals) {
  if (v == null || !Number.isFinite(v)) return '\\u2014';
  if (decimals != null) return v.toFixed(decimals);
  return String(v);
}

function fmtTokens(n) {
  if (n == null || !Number.isFinite(n)) return '0';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return String(n);
}

// ---------- Polling ----------

async function pollDetail() {
  if (!selectedProjectId || currentView !== "detail") return;
  try {
    var res = await fetch(API_BASE + "/api/status?project_id=" + encodeURIComponent(selectedProjectId));
    if (!res.ok) throw new Error(res.status);
    var data = await res.json();
    if (data.project) renderDetail(data);
  } catch(e) {
    document.getElementById("connDot").className = "dot dot-err";
    document.getElementById("connLabel").textContent = "disconnected";
  }
}

async function tick() {
  if (currentView === "projects") await fetchProjects();
  else if (currentView === "agents") { await fetchLiveProjects(); await fetchProjects(); }
  else await pollDetail();
}

// Initial load
fetchProjects();
setInterval(tick, 3000);

`;
}
