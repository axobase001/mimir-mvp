// ═══════════════════════════════════════════════
// Skuld Dashboard — app.js
// ═══════════════════════════════════════════════

// ═══ i18n ═══
const i18n = {
  zh: {
    beliefGraph: '信念图谱',
    secMatrix: 'SEC注意力矩阵',
    goals: '目标',
    learningCurve: '学习曲线',
    cycle: '周期',
    beliefs: '信念',
    cost: '成本',
    active: '活跃',
    completed: '已完成',
    abandoned: '已放弃',
    endogenous: '内生',
    exogenous: '外生',
    fact: '事实',
    preference: '偏好',
    procedure: '程序',
    hypothesis: '假设',
    chat: '对话',
    settings: '设置',
    add: '添加',
    done: '完成',
    drop: '放弃',
    noBeliefs: '暂无信念',
    searching: '搜索中...',
    send: '发送',
    askSkuld: '问Skuld...',
    login: '登录',
    register: '注册',
    noAccount: '没有账号？',
    hasAccount: '已有账号？',
    logout: '退出',
    email: '邮箱',
    password: '密码',
    displayName: '显示名称（可选）',
    completedAbandoned: '已完成 / 已放弃',
  },
  en: {
    beliefGraph: 'Belief Graph',
    secMatrix: 'SEC Attention Matrix',
    goals: 'Goals',
    learningCurve: 'Learning Curve',
    cycle: 'Cycle',
    beliefs: 'Beliefs',
    cost: 'Cost',
    active: 'Active',
    completed: 'Completed',
    abandoned: 'Abandoned',
    endogenous: 'Endogenous',
    exogenous: 'Exogenous',
    fact: 'Fact',
    preference: 'Preference',
    procedure: 'Procedure',
    hypothesis: 'Hypothesis',
    chat: 'Chat',
    settings: 'Settings',
    add: 'Add',
    done: 'Done',
    drop: 'Drop',
    noBeliefs: 'No beliefs yet',
    searching: 'Searching...',
    send: 'Send',
    askSkuld: 'Ask Skuld...',
    login: 'Login',
    register: 'Register',
    noAccount: "Don't have an account?",
    hasAccount: 'Already have an account?',
    logout: 'Logout',
    email: 'Email',
    password: 'Password (min 8 chars)',
    displayName: 'Display name (optional)',
    completedAbandoned: 'completed / abandoned',
  }
};

let currentLang = localStorage.getItem('skuld_lang') || 'zh';

function t(key) {
  return (i18n[currentLang] && i18n[currentLang][key]) || (i18n.zh[key]) || key;
}

function applyI18n() {
  // Update all elements with data-i18n attribute
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (i18n[currentLang] && i18n[currentLang][key]) {
      el.textContent = i18n[currentLang][key];
    }
  });

  // Update all elements with data-i18n-placeholder attribute
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    if (i18n[currentLang] && i18n[currentLang][key]) {
      el.placeholder = i18n[currentLang][key];
    }
  });

  // Update auth form placeholders and buttons
  const authEmail = document.getElementById('auth-email');
  const authPassword = document.getElementById('auth-password');
  const authName = document.getElementById('auth-name');
  const authSubmit = document.getElementById('auth-submit');
  const authToggleText = document.getElementById('auth-toggle-text');
  const authToggleLink = document.getElementById('auth-toggle-link');

  if (authEmail) authEmail.placeholder = t('email');
  if (authPassword) authPassword.placeholder = t('password');
  if (authName) authName.placeholder = t('displayName');

  if (authSubmit) {
    authSubmit.textContent = isRegistering ? t('register') : t('login');
  }
  if (authToggleText) {
    authToggleText.textContent = isRegistering ? t('hasAccount') : t('noAccount');
  }
  if (authToggleLink) {
    authToggleLink.textContent = isRegistering ? t('login') : t('register');
  }

  // Update logout button
  const logoutBtn = document.getElementById('logout-btn');
  if (logoutBtn) logoutBtn.textContent = t('logout');

  // Update lang toggle buttons
  const langBtn = document.getElementById('lang-toggle');
  const langBtnSettings = document.getElementById('lang-toggle-settings');
  const nextLabel = currentLang === 'zh' ? 'EN' : '中';
  if (langBtn) langBtn.textContent = nextLabel;
  if (langBtnSettings) langBtnSettings.textContent = nextLabel;
}

function toggleLang() {
  currentLang = currentLang === 'zh' ? 'en' : 'zh';
  localStorage.setItem('skuld_lang', currentLang);
  applyI18n();
  // Re-render goals to update translated status labels
  if (lastGoalsData) renderGoals(lastGoalsData);
}

// ═══ Auth State ═══
let authToken = localStorage.getItem('mimir_token');
let currentUser = null;
let isRegistering = false;

