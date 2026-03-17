// ═══ Auth State ═══
let authToken = localStorage.getItem('mimir_token');
let currentUser = null;

// ═══ State ═══
let graphSim = null;
let secChart = null;
let ws = null;

const SOURCE_COLORS = {
  seed: '#3b82f6',
  observation: '#22c55e',
  inference: '#f97316',
  abstraction: '#a78bfa',
};

// ═══ Fetch helpers (with auth) ═══
async function api(path, opts = {}) {
  if (!opts.headers) opts.headers = {};
  if (authToken) {
    opts.headers['Authorization'] = 'Bearer ' + authToken;
  }
  const r = await fetch(path, opts);
  if (r.status === 401) {
    // Token expired or invalid
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
      // Redirect to onboarding
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
    document.getElementById('s-cost').textContent = '$' + (d.usage_stats.estimated_cost_usd || 0).toFixed(4);

    renderGraph(d.belief_graph);
    renderSEC(d.sec_matrix.clusters);
    renderGoals(d.all_goals);
    renderLog(d.recent_episodes);
    renderNotifications(d.notifications);
  } catch (e) {
    // Silently fail on refresh errors
  }
}

// ═══ Belief Graph (D3 force) ═══
function renderGraph(bg) {
  const svg = d3.select('#graph-svg');
  const container = document.getElementById('graph-panel');
  if (!container) return;
  const W = container.clientWidth - 24;
  const H = container.clientHeight - 40;
  svg.attr('width', W).attr('height', H);
  svg.selectAll('*').remove();

  const g = svg.append('g');

  // Zoom
  svg.call(d3.zoom().scaleExtent([0.3, 5]).on('zoom', (e) => g.attr('transform', e.transform)));

  const nodes = bg.nodes.map(n => ({ ...n }));
  const nodeMap = {};
  nodes.forEach(n => nodeMap[n.id] = n);

  const links = bg.edges
    .filter(e => nodeMap[e.from] && nodeMap[e.to])
    .map(e => ({ source: e.from, target: e.to, weight: e.weight }));

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(60))
    .force('charge', d3.forceManyBody().strength(-80))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => Math.max(6, d.confidence * 18) + 4));

  const link = g.selectAll('.link')
    .data(links).enter().append('line')
    .attr('stroke', '#1a2035')
    .attr('stroke-width', d => Math.max(0.5, d.weight * 2));

  const node = g.selectAll('.node')
    .data(nodes).enter().append('circle')
    .attr('r', d => Math.max(4, d.confidence * 16))
    .attr('fill', d => SOURCE_COLORS[d.source] || '#64748b')
    .attr('opacity', d => Math.max(0.2, d.confidence))
    .attr('stroke', '#0a0e17')
    .attr('stroke-width', 0.5)
    .style('cursor', 'pointer')
    .on('click', (e, d) => showDetail(d))
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end', (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
    );

  // Tooltips
  node.append('title').text(d => `${d.id}: ${d.statement.slice(0, 60)}... (${d.confidence.toFixed(2)})`);

  const label = g.selectAll('.node-label')
    .data(nodes.filter(n => n.confidence > 0.5)).enter().append('text')
    .attr('class', 'node-label')
    .attr('text-anchor', 'middle')
    .attr('dy', d => -Math.max(6, d.confidence * 16) - 4)
    .text(d => d.statement.slice(0, 20));

  sim.on('tick', () => {
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node.attr('cx', d => d.x).attr('cy', d => d.y);
    label.attr('x', d => d.x).attr('y', d => d.y);
  });

  graphSim = sim;
}

// ═══ SEC Matrix (Chart.js) ═══
function renderSEC(clusters) {
  const canvas = document.getElementById('sec-chart');
  if (!canvas) return;
  const top20 = clusters.slice(0, 20);

  const data = {
    labels: top20.map(c => c.name),
    datasets: [{
      data: top20.map(c => c.c_value),
      backgroundColor: top20.map(c => c.c_value >= 0 ? 'rgba(34,197,94,0.7)' : 'rgba(239,68,68,0.7)'),
      borderWidth: 0,
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
          callbacks: {
            label: (ctx) => {
              const c = top20[ctx.dataIndex];
              return `C=${c.c_value.toFixed(3)} (obs=${c.obs_count}, not=${c.not_count})`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: { color: '#1a2035' },
          ticks: { color: '#64748b', font: { size: 10 } },
        },
        y: {
          grid: { display: false },
          ticks: { color: '#c8cdd5', font: { size: 10, family: 'monospace' } },
        },
      },
      onClick: (e, els) => {
        if (els.length) showSECDetail(top20[els[0].index].name);
      },
    },
  });
}

