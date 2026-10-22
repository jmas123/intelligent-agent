// Deadline Agent Widget — Main JS

let currentTab = 'dashboard';
let chatHistory = [];
let refreshInterval = null;
let ambientInterval = null;
let isVoiceRecording = false;
let lastSimResultHtml = '';  // Preserve simulation results across dashboard refreshes

// ─── Tab Navigation ──────────────────────────────────────

const MORE_TABS = ['digest', 'patterns', 'affects', 'recruiting', 'goals', 'decisions', 'relationships', 'day', 'brain', 'me'];

function switchTab(tab) {
  currentTab = tab;
  // Clear active on all tabs and menu items
  document.querySelectorAll('#tabs .tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-more-item').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));

  if (MORE_TABS.includes(tab)) {
    // Highlight the "More" button + the specific menu item
    document.getElementById('tab-more-btn').classList.add('active');
    const menuItem = document.querySelector(`.tab-more-item[data-tab="${tab}"]`);
    if (menuItem) menuItem.classList.add('active');
  } else {
    const primary = document.querySelector(`#tabs > .tab[data-tab="${tab}"]`);
    if (primary) primary.classList.add('active');
  }

  document.getElementById(`panel-${tab}`).classList.add('active');
  loadTab(tab);
}

function toggleMoreMenu() {
  const menu = document.getElementById('tab-more-menu');
  menu.classList.toggle('open');
}

function closeMoreMenu() {
  document.getElementById('tab-more-menu').classList.remove('open');
}

// Close menu when clicking outside
document.addEventListener('click', function (e) {
  const wrap = document.querySelector('.tab-more-wrap');
  if (wrap && !wrap.contains(e.target)) {
    closeMoreMenu();
  }
});

function loadTab(tab) {
  switch (tab) {
    case 'dashboard': loadDashboard(); break;
    case 'chat': break; // Chat state persists
    case 'insights': loadInsights(); break;
    case 'actions': loadActions(); break;
    case 'digest': loadDigest(); break;
    case 'patterns': loadPatterns(); break;
    case 'recruiting': loadRecruiting(); break;
    case 'goals': loadGoals(); break;
    case 'decisions': loadDecisions(); break;
    case 'affects': loadAffects(); break;
    case 'relationships': loadRelationships(); break;
    case 'day': loadDay(); break;
    case 'brain': loadBrain(); break;
    case 'me': loadMe(); break;
  }
}

function refreshCurrentTab() {
  loadTab(currentTab);
}

// ─── Dashboard ───────────────────────────────────────────

async function loadDashboard() {
  const loading = document.getElementById('dashboard-loading');
  const content = document.getElementById('dashboard-content');
  loading.style.display = 'block';
  content.innerHTML = '';

  try {
    const ctx = await pywebview.api.get_context();
    loading.style.display = 'none';
    updateConnectionStatus(true);

    if (ctx.error) {
      content.innerHTML = `<div class="empty-state"><div class="empty-state-icon">&#x26a0;</div>Cannot connect to backend<br><small>${ctx.error}</small></div>`;
      updateConnectionStatus(false);
      return;
    }

    let html = '';

    // Compute week task stats for progress bar
    const totalWeek = (ctx.overdue_tasks ? ctx.overdue_tasks.length : 0)
      + (ctx.tasks_due_today ? ctx.tasks_due_today.length : 0)
      + (ctx.tasks_due_this_week ? ctx.tasks_due_this_week.length : 0);
    const doneWeek = ctx.done_this_week || 0;
    const taskStats = { done: doneWeek, total: doneWeek + totalWeek };

    // Mode banner + life context manager
    if (ctx.life_contexts && ctx.life_contexts.length > 0) {
      html += renderModeBanner(ctx.life_contexts, taskStats);
    }
    html += renderLifeContextManager(ctx.life_contexts || []);

    // Quick add task + What-if simulation prompt
    html += renderAddTaskForm();
    html += renderSimulationButton();

    // Ambient notification ticker
    try {
      const ambient = await pywebview.api.get_ambient_notifications();
      if (ambient && ambient.length > 0) {
        html += renderAmbientTicker(ambient);
      }
    } catch (e) { /* ambient unavailable */ }

    // Overdue
    if (ctx.overdue_tasks && ctx.overdue_tasks.length > 0) {
      html += renderSection('Overdue', ctx.overdue_tasks, 'overdue');
    }

    // Due today
    if (ctx.tasks_due_today && ctx.tasks_due_today.length > 0) {
      html += renderSection('Due Today', ctx.tasks_due_today, 'today');
    }

    // Due this week
    if (ctx.tasks_due_this_week && ctx.tasks_due_this_week.length > 0) {
      html += renderSection('This Week', ctx.tasks_due_this_week, 'week');
    }

    // Other pending tasks (no due date or due later)
    try {
      const allPending = await pywebview.api.get_tasks('pending', false, false, 50);
      const shownIds = new Set();
      for (const list of [ctx.overdue_tasks, ctx.tasks_due_today, ctx.tasks_due_this_week]) {
        if (list) list.forEach(t => shownIds.add(t.id));
      }
      const other = allPending.filter(t => !shownIds.has(t.id));
      if (other.length > 0) {
        html += renderSection('Other Pending', other, 'other');
      }
    } catch (e) { /* fallback: skip */ }

    // Recovery plans for overdue tasks
    if (ctx.overdue_tasks && ctx.overdue_tasks.length > 0) {
      try {
        const recoveryHtml = await loadRecoveryPlans();
        html += recoveryHtml;
      } catch (e) { /* recovery unavailable */ }
    }

    // Empty state
    if (!html || html.trim() === '') {
      html = '<div class="empty-state"><div class="empty-state-icon">&#x2705;</div>All clear! No upcoming tasks.</div>';
    }

    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    content.innerHTML = `<div class="empty-state"><div class="empty-state-icon">&#x26a0;</div>Cannot connect to backend</div>`;
    updateConnectionStatus(false);
  }
}

function renderSection(title, tasks, urgencyClass) {
  let html = `<div class="section-header">${title}<span class="section-count">${tasks.length}</span></div>`;
  for (const task of tasks) {
    html += renderTaskCard(task, urgencyClass);
  }
  return html;
}

function renderTaskCard(task, urgencyClass) {
  const course = task.course ? `<span class="task-course">${escapeHtml(task.course)}</span>` : '';

  // Phase 27: Priority rank badge
  let priorityBadge = '';
  if (task.priority_rank && task.priority_rank <= 5) {
    priorityBadge = `<span class="priority-badge" title="Priority #${task.priority_rank}">${task.priority_rank}</span>`;
  }

  // Phase 28: Risk badge
  let riskBadge = '';
  let riskClass = '';
  if (task.risk_level && task.risk_level !== 'low') {
    const riskLabel = task.risk_pct != null ? `${task.risk_pct}%` : task.risk_level.toUpperCase();
    riskBadge = `<span class="risk-badge risk-${task.risk_level}" title="${task.risk_summary || ''}">${riskLabel}</span>`;
    riskClass = ` risk-${task.risk_level}`;
  }

  // Due date line with countdown chip + risk badge
  let dueLine = '';
  if (task.due) {
    const countdown = computeCountdown(task.due);
    const chipClass = countdown.urgent ? 'task-countdown urgent' : 'task-countdown';
    dueLine = `<div class="task-due-line">${escapeHtml(task.due)}<span class="${chipClass}">${countdown.label}</span>${riskBadge}</div>`;
  }

  // Phase 24: Affect badge
  const affectIcons = { enjoyment: '+', avoidance: '!', anxiety: 'x' };
  let affectBadge = '';
  let affectClass = '';
  if (task.affect && affectIcons[task.affect]) {
    affectBadge = `<span class="affect-badge affect-${task.affect}" title="${task.affect}">${affectIcons[task.affect]}</span> `;
    affectClass = ` affect-${task.affect}`;
  }

  // Phase 27: Suggested action line
  let actionLine = '';
  if (task.priority_action && task.priority_rank && task.priority_rank <= 3) {
    actionLine = `<div class="task-action-hint">${escapeHtml(task.priority_action)}</div>`;
  }

  return `
    <div class="task-card urgency-${urgencyClass}${affectClass}${riskClass}">
      <div class="task-title">${priorityBadge}${affectBadge}${escapeHtml(task.title)}</div>
      ${dueLine}
      ${actionLine}
      <div class="task-meta">${course}</div>
      <div class="task-actions">
        <button class="btn btn-done" onclick="markTask(${task.id}, 'done')">Done</button>
        <button class="btn btn-dismiss" onclick="markTask(${task.id}, 'dismissed')">Dismiss</button>
      </div>
    </div>
  `;
}

function computeCountdown(dueStr) {
  try {
    const now = new Date();
    const due = new Date(dueStr);
    if (isNaN(due.getTime())) return { label: '', urgent: false };
    const diffMs = due - now;
    if (diffMs < 0) return { label: 'overdue', urgent: true };
    const diffH = Math.floor(diffMs / 3600000);
    if (diffH < 1) return { label: 'due now', urgent: true };
    if (diffH < 24) return { label: `due in ${diffH}h`, urgent: diffH < 12 };
    const diffD = Math.floor(diffH / 24);
    return { label: `due in ${diffD}d`, urgent: false };
  } catch (e) {
    return { label: '', urgent: false };
  }
}

async function markTask(taskId, status) {
  try {
    await pywebview.api.update_task_status(taskId, status);
    loadDashboard();
  } catch (e) {
    console.error('Failed to update task:', e);
  }
}

// ─── Chat ────────────────────────────────────────────────