// ═══ App State ═══
let graphSim = null;
let secChart = null;
let learningChart = null;
let ws = null;
let chatOpen = false;
let prevNodes = [];
let prevNodeIds = new Set();
let lastGoalsData = null;

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

// ═══ Screen Management ═══
function hideAllScreens() {
  document.getElementById('auth-screen').style.display = 'none';
  document.getElementById('onboarding-screen').style.display = 'none';
  document.getElementById('dashboard-screen').style.display = 'none';
  document.getElementById('settings-screen').style.display = 'none';
}

function showAuthScreen() {
  hideAllScreens();
  document.getElementById('auth-screen').style.display = 'flex';
}

function showOnboarding() {
  hideAllScreens();
  document.getElementById('onboarding-screen').style.display = 'flex';
  // Reset onboarding state
  onbDirections = [];
  onbCustomDirections = [];
  document.querySelectorAll('.preset-tag').forEach(b => b.classList.remove('selected'));
  renderOnbSelected();
  showOnbStep(1);
}

function showDashboard() {
  hideAllScreens();
  document.getElementById('dashboard-screen').style.display = 'block';
  loadChatHistory();
}

function showSettings() {
  hideAllScreens();
  document.getElementById('settings-screen').style.display = 'block';
  loadSettingsData();
}

function hideSettings() {
  showDashboard();
}

