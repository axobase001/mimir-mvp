// ═══════════════════════════════════════════════
// Skuld Dashboard — app.js
// ═══════════════════════════════════════════════

// ═══ Auth State ═══
let authToken = localStorage.getItem('mimir_token');
let currentUser = null;

// ═══ App State ═══
let graphSim = null;
let secChart = null;
let learningChart = null;
let ws = null;
let chatOpen = false;
let prevNodes = [];  // for incremental graph updates
let prevNodeIds = new Set();

// ═══ Category Colors (by category, not source) ═══
const CATEGORY_COLORS = {
  fact: '#0099CC',
  preference: '#5DCAA5',
  procedure: '#F0997B',
  hypothesis: '#AFA9EC',
};

// ═══ Source Colors (for graph nodes) ═══
const SOURCE_COLORS = {
  seed: '#9494A0',
  observation: '#5DCAA5',
  inference: '#85B7EB',
  abstraction: '#AFA9EC',
};

// Fallback: map old source names to categories for backward compat
const SOURCE_TO_CATEGORY = {
  seed: 'fact',
  observation: 'fact',
  inference: 'hypothesis',
  abstraction: 'procedure',
};

function getNodeColor(node) {
  // Prefer category field, fall back to source-based mapping
  const cat = node.category || SOURCE_TO_CATEGORY[node.source] || 'fact';
  return CATEGORY_COLORS[cat] || '#64748B';
}

function getNodeCategory(node) {
  return node.category || SOURCE_TO_CATEGORY[node.source] || 'fact';
}

// ═══ Fetch helpers (with auth) ═══
async function api(path, opts = {}) {
  if (!opts.headers) opts.headers = {};
  if (authToken) {
    opts.headers['Authorization'] = 'Bearer ' + authToken;
  }
  const r = await fetch(path, opts);
  if (r.status === 401) {
    localStorage.removeItem('mimir_token');
    authToken = null;
    showAuthScreen();
    throw new Error('Authentication required');
  }
  return r.json();
}

// ═══ Auth Logic ═══
let isRegistering = false;

function showAuthScreen() {
  const authScreen = document.getElementById('auth-screen');
  const dashScreen = document.getElementById('dashboard-screen');
  if (authScreen) authScreen.style.display = 'flex';
  if (dashScreen) dashScreen.style.display = 'none';
}

function showDashboard() {
  const authScreen = document.getElementById('auth-screen');
  const dashScreen = document.getElementById('dashboard-screen');
  if (authScreen) authScreen.style.display = 'none';
  if (dashScreen) dashScreen.style.display = 'block';
}

function setupAuthForm() {
  const form = document.getElementById('auth-form');
  const toggleLink = document.getElementById('auth-toggle-link');
  const toggleText = document.getElementById('auth-toggle-text');
  const nameInput = document.getElementById('auth-name');
  const submitBtn = document.getElementById('auth-submit');

  if (!form) return;

  toggleLink.onclick = (e) => {
    e.preventDefault();
    isRegistering = !isRegistering;
    if (isRegistering) {
      nameInput.style.display = 'block';
      submitBtn.textContent = 'Register';
      toggleText.textContent = 'Already have an account?';
      toggleLink.textContent = 'Login';
    } else {
      nameInput.style.display = 'none';
      submitBtn.textContent = 'Login';
      toggleText.textContent = "Don't have an account?";
      toggleLink.textContent = 'Register';
    }
  };

  form.onsubmit = async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('auth-error');
    errEl.style.display = 'none';

    const email = document.getElementById('auth-email').value.trim();
    const password = document.getElementById('auth-password').value;
    const displayName = document.getElementById('auth-name').value.trim();

    const endpoint = isRegistering ? '/api/auth/register' : '/api/auth/login';
    const body = { email, password };
    if (isRegistering && displayName) body.display_name = displayName;

    try {
      const data = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(r => r.json());

      if (data.detail) {
        errEl.textContent = data.detail;
        errEl.style.display = 'block';
        return;
      }

      if (data.token) {
        authToken = data.token;
        localStorage.setItem('mimir_token', authToken);
        currentUser = data.user;
        await checkAndRoute();
      }
    } catch (err) {
      errEl.textContent = 'Connection error: ' + err.message;
      errEl.style.display = 'block';
    }
  };
}

