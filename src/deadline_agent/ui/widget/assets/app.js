// Deadline Agent Widget — Main JS

let currentTab = 'dashboard';
let chatHistory = [];
let refreshInterval = null;

// ─── Tab Navigation ──────────────────────────────────────

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelector(`.tab[data-tab="${tab}"]`).classList.add('active');
  document.getElementById(`panel-${tab}`).classList.add('active');
  loadTab(tab);
}

function loadTab(tab) {
  switch (tab) {
    case 'dashboard': loadDashboard(); break;
    case 'chat': break; // Chat state persists
    case 'insights': loadInsights(); break;
    case 'actions': loadActions(); break;
    case 'digest': loadDigest(); break;
    case 'patterns': loadPatterns(); break;
    case 'recruiting': loadRecruiting(); break;
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

    // Empty state
    if (!html) {
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
  const due = task.due ? `<span class="task-due">${escapeHtml(task.due)}</span>` : '';
  const urgency = task.urgency ? `<span class="task-urgency">${escapeHtml(task.urgency.split(' ')[0])}</span>` : '';

  return `
    <div class="task-card urgency-${urgencyClass}">
      <div class="task-title">${escapeHtml(task.title)}</div>
      <div class="task-meta">${urgency}${course}${due}</div>
      <div class="task-actions">
        <button class="btn btn-done" onclick="markTask(${task.id}, 'done')">Done</button>
        <button class="btn btn-dismiss" onclick="markTask(${task.id}, 'dismissed')">Dismiss</button>
      </div>
    </div>
  `;
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
    for (const action of actions) {
      html += renderActionCard(action);
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

    content.innerHTML = renderMarkdown(digest.text || 'No digest available.');
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

    let html = '';

    // Summary bar
    if (data.summary) {
      html += renderPipelineSummary(data.summary);
    }

    // Upcoming interviews
    if (data.upcoming_interviews && data.upcoming_interviews.length > 0) {
      html += '<div class="section-header">Upcoming Interviews' +
        '<span class="section-count">' + data.upcoming_interviews.length + '</span></div>';
      for (const iv of data.upcoming_interviews) {
        html += renderInterviewCard(iv);
      }
    }

    // Applications grouped by status
    const statusOrder = ['interview', 'response', 'applied', 'offer', 'closed'];
    const statusLabels = {
      interview: 'Interviewing', response: 'Response', applied: 'Applied',
      offer: 'Offer', closed: 'Closed'
    };
    for (const status of statusOrder) {
      const apps = (data.applications || []).filter(a => a.status === status);
      if (apps.length > 0) {
        html += '<div class="section-header">' + statusLabels[status] +
          '<span class="section-count">' + apps.length + '</span></div>';
        for (const app of apps) {
          html += renderAppCard(app);
        }
      }
    }

    if (!html || (!data.applications?.length && !data.upcoming_interviews?.length)) {
      html = '<div class="empty-state"><div class="empty-state-icon">&#x1f4bc;</div>' +
        'No applications detected yet.<br><small>Auto-detected from resumes, emails, and calendar events.</small></div>';
    }

    html += '<button class="refresh-pipeline-btn" onclick="refreshPipeline()">Scan files &amp; emails for applications</button>';
    content.innerHTML = html;
  } catch (e) {
    loading.style.display = 'none';
    updateConnectionStatus(false);
    content.innerHTML = '<div class="empty-state">Failed to load pipeline.</div>';
  }
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
        '<div class="app-company">' + escapeHtml(app.company_name) + '</div>' +
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