// ═══ Auth Logic ═══
function setupAuthForm() {
  const form = document.getElementById('auth-form');
  const toggleLink = document.getElementById('auth-toggle-link');

  if (!form) return;

  toggleLink.onclick = (e) => {
    e.preventDefault();
    isRegistering = !isRegistering;
    const nameInput = document.getElementById('auth-name');
    if (isRegistering) {
      nameInput.style.display = 'block';
    } else {
      nameInput.style.display = 'none';
    }
    applyI18n();
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
      showOnboarding();
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

// ═══ Onboarding Logic ═══
let onbDirections = [];      // selected preset labels
let onbCustomDirections = []; // user-added custom strings

const PRESET_TAG_MAP = {
  '时政新闻': { tags: ['时政', '新闻'] },
  '科技动态': { tags: ['科技', 'tech'] },
  '金融市场': { tags: ['金融', 'finance'] },
  '行业分析': { tags: ['行业', 'industry'] },
  '国际关系': { tags: ['国际', 'geopolitics'] },
  '社会民生': { tags: ['社会', '民生'] },
};

function togglePreset(btn) {
  const label = btn.dataset.label;
  btn.classList.toggle('selected');
  if (btn.classList.contains('selected')) {
    if (!onbDirections.includes(label)) onbDirections.push(label);
  } else {
    onbDirections = onbDirections.filter(d => d !== label);
  }
  renderOnbSelected();
}

function addCustomDirection() {
  const input = document.getElementById('onb-custom-input');
  const val = input.value.trim();
  if (!val) return;
  if (onbCustomDirections.length >= 5) return;
  if (onbCustomDirections.includes(val) || onbDirections.includes(val)) return;
  onbCustomDirections.push(val);
  input.value = '';
  renderOnbSelected();
}

function removeDirection(label, isCustom) {
  if (isCustom) {
    onbCustomDirections = onbCustomDirections.filter(d => d !== label);
  } else {
    onbDirections = onbDirections.filter(d => d !== label);
    // Unselect the preset button
    document.querySelectorAll('.preset-tag').forEach(btn => {
      if (btn.dataset.label === label) btn.classList.remove('selected');
    });
  }
  renderOnbSelected();
}

function renderOnbSelected() {
  const container = document.getElementById('onb-selected-list');
  if (!container) return;

  const all = [
    ...onbDirections.map(d => ({ label: d, custom: false })),
    ...onbCustomDirections.map(d => ({ label: d, custom: true })),
  ];

  container.innerHTML = all.map(d => `
    <span class="onb-selected-item">
      ${esc(d.label)}
      <button class="remove-dir" onclick="removeDirection('${esc(d.label)}', ${d.custom})">&times;</button>
    </span>
  `).join('');
}

function showOnbStep(step) {
  for (let i = 1; i <= 3; i++) {
    const el = document.getElementById('onb-step-' + i);
    if (el) el.style.display = (i === step) ? 'block' : 'none';
  }
}

function onbNext(currentStep) {
  if (currentStep === 1) {
    const allDirs = [...onbDirections, ...onbCustomDirections];
    if (allDirs.length === 0) return; // must select at least one
    showOnbStep(2);
  } else if (currentStep === 2) {
    populateSummary();
    showOnbStep(3);
  }
}

function onbSkipApi() {
  populateSummary();
  showOnbStep(3);
}

function onLLMProviderChange() {
  const provider = document.getElementById('onb-llm-provider').value;
  const baseUrlInput = document.getElementById('onb-llm-base-url');
  const defaults = {
    deepseek: 'https://api.deepseek.com',
    gpt4o: 'https://api.openai.com',
    claude: 'https://api.anthropic.com',
  };
  if (baseUrlInput) baseUrlInput.value = defaults[provider] || '';
}

function onSearchProviderChange() {
  const provider = document.getElementById('onb-search-provider').value;
  const keyRow = document.getElementById('onb-search-key-row');
  if (keyRow) {
    keyRow.style.display = (provider === 'searxng') ? 'none' : 'flex';
  }
}

async function testLLMConnection() {
  const resultEl = document.getElementById('llm-test-result');
  const key = document.getElementById('onb-llm-key').value.trim();
  if (!key) {
    resultEl.textContent = 'Please enter an API key';
    resultEl.className = 'onb-test-result error';
    return;
  }
  resultEl.textContent = '...';
  resultEl.className = 'onb-test-result';
  try {
    // Simple validation - just check key format
    if (key.length > 10) {
      resultEl.textContent = 'Key format OK';
      resultEl.className = 'onb-test-result success';
    } else {
      resultEl.textContent = 'Key too short';
      resultEl.className = 'onb-test-result error';
    }
  } catch (e) {
    resultEl.textContent = 'Error: ' + e.message;
    resultEl.className = 'onb-test-result error';
  }
}

function populateSummary() {
  const allDirs = [...onbDirections, ...onbCustomDirections];
  const dirEl = document.getElementById('onb-summary-directions');
  const llmEl = document.getElementById('onb-summary-llm');
  const searchEl = document.getElementById('onb-summary-search');

  if (dirEl) dirEl.textContent = allDirs.join('、');

  const llmProvider = document.getElementById('onb-llm-provider');
  const llmKey = document.getElementById('onb-llm-key');
  const hasLlmKey = llmKey && llmKey.value.trim();
  if (llmEl) {
    const providerName = llmProvider ? llmProvider.options[llmProvider.selectedIndex].text : 'DeepSeek';
    llmEl.textContent = providerName + (hasLlmKey ? '' : '（默认）');
  }

  const searchProvider = document.getElementById('onb-search-provider');
  const searchKey = document.getElementById('onb-search-key');
  const hasSearchKey = searchKey && searchKey.value.trim();
  if (searchEl) {
    const searchName = searchProvider ? searchProvider.options[searchProvider.selectedIndex].text : 'SearXNG';
    searchEl.textContent = searchName + (hasSearchKey ? '' : '（默认）');
  }
}

async function launchBrain() {
  const errEl = document.getElementById('onb-error');
  errEl.style.display = 'none';

  const allDirs = [...onbDirections, ...onbCustomDirections];
  if (allDirs.length === 0) {
    errEl.textContent = '请至少选择一个关注方向';
    errEl.style.display = 'block';
    return;
  }

  // Build seed beliefs from directions
  const seedBeliefs = allDirs.map(dir => {
    const presetInfo = PRESET_TAG_MAP[dir];
    if (presetInfo) {
      return {
        statement: `追踪${dir}最新动态`,
        tags: presetInfo.tags,
      };
    } else {
      return {
        statement: `追踪${dir}最新发展`,
        tags: [dir],
      };
    }
  });

  const llmKey = document.getElementById('onb-llm-key').value.trim();
  const searchKey = document.getElementById('onb-search-key');
  const braveKey = searchKey ? searchKey.value.trim() : '';

  const payload = {
    seed_beliefs: seedBeliefs,
    api_keys: {
      llm_api_key: llmKey || '',
      brave_api_key: braveKey || '',
    },
  };

  try {
    const result = await api('/api/onboarding/init', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (result.detail) {
      errEl.textContent = result.detail;
      errEl.style.display = 'block';
      return;
    }

    // Success - go to dashboard
    showDashboard();
    refresh();
    connectWS();
  } catch (e) {
    errEl.textContent = '初始化失败: ' + e.message;
    errEl.style.display = 'block';
  }
}

// ═══ Settings Logic ═══
function switchSettingsTab(tab, btn) {
  // Hide all panels
  document.getElementById('settings-tab-brain').style.display = 'none';
  document.getElementById('settings-tab-api').style.display = 'none';
  document.getElementById('settings-tab-account').style.display = 'none';
  const notifPanel = document.getElementById('settings-tab-notifications');
  if (notifPanel) notifPanel.style.display = 'none';

  // Deactivate all tabs
  document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));

  // Show selected
  document.getElementById('settings-tab-' + tab).style.display = 'block';
  btn.classList.add('active');

  // Load email settings when switching to notifications tab
  if (tab === 'notifications') loadEmailSettings();
}

async function loadSettingsData() {
  try {
    const d = await api('/api/dashboard');
    // Render seed beliefs
    const seedEl = document.getElementById('settings-seed-beliefs');
    if (seedEl && d.belief_graph && d.belief_graph.nodes) {
      const seeds = d.belief_graph.nodes.filter(n => n.source === 'seed');
      seedEl.innerHTML = seeds.map(s => `
        <div class="settings-item">
          <span>${esc(s.statement)}</span>
        </div>
      `).join('') || '<p style="color:var(--text3);font-size:13px">暂无种子信念</p>';
    }

    // Render exogenous goals
    const exoEl = document.getElementById('settings-exo-goals');
    if (exoEl && d.all_goals) {
      const exoGoals = d.all_goals.filter(g => g.origin === 'exogenous' && g.status === 'active');
      exoEl.innerHTML = exoGoals.map(g => `
        <div class="settings-item">
          <span>${esc(g.description)}</span>
          <button onclick="abandonGoal('${g.id}');loadSettingsData()">&times;</button>
        </div>
      `).join('') || '<p style="color:var(--text3);font-size:13px">暂无目标</p>';
    }
  } catch (e) {
    // Silently fail
  }
}

async function addSettingsBelief() {
  const input = document.getElementById('settings-new-belief');
  const stmt = input.value.trim();
  if (!stmt) return;

  try {
    await api('/api/beliefs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        statement: stmt,
        source: 'seed',
        category: 'fact',
        confidence: 0.7,
        tags: [],
      }),
    });
    input.value = '';
    loadSettingsData();
  } catch (e) {
    // Silently fail
  }
}