async function checkAndRoute() {
  if (!authToken) {
    showAuthScreen();
    return;
  }

  try {
    const me = await api('/api/auth/me');
    if (me.detail) {
      showAuthScreen();
      return;
    }
    currentUser = me;

    // Update user info in topbar
    const nameEl = document.getElementById('user-display-name');
    const planEl = document.getElementById('user-plan');
    if (nameEl) nameEl.textContent = me.display_name || me.email;
    if (planEl) {
      planEl.textContent = me.plan.toUpperCase();
      planEl.className = 'plan-badge plan-' + me.plan;
    }

    if (!me.has_brain) {
      window.location.href = '/onboarding.html';
      return;
    }

    showDashboard();
    refresh();
    connectWS();
  } catch (e) {
    showAuthScreen();
  }
}

function logout() {
  localStorage.removeItem('mimir_token');
  authToken = null;
  currentUser = null;
  if (ws) ws.close();
  showAuthScreen();
}

// ═══ Dashboard refresh ═══
async function refresh() {
  try {
    const d = await api('/api/dashboard');
    document.getElementById('s-beliefs').textContent = d.belief_count;
    document.getElementById('s-clusters').textContent = d.sec_matrix.clusters.length;
    document.getElementById('s-goals').textContent = d.active_goals.length;
    document.getElementById('s-cycle').textContent = d.cycle_count;
    document.getElementById('s-cost').textContent = (d.usage_stats.estimated_cost_usd || 0).toFixed(4);

    renderGraph(d.belief_graph);
    renderSEC(d.sec_matrix.clusters);
    renderGoals(d.all_goals);
    renderNotifications(d.notifications);
    refreshLearningCurve();
  } catch (e) {
    // Silently fail on refresh errors
  }
}

// ═══ Belief Graph (D3 force, incremental) ═══
let graphG = null;
let graphZoom = null;
let currentNodes = [];
let currentLinks = [];
let graphLink = null;
let graphNode = null;
let graphLabel = null;

function renderGraph(bg) {
  const svg = d3.select('#graph-svg');
  const container = document.getElementById('graph-panel');
  if (!container) return;
  const W = container.clientWidth - 32;
  const H = container.clientHeight - 56;
  svg.attr('width', W).attr('height', H);

  if (!bg || !bg.nodes) {
    // Empty state
    if (!graphG) {
      svg.selectAll('*').remove();
      const emptyG = svg.append('g');
      emptyG.append('text')
        .attr('x', W / 2)
        .attr('y', H / 2)
        .attr('text-anchor', 'middle')
        .attr('fill', '#9494A0')
        .attr('font-size', '14px')
        .attr('font-family', "'Source Sans 3', sans-serif")
        .text('No beliefs yet. Start a cycle to populate the graph.');
    }
    return;
  }

  const newNodeIds = new Set(bg.nodes.map(n => n.id));
  const oldNodeIds = prevNodeIds;

  const nodes = bg.nodes.map(n => {
    // Preserve positions from previous simulation
    const prev = currentNodes.find(cn => cn.id === n.id);
    const copy = { ...n };
    if (prev) {
      copy.x = prev.x;
      copy.y = prev.y;
      copy.vx = prev.vx;
      copy.vy = prev.vy;
    }
    return copy;
  });

  const nodeMap = {};
  nodes.forEach(n => nodeMap[n.id] = n);

  const links = bg.edges
    .filter(e => nodeMap[e.from] && nodeMap[e.to])
    .map(e => ({ source: e.from, target: e.to, weight: e.weight }));

  // Detect new and removed nodes
  const addedIds = new Set();
  const removedIds = new Set();
  newNodeIds.forEach(id => { if (!oldNodeIds.has(id)) addedIds.add(id); });
  oldNodeIds.forEach(id => { if (!newNodeIds.has(id)) removedIds.add(id); });

  // Full rebuild if first render
  if (!graphG) {
    svg.selectAll('*').remove();
    graphG = svg.append('g');
    graphZoom = d3.zoom()
      .scaleExtent([0.2, 6])
      .on('zoom', (e) => graphG.attr('transform', e.transform));
    svg.call(graphZoom);
  }

  // Stop old simulation
  if (graphSim) graphSim.stop();

  // Create simulation
  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(60))
    .force('charge', d3.forceManyBody().strength(-120))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => Math.max(5, d.confidence * 20) + 6))
    .alphaDecay(0.02);

  // If we have existing positions, start with low alpha
  if (prevNodeIds.size > 0 && addedIds.size < nodes.length) {
    sim.alpha(0.3);
  }

  // Update links
  graphG.selectAll('.graph-link').remove();
  graphLink = graphG.selectAll('.graph-link')
    .data(links)
    .enter().append('line')
    .attr('class', 'graph-link')
    .attr('stroke', 'rgba(80,80,100,0.12)')
    .attr('stroke-width', d => Math.max(0.8, (d.weight || 0.5) * 2))
    .attr('stroke-opacity', 1);

  // Update nodes
  graphG.selectAll('.graph-node').remove();
  graphNode = graphG.selectAll('.graph-node')
    .data(nodes, d => d.id)
    .enter().append('circle')
    .attr('class', d => 'graph-node' + (addedIds.has(d.id) ? ' node-enter' : ''))
    .attr('r', d => Math.max(4, d.confidence * 20))
    .attr('fill', d => getNodeColor(d))
    .attr('opacity', d => Math.max(0.25, d.confidence))
    .attr('stroke', '#FAFAF8')
    .attr('stroke-width', 1)
    .style('cursor', 'pointer')
    .on('click', (e, d) => showDetail(d))
    .on('mouseenter', (e, d) => showGraphTooltip(e, d))
    .on('mousemove', (e) => moveGraphTooltip(e))
    .on('mouseleave', () => hideGraphTooltip())
    .call(d3.drag()
      .on('start', (e, d) => {
        if (!e.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end', (e, d) => {
        if (!e.active) sim.alphaTarget(0);
        d.fx = null; d.fy = null;
      })
    );

  // Update labels (only for high-confidence nodes)
  graphG.selectAll('.node-label').remove();
  graphLabel = graphG.selectAll('.node-label')
    .data(nodes.filter(n => n.confidence > 0.6))
    .enter().append('text')
    .attr('class', 'node-label')
    .attr('text-anchor', 'middle')
    .attr('dy', d => -Math.max(5, d.confidence * 20) - 5)
    .text(d => d.statement ? d.statement.slice(0, 24) : d.id);

  sim.on('tick', () => {
    graphLink
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    graphNode
      .attr('cx', d => d.x).attr('cy', d => d.y);
    graphLabel
      .attr('x', d => d.x).attr('y', d => d.y);
  });

  // Store state for next incremental update
  currentNodes = nodes;
  currentLinks = links;
  prevNodeIds = newNodeIds;
  graphSim = sim;
}