async function sendChat() {
  const input = document.getElementById('chat-input');
  const question = input.value.trim();
  if (!question) return;

  input.value = '';
  addChatMessage('user', question);

  const loadingId = addChatMessage('assistant', 'Thinking...', true);

  try {
    const result = await pywebview.api.ask_question(question);
    removeChatMessage(loadingId);

    if (result.error) {
      addChatMessage('assistant', `Error: ${result.error}`);
    } else {
      addChatMessage('assistant', result.answer || 'No response.');
    }
  } catch (e) {
    removeChatMessage(loadingId);
    addChatMessage('assistant', 'Failed to get response. Is the backend running?');
  }
}

let msgCounter = 0;

function addChatMessage(role, text, isLoading = false) {
  const container = document.getElementById('chat-messages');
  const id = `msg-${++msgCounter}`;
  const div = document.createElement('div');
  div.id = id;
  div.className = `chat-msg ${role}${isLoading ? ' loading' : ''}`;

  if (role === 'assistant' && !isLoading) {
    div.innerHTML = renderMarkdown(text);
  } else {
    div.textContent = text;
  }

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return id;
}

/**
 * Lightweight markdown → HTML renderer for chat responses.
 * Handles: tables, bold, italic, inline code, code blocks, lists, headers, paragraphs.
 */
function renderMarkdown(src) {
  // Escape HTML first to prevent injection, then selectively render markdown
  src = src.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // Fenced code blocks
  src = src.replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
    return '<pre class="md-code-block"><code>' + code.trimEnd() + '</code></pre>';
  });

  // Tables: detect lines with pipes
  src = src.replace(/((?:^\|.+\|$\n?)+)/gm, function (block) {
    const rows = block.trim().split('\n').filter(r => r.trim());
    if (rows.length < 2) return block;

    // Check if second row is a separator (|---|---|)
    const isSep = /^\|[\s\-:]+(\|[\s\-:]+)+\|?$/.test(rows[1]);
    if (!isSep) return block;

    const parseRow = r => r.replace(/^\||\|$/g, '').split('|').map(c => c.trim());
    const headers = parseRow(rows[0]);
    const bodyRows = rows.slice(2);

    let html = '<table class="md-table"><thead><tr>';
    for (const h of headers) html += '<th>' + inlineMarkdown(h) + '</th>';
    html += '</tr></thead><tbody>';
    for (const row of bodyRows) {
      const cells = parseRow(row);
      html += '<tr>';
      for (const c of cells) html += '<td>' + inlineMarkdown(c) + '</td>';
      html += '</tr>';
    }
    html += '</tbody></table>';
    return html;
  });

  // Process remaining lines
  const lines = src.split('\n');
  let html = '';
  let inList = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Skip if already processed (contains HTML tags from above)
    if (line.startsWith('<pre') || line.startsWith('<table')) {
      if (inList) { html += '</ul>'; inList = false; }
      html += line;
      continue;
    }

    // Headers
    const hMatch = line.match(/^(#{1,3})\s+(.+)$/);
    if (hMatch) {
      if (inList) { html += '</ul>'; inList = false; }
      const level = hMatch[1].length;
      html += `<h${level} class="md-h">${inlineMarkdown(hMatch[2])}</h${level}>`;
      continue;
    }

    // Unordered list items
    const liMatch = line.match(/^[\s]*[-*]\s+(.+)$/);
    if (liMatch) {
      if (!inList) { html += '<ul class="md-list">'; inList = true; }
      html += '<li>' + inlineMarkdown(liMatch[1]) + '</li>';
      continue;
    }

    // End list if non-list line
    if (inList) { html += '</ul>'; inList = false; }

    // Empty line → break
    if (line.trim() === '') {
      html += '<br>';
      continue;
    }

    // Regular paragraph line
    html += '<p class="md-p">' + inlineMarkdown(line) + '</p>';
  }

  if (inList) html += '</ul>';
  return html;
}

/** Render inline markdown: bold, italic, inline code */
function inlineMarkdown(text) {
  // Inline code (must come first to protect contents)
  text = text.replace(/`([^`]+)`/g, '<code class="md-inline-code">$1</code>');
  // Bold
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Italic
  text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
  return text;
}

function removeChatMessage(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

// ─── Insights ────────────────────────────────────────────

async function loadInsights() {
  const loading = document.getElementById('insights-loading');
  const content = document.getElementById('insights-content');
  loading.style.display = 'block';
  content.innerHTML = '';

  try {
    const insights = await pywebview.api.get_insights();
    loading.style.display = 'none';
    updateConnectionStatus(true);

    if (!insights || insights.length === 0) {
      content.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#x1f4a1;</div>No active insights.</div>';
      return;
    }

    let html = '';
    for (const insight of insights) {
      html += `
        <div class="insight-card">
          <div class="insight-type">${escapeHtml(insight.type)}</div>
          <div class="insight-content">${escapeHtml(insight.content)}</div>
          <div class="task-actions">
            <button class="btn btn-dismiss" onclick="dismissInsight(${insight.id})">Dismiss</button>
          </div>
        </div>
      `;
    }
    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    content.innerHTML = '<div class="empty-state">Failed to load insights.</div>';
  }
}

async function dismissInsight(insightId) {
  try {
    await pywebview.api.dismiss_insight(insightId);
    loadInsights();
  } catch (e) {
    console.error('Failed to dismiss insight:', e);
  }
}

// ─── Actions ─────────────────────────────────────────────

async function loadActions() {
  const loading = document.getElementById('actions-loading');
  const content = document.getElementById('actions-content');
  loading.style.display = 'block';
  content.innerHTML = '';

  try {
    const actions = await pywebview.api.get_actions();
    loading.style.display = 'none';
    updateConnectionStatus(true);

    if (!actions || actions.length === 0) {
      content.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#x1f4c5;</div>No pending actions.</div>';
      return;
    }

    let html = '';

    // Calendar gaps + scheduler at top
    try {
      const gapsHtml = await loadCalendarGaps();
      html += gapsHtml;
    } catch (e) { /* gaps unavailable */ }

    // Pending actions
    if (actions.length > 0) {
      html += '<div class="section-header">Pending Actions<span class="section-count">' + actions.length + '</span></div>';
      for (const action of actions) {
        html += renderActionCard(action);
      }
    }

    // Active negotiations
    try {
      const negHtml = await loadNegotiations();
      html += negHtml;
    } catch (e) { /* negotiations unavailable */ }

    if (!html) {
      html = '<div class="empty-state"><div class="empty-state-icon">&#x1f4c5;</div>No pending actions.</div>';
    }

    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    content.innerHTML = '<div class="empty-state">Failed to load actions.</div>';
  }
}

function renderActionCard(action) {
  let payload = {};
  try { payload = JSON.parse(action.payload || '{}'); } catch (_) {}

  let editFields = '';
  if (action.type === 'calendar_block') {
    // Parse proposed start and compute duration
    const startIso = payload.start_iso || '';
    const endIso = payload.end_iso || '';
    let durationMin = 60;
    if (startIso && endIso) {
      durationMin = Math.round((new Date(endIso) - new Date(startIso)) / 60000);
    }
    // Format for datetime-local input (YYYY-MM-DDTHH:MM)
    const localStart = startIso ? startIso.slice(0, 16) : '';

    editFields = `
      <div class="action-edit">
        <label>Start</label>
        <input type="datetime-local" id="action-start-${action.id}" value="${localStart}">
        <label>Duration</label>
        <select id="action-dur-${action.id}">
          <option value="30"${durationMin === 30 ? ' selected' : ''}>30 min</option>
          <option value="60"${durationMin === 60 ? ' selected' : ''}>1 hour</option>
          <option value="90"${durationMin === 90 ? ' selected' : ''}>1.5 hours</option>
          <option value="120"${durationMin === 120 ? ' selected' : ''}>2 hours</option>
          <option value="180"${durationMin === 180 ? ' selected' : ''}>3 hours</option>
        </select>
      </div>
    `;
  }

  return `
    <div class="action-card" id="action-card-${action.id}">
      <div class="action-type">${escapeHtml(action.type === 'calendar_block' ? 'Calendar Block' : action.type)}</div>
      <div class="action-title">${escapeHtml(action.title)}</div>
      ${editFields}
      <div class="action-error" id="action-error-${action.id}" style="display:none"></div>
      <div class="task-actions">
        <button class="btn btn-approve" onclick="approveAction(${action.id})">Approve</button>
        <button class="btn btn-reject" onclick="rejectAction(${action.id})">Reject</button>
      </div>
    </div>
  `;
}

async function approveAction(actionId) {
  const errorEl = document.getElementById(`action-error-${actionId}`);

  // Read user-edited values for calendar blocks
  const startInput = document.getElementById(`action-start-${actionId}`);
  const durSelect = document.getElementById(`action-dur-${actionId}`);
  let startIso = null;
  let durationMinutes = null;
  if (startInput && startInput.value) {
    startIso = startInput.value;
  }
  if (durSelect && durSelect.value) {
    durationMinutes = parseInt(durSelect.value, 10);
  }

  try {
    const result = await pywebview.api.approve_action(actionId, startIso, durationMinutes);
    if (result && result.error) {
      if (errorEl) {
        errorEl.textContent = result.error;
        errorEl.style.display = 'block';
      }
      return;
    }
    loadActions();
  } catch (e) {
    console.error('Failed to approve action:', e);
    if (errorEl) {
      errorEl.textContent = 'Failed to approve action.';
      errorEl.style.display = 'block';
    }
  }
}

async function rejectAction(actionId) {
  try {
    await pywebview.api.reject_action(actionId);
    loadActions();
  } catch (e) {
    console.error('Failed to reject action:', e);
  }
}

// ─── Digest ──────────────────────────────────────────────

async function loadDigest() {
  const loading = document.getElementById('digest-loading');
  const content = document.getElementById('digest-content');
  loading.style.display = 'block';
  content.innerHTML = '';

  try {
    const digest = await pywebview.api.get_digest();
    loading.style.display = 'none';
    updateConnectionStatus(true);

    if (digest.error) {
      content.innerHTML = `<div class="empty-state">Failed to load digest.</div>`;
      return;
    }

    let html = renderMarkdown(digest.text || 'No digest available.');

    // Weekly snapshots
    try {
      const snapshotsHtml = await loadWeeklySnapshots();
      html += snapshotsHtml;
    } catch (e) { /* snapshots unavailable */ }

    // Debrief section
    try {
      const debriefHtml = await loadDebriefSection();
      html += debriefHtml;
    } catch (e) { /* debrief unavailable */ }

    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    content.innerHTML = '<div class="empty-state">Failed to load digest.</div>';
  }
}

// ─── Patterns ───────────────────────────────────────────

async function loadPatterns() {
  const loading = document.getElementById('patterns-loading');
  const content = document.getElementById('patterns-content');
  loading.style.display = 'block';
  content.innerHTML = '';

  try {
    const patterns = await pywebview.api.get_patterns();
    loading.style.display = 'none';
    updateConnectionStatus(true);

    if (!patterns || patterns.length === 0) {
      content.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon">&#x1f9e0;</div>
          No behavioral patterns learned yet.<br>
          <small style="color:var(--text-muted)">Patterns appear after enough file activity and completed tasks.</small>
          <div style="margin-top:12px">
            <button class="btn btn-approve" onclick="runAnalysis()">Analyze Now</button>
          </div>
        </div>`;
      return;
    }

    // Group by pattern_type
    const grouped = {};
    for (const p of patterns) {
      if (!grouped[p.pattern_type]) grouped[p.pattern_type] = [];
      grouped[p.pattern_type].push(p);
    }

    let html = '<div style="margin-bottom:8px"><button class="btn btn-approve" onclick="runAnalysis()">Analyze Now</button></div>';

    const typeLabels = {
      effort_accuracy: 'Effort Accuracy',
      peak_hours: 'Peak Hours',
      lead_time: 'Lead Time',
      session_duration: 'Session Duration',
      procrastination: 'Procrastination',
      work_by_time: 'Work By Time of Day',
    };

    const typeColors = {
      effort_accuracy: 'var(--accent-orange)',
      peak_hours: 'var(--accent-green)',
      lead_time: 'var(--accent-blue)',
      session_duration: 'var(--accent-purple)',
      procrastination: 'var(--accent-red)',
      work_by_time: 'var(--accent-orange)',
    };

    for (const [type, items] of Object.entries(grouped)) {
      const label = typeLabels[type] || type.replace(/_/g, ' ').toUpperCase();
      const color = typeColors[type] || 'var(--accent-blue)';
      html += `<div class="section-header">${escapeHtml(label)}<span class="section-count">${items.length}</span></div>`;
      for (const p of items) {
        const confPct = Math.round(p.confidence * 100);
        const confLabel = confPct >= 80 ? 'High' : confPct >= 50 ? 'Medium' : 'Low';
        html += `
          <div class="pattern-card" style="border-left-color:${color}">
            <div class="pattern-desc">${escapeHtml(p.description)}</div>
            <div class="pattern-meta">
              <span class="pattern-samples">${p.sample_count} observation${p.sample_count !== 1 ? 's' : ''}</span>
              <span class="pattern-conf" title="Based on ${p.sample_count} data points">
                <span class="conf-bar" style="width:${confPct}%;background:${color}"></span>
                ${confLabel} confidence
              </span>
            </div>
          </div>
        `;
      }
    }
    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    content.innerHTML = '<div class="empty-state">Failed to load patterns.</div>';
  }
}