async function addSettingsGoal() {
  const input = document.getElementById('settings-new-goal');
  const desc = input.value.trim();
  if (!desc) return;

  try {
    await api('/api/goals', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description: desc, priority: 0.5 }),
    });
    input.value = '';
    loadSettingsData();
  } catch (e) {
    // Silently fail
  }
}

async function triggerManualCycle() {
  try {
    await api('/api/cycle/trigger', { method: 'POST' });
    showNotification({ level: 'result', title: 'Cycle', body: 'Manual cycle triggered' });
  } catch (e) {
    showNotification({ level: 'urgent', title: 'Error', body: e.message });
  }
}

async function testSettingsLLM() {
  const resultEl = document.getElementById('settings-llm-test-result');
  const key = document.getElementById('settings-llm-key').value.trim();
  if (!key) {
    resultEl.textContent = 'Please enter an API key';
    resultEl.className = 'onb-test-result error';
    return;
  }
  if (key.length > 10) {
    resultEl.textContent = 'Key format OK';
    resultEl.className = 'onb-test-result success';
  } else {
    resultEl.textContent = 'Key too short';
    resultEl.className = 'onb-test-result error';
  }
}

async function saveApiSettings() {
  const llmKey = document.getElementById('settings-llm-key').value.trim();
  const searchKey = document.getElementById('settings-search-key').value.trim();

  try {
    await api('/api/onboarding/init', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_keys: {
          llm_api_key: llmKey,
          brave_api_key: searchKey,
        },
      }),
    });
    showNotification({ level: 'result', title: 'Settings', body: 'API settings saved' });
  } catch (e) {
    // May fail if brain already exists, that's OK - keys might still be saved
    showNotification({ level: 'periodic', title: 'Settings', body: 'Settings updated' });
  }
}

// ═══ Email Settings ═══
async function loadEmailSettings() {
  try {
    const d = await api('/api/email_settings');
    document.getElementById('email-smtp-host').value = d.smtp_host || '';
    document.getElementById('email-smtp-port').value = d.smtp_port || 587;
    document.getElementById('email-smtp-user').value = d.smtp_user || '';
    document.getElementById('email-from-addr').value = d.from_addr || '';
    document.getElementById('email-to-addr').value = d.to_addr || '';
    document.getElementById('email-daily-digest').checked = d.daily_digest;
    document.getElementById('email-weekly-digest').checked = d.weekly_digest;
    document.getElementById('email-realtime-alerts').checked = d.realtime_alerts;
    document.getElementById('email-digest-hour').value = d.digest_hour || 8;
  } catch (e) {
    // Silently fail — brain may not be started yet
  }
}