// ═══ Graph Tooltip ═══
function showGraphTooltip(event, d) {
  const tt = document.getElementById('graph-tooltip');
  if (!tt) return;
  const cat = getNodeCategory(d);
  const catColor = CATEGORY_COLORS[cat] || '#9494A0';

  tt.innerHTML = `
    <div class="tt-id">${esc(d.id)}</div>
    <div class="tt-stmt">${esc(d.statement ? d.statement.slice(0, 100) : '')}</div>
    <div class="tt-meta">
      <span class="tt-cat" style="background:${catColor}18;color:${catColor}">${cat}</span>
      &nbsp; conf: ${(d.confidence || 0).toFixed(2)}
    </div>
  `;
  tt.style.display = 'block';
  moveGraphTooltip(event);
}

function moveGraphTooltip(event) {
  const tt = document.getElementById('graph-tooltip');
  if (!tt) return;
  tt.style.left = (event.clientX + 14) + 'px';
  tt.style.top = (event.clientY - 10) + 'px';
}

function hideGraphTooltip() {
  const tt = document.getElementById('graph-tooltip');
  if (tt) tt.style.display = 'none';
}

// ═══ SEC Matrix (Chart.js horizontal bar) ═══
function renderSEC(clusters) {
  const canvas = document.getElementById('sec-chart');
  if (!canvas) return;

  // Filter: only clusters with obs_count >= 2 && not_count >= 2
  let filtered = clusters.filter(c =>
    (c.obs_count >= 2 || c.obs_count === undefined) &&
    (c.not_count >= 2 || c.not_count === undefined)
  );

  // Sort by C value descending
  filtered.sort((a, b) => b.c_value - a.c_value);

  // Limit to top 15
  const top = filtered.slice(0, 15);

  // Count pos/neg
  const posCount = clusters.filter(c => c.c_value > 0).length;
  const negCount = clusters.filter(c => c.c_value < 0).length;
  const badge = document.getElementById('sec-pos-neg');
  if (badge) badge.textContent = `${posCount}+ / ${negCount}\u2212`;

  const data = {
    labels: top.map(c => c.name),
    datasets: [{
      data: top.map(c => c.c_value),
      backgroundColor: top.map(c => {
        if (c.c_value > 0) return 'rgba(0, 153, 204, 0.6)';
        if (c.c_value < 0) return 'rgba(240, 153, 123, 0.6)';
        return 'rgba(148, 148, 160, 0.3)';
      }),
      borderColor: top.map(c => {
        if (c.c_value > 0) return '#0099CC';
        if (c.c_value < 0) return '#F0997B';
        return '#9494A0';
      }),
      borderWidth: 1,
      borderRadius: 4,
      barThickness: 14,
    }],
  };

  if (secChart) {
    secChart.data = data;
    secChart.update('none');
    return;
  }

  secChart = new Chart(canvas, {
    type: 'bar',
    data,
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#FFFFFF',
          borderColor: 'rgba(0,0,0,0.06)',
          borderWidth: 1,
          titleColor: '#1A1A1F',
          bodyColor: '#5C5C6A',
          bodyFont: { family: "'JetBrains Mono', monospace", size: 11 },
          padding: 12,
          boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
          callbacks: {
            label: (ctx) => {
              const c = top[ctx.dataIndex];
              return `C=${c.c_value.toFixed(3)} (obs=${c.obs_count}, not=${c.not_count})`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: { color: 'rgba(0,0,0,0.04)', drawBorder: false },
          ticks: { color: '#9494A0', font: { size: 11, family: "'JetBrains Mono', monospace" } },
        },
        y: {
          grid: { display: false },
          ticks: {
            color: '#9494A0',
            font: { size: 11, family: "'JetBrains Mono', monospace" },
            callback: function(val, idx) {
              const label = this.getLabelForValue(val);
              return label.length > 20 ? label.slice(0, 18) + '..' : label;
            },
          },
        },
      },
      onClick: (e, els) => {
        if (els.length) showSECDetail(top[els[0].index].name);
      },
    },
  });
}