async function runAnalysis() {
  const content = document.getElementById('patterns-content');
  content.innerHTML = '<div class="loading">Running analysis...</div>';
  try {
    await pywebview.api.run_analysis();
    loadPatterns();
  } catch (e) {
    content.innerHTML = '<div class="empty-state">Analysis failed. Check the backend logs.</div>';
  }
}

// ─── Utilities ───────────────────────────────────────────

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function updateConnectionStatus(connected) {
  const el = document.getElementById('connection-status');
  if (connected) {
    el.textContent = 'Connected';
    el.className = 'connected';
  } else {
    el.textContent = 'Disconnected — is the server running?';
    el.className = 'error';
  }
}

// ─── Recruiting Pipeline ────────────────────────────────

// ─── Affects ─────────────────────────────────────────────

async function loadAffects() {
  const panel = document.getElementById('panel-affects');
  if (!panel) return;
  panel.innerHTML = '<div class="loading-indicator">Loading affects...</div>';

  try {
    const affects = await pywebview.api.get_affects();
    if (!affects || affects.length === 0) {
      panel.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#x1f9e0;</div>No task affect patterns yet<br><small>Affects are inferred after enough completed tasks</small></div>';
      return;
    }

    const affectIcons = { enjoyment: '+', neutral: '·', avoidance: '!', anxiety: 'x' };
    const groups = {};
    for (const a of affects) {
      if (!groups[a.affect_label]) groups[a.affect_label] = [];
      groups[a.affect_label].push(a);
    }

    let html = '';
    for (const label of ['enjoyment', 'avoidance', 'anxiety', 'neutral']) {
      const items = groups[label];
      if (!items) continue;
      const icon = affectIcons[label] || '·';
      html += `<div class="section-header">[${icon}] ${label.toUpperCase()}</div>`;
      for (const a of items) {
        const conf = Math.round(a.confidence * 100);
        const lag = a.evidence.mean_start_lag_pct;
        const lagStr = lag !== undefined ? `start at ${Math.round(lag * 100)}% of time` : '';
        const energyStr = a.energy_label ? ` · ${a.energy_label}` : '';
        html += `
          <div class="affect-card affect-${a.affect_label}">
            <div class="affect-type">${escapeHtml(a.task_type)}</div>
            <div class="affect-detail">${lagStr}${energyStr} (${conf}% confidence)</div>
            ${a.intervention ? `<div class="affect-intervention">${escapeHtml(a.intervention)}</div>` : ''}
          </div>`;
      }
    }
    panel.innerHTML = html;
  } catch (e) {
    panel.innerHTML = `<div class="empty-state">Failed to load affects</div>`;
  }
}

let pipelineFilter = 'recent'; // recent | all | active

async function loadRecruiting() {
  const loading = document.getElementById('recruiting-loading');
  const content = document.getElementById('recruiting-content');
  loading.style.display = 'block';
  content.innerHTML = '';

  try {
    const data = await pywebview.api.get_recruiting_pipeline();
    loading.style.display = 'none';
    updateConnectionStatus(true);

    if (data.error) {
      content.innerHTML = '<div class="empty-state">Failed to load pipeline.</div>';
      return;
    }

    let allApps = data.applications || [];
    let html = '';

    // Filter controls
    html += '<div class="pipeline-filters">' +
      '<button class="filter-btn ' + (pipelineFilter === 'recent' ? 'active' : '') + '" onclick="setPipelineFilter(\'recent\')">Recent (30d)</button>' +
      '<button class="filter-btn ' + (pipelineFilter === 'active' ? 'active' : '') + '" onclick="setPipelineFilter(\'active\')">Active</button>' +
      '<button class="filter-btn ' + (pipelineFilter === 'all' ? 'active' : '') + '" onclick="setPipelineFilter(\'all\')">All (' + allApps.length + ')</button>' +
    '</div>';

    // Apply filter
    let apps;
    if (pipelineFilter === 'recent') {
      apps = allApps.filter(a => a.days_since === null || a.days_since <= 30);
    } else if (pipelineFilter === 'active') {
      apps = allApps.filter(a => a.status !== 'closed');
    } else {
      apps = allApps;
    }

    // Summary bar (of filtered set)
    const summary = { applied: 0, response: 0, interview: 0, offer: 0, closed: 0 };
    for (const a of apps) { if (a.status in summary) summary[a.status]++; }
    html += renderPipelineSummary(summary);

    // Phase 25: Analytics section
    try {
      const analytics = await pywebview.api.get_recruiting_analytics();
      if (analytics && !analytics.error) {
        html += renderRecruitingAnalytics(analytics);
      }
    } catch (e) {
      // Analytics unavailable — proceed without
    }

    // Upcoming interviews
    if (data.upcoming_interviews && data.upcoming_interviews.length > 0) {
      html += '<div class="section-header">Upcoming Interviews' +
        '<span class="section-count">' + data.upcoming_interviews.length + '</span></div>';
      for (const iv of data.upcoming_interviews) {
        html += renderInterviewCard(iv);
      }
    }

    // Applications grouped by status, sorted by recency within each group
    const statusOrder = ['interview', 'response', 'offer', 'applied', 'closed'];
    const statusLabels = {
      interview: 'Interviewing', response: 'Response', applied: 'Applied',
      offer: 'Offer', closed: 'Closed'
    };
    for (const status of statusOrder) {
      let group = apps.filter(a => a.status === status);
      // Sort by days_since ascending (most recent first), nulls last
      group.sort((a, b) => {
        const da = a.days_since !== null ? a.days_since : 9999;
        const db = b.days_since !== null ? b.days_since : 9999;
        return da - db;
      });
      if (group.length > 0) {
        html += '<div class="section-header">' + statusLabels[status] +
          '<span class="section-count">' + group.length + '</span></div>';
        for (const app of group) {
          html += renderAppCard(app);
        }
      }
    }

    if (apps.length === 0) {
      html += '<div class="empty-state"><div class="empty-state-icon">&#x1f4bc;</div>' +
        (pipelineFilter === 'recent' ? 'No applications in the last 30 days.' : 'No applications detected yet.') +
        '<br><small>Auto-detected from resumes, emails, and calendar events.</small></div>';
    }

    html += '<button class="refresh-pipeline-btn" onclick="refreshPipeline()">Scan files &amp; emails for applications</button>';
    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    updateConnectionStatus(false);
    content.innerHTML = '<div class="empty-state">Failed to load pipeline.</div>';
  }
}

function setPipelineFilter(filter) {
  pipelineFilter = filter;
  loadRecruiting();
}