async function saveEmailSettings(sendTest) {
  const msgEl = document.getElementById('email-settings-msg');
  const payload = {
    smtp_host: document.getElementById('email-smtp-host').value.trim(),
    smtp_port: parseInt(document.getElementById('email-smtp-port').value) || 587,
    smtp_user: document.getElementById('email-smtp-user').value.trim(),
    smtp_pass: document.getElementById('email-smtp-pass').value.trim(),
    from_addr: document.getElementById('email-from-addr').value.trim(),
    to_addr: document.getElementById('email-to-addr').value.trim(),
    daily_digest: document.getElementById('email-daily-digest').checked,
    weekly_digest: document.getElementById('email-weekly-digest').checked,
    realtime_alerts: document.getElementById('email-realtime-alerts').checked,
    digest_hour: parseInt(document.getElementById('email-digest-hour').value) || 8,
    send_test: !!sendTest,
  };

  try {
    const result = await api('/api/email_settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (sendTest) {
      if (result.test_sent) {
        msgEl.textContent = '测试邮件已发送';
        msgEl.className = 'settings-msg success';
      } else {
        msgEl.textContent = result.test_error || '发送失败';
        msgEl.className = 'settings-msg error';
      }
    } else {
      msgEl.textContent = '设置已保存';
      msgEl.className = 'settings-msg success';
    }
    showNotification({ level: 'result', title: 'Email', body: sendTest ? '测试邮件已发送' : '邮件通知设置已保存' });
  } catch (e) {
    msgEl.textContent = '保存失败: ' + e.message;
    msgEl.className = 'settings-msg error';
  }
}

async function changePassword() {
  const msgEl = document.getElementById('password-msg');
  const oldPw = document.getElementById('settings-old-password').value;
  const newPw = document.getElementById('settings-new-password').value;
  const confirmPw = document.getElementById('settings-confirm-password').value;

  if (newPw.length < 8) {
    msgEl.textContent = '密码至少8位';
    msgEl.className = 'settings-msg error';
    return;
  }
  if (newPw !== confirmPw) {
    msgEl.textContent = '两次密码不一致';
    msgEl.className = 'settings-msg error';
    return;
  }

  try {
    const result = await api('/api/auth/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
    });
    if (result.detail) {
      msgEl.textContent = result.detail;
      msgEl.className = 'settings-msg error';
    } else {
      msgEl.textContent = '密码修改成功';
      msgEl.className = 'settings-msg success';
      document.getElementById('settings-old-password').value = '';
      document.getElementById('settings-new-password').value = '';
      document.getElementById('settings-confirm-password').value = '';
    }
  } catch (e) {
    msgEl.textContent = '修改失败: ' + e.message;
    msgEl.className = 'settings-msg error';
  }
}

async function exportBrainData() {
  try {
    const d = await api('/api/dashboard');
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'skuld-brain-export.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (e) {
    showNotification({ level: 'urgent', title: 'Export', body: 'Export failed: ' + e.message });
  }
}