// ═══ Goals ═══
function renderGoals(goals) {
  const el = document.getElementById('goals-list');
  if (!el) return;

  const active = goals.filter(g => g.status === 'active');
  const done = goals.filter(g => g.status !== 'active');

  let html = active.map(g => {
    const origin = g.origin || 'exogenous';
    return `
      <div class="goal-item active-goal">
        <span class="goal-status active">ACTIVE</span>
        <span class="goal-origin ${origin}">${origin.toUpperCase()}</span>
        <span class="goal-desc">${esc(g.description)}</span>
        <span class="goal-pri">${(g.priority || 0).toFixed(2)}</span>
        <button class="goal-btn" onclick="completeGoal('${g.id}')">Done</button>
        <button class="goal-btn" onclick="abandonGoal('${g.id}')">Drop</button>
      </div>
    `;
  }).join('');

  if (done.length > 0) {
    html += `
      <div class="goals-done-section">
        <button class="goals-done-toggle" onclick="toggleDoneGoals(this)">
          <span class="arrow">&#9654;</span> ${done.length} completed / abandoned
        </button>
        <div class="goals-done-list">
          ${done.map(g => `
            <div class="goal-item ${g.status === 'completed' ? 'completed-goal' : 'abandoned-goal'}">
              <span class="goal-status ${g.status}">${g.status.toUpperCase()}</span>
              <span class="goal-desc">${esc(g.description)}</span>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }

  el.innerHTML = html;
}

function toggleDoneGoals(btn) {
  const arrow = btn.querySelector('.arrow');
  const list = btn.parentElement.querySelector('.goals-done-list');
  if (list.classList.contains('open')) {
    list.classList.remove('open');
    arrow.classList.remove('open');
  } else {
    list.classList.add('open');
    arrow.classList.add('open');
  }
}

let goalFormVisible = false;

function toggleGoalForm() {
  goalFormVisible = !goalFormVisible;
  const form = document.getElementById('add-goal-form');
  if (form) {
    form.style.display = goalFormVisible ? 'flex' : 'none';
    if (goalFormVisible) {
      const input = document.getElementById('new-goal-input');
      if (input) input.focus();
    }
  }
}

async function addGoal() {
  const input = document.getElementById('new-goal-input');
  const desc = input.value.trim();
  if (!desc) return;
  await api('/api/goals', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ description: desc, priority: 0.5 }),
  });
  input.value = '';
  goalFormVisible = false;
  document.getElementById('add-goal-form').style.display = 'none';
  refresh();
}