function renderPipelineSummary(summary) {
  const items = [
    { label: 'Applied', count: summary.applied || 0, color: 'var(--accent-blue)' },
    { label: 'Response', count: summary.response || 0, color: 'var(--accent-purple)' },
    { label: 'Interview', count: summary.interview || 0, color: 'var(--accent-orange)' },
    { label: 'Offer', count: summary.offer || 0, color: 'var(--accent-green)' },
    { label: 'Closed', count: summary.closed || 0, color: 'var(--text-muted)' },
  ];
  let html = '<div class="pipeline-summary">';
  for (const item of items) {
    if (item.count > 0) {
      html += '<div class="pipeline-stat">' +
        '<span class="pipeline-stat-dot" style="background:' + item.color + '"></span>' +
        '<span>' + item.count + ' ' + item.label + '</span></div>';
    }
  }
  html += '</div>';
  return html;
}

function renderRecruitingAnalytics(analytics) {
  let html = '<div class="analytics-section">';

  // Response rate summary
  const rates = (analytics.response_rates || []).filter(r => r.key === 'overall' || r.key.startsWith('tier:'));
  if (rates.length > 0) {
    html += '<div class="section-header">Response Rates</div>';
    for (const r of rates) {
      const label = r.key === 'overall' ? 'Overall' : r.key.replace('tier:', '');
      const pct = Math.round(r.rate * 100);
      html += '<div class="analytics-row">' +
        '<span class="analytics-label">' + escapeHtml(label) + '</span>' +
        '<div class="analytics-bar-bg"><div class="analytics-bar" style="width:' + Math.min(pct, 100) + '%"></div></div>' +
        '<span class="analytics-value">' + pct + '% (' + r.responded + '/' + r.total + ')</span>' +
      '</div>';
    }
  }

  // Over-indexing alerts
  const alerts = analytics.over_indexing_alerts || [];
  if (alerts.length > 0) {
    for (const a of alerts) {
      html += '<div class="analytics-alert">' +
        Math.round(a.pct) + '% of applications target ' + escapeHtml(a.dominant) +
        ' (response rate ' + Math.round(a.response_rate_dominant * 100) + '% vs ' +
        Math.round(a.response_rate_others * 100) + '% for others)' +
      '</div>';
    }
  }

  // Tier gaps
  const gaps = analytics.tier_gaps || [];
  if (gaps.length > 0) {
    html += '<div class="section-header">Tier Gaps</div>';
    for (const g of gaps) {
      html += '<div class="analytics-gap">' +
        escapeHtml(g.tier) + ': ' + (g.count === 0 ? 'no applications' : g.count + ' (' + Math.round(g.pct) + '%)') +
      '</div>';
    }
  }

  html += '</div>';
  return html;
}

function renderAppCard(app) {
  const daysText = app.days_since !== null ? app.days_since + 'd ago' : '';
  const cardId = 'app-detail-' + app.id;
  let signalsHtml = '';
  if (app.signals && app.signals.length > 0) {
    signalsHtml = '<div class="app-signals" id="' + cardId + '">';
    for (const s of app.signals) {
      const icon = s.type === 'file' ? '&#x1f4c4;' :
                   s.type === 'email' ? '&#x2709;' :
                   s.type === 'status_change' ? '&#x2192;' : '&#x2022;';
      signalsHtml += '<div class="app-signal">' +
        '<span class="signal-icon">' + icon + '</span>' +
        '<span class="signal-text">' + escapeHtml(s.summary) + '</span>' +
        (s.date ? '<span class="signal-date">' + escapeHtml(s.date.split(',')[0] || s.date.substring(0, 10)) + '</span>' : '') +
      '</div>';
    }
    signalsHtml += '</div>';
  }
  return '<div class="app-card-wrapper">' +
    '<div class="app-card" onclick="toggleAppDetail(\'' + cardId + '\')">' +
      '<div class="app-staleness staleness-' + app.staleness + '"></div>' +
      '<div class="app-info">' +
        '<div class="app-company">' + escapeHtml(app.company_name) +
          (app.company_tier ? ' <span class="tier-badge">' + escapeHtml(app.company_tier) + '</span>' : '') +
        '</div>' +
        '<div class="app-meta">' +
          (daysText ? '<span>' + daysText + '</span>' : '') +
          '<span>' + app.signal_count + ' signal' + (app.signal_count !== 1 ? 's' : '') + '</span>' +
        '</div>' +
      '</div>' +
      '<span class="app-status-badge status-' + app.status + '">' + app.status + '</span>' +
    '</div>' +
    signalsHtml +
  '</div>';
}

function toggleAppDetail(id) {
  const el = document.getElementById(id);
  if (el) {
    el.classList.toggle('expanded');
  }
}

function renderInterviewCard(iv) {
  return '<div class="interview-card">' +
    '<div class="interview-title">' + escapeHtml(iv.title) + '</div>' +
    '<div class="interview-meta">' +
      (iv.company_name ? escapeHtml(iv.company_name) + ' · ' : '') +
      (iv.due || '') +
    '</div>' +
  '</div>';
}

async function refreshPipeline() {
  const btn = document.querySelector('.refresh-pipeline-btn');
  if (btn) {
    btn.textContent = 'Scanning...';
    btn.disabled = true;
  }
  try {
    await pywebview.api.refresh_recruiting_pipeline();
    loadRecruiting();
  } catch (e) {
    if (btn) {
      btn.textContent = 'Scan failed — try again';
      btn.disabled = false;
    }
  }
}

// ─── Mode Banner ────────────────────────────────────────

function renderModeBanner(contexts, taskStats) {
  if (!contexts || contexts.length === 0) return '';
  const primary = contexts[0];
  const modeColors = {
    recruiting: '#4a9eff',
    exams: '#ff4a4a',
    crunch_week: '#ff8c00',
    burnout: '#ff4a4a',
    light_week: '#4aff8c',
  };
  const color = modeColors[primary.season] || '#888';
  // Sentence-case: "Crunch week" instead of "CRUNCH WEEK MODE"
  const raw = primary.season.replace(/_/g, ' ');
  const seasonLabel = raw.charAt(0).toUpperCase() + raw.slice(1);

  // Progress bar from task stats if available
  let progressHtml = '';
  if (taskStats && taskStats.total > 0) {
    const pct = Math.round((taskStats.done / taskStats.total) * 100);
    progressHtml = `
      <div class="mode-progress-wrap">
        <div class="mode-progress-bar"><div class="mode-progress-fill" style="width:${pct}%;background:${color}"></div></div>
        <span class="mode-progress-label">${taskStats.done} / ${taskStats.total} this week</span>
      </div>`;
  }

  return `<div class="mode-banner" style="border-left-color: ${color}">
    <span class="mode-season">${seasonLabel}</span>
    ${primary.label ? `<span class="mode-label">${escapeHtml(primary.label)}</span>` : ''}
    ${progressHtml}
  </div>`;
}

// ─── Ambient Ticker ─────────────────────────────────────

function renderAmbientTicker(notifications) {
  if (!notifications || notifications.length === 0) return '';
  const latest = notifications.slice(-3);
  let html = '<div class="ambient-ticker">';
  for (const n of latest) {
    html += `<div class="ambient-item">
      <span class="ambient-category">${escapeHtml(n.category || '')}</span>
      <span class="ambient-body">${escapeHtml(n.body)}</span>
    </div>`;
  }
  html += '</div>';
  return html;
}

// ─── Voice Input ────────────────────────────────────────

async function toggleVoice() {
  const btn = document.getElementById('voice-btn');
  if (!btn) return;

  if (!isVoiceRecording) {
    try {
      const result = await pywebview.api.start_voice_input();
      if (result.error) {
        addChatMessage('assistant', `Voice error: ${result.error}`);
        return;
      }
      isVoiceRecording = true;
      btn.classList.add('recording');
      btn.title = 'Stop recording';
    } catch (e) {
      addChatMessage('assistant', 'Voice input unavailable.');
    }
  } else {
    isVoiceRecording = false;
    btn.classList.remove('recording');
    btn.title = 'Voice input';

    const loadingId = addChatMessage('assistant', 'Transcribing...', true);
    try {
      const result = await pywebview.api.stop_voice_input();
      removeChatMessage(loadingId);

      if (result.error) {
        addChatMessage('assistant', `Voice error: ${result.error}`);
      } else {
        if (result.transcription) {
          addChatMessage('user', result.transcription);
        }
        addChatMessage('assistant', result.answer || 'No response.');
      }
    } catch (e) {
      removeChatMessage(loadingId);
      addChatMessage('assistant', 'Voice transcription failed.');
    }
  }
}

// ─── Goals ──────────────────────────────────────────────

const GOAL_CATEGORIES = ['academic', 'recruiting', 'health', 'social', 'personal'];

async function loadGoals() {
  const loading = document.getElementById('goals-loading');
  const content = document.getElementById('goals-content');
  loading.style.display = 'block';
  content.innerHTML = '';

  try {
    const goals = await pywebview.api.get_goals();
    loading.style.display = 'none';
    updateConnectionStatus(true);

    let html = `
      <div class="add-form" id="goal-form">
        <input type="text" id="goal-desc" placeholder="New goal description..." class="form-input">
        <select id="goal-category" class="form-select">
          ${GOAL_CATEGORIES.map(c => `<option value="${c}">${c}</option>`).join('')}
        </select>
        <input type="text" id="goal-metric" placeholder="Target metric (optional)" class="form-input">
        <button class="btn btn-approve" onclick="addGoal()">Add Goal</button>
      </div>
    `;

    if (!goals || goals.length === 0) {
      html += '<div class="empty-state"><div class="empty-state-icon">&#x1f3af;</div>No active goals yet.</div>';
      content.innerHTML = html;
      return;
    }

    for (const g of goals) {
      const catColor = {
        academic: 'var(--accent-blue)', recruiting: 'var(--accent-purple)',
        health: 'var(--accent-green)', social: 'var(--accent-orange)',
        personal: 'var(--text-muted)',
      }[g.category] || 'var(--text-muted)';

      html += `
        <div class="pattern-card" style="border-left-color:${catColor}">
          <div class="pattern-desc">${escapeHtml(g.description)}</div>
          ${g.target_metric ? `<div class="pattern-meta" style="margin-top:4px"><small>${escapeHtml(g.target_metric)}</small></div>` : ''}
          <div class="pattern-meta" style="margin-top:6px">
            <span style="color:${catColor};font-weight:600">${escapeHtml(g.category)}</span>
            <span>
              <button class="btn-sm btn-approve" onclick="setGoalStatus(${g.id},'achieved')">Done</button>
              <button class="btn-sm btn-reject" onclick="setGoalStatus(${g.id},'abandoned')">Drop</button>
            </span>
          </div>
        </div>
      `;
    }
    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    content.innerHTML = '<div class="empty-state">Failed to load goals.</div>';
  }
}