async function resetBrain() {
  if (!confirm('确定要重置Brain吗？所有数据将被删除，此操作不可逆。')) return;
  if (!confirm('再次确认：这将永久删除所有信念、目标和学习数据。')) return;

  try {
    const result = await api('/api/onboarding/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true }),
    });
    if (result.status === 'reset') {
      showOnboarding();
    }
  } catch (e) {
    showNotification({ level: 'urgent', title: 'Reset', body: 'Reset failed: ' + e.message });
  }
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
        .text(t('noBeliefs'));
    }
    return;
  }

  const newNodeIds = new Set(bg.nodes.map(n => n.id));
  const oldNodeIds = prevNodeIds;

  const nodes = bg.nodes.map(n => {
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

  const addedIds = new Set();
  const removedIds = new Set();
  newNodeIds.forEach(id => { if (!oldNodeIds.has(id)) addedIds.add(id); });
  oldNodeIds.forEach(id => { if (!newNodeIds.has(id)) removedIds.add(id); });

  if (!graphG) {
    svg.selectAll('*').remove();
    graphG = svg.append('g');
    // 3D globe: nodes have virtual 3D positions, drag rotates around Y-axis
    let globeRotY = 0;  // rotation around Y-axis (left-right drag)
    let globeRotX = 0;  // rotation around X-axis (up-down drag)
    let globeScale = 1.3;
    const cx = W / 2, cy = H / 2;

    // Project 3D position to 2D with perspective
    window._globeProject = function(x, y, z) {
      const perspective = 600;
      // Rotate around Y-axis
      const cosY = Math.cos(globeRotY), sinY = Math.sin(globeRotY);
      let x1 = x * cosY - z * sinY;
      let z1 = x * sinY + z * cosY;
      // Rotate around X-axis
      const cosX = Math.cos(globeRotX), sinX = Math.sin(globeRotX);
      let y1 = y * cosX - z1 * sinX;
      let z2 = y * sinX + z1 * cosX;
      // Perspective projection
      const scale = perspective / (perspective + z2);
      return { px: cx + x1 * scale * globeScale, py: cy + y1 * scale * globeScale, s: scale, z: z2 };
    };

    // Assign 3D positions to nodes (spread on sphere surface)
    window._assignGlobePositions = function(nodes) {
      const R = Math.min(W, H) * 0.35;
      nodes.forEach((n, i) => {
        if (n._gx === undefined) {
          // Golden spiral distribution on sphere
          const phi = Math.acos(1 - 2 * (i + 0.5) / Math.max(nodes.length, 1));
          const theta = Math.PI * (1 + Math.sqrt(5)) * i;
          n._gx = R * Math.sin(phi) * Math.cos(theta);
          n._gy = R * Math.sin(phi) * Math.sin(theta) * 0.7; // flatten Y
          n._gz = R * Math.cos(phi);
        }
      });
    };

    // Drag to rotate globe
    svg.call(d3.drag()
      .filter(e => !e.target.closest('circle'))  // don't interfere with node drag
      .on('drag', (e) => {
        globeRotY += e.dx * 0.005;
        globeRotX -= e.dy * 0.005;
        globeRotX = Math.max(-0.8, Math.min(0.8, globeRotX));
        updateGlobePositions();
      })
    );

    // Scroll to zoom
    svg.on('wheel', (e) => {
      e.preventDefault();
      globeScale *= e.deltaY > 0 ? 0.95 : 1.05;
      globeScale = Math.max(0.4, Math.min(2.5, globeScale));
      updateGlobePositions();
    }, { passive: false });

    window._globeRotY = () => globeRotY;
    window._globeRotX = () => globeRotX;
  }

  if (graphSim) graphSim.stop();

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(60))
    .force('charge', d3.forceManyBody().strength(-40))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => Math.max(4, d.confidence * 14) + 3))
    .force('x', d3.forceX(W / 2).strength(0.05))
    .force('y', d3.forceY(H / 2).strength(0.05))
    .alphaDecay(0.02);

  if (prevNodeIds.size > 0 && addedIds.size < nodes.length) {
    sim.alpha(0.3);
  }

  graphG.selectAll('.graph-link').remove();
  graphLink = graphG.selectAll('.graph-link')
    .data(links)
    .enter().append('line')
    .attr('class', 'graph-link')
    .attr('stroke', 'rgba(80,80,100,0.12)')
    .attr('stroke-width', d => Math.max(0.8, (d.weight || 0.5) * 2))
    .attr('stroke-opacity', 1);

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

  graphG.selectAll('.node-label').remove();
  graphLabel = graphG.selectAll('.node-label')
    .data(nodes.filter(n => n.confidence > 0.6))
    .enter().append('text')
    .attr('class', 'node-label')
    .attr('text-anchor', 'middle')
    .attr('dy', d => -Math.max(5, d.confidence * 20) - 5)
    .text(d => d.statement ? d.statement.slice(0, 24) : d.id);

  // Assign 3D globe positions
  if (window._assignGlobePositions) {
    window._assignGlobePositions(nodes);
  }

  // Globe-aware tick: project 3D positions
  window.updateGlobePositions = function() {
    if (!window._globeProject) return;
    graphNode.each(function(d) {
      const p = window._globeProject(d._gx || 0, d._gy || 0, d._gz || 0);
      d._px = p.px; d._py = p.py; d._s = p.s; d._z = p.z;
    });
    // Sort by depth (back to front)
    graphNode.sort((a, b) => (a._z || 0) - (b._z || 0));

    graphNode
      .attr('cx', d => d._px || d.x)
      .attr('cy', d => d._py || d.y)
      .attr('r', d => Math.max(3, d.confidence * 14) * (d._s || 1))
      .attr('opacity', d => {
        const s = d._s || 1;
        return Math.max(0.15, Math.min(1, s * 0.8 + 0.2)) * Math.max(0.3, d.confidence);
      });

    graphLink
      .attr('x1', d => { const p = window._globeProject(d.source._gx||0, d.source._gy||0, d.source._gz||0); return p.px; })
      .attr('y1', d => { const p = window._globeProject(d.source._gx||0, d.source._gy||0, d.source._gz||0); return p.py; })
      .attr('x2', d => { const p = window._globeProject(d.target._gx||0, d.target._gy||0, d.target._gz||0); return p.px; })
      .attr('y2', d => { const p = window._globeProject(d.target._gx||0, d.target._gy||0, d.target._gz||0); return p.py; })
      .attr('opacity', d => {
        const s1 = window._globeProject(d.source._gx||0, d.source._gy||0, d.source._gz||0).s;
        const s2 = window._globeProject(d.target._gx||0, d.target._gy||0, d.target._gz||0).s;
        return Math.min(s1, s2) * 0.5;
      });

    graphLabel
      .attr('x', d => d._px || d.x)
      .attr('y', d => (d._py || d.y) - Math.max(3, d.confidence * 14) * (d._s || 1) - 4)
      .attr('opacity', d => (d._s || 1) > 0.7 ? 0.7 : 0);
  };

  sim.on('tick', () => {
    // Use force simulation to set _gx/_gy/_gz initial positions on first run
    if (!nodes[0]?._gx && nodes[0]?.x) {
      nodes.forEach(n => {
        n._gx = n.x - W/2;
        n._gy = n.y - H/2;
        n._gz = (Math.random() - 0.5) * Math.min(W,H) * 0.3;
      });
    }
    updateGlobePositions();
  });

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
      <span class="tt-cat" style="background:${catColor}18;color:${catColor}">${t(cat)}</span>
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

  let filtered = clusters.filter(c =>
    (c.obs_count >= 2 || c.obs_count === undefined) &&
    (c.not_count >= 2 || c.not_count === undefined)
  );

  filtered.sort((a, b) => b.c_value - a.c_value);
  const top = filtered.slice(0, 15);

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

  lastGoalsData = goals;

  const active = goals.filter(g => g.status === 'active');
  const done = goals.filter(g => g.status !== 'active');

  let html = active.map(g => {
    const origin = g.origin || 'exogenous';
    return `
      <div class="goal-item active-goal">
        <span class="goal-status active">${t('active').toUpperCase()}</span>
        <span class="goal-origin ${origin}">${t(origin).toUpperCase()}</span>
        <span class="goal-desc">${esc(g.description)}</span>
        <span class="goal-pri">${(g.priority || 0).toFixed(2)}</span>
        <button class="goal-btn" onclick="completeGoal('${g.id}')">${t('done')}</button>
        <button class="goal-btn" onclick="abandonGoal('${g.id}')">${t('drop')}</button>
      </div>
    `;
  }).join('');

  if (done.length > 0) {
    html += `
      <div class="goals-done-section">
        <button class="goals-done-toggle" onclick="toggleDoneGoals(this)">
          <span class="arrow">&#9654;</span> ${done.length} ${t('completedAbandoned')}
        </button>
        <div class="goals-done-list">
          ${done.map(g => `
            <div class="goal-item ${g.status === 'completed' ? 'completed-goal' : 'abandoned-goal'}">
              <span class="goal-status ${g.status}">${t(g.status).toUpperCase()}</span>
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

function showDiscovery(data) {
  const el = document.createElement('div');
  el.className = 'notification discovery';
  el.innerHTML = '<strong>' + esc(data.title) + '</strong><br>' + esc(data.body);
  const container = document.getElementById('notifications');
  if (container) container.appendChild(el);
  setTimeout(() => {
    el.style.animation = 'notifOut 0.3s ease-out forwards';
    setTimeout(() => el.remove(), 300);
  }, 8000);
}

function showProactiveMessage(data) {
  // Skuld wants to talk — show as a chat message from Skuld
  const chatMessages = document.getElementById('chat-messages');
  if (chatMessages) {
    const div = document.createElement('div');
    div.className = 'chat-msg skuld-msg proactive';
    div.innerHTML = '<span class="proactive-tag">Skuld</span> ' + miniMarkdown(data.message);
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    // Save to history
    const hist = JSON.parse(localStorage.getItem('chat_history') || '[]');
    hist.push({role: 'skuld', content: data.message, ts: Date.now(), proactive: true});
    if (hist.length > 50) hist.splice(0, hist.length - 50);
    localStorage.setItem('chat_history', JSON.stringify(hist));
  }
  // Also show as a brief notification
  const el = document.createElement('div');
  el.className = 'notification proactive';
  el.innerHTML = '<strong>Skuld says:</strong><br>' + esc(data.message).slice(0, 120);
  const container = document.getElementById('notifications');
  if (container) container.appendChild(el);
  setTimeout(() => {
    el.style.animation = 'notifOut 0.3s ease-out forwards';
    setTimeout(() => el.remove(), 300);
  }, 10000);
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
      <p><b>${t(cat)}:</b> <span style="color:${catColor};font-weight:500">${t(cat)}</span>
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
  // Inline chat panel (primary)
  const sendBtnInline = document.getElementById('chat-send-inline');
  const chatInputInline = document.getElementById('chat-input-inline');
  if (sendBtnInline) sendBtnInline.onclick = sendChat;
  if (chatInputInline) chatInputInline.onkeydown = (e) => { if (e.key === 'Enter') sendChat(); };
  // Legacy drawer (fallback)
  const sendBtn = document.getElementById('chat-send');
  const chatInput = document.getElementById('chat-input');
  if (sendBtn) sendBtn.onclick = sendChat;
  if (chatInput) chatInput.onkeydown = (e) => { if (e.key === 'Enter') sendChat(); };
}

async function sendChat() {
  const input = document.getElementById('chat-input-inline') || document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send-inline');
  if (!input) return;
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';

  // Show user message
  appendChat('user', msg);

  // Loading state
  if (sendBtn) { sendBtn.disabled = true; sendBtn.textContent = '...'; }
  appendChat('skuld', '思考中...', null, 'chat-loading');

  try {
    const data = await api('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg }),
    });

    // Remove loading message
    const loadingEl = document.getElementById('chat-loading');
    if (loadingEl) loadingEl.remove();

    let meta = `conf=${data.confidence ? data.confidence.toFixed(2) : '?'}`;
    if (data.sources && data.sources.length) meta += ` | sources: ${data.sources.join(', ')}`;
    if (data.searching) meta += ` | ${t('searching')}`;

    appendChat('skuld', data.reply, meta);
  } catch (e) {
    const loadingEl = document.getElementById('chat-loading');
    if (loadingEl) loadingEl.remove();
    appendChat('skuld', 'Error: ' + e.message);
  } finally {
    if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = t('send'); }
  }
}