async function completeGoal(id) {
  await api(`/api/goals/${id}/complete`, { method: 'PUT' });
  refresh();
}

async function abandonGoal(id) {
  await api(`/api/goals/${id}/abandon`, { method: 'PUT' });
  refresh();
}

// ═══ Notifications ═══
function renderNotifications(notifs) {
  // Show only latest unread
}

function showNotification(n) {
  const el = document.createElement('div');
  el.className = `notification ${n.level}`;
  el.textContent = `[${n.level.toUpperCase()}] ${n.title}: ${n.body}`;
  const container = document.getElementById('notifications');
  if (container) container.appendChild(el);
  setTimeout(() => {
    el.style.animation = 'notifOut 0.25s ease-out forwards';
    setTimeout(() => el.remove(), 250);
  }, 3000);
}

// ═══ Detail overlay ═══
async function showDetail(node) {
  try {
    const d = await api(`/api/beliefs/${node.id}`);
    if (d.error) return;
    const el = document.getElementById('detail-content');
    if (!el) return;

    const cat = d.category || SOURCE_TO_CATEGORY[d.source] || 'fact';
    const catColor = CATEGORY_COLORS[cat] || '#9494A0';

    el.innerHTML = `
      <h3>${esc(d.statement)}</h3>
      <p><b>ID:</b> <span style="font-family:var(--font-mono);font-size:11px;color:var(--text2)">${d.id}</span></p>
      <p><b>Category:</b> <span style="color:${catColor};font-weight:500">${cat}</span>
         &nbsp;&nbsp; <b>Source:</b> <span style="color:var(--text2)">${d.source}</span></p>
      <p><b>Confidence:</b> ${d.confidence.toFixed(4)} &nbsp;&nbsp; <b>Tags:</b> ${(d.tags || []).join(', ') || 'none'}</p>
      <p><b>Created:</b> Cycle ${d.created_at} &nbsp;&nbsp; <b>Verified:</b> Cycle ${d.last_verified}</p>
      ${d.parent_ids && d.parent_ids.length ? `<p><b>Parents:</b> ${d.parent_ids.join(', ')}</p>` : ''}
      <p style="margin-top:14px"><b>PE History:</b></p>
      <div style="display:flex;gap:2px;height:40px;align-items:flex-end;margin-top:4px">
        ${(d.pe_history || []).map(pe => `<div style="width:8px;background:${pe > 0.3 ? '#F0997B' : '#5DCAA5'};height:${Math.max(2, pe * 40)}px;border-radius:2px;opacity:0.8" title="${pe.toFixed(3)}"></div>`).join('')}
      </div>
    `;
    document.getElementById('detail-overlay').classList.add('visible');
  } catch (e) {
    // Silently fail
  }
}

async function showSECDetail(name) {
  try {
    const d = await api(`/api/sec/${name}`);
    if (d.error) return;
    const el = document.getElementById('detail-content');
    if (!el) return;
    const cColor = d.c_value >= 0 ? '#0099CC' : '#F0997B';
    el.innerHTML = `
      <h3>${esc(d.cluster)}</h3>
      <p><b>C value:</b> <span style="color:${cColor};font-weight:600;font-family:var(--font-mono)">${d.c_value.toFixed(4)}</span></p>
      <p><b>d_obs:</b> ${d.d_obs.toFixed(4)} &nbsp;&nbsp; <b>d_not:</b> ${d.d_not.toFixed(4)}</p>
      <p><b>Observed:</b> ${d.obs_count} cycles &nbsp;&nbsp; <b>Not observed:</b> ${d.not_count} cycles</p>
    `;
    document.getElementById('detail-overlay').classList.add('visible');
  } catch (e) {
    // Silently fail
  }
}

function closeDetail() {
  const el = document.getElementById('detail-overlay');
  if (el) el.classList.remove('visible');
}

// Close on Escape key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeDetail();
    if (chatOpen) toggleChat();
  }
});

// ═══ Chat ═══
function toggleChat() {
  chatOpen = !chatOpen;
  const drawer = document.getElementById('chat-drawer');
  const toggle = document.getElementById('chat-toggle');
  if (drawer) {
    drawer.classList.toggle('open', chatOpen);
  }
  if (toggle) {
    toggle.classList.toggle('hidden', chatOpen);
  }
  if (chatOpen) {
    const input = document.getElementById('chat-input');
    if (input) setTimeout(() => input.focus(), 300);
  }
}