async function addGoal() {
  const desc = document.getElementById('goal-desc').value.trim();
  const category = document.getElementById('goal-category').value;
  const metric = document.getElementById('goal-metric').value.trim();
  if (!desc) return;
  await pywebview.api.create_goal(desc, category, metric);
  loadGoals();
}

async function setGoalStatus(goalId, status) {
  await pywebview.api.update_goal_status(goalId, status);
  loadGoals();
}

// ─── Decisions ──────────────────────────────────────────

async function loadDecisions() {
  const loading = document.getElementById('decisions-loading');
  const content = document.getElementById('decisions-content');
  loading.style.display = 'block';
  content.innerHTML = '';

  try {
    const decisions = await pywebview.api.get_decisions(20);
    loading.style.display = 'none';
    updateConnectionStatus(true);

    let html = `
      <div class="add-form" id="decision-form">
        <input type="text" id="decision-desc" placeholder="What did you decide?" class="form-input">
        <input type="text" id="decision-chosen" placeholder="Chosen option" class="form-input">
        <input type="text" id="decision-alts" placeholder="Alternatives (comma-separated)" class="form-input">
        <button class="btn btn-approve" onclick="addDecision()">Record</button>
      </div>
    `;

    if (!decisions || decisions.length === 0) {
      html += '<div class="empty-state"><div class="empty-state-icon">&#x2696;</div>No decisions recorded yet.</div>';
      content.innerHTML = html;
      return;
    }

    for (const d of decisions) {
      const hasOutcome = d.outcome !== null;
      const alts = d.alternatives_considered && d.alternatives_considered.length > 0
        ? d.alternatives_considered.join(', ')
        : '';

      html += `
        <div class="pattern-card" style="border-left-color:${hasOutcome ? 'var(--accent-green)' : 'var(--accent-orange)'}">
          <div class="pattern-desc">${escapeHtml(d.description)}</div>
          <div class="pattern-meta" style="margin-top:4px">
            <span style="font-weight:600">Chose: ${escapeHtml(d.chosen_option)}</span>
          </div>
          ${alts ? `<div class="pattern-meta"><small>Over: ${escapeHtml(alts)}</small></div>` : ''}
          ${hasOutcome
            ? `<div class="pattern-meta" style="margin-top:4px;color:var(--accent-green)"><small>Outcome: ${escapeHtml(d.outcome)}</small></div>`
            : `<div class="pattern-meta" style="margin-top:6px">
                <input type="text" id="outcome-${d.id}" placeholder="Record outcome..." class="form-input" style="flex:1;margin-right:4px">
                <button class="btn-sm btn-approve" onclick="recordOutcome(${d.id})">Save</button>
              </div>`
          }
          <div class="pattern-meta" style="margin-top:2px"><small style="color:var(--text-muted)">${escapeHtml(d.created_at)}</small></div>
        </div>
      `;
    }
    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    content.innerHTML = '<div class="empty-state">Failed to load decisions.</div>';
  }
}

async function addDecision() {
  const desc = document.getElementById('decision-desc').value.trim();
  const chosen = document.getElementById('decision-chosen').value.trim();
  const alts = document.getElementById('decision-alts').value.trim();
  if (!desc || !chosen) return;
  await pywebview.api.create_decision(desc, chosen, alts);
  loadDecisions();
}

async function recordOutcome(decisionId) {
  const input = document.getElementById(`outcome-${decisionId}`);
  const outcome = input ? input.value.trim() : '';
  if (!outcome) return;
  await pywebview.api.record_outcome(decisionId, outcome);
  loadDecisions();
}

// ─── Relationships ──────────────────────────────────────