function miniMarkdown(text) {
  if (!text) return '';
  let html = esc(text);
  // Bold: **text** or __text__
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');
  // Italic: *text* (but not inside bold)
  html = html.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');
  // Inline code: `text`
  html = html.replace(/`(.+?)`/g, '<code style="background:rgba(0,0,0,0.05);padding:1px 4px;border-radius:3px;font-family:var(--font-mono);font-size:12px;">$1</code>');
  // Bullet lists: lines starting with - or *
  html = html.replace(/^[\-\*]\s+(.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul style="margin:6px 0;padding-left:18px;">$&</ul>');
  // Numbered lists: lines starting with 1. 2. etc
  html = html.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');
  // Headings: lines starting with ###, ##, #
  html = html.replace(/^###\s+(.+)$/gm, '<div style="font-weight:600;margin-top:8px;margin-bottom:4px;">$1</div>');
  html = html.replace(/^##\s+(.+)$/gm, '<div style="font-weight:600;font-size:15px;margin-top:10px;margin-bottom:4px;">$1</div>');
  // Line breaks
  html = html.replace(/\n/g, '<br>');
  // Clean up double <br> after block elements
  html = html.replace(/<\/li><br>/g, '</li>');
  html = html.replace(/<\/ul><br>/g, '</ul>');
  html = html.replace(/<\/div><br>/g, '</div>');
  return html;
}

function appendChat(role, text, meta, id) {
  const el = document.getElementById('chat-messages-inline') || document.getElementById('chat-messages');
  if (!el) return;
  const div = document.createElement('div');
  div.className = `msg msg-${role}`;
  if (id) div.id = id;
  const rendered = role === 'user' ? esc(text) : miniMarkdown(text);
  div.innerHTML = rendered + (meta ? `<div class="msg-meta">${esc(meta)}</div>` : '');
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;

  // Persist to localStorage (skip loading messages)
  if (id !== 'chat-loading') {
    saveChatHistory();
  }
}

function saveChatHistory() {
  const el = document.getElementById('chat-messages-inline');
  if (!el) return;
  const msgs = [];
  el.querySelectorAll('.msg').forEach(div => {
    if (div.id === 'chat-loading') return;
    msgs.push({ html: div.innerHTML, cls: div.className });
  });
  // Keep last 50 messages
  const toSave = msgs.slice(-50);
  try { localStorage.setItem('skuld_chat_history', JSON.stringify(toSave)); } catch(e) {}
}

function loadChatHistory() {
  const el = document.getElementById('chat-messages-inline');
  if (!el) return;
  try {
    const saved = JSON.parse(localStorage.getItem('skuld_chat_history') || '[]');
    saved.forEach(m => {
      const div = document.createElement('div');
      div.className = m.cls;
      div.innerHTML = m.html;
      el.appendChild(div);
    });
    el.scrollTop = el.scrollHeight;
  } catch(e) {}
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
    if (data.type === 'discovery') showDiscovery(data);
    if (data.type === 'proactive_message') showProactiveMessage(data);
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
      <span>Beliefs/cycle: ${data.beliefs_per_cycle || '\u2014'}</span>
      <span>SEC spread: ${data.sec_spread || '\u2014'}</span>
      <span>Avg tokens/cycle: ${data.avg_tokens_per_cycle || '\u2014'}</span>
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
applyI18n();

// Handle onboarding custom input Enter key
const onbCustomInput = document.getElementById('onb-custom-input');
if (onbCustomInput) {
  onbCustomInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addCustomDirection();
    }
  });
}

// Route on page load
checkAndRoute();

// Auto-refresh when on dashboard
setInterval(() => {
  if (document.getElementById('dashboard-screen').style.display !== 'none') {
    refresh();
  }
}, 15000);