function setupChat() {
  const sendBtn = document.getElementById('chat-send');
  const chatInput = document.getElementById('chat-input');
  if (sendBtn) sendBtn.onclick = sendChat;
  if (chatInput) chatInput.onkeydown = (e) => { if (e.key === 'Enter') sendChat(); };
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  if (!input) return;
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  appendChat('user', msg);

  try {
    const data = await api('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg }),
    });

    let meta = `conf=${data.confidence.toFixed(2)}`;
    if (data.sources && data.sources.length) meta += ` | sources: ${data.sources.join(', ')}`;
    if (data.searching) meta += ' | searching...';

    appendChat('skuld', data.reply, meta);
  } catch (e) {
    appendChat('skuld', 'Error: ' + e.message);
  }
}

function appendChat(role, text, meta) {
  const el = document.getElementById('chat-messages');
  if (!el) return;
  const div = document.createElement('div');
  div.className = `msg msg-${role}`;
  div.innerHTML = esc(text) + (meta ? `<div class="msg-meta">${esc(meta)}</div>` : '');
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

// ═══ WebSocket ═══
function connectWS() {
  if (ws) {
    try { ws.close(); } catch (e) {}
  }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const tokenParam = authToken ? `?token=${encodeURIComponent(authToken)}` : '';
  ws = new WebSocket(`${proto}://${location.host}/ws${tokenParam}`);

  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'cycle_end') refresh();
    if (data.type === 'notification') showNotification(data);
  };

  ws.onclose = () => {
    if (authToken) setTimeout(connectWS, 3000);
  };
  ws.onerror = () => ws.close();
}

// ═══ Learning Curve ═══
async function refreshLearningCurve() {
  try {
    const d = await api('/api/metrics/learning_curve');
    renderLearningCurve(d);
  } catch (e) {
    // Silently fail
  }
}

function renderLearningCurve(data) {
  const canvas = document.getElementById('learning-chart');
  if (!canvas || !data.pe_trend || data.pe_trend.length === 0) return;

  const labels = data.pe_trend.map(p => 'C' + p.cycle);
  const peData = data.pe_trend.map(p => p.pe_before);

  const chartData = {
    labels: labels,
    datasets: [{
      label: 'PE per cycle',
      data: peData,
      borderColor: '#0099CC',
      backgroundColor: 'rgba(0, 153, 204, 0.06)',
      fill: true,
      tension: 0.3,
      pointRadius: 2,
      pointBackgroundColor: '#0099CC',
      borderWidth: 1.5,
    }],
  };

  if (learningChart) {
    learningChart.data = chartData;
    learningChart.update('none');
    return;
  }

  learningChart = new Chart(canvas, {
    type: 'line',
    data: chartData,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          labels: {
            color: '#9494A0',
            font: { size: 11, family: "'Source Sans 3', sans-serif" },
            boxWidth: 12,
            boxHeight: 2,
          },
        },
        tooltip: {
          backgroundColor: '#FFFFFF',
          borderColor: 'rgba(0,0,0,0.06)',
          borderWidth: 1,
          titleColor: '#1A1A1F',
          bodyColor: '#5C5C6A',
          bodyFont: { family: "'JetBrains Mono', monospace", size: 11 },
          padding: 12,
          callbacks: {
            label: (ctx) => `PE=${ctx.parsed.y.toFixed(4)}`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: 'rgba(0,0,0,0.04)', drawBorder: false },
          ticks: {
            color: '#9494A0',
            font: { size: 11, family: "'JetBrains Mono', monospace" },
            maxTicksLimit: 20,
          },
        },
        y: {
          grid: { color: 'rgba(0,0,0,0.04)', drawBorder: false },
          ticks: {
            color: '#9494A0',
            font: { size: 11, family: "'JetBrains Mono', monospace" },
          },
          beginAtZero: true,
        },
      },
    },
  });

  const statsEl = document.getElementById('learning-stats');
  if (statsEl) {
    statsEl.innerHTML = `
      <span>Beliefs/cycle: ${data.beliefs_per_cycle || '—'}</span>
      <span>SEC spread: ${data.sec_spread || '—'}</span>
      <span>Avg tokens/cycle: ${data.avg_tokens_per_cycle || '—'}</span>
    `;
  }
}

// ═══ Utils ═══
function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

// ═══ Init ═══
setupAuthForm();
setupChat();

// DEV MODE: skip auth, show dashboard directly
showDashboard();
refresh();
connectWS();
setInterval(refresh, 15000);