async function loadRelationships() {
  const loading = document.getElementById('relationships-loading');
  const content = document.getElementById('relationships-content');
  loading.style.display = 'block';
  content.innerHTML = '';

  try {
    const rels = await pywebview.api.get_relationships();
    loading.style.display = 'none';
    updateConnectionStatus(true);

    if (!rels || rels.length === 0) {
      content.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon">&#x1f465;</div>
          No relationships tracked yet.<br>
          <small style="color:var(--text-muted)">Enable social graph in config.toml:<br><code>[social]<br>enable_social_graph = true</code></small>
        </div>`;
      return;
    }

    const trendColors = {
      growing: 'var(--accent-green)',
      stable: 'var(--accent-blue)',
      atrophying: 'var(--accent-red)',
    };

    const trendIcons = {
      growing: '&#x2197;',
      stable: '&#x2192;',
      atrophying: '&#x2198;',
    };

    let html = '';
    for (const r of rels) {
      const color = trendColors[r.trend] || 'var(--text-muted)';
      const icon = trendIcons[r.trend] || '';
      const lastSeen = r.last_interaction_at
        ? new Date(r.last_interaction_at).toLocaleDateString()
        : 'unknown';

      html += `
        <div class="pattern-card" style="border-left-color:${color}">
          <div class="pattern-desc">${escapeHtml(r.person)}</div>
          <div class="pattern-meta" style="margin-top:4px">
            <span>${escapeHtml(r.channel)}</span>
            <span>${r.interaction_count} interaction${r.interaction_count !== 1 ? 's' : ''}</span>
          </div>
          <div class="pattern-meta" style="margin-top:4px">
            <span style="color:${color}">${icon} ${escapeHtml(r.trend)}</span>
            <small style="color:var(--text-muted)">Last: ${lastSeen}</small>
          </div>
        </div>
      `;
    }
    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    content.innerHTML = '<div class="empty-state">Failed to load relationships.</div>';
  }
}

// ─── Day (Daily Reconstruction Timeline) ───────────────────────────

let currentDayDate = ''; // track which date is loaded

async function loadDay(date) {
  const loading = document.getElementById('day-loading');
  const content = document.getElementById('day-content');
  loading.style.display = 'block';
  content.innerHTML = '';

  try {
    const dateParam = date || '';
    const data = await pywebview.api.get_daily_reconstruction(dateParam);
    loading.style.display = 'none';
    updateConnectionStatus(true);

    if (data.error) {
      content.innerHTML = '<div class="empty-state">' + escapeHtml(data.error) + '</div>';
      return;
    }

    currentDayDate = data.date;
    let html = '';

    // Header with date nav
    html += '<div class="day-header">' +
      '<button class="btn btn-sm" onclick="navigateDay(-1)" title="Previous day">&#x25c0;</button>' +
      '<span class="day-date">' + data.date + '</span>' +
      '<button class="btn btn-sm" onclick="navigateDay(1)" title="Next day">&#x25b6;</button>' +
      '</div>';

    // Overall confidence
    const confPct = Math.round(data.overall_confidence * 100);
    const confColor = confPct >= 80 ? 'var(--accent-green)' : confPct >= 60 ? 'var(--accent-orange)' : 'var(--accent-red)';
    html += '<div class="day-confidence">' +
      '<span style="color:' + confColor + '">' + confPct + '% confidence</span>' +
      (data.user_confirmed ? ' <span class="intervention-badge badge-acted">confirmed</span>' : '') +
      '</div>';

    // Timeline segments
    if (data.segments && data.segments.length > 0) {
      html += '<div class="day-timeline">';
      const typeIcons = { work: 'W', meeting: 'M', idle: '-', email: 'E', unknown: '?' };
      const typeColors = {
        work: 'var(--accent-blue)',
        meeting: 'var(--accent-purple)',
        idle: 'var(--text-muted)',
        email: 'var(--accent-orange)',
        unknown: 'var(--text-muted)',
      };

      for (let i = 0; i < data.segments.length; i++) {
        const seg = data.segments[i];
        const icon = typeIcons[seg.activity_type] || '?';
        const color = typeColors[seg.activity_type] || 'var(--text-muted)';
        const segConf = Math.round(seg.confidence * 100);
        const startT = formatTimeShort(seg.start_time);
        const endT = formatTimeShort(seg.end_time);

        // Confidence indicator
        let confIndicator = '';
        if (segConf < 60) confIndicator = ' <span style="color:var(--accent-red)">?</span>';
        else if (segConf < 80) confIndicator = ' <span style="color:var(--accent-orange)">~</span>';

        // Confirmed status
        let confirmedBadge = '';
        if (seg.confirmed === true) confirmedBadge = ' <span class="intervention-badge badge-acted">yes</span>';
        else if (seg.confirmed === false) confirmedBadge = ' <span class="intervention-badge badge-suppressed">no</span>';

        html += '<div class="day-segment" style="border-left-color:' + color + '">' +
          '<div class="day-segment-header">' +
            '<span class="day-segment-icon" style="color:' + color + '">' + icon + '</span>' +
            '<span class="day-segment-time">' + startT + ' - ' + endT + '</span>' +
            '<span class="day-segment-conf">' + segConf + '%' + confIndicator + '</span>' +
          '</div>' +
          '<div class="day-segment-desc">' + escapeHtml(seg.description || seg.task_title || '') + '</div>';

        // Confirmation buttons for low-confidence unconfirmed work segments
        if (seg.activity_type === 'work' && seg.confidence < 0.6 && seg.confirmed === null) {
          html += '<div class="micro-card-actions" style="margin-top:4px">' +
            '<button class="btn btn-approve btn-sm" onclick="respondDayConfirm(\'' + data.date + '\',' + i + ',true)">Yes</button>' +
            '<button class="btn btn-reject btn-sm" onclick="respondDayConfirm(\'' + data.date + '\',' + i + ',false)">No</button>' +
          '</div>';
        }

        html += confirmedBadge + '</div>';
      }
      html += '</div>';
    } else {
      html += '<div class="empty-state">No activity detected.</div>';
    }

    // Narrative
    if (data.narrative) {
      html += '<div class="day-narrative">' + escapeHtml(data.narrative) + '</div>';
    }

    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    updateConnectionStatus(false);
    content.innerHTML = '<div class="empty-state">Failed to load day timeline.</div>';
  }
}

function navigateDay(offset) {
  const d = currentDayDate ? new Date(currentDayDate + 'T12:00:00') : new Date();
  d.setDate(d.getDate() + offset);
  const iso = d.toISOString().slice(0, 10);
  loadDay(iso);
}

async function respondDayConfirm(date, segmentIndex, confirmed) {
  try {
    await pywebview.api.respond_micro_confirmation(date, segmentIndex, confirmed);
    loadDay(date);
  } catch (e) {
    // Silently fail
  }
}


// ─── Brain (Intervention Engine + Micro-confirmations + Briefing) ───

async function loadBrain() {
  const loading = document.getElementById('brain-loading');
  const content = document.getElementById('brain-content');
  loading.style.display = 'block';
  content.innerHTML = '';

  try {
    const [stats, interventions, escalations, confirmations] = await Promise.all([
      pywebview.api.get_intervention_stats(),
      pywebview.api.get_interventions(30),
      pywebview.api.get_intervention_escalations(),
      pywebview.api.get_micro_confirmations(''),
    ]);
    loading.style.display = 'none';
    updateConnectionStatus(true);

    let html = '';

    // ── Section A: Session Briefing ──
    html += '<div class="section-header">Session Briefing</div>';
    html += '<div id="briefing-container">' +
      '<button class="btn btn-approve" onclick="triggerSessionBriefing()">What should I do right now?</button>' +
      '<div id="briefing-result"></div>' +
      '</div>';

    // ── Section B: Intervention Budget ──
    if (stats && !stats.error) {
      const b = stats.budget;
      const pct = b.max_per_day > 0 ? Math.min(100, Math.round((b.fired_today / b.max_per_day) * 100)) : 0;
      const budgetColor = pct >= 80 ? 'var(--accent-red)' : pct >= 50 ? 'var(--accent-orange)' : 'var(--accent-green)';

      html += '<div class="section-header">Interrupt Budget</div>';
      html += '<div class="brain-budget-card">' +
        '<div class="brain-budget-label">' + b.fired_today + ' / ' + b.max_per_day + ' interrupts used today</div>' +
        '<div class="brain-budget-bar-track">' +
          '<div class="brain-budget-bar-fill" style="width:' + pct + '%;background:' + budgetColor + '"></div>' +
        '</div>' +
        '<div class="brain-budget-meta">' +
          '<span>Quiet hours: ' + (b.quiet_hours_active ? '<span style="color:var(--accent-green)">active</span>' : 'off') +
          ' (' + b.quiet_hours_start + ' - ' + b.quiet_hours_end + ')</span>' +
        '</div>' +
      '</div>';

      // ── Section C: Effectiveness ──
      if (stats.effectiveness && stats.effectiveness.length > 0) {
        html += '<div class="section-header">Effectiveness <span class="section-count">30d</span></div>';
        const tierOrder = ['silent', 'menubar', 'notification', 'voice'];
        const tierColors = {
          silent: 'var(--text-muted)',
          menubar: 'var(--accent-blue)',
          notification: 'var(--accent-orange)',
          voice: 'var(--accent-red)',
        };
        const sorted = tierOrder.map(t => stats.effectiveness.find(e => e.tier === t)).filter(Boolean);
        for (const e of sorted) {
          const ratePct = Math.round(e.rate * 100);
          html += '<div class="brain-tier-row">' +
            '<span class="brain-tier-label" style="color:' + (tierColors[e.tier] || 'var(--text-secondary)') + '">' + e.tier + '</span>' +
            '<div class="brain-tier-bar-track">' +
              '<div class="brain-tier-bar-fill" style="width:' + ratePct + '%;background:' + (tierColors[e.tier] || 'var(--text-muted)') + '"></div>' +
            '</div>' +
            '<span class="brain-tier-stat">' + ratePct + '% (' + e.acted + '/' + e.total + ')</span>' +
          '</div>';
        }
      }

      // ── Section D: Pending Escalations ──
      if (stats.escalations_pending > 0 && escalations && escalations.length > 0) {
        html += '<div class="section-header">Pending Escalations <span class="section-count">' + escalations.length + '</span></div>';
        for (const esc of escalations) {
          const escTime = formatRelativeTime(esc.escalate_after);
          html += '<div class="escalation-card">' +
            '<div class="escalation-tiers">' +
              '<span class="intervention-badge badge-' + esc.current_tier + '">' + esc.current_tier + '</span>' +
              ' <span style="color:var(--text-muted)">&#x2192;</span> ' +
              '<span class="intervention-badge badge-' + esc.next_tier + '">' + esc.next_tier + '</span>' +
            '</div>' +
            '<div style="color:var(--text-muted);font-size:11px">Task #' + esc.task_id + ' &middot; ' + escTime + '</div>' +
          '</div>';
        }
      }
    }

    // ── Section E: Micro-Confirmations ──
    html += '<div class="section-header">Micro-Confirmations' +
      (confirmations && confirmations.length > 0 ? ' <span class="section-count">' + confirmations.length + '</span>' : '') +
      '</div>';
    if (!confirmations || confirmations.length === 0) {
      html += '<div class="empty-state" style="padding:8px 0;font-size:12px;color:var(--text-muted)">No confirmations needed.</div>';
    } else {
      for (const mc of confirmations) {
        const startT = formatTimeShort(mc.start_time);
        const endT = formatTimeShort(mc.end_time);
        const confPct = Math.round(mc.confidence * 100);
        html += '<div class="micro-card">' +
          '<div class="micro-card-header">' +
            '<span class="micro-card-title">' + (mc.task_title || 'Unknown task') + '</span>' +
            '<span class="micro-card-conf" title="Confidence">' + confPct + '%</span>' +
          '</div>' +
          '<div class="micro-card-time">' + startT + ' - ' + endT + '</div>' +
          '<div class="micro-card-actions">' +
            '<button class="btn btn-approve btn-sm" onclick="respondMicroConfirmation(\'' + mc.date + '\',' + mc.segment_index + ',true)">Yes</button>' +
            '<button class="btn btn-reject btn-sm" onclick="respondMicroConfirmation(\'' + mc.date + '\',' + mc.segment_index + ',false)">No</button>' +
          '</div>' +
        '</div>';
      }
    }

    // ── Section F: Recent Interventions ──
    if (interventions && interventions.length > 0 && !interventions.error) {
      html += '<div class="section-header">Recent Interventions <span class="section-count">' + interventions.length + '</span></div>';
      for (const r of interventions) {
        const timeAgo = formatRelativeTime(r.created_at);
        const tierColors = {
          silent: 'var(--text-muted)',
          menubar: 'var(--accent-blue)',
          notification: 'var(--accent-orange)',
          voice: 'var(--accent-red)',
        };
        const borderColor = tierColors[r.intervention_tier] || 'var(--text-muted)';
        const firedBadge = r.fired
          ? '<span class="intervention-badge badge-fired">fired</span>'
          : '<span class="intervention-badge badge-suppressed">' + (r.suppression_reason || 'suppressed') + '</span>';
        const actedBadge = r.fired && r.user_acted
          ? ' <span class="intervention-badge badge-acted">acted</span>'
          : '';

        html += '<div class="intervention-card" style="border-left-color:' + borderColor + '">' +
          '<div class="intervention-card-header">' +
            '<span class="intervention-card-title">' + escapeHtml(r.title) + '</span>' +
            '<span class="intervention-card-time">' + timeAgo + '</span>' +
          '</div>' +
          '<div class="intervention-card-body">' + escapeHtml(r.body) + '</div>' +
          '<div class="intervention-card-meta">' +
            '<span class="intervention-badge badge-' + r.intervention_tier + '">' + r.intervention_tier + '</span> ' +
            firedBadge + actedBadge +
          '</div>' +
        '</div>';
      }
    }

    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    updateConnectionStatus(false);
    content.innerHTML = '<div class="empty-state">Failed to load brain state.</div>';
  }
}

async function triggerSessionBriefing() {
  const container = document.getElementById('briefing-result');
  container.innerHTML = '<div class="loading" style="display:block;padding:8px 0">Generating briefing...</div>';
  try {
    const data = await pywebview.api.get_session_briefing();
    if (data && data.briefing) {
      container.innerHTML = '<div class="briefing-box">' + escapeHtml(data.briefing) + '</div>';
    } else {
      container.innerHTML = '<div class="briefing-box" style="color:var(--text-muted)">No briefing available — not enough data yet.</div>';
    }
  } catch (e) {
    container.innerHTML = '<div class="briefing-box" style="color:var(--accent-red)">Failed to generate briefing.</div>';
  }
}

async function respondMicroConfirmation(date, segmentIndex, confirmed) {
  try {
    await pywebview.api.respond_micro_confirmation(date, segmentIndex, confirmed);
    // Reload just the micro-confirmations section
    loadBrain();
  } catch (e) {
    // Silently fail — will reload on next refresh
  }
}

function formatRelativeTime(isoStr) {
  if (!isoStr) return '';
  try {
    const dt = new Date(isoStr);
    const now = new Date();
    const diffMs = now - dt;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return diffMin + 'm ago';
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return diffHr + 'h ago';
    const diffDays = Math.floor(diffHr / 24);
    return diffDays + 'd ago';
  } catch (e) {
    return isoStr;
  }
}

function formatTimeShort(isoStr) {
  if (!isoStr) return '';
  try {
    const dt = new Date(isoStr);
    return dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
  } catch (e) {
    return isoStr;
  }
}

// ─── Me (Identity + Knowledge Graph) ───────────────────

async function loadMe() {
  const loading = document.getElementById('me-loading');
  const content = document.getElementById('me-content');
  loading.style.display = 'block';
  content.innerHTML = '';

  try {
    const [identity, kg] = await Promise.all([
      pywebview.api.get_identity(),
      pywebview.api.get_knowledge_graph(),
    ]);
    loading.style.display = 'none';
    updateConnectionStatus(true);

    let html = '';

    // Identity document
    html += '<div class="section-header">Identity<span class="section-count">v' + (identity.version || '?') + '</span></div>';
    if (identity.markdown) {
      html += '<div class="identity-doc">' + renderMarkdown(identity.markdown) + '</div>';
    } else {
      html += '<div class="empty-state">No identity document yet.</div>';
    }
    html += '<div style="margin:8px 0"><button class="btn btn-approve" onclick="resynthesizeIdentity()">Resynthesize</button>';
    if (identity.last_synthesis_at) {
      html += ' <small style="color:var(--text-muted)">Last: ' + escapeHtml(identity.last_synthesis_at.substring(0, 16)) + '</small>';
    }
    html += '</div>';

    // Knowledge Graph
    if (kg.entities && kg.entities.length > 0) {
      const grouped = {};
      for (const e of kg.entities) {
        const t = e.type || 'other';
        if (!grouped[t]) grouped[t] = [];
        grouped[t].push(e);
      }
      for (const [type, items] of Object.entries(grouped)) {
        html += `<div class="section-header">${escapeHtml(type)}s<span class="section-count">${items.length}</span></div>`;
        html += '<div class="kg-grid">';
        for (const e of items.slice(0, 20)) {
          html += `<span class="kg-entity">${escapeHtml(e.name)}<small>${e.mention_count}</small></span>`;
        }
        if (items.length > 20) html += `<span class="kg-entity" style="opacity:0.5">+${items.length - 20} more</span>`;
        html += '</div>';
      }
    } else if (kg.summary) {
      html += '<div class="section-header">Knowledge Graph</div>';
      html += '<div style="font-size:12px;color:var(--text-secondary);padding:4px">' + escapeHtml(kg.summary) + '</div>';
    }

    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    content.innerHTML = '<div class="empty-state">Failed to load identity data.</div>';
  }
}

async function resynthesizeIdentity() {
  const content = document.getElementById('me-content');
  content.innerHTML = '<div class="loading">Synthesizing identity... this may take a minute.</div>';
  try {
    await pywebview.api.synthesize_identity();
    loadMe();
  } catch (e) {
    content.innerHTML = '<div class="empty-state">Synthesis failed.</div>';
  }
}

// ─── Simulation ─────────────────────────────────────────

function renderAddTaskForm() {
  return `<div class="add-task-section">
    <div class="section-header" style="cursor:pointer" onclick="toggleAddTask()">
      + Add Task <span id="add-task-toggle" style="font-size:10px;color:var(--text-muted)">&#x25bc;</span>
    </div>
    <div id="add-task-form" style="display:none">
      <input type="text" id="add-task-title" class="form-input" placeholder="Task title">
      <div style="display:flex;gap:6px;margin-top:4px">
        <input type="text" id="add-task-due" class="form-input" placeholder="Due: YYYY-MM-DD" style="flex:1">
        <select id="add-task-type" class="form-input" style="flex:1">
          <option value="personal">Personal</option>
          <option value="project">Project</option>
          <option value="assignment">Assignment</option>
          <option value="exam">Exam</option>
          <option value="meeting">Meeting</option>
          <option value="interview_prep">Interview Prep</option>
        </select>
      </div>
      <div style="display:flex;gap:6px;margin-top:4px">
        <input type="text" id="add-task-course" class="form-input" placeholder="Course / project (optional)" style="flex:1">
        <button class="btn btn-approve" onclick="submitAddTask()">Add</button>
      </div>
      <div id="add-task-result" style="font-size:11px;margin-top:4px"></div>
    </div>
  </div>`;
}

function toggleAddTask() {
  const form = document.getElementById('add-task-form');
  const toggle = document.getElementById('add-task-toggle');
  if (form.style.display === 'none') {
    form.style.display = 'block';
    toggle.innerHTML = '&#x25b2;';
  } else {
    form.style.display = 'none';
    toggle.innerHTML = '&#x25bc;';
  }
}

async function submitAddTask() {
  const title = document.getElementById('add-task-title').value.trim();
  const due = document.getElementById('add-task-due').value;
  const type = document.getElementById('add-task-type').value;
  const course = document.getElementById('add-task-course').value.trim();
  const resultDiv = document.getElementById('add-task-result');

  if (!title) {
    resultDiv.innerHTML = '<span style="color:var(--accent-red)">Title is required</span>';
    return;
  }

  resultDiv.innerHTML = '<span style="color:var(--text-muted)">Adding...</span>';

  try {
    const result = await pywebview.api.create_task(title, due || '', type, course, 3);
    if (result.error) {
      resultDiv.innerHTML = '<span style="color:var(--accent-red)">' + escapeHtml(result.error) + '</span>';
    } else {
      resultDiv.innerHTML = '<span style="color:var(--accent-green)">Added: ' + escapeHtml(result.title) + '</span>';
      document.getElementById('add-task-title').value = '';
      document.getElementById('add-task-due').value = '';
      document.getElementById('add-task-course').value = '';
    }
  } catch (e) {
    resultDiv.innerHTML = '<span style="color:var(--accent-red)">Failed to add task</span>';
  }
}

function renderSimulationButton() {
  return `<div class="sim-prompt" style="margin-bottom:10px">
    <div class="section-header">What if...</div>
    <div style="display:flex;gap:6px">
      <input type="text" id="sim-input" class="form-input" placeholder="e.g. I take a part-time job 15h/week" onkeydown="if(event.key==='Enter')runSimulation()">
      <button class="btn btn-approve" onclick="runSimulation()">Simulate</button>
    </div>
    <div id="sim-result">${lastSimResultHtml}</div>
  </div>`;
}

let simSessionId = '';  // for conversational follow-up

async function runSimulation() {
  const input = document.getElementById('sim-input');
  const resultDiv = document.getElementById('sim-result');
  const scenario = input ? input.value.trim() : '';
  if (!scenario) return;

  resultDiv.innerHTML = '<div class="loading" style="padding:10px">Running simulation...</div>';

  try {
    const result = await pywebview.api.run_simulation_v2(scenario, simSessionId);

    if (result.error) {
      resultDiv.innerHTML = `<div class="empty-state">${escapeHtml(result.error)}</div>`;
      return;
    }

    // Track session for follow-up
    simSessionId = result.session_id || '';

    const confColors = { high: 'var(--accent-green)', medium: 'var(--accent-orange)', low: 'var(--accent-red)' };
    const sevColors = { positive: 'var(--accent-green)', neutral: 'var(--text-muted)', concerning: 'var(--accent-orange)', critical: 'var(--accent-red)' };

    let html = `<div class="sim-result-card">`;
    html += `<div class="sim-confidence" style="color:${confColors[result.confidence] || 'var(--text-muted)'}">
      ${escapeHtml(result.confidence)} confidence — ${escapeHtml(result.confidence_reason)}</div>`;

    // Risk deltas table (Phase 31)
    if (result.risk_deltas && result.risk_deltas.length > 0) {
      const significant = result.risk_deltas.filter(d => Math.abs(d.delta) >= 0.01);
      if (significant.length > 0) {
        html += '<div class="sim-deltas">';
        html += '<div class="section-header" style="margin-top:8px">Risk Impact</div>';
        for (const d of significant) {
          const sign = d.delta > 0 ? '+' : '';
          const deltaColor = d.delta > 0 ? 'var(--accent-red)' : d.delta < 0 ? 'var(--accent-green)' : 'var(--text-muted)';
          const arrow = d.delta > 0 ? '&#x25b2;' : '&#x25bc;';
          const course = d.course ? ' (' + escapeHtml(d.course) + ')' : '';
          html += `<div class="sim-delta-row">
            <span class="sim-delta-task">${escapeHtml(d.task_title)}${course}</span>
            <span class="sim-delta-values">
              ${Math.round(d.p_miss_before * 100)}% <span style="color:var(--text-muted)">&#x2192;</span> ${Math.round(d.p_miss_after * 100)}%
              <span class="sim-delta-change" style="color:${deltaColor}">${arrow} ${sign}${Math.round(d.delta * 100)}%</span>
            </span>
          </div>`;
        }
        html += '</div>';
      }
    }

    // Drop analysis (Phase 31)
    if (result.drop_analysis) {
      const da = result.drop_analysis;
      html += '<div class="sim-drop-analysis">';
      html += `<div class="section-header" style="margin-top:8px">Drop Analysis</div>`;
      html += `<div class="sim-drop-detail">Dropped: <strong>${escapeHtml(da.dropped_task)}</strong></div>`;
      html += `<div class="sim-drop-detail">Freed: ${da.freed_hours.toFixed(1)}h &middot; Net risk: <span style="color:${da.net_risk_change <= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}">${da.net_risk_change > 0 ? '+' : ''}${Math.round(da.net_risk_change * 100)}%</span></div>`;
      if (da.beneficiaries && da.beneficiaries.length > 0) {
        for (const b of da.beneficiaries) {
          html += `<div class="sim-drop-benefit">&#x2193; ${escapeHtml(b.task_title)} (${Math.round(b.delta * 100)}%)</div>`;
        }
      }
      html += '</div>';
    }

    if (result.projected_impacts && result.projected_impacts.length > 0) {
      html += '<div class="sim-impacts">';
      for (const impact of result.projected_impacts) {
        const color = sevColors[impact.severity] || 'var(--text-muted)';
        html += `<div class="sim-impact" style="border-left-color:${color}">
          <span class="sim-domain">${escapeHtml(impact.domain)}</span>
          <span class="sim-impact-text">${escapeHtml(impact.impact)}</span>
        </div>`;
      }
      html += '</div>';
    }

    if (result.recommendation) {
      html += `<div class="sim-recommendation">${renderMarkdown(result.recommendation)}</div>`;
    }

    if (result.weekly_projection) {
      html += `<div class="sim-projection">${renderMarkdown(result.weekly_projection)}</div>`;
    }

    // Follow-up hint
    if (simSessionId) {
      html += `<div style="font-size:10px;color:var(--text-muted);margin-top:6px">Session active — type another scenario to follow up</div>`;
    }

    html += '</div>';
    resultDiv.innerHTML = html;
    lastSimResultHtml = html;
    if (input) input.value = '';
  } catch (e) {
    resultDiv.innerHTML = '<div class="empty-state">Simulation failed.</div>';
  }
}