// ═══ Goals ═══
function renderGoals(goals) {
  const el = document.getElementById('goals-list');
  if (!el) return;
  const active = goals.filter(g => g.status === 'active');
  const done = goals.filter(g => g.status !== 'active').slice(-5);

  el.innerHTML = active.map(g => `
    <div class="goal-item">
      <span class="goal-status active">ACTIVE</span>
      <span class="goal-desc">${esc(g.description)}</span>
      <span class="goal-pri">${g.priority.toFixed(2)}</span>
      <button class="goal-btn" onclick="completeGoal('${g.id}')">Done</button>
      <button class="goal-btn" onclick="abandonGoal('${g.id}')">Drop</button>
    </div>
  `).join('') + done.map(g => `
    <div class="goal-item" style="opacity:0.4">
      <span class="goal-status completed">${g.status.toUpperCase()}</span>
      <span class="goal-desc">${esc(g.description)}</span>
    </div>
  `).join('');
}

async function addGoal() {
  const input = document.getElementById('new-goal-input');
  const desc = input.value.trim();
  if (!desc) return;
  await api('/api/goals', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ description: desc, priority: 0.5 }) });
  input.value = '';
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

// ═══ Cycle Log ═══
function renderLog(episodes) {
  const el = document.getElementById('log-list');
  if (!el) return;
  el.innerHTML = episodes.reverse().map(ep => `
    <div class="log-item">
      <span class="log-cycle">Cycle ${ep.cycle}</span>
      ${esc(ep.outcome || ep.action)}
    </div>
  `).join('');
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
  setTimeout(() => el.remove(), 8000);
}

// ═══ Detail overlay ═══
async function showDetail(node) {
  const d = await api(`/api/beliefs/${node.id}`);
  if (d.error) return;
  const el = document.getElementById('detail-content');
  if (!el) return;
  el.innerHTML = `
    <h3 style="color:var(--text-bright);margin-bottom:8px">${esc(d.statement)}</h3>
    <p><b>ID:</b> ${d.id} &nbsp; <b>Source:</b> <span style="color:${SOURCE_COLORS[d.source]}">${d.source}</span></p>
    <p><b>Confidence:</b> ${d.confidence.toFixed(4)} &nbsp; <b>Tags:</b> ${d.tags.join(', ')}</p>
    <p><b>Created:</b> Cycle ${d.created_at} &nbsp; <b>Verified:</b> Cycle ${d.last_verified}</p>
    ${d.parent_ids.length ? `<p><b>Parents:</b> ${d.parent_ids.join(', ')}</p>` : ''}
    <p style="margin-top:12px"><b>PE History:</b></p>
    <div style="display:flex;gap:2px;height:40px;align-items:flex-end">
      ${d.pe_history.map(pe => `<div style="width:8px;background:${pe > 0.3 ? 'var(--red)' : 'var(--green)'};height:${Math.max(2, pe * 40)}px;border-radius:1px" title="${pe.toFixed(3)}"></div>`).join('')}
    </div>
  `;
  document.getElementById('detail-overlay').classList.add('visible');
}

async function showSECDetail(name) {
  const d = await api(`/api/sec/${name}`);
  if (d.error) return;
  const el = document.getElementById('detail-content');
  if (!el) return;
  el.innerHTML = `
    <h3 style="color:var(--text-bright)">${esc(d.cluster)}</h3>
    <p><b>C value:</b> <span style="color:${d.c_value >= 0 ? 'var(--green)' : 'var(--red)'}">${d.c_value.toFixed(4)}</span></p>
    <p><b>d_obs:</b> ${d.d_obs.toFixed(4)} &nbsp; <b>d_not:</b> ${d.d_not.toFixed(4)}</p>
    <p><b>Observed:</b> ${d.obs_count} cycles &nbsp; <b>Not observed:</b> ${d.not_count} cycles</p>
  `;
  document.getElementById('detail-overlay').classList.add('visible');
}

function closeDetail() {
  const el = document.getElementById('detail-overlay');
  if (el) el.classList.remove('visible');
}

// ═══ Chat ═══
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
    if (data.sources.length) meta += ` | sources: ${data.sources.join(', ')}`;
    if (data.searching) meta += ' | searching...';

    appendChat('mimir', data.reply, meta);
  } catch (e) {
    appendChat('mimir', 'Error: ' + e.message);
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
    try { ws.close(); } catch(e) {}
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

// Check token on load
if (authToken) {
  checkAndRoute();
} else {
  showAuthScreen();
}