// ─── Enhanced Dashboard: Recovery Plans + Life Context ──

async function loadRecoveryPlans() {
  try {
    const data = await pywebview.api.get_recovery_plans();
    if (data.plans && data.plans.length > 0) {
      let html = '<div class="section-header">Recovery Plans<span class="section-count">' + data.plans.length + '</span></div>';
      for (const plan of data.plans) {
        html += `<div class="recovery-card">
          <div class="recovery-title">${escapeHtml(plan.task_title || 'Overdue task')}</div>
          <div class="recovery-body">${renderMarkdown(plan.plan || plan.content || '')}</div>
        </div>`;
      }
      return html;
    }
  } catch (e) { /* recovery plans unavailable */ }
  return '';
}

function renderLifeContextManager(contexts) {
  let html = '<div class="context-manager">';

  // Active contexts list
  if (contexts && contexts.length > 0) {
    for (const ctx of contexts) {
      const seasonLabel = ctx.season.replace(/_/g, ' ');
      const displayLabel = seasonLabel.charAt(0).toUpperCase() + seasonLabel.slice(1);
      html += `<div class="context-tag">
        <span>${displayLabel}</span>
        ${ctx.source === 'manual' ? `<button class="context-remove" onclick="removeContext(${ctx.id})" title="Remove">&times;</button>` : ''}
      </div>`;
    }
  }

  // Add context button
  html += `<button class="btn-sm btn-approve" onclick="toggleContextForm()" id="ctx-add-btn">+ Add</button>`;
  html += `<div class="context-form" id="context-form" style="display:none">
    <select id="ctx-season" class="form-select">
      <option value="recruiting">Recruiting</option>
      <option value="exams">Exams</option>
      <option value="crunch_week">Crunch week</option>
      <option value="light_week">Light week</option>
    </select>
    <input type="text" id="ctx-label" class="form-input" placeholder="Label (optional)" style="min-width:80px">
    <input type="date" id="ctx-start" class="form-input">
    <input type="date" id="ctx-end" class="form-input">
    <button class="btn btn-approve" onclick="addContext()">Set</button>
  </div>`;

  html += '</div>';
  return html;
}

function toggleContextForm() {
  const form = document.getElementById('context-form');
  if (form) form.style.display = form.style.display === 'none' ? 'flex' : 'none';
}

async function addContext() {
  const season = document.getElementById('ctx-season').value;
  const label = document.getElementById('ctx-label').value.trim();
  const start = document.getElementById('ctx-start').value;
  const end = document.getElementById('ctx-end').value;
  if (!start || !end) return;
  await pywebview.api.set_life_context(season, start, end, label);
  loadDashboard();
}

async function removeContext(contextId) {
  await pywebview.api.deactivate_life_context(contextId);
  loadDashboard();
}

// ─── Enhanced Actions: Calendar Gaps + Scheduler + Negotiations ──

async function loadCalendarGaps() {
  try {
    const gaps = await pywebview.api.get_calendar_gaps();
    if (!gaps || gaps.length === 0) return '';

    let html = '<div class="section-header">Free Windows<span class="section-count">' + gaps.length + '</span></div>';
    html += '<div class="gaps-grid">';
    for (const gap of gaps.slice(0, 8)) {
      const start = new Date(gap.start);
      const dayLabel = start.toLocaleDateString('en-US', { weekday: 'short' });
      const timeLabel = start.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
      const durH = Math.floor(gap.duration_minutes / 60);
      const durM = gap.duration_minutes % 60;
      const durLabel = durM > 0 ? `${durH}h${durM}m` : `${durH}h`;
      html += `<div class="gap-block">
        <span class="gap-day">${dayLabel}</span>
        <span class="gap-time">${timeLabel}</span>
        <span class="gap-dur">${durLabel}</span>
      </div>`;
    }
    html += '</div>';
    html += '<div style="margin:6px 0"><button class="btn btn-approve" onclick="scheduleWeek()">Schedule my week</button></div>';
    return html;
  } catch (e) { return ''; }
}

async function scheduleWeek() {
  const content = document.getElementById('actions-content');
  const orig = content.innerHTML;
  content.innerHTML = '<div class="loading">Generating schedule...</div>';
  try {
    const blocks = await pywebview.api.propose_schedule();
    if (blocks && blocks.length > 0) {
      loadActions();
    } else {
      content.innerHTML = orig;
    }
  } catch (e) {
    content.innerHTML = orig;
  }
}

async function loadNegotiations() {
  try {
    const sessions = await pywebview.api.list_negotiations('active');
    if (!sessions || sessions.length === 0) return '';

    let html = '<div class="section-header">Active Negotiations<span class="section-count">' + sessions.length + '</span></div>';
    for (const s of sessions) {
      html += `<div class="negotiation-card">
        <div class="negotiation-status">Session #${s.id}</div>
        <div class="negotiation-reply">${escapeHtml(s.last_reply || s.status || '')}</div>
        <div class="negotiation-input">
          <input type="text" id="neg-input-${s.id}" class="form-input" placeholder="Reply..." onkeydown="if(event.key==='Enter')sendNegotiation(${s.id})">
          <button class="btn btn-approve" onclick="sendNegotiation(${s.id})">Send</button>
        </div>
      </div>`;
    }
    return html;
  } catch (e) { return ''; }
}

async function sendNegotiation(sessionId) {
  const input = document.getElementById(`neg-input-${sessionId}`);
  const msg = input ? input.value.trim() : '';
  if (!msg) return;
  input.value = '';
  try {
    await pywebview.api.send_negotiation_message(sessionId, msg);
    loadActions();
  } catch (e) {
    console.error('Negotiation failed:', e);
  }
}

// ─── Enhanced Digest: Weekly Snapshots + Debrief ────────

async function loadWeeklySnapshots() {
  try {
    const snapshots = await pywebview.api.get_weekly_snapshots();
    if (!snapshots || snapshots.length === 0) return '';

    let html = '<div class="section-header">Weekly History<span class="section-count">' + snapshots.length + '</span></div>';
    for (const s of snapshots.slice(0, 6)) {
      const weekLabel = s.week_start ? s.week_start.substring(0, 10) : '?';
      html += `<div class="snapshot-card">
        <div class="snapshot-week">Week of ${weekLabel}</div>
        <div class="snapshot-stats">
          ${s.tasks_completed !== undefined ? `<span>${s.tasks_completed} done</span>` : ''}
          ${s.tasks_slipped !== undefined ? `<span>${s.tasks_slipped} slipped</span>` : ''}
          ${s.total_hours !== undefined ? `<span>${s.total_hours.toFixed(1)}h</span>` : ''}
        </div>
        ${s.key_events ? `<div class="snapshot-events">${escapeHtml(s.key_events.substring(0, 100))}</div>` : ''}
      </div>`;
    }
    return html;
  } catch (e) { return ''; }
}

async function loadDebriefSection() {
  try {
    const debriefs = await pywebview.api.get_debriefs();
    let html = '<div class="section-header">Semester Debriefs</div>';

    if (debriefs && debriefs.length > 0) {
      for (const d of debriefs.slice(0, 3)) {
        html += `<div class="pattern-card" style="border-left-color:var(--accent-purple)">
          <div class="pattern-desc">${escapeHtml(d.term || 'Debrief')}</div>
          <div class="pattern-meta"><small>${escapeHtml((d.created_at || '').substring(0, 10))}</small></div>
        </div>`;
      }
    }

    html += '<button class="btn btn-approve" onclick="generateDebrief()" style="margin-top:6px">Generate Debrief</button>';
    return html;
  } catch (e) { return ''; }
}

async function generateDebrief() {
  const content = document.getElementById('digest-content');
  content.innerHTML = '<div class="loading">Generating debrief... this may take a minute.</div>';
  try {
    const result = await pywebview.api.generate_debrief('');
    if (result.narrative) {
      content.innerHTML = renderMarkdown(result.narrative);
    } else if (result.error) {
      content.innerHTML = '<div class="empty-state">Debrief failed: ' + escapeHtml(result.error) + '</div>';
    } else {
      content.innerHTML = renderMarkdown(JSON.stringify(result, null, 2));
    }
  } catch (e) {
    content.innerHTML = '<div class="empty-state">Debrief generation failed.</div>';
  }
}

// ─── Init ────────────────────────────────────────────────

window.addEventListener('pywebviewready', function () {
  loadDashboard();
  // Auto-refresh dashboard every 60 seconds
  refreshInterval = setInterval(() => {
    if (currentTab === 'dashboard') {
      loadDashboard();
    }
  }, 60000);
});
