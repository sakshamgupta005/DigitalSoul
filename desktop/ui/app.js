'use strict';

const $ = (id) => document.getElementById(id);
const state = { expanded: false, connected: false, recording: false, running: false, workflows: [], patterns: [], jobs: [], detail: null, pending: false, user_memory: {}, recent_routines: [] };
const tauri = window.__TAURI__;
const timeLabel = (seconds) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;
const dateLabel = (date) => new Date(date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

const IDLE_LABEL = 'Boo! I remember your routines.';
const ago = (date) => {
  const minutes = Math.round((Date.now() - new Date(date)) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)}h ago`;
  return dateLabel(date);
};
const shortDuration = (seconds = 0) => seconds < 60 ? `${Math.round(seconds)}s` : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;

function notice(message, kind = '') {
  $('notice').textContent = message || '';
  $('notice').className = `notice ${kind}`;
  $('notice').hidden = !message;
}

// One accent drives the theme; keep it readable on the current light/dark background.
function applyAccent(hex) {
  const rgb = /^#[0-9a-f]{6}$/i.test(hex || '') ? [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) : [169, 155, 255];
  const light = window.matchMedia('(prefers-color-scheme: light)').matches;
  const lum = (c) => c.map((v) => v / 255).map((v) => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
  let color = rgb;
  for (let step = 0; step < 10; step += 1) {
    const value = lum(color);
    if (light ? value <= 0.3 : value >= 0.32) break;
    color = color.map((v) => Math.round(light ? v * 0.85 : v + (255 - v) * 0.18));
  }
  const root = document.documentElement.style;
  root.setProperty('--accent', `rgb(${color.join(',')})`);
  root.setProperty('--accent-ink', lum(color) > 0.4 ? '#14121f' : '#ffffff');
}

async function syncTheme() {
  try { applyAccent((await rpc('hello')).accent); } catch { /* keep the current accent */ }
}

async function rpc(method, payload = {}) {
  if (!tauri) throw new Error('Open the Tauri app to record desktop steps. This browser view has no native backend.');
  return tauri.core.invoke('backend_request', { method, payload });
}

const settle = (ms) => new Promise((resolve) => setTimeout(resolve, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : ms));

// macOS-style: the transparent window resizes instantly and invisibly; the notch inside it
// springs between banner and panel size, so the motion is smooth.
async function expanded(value) {
  if (value) {
    // Lay the panel out while the native window grows (it stays clipped until .open), not after.
    $('panel').hidden = false;
    if (tauri) await tauri.core.invoke('set_notch_expanded', { expanded: true });
    requestAnimationFrame(() => { $('notch').classList.add('open'); movePill(); });
    clearTimeout(roam.timer);
    roam.timer = setTimeout(startRoam, 560);
  } else {
    clearTimeout(roam.timer);
    ghostHome();
    $('notch').classList.remove('open');
    if (state.expanded) await settle(420);
    $('panel').hidden = true;
    if (tauri) await tauri.core.invoke('set_notch_expanded', { expanded: false });
  }
  state.expanded = value;
  $('collapse').hidden = !value;
  $('toggle').setAttribute('aria-expanded', String(value));
  $('toggle').setAttribute('aria-label', value ? 'Collapse DigitalSoul' : 'Expand DigitalSoul');
}

function view(name) {
  for (const id of ['library', 'patterns', 'activity', 'detail', 'create', 'approve', 'add-workflow-view']) if ($(id)) $(id).hidden = id !== name;
  const tab = ['activity', 'patterns'].includes(name) ? name : 'library';
  document.querySelectorAll('[data-tab]').forEach((button) => button.classList.toggle('active', button.dataset.tab === tab));
  movePill();
  $('panel').dataset.view = name;
  notice('');
  $('panel').scrollTop = 0;
}

function movePill() {
  const active = document.querySelector('[data-tab].active');
  const pill = $('tab-pill');
  if (!active || !pill || !active.offsetWidth) return;
  pill.style.width = `${active.offsetWidth}px`;
  pill.style.transform = `translateX(${active.offsetLeft - 3}px)`;
}

function busy(value) {
  state.pending = value;
  for (const id of ['new-recording', 'chain-drop-btn', 'chain-run-btn', 'chain-preview-btn', 'scan-screen-btn', 'new-shopify', 'new-excel', 'new-gform', 'gform-submit-btn', 'new-ai-workflow', 'ai-synthesize-btn', 'empty-ai-workflow', 'shopify-submit-btn', 'shopify-ai-btn', 'empty-record', 'stop-recording', 'compact-stop', 'confirm-run', 'screen-approve-btn']) if ($(id)) $(id).disabled = value;
  $('record-form').querySelector('[type=submit]').disabled = value;
}

async function action(callback) {
  if (state.pending) return;
  busy(true);
  notice('');
  try { await callback(); } catch (error) {
    await expanded(true).catch(() => {});
    notice(String(error?.message || error));
  } finally { busy(false); }
}

function setRecording(value) {
  state.recording = value;
  $('notch').classList.toggle('recording', value);
  $('live').hidden = !value;
  $('compact-stop').hidden = !value;
  $('compact-time').hidden = !value;
  $('compact-label').textContent = value ? 'Watching closely… F8 when done' : IDLE_LABEL;
  $('quit').disabled = value || state.running;
  if ($('new-recording')) $('new-recording').disabled = value || state.running;
}

const APP_NAMES = { 'whatsapp.exe': 'WhatsApp', 'whatsapp.root.exe': 'WhatsApp', 'brave.exe': 'Brave', 'chrome.exe': 'Chrome', 'msedge.exe': 'Edge', 'excel.exe': 'Excel', 'winword.exe': 'Word', 'powerpnt.exe': 'PowerPoint', 'explorer.exe': 'File Explorer', 'calculatorapp.exe': 'Calculator', 'file explorer': 'File Explorer', '*': 'Everything' };
function appName(app) {
  if (!app) return 'Desktop';
  const name = APP_NAMES[app.toLowerCase()] || app.replace(/\.exe$/i, '');
  return name[0].toUpperCase() + name.slice(1);
}

function pageHost(step) {
  try { return new URL(step.url).host.replace(/^www\./, ''); } catch { return ''; }
}

// "e2e-notes.txt - Notepad" -> "e2e-notes.txt": the document an app window showed, without the app name.
function documentName(step) {
  const name = appName(step.app);
  const title = (step.title || '').replace(/^\*/, '').trim();
  const doc = title.toLowerCase().endsWith(` - ${name.toLowerCase()}`) ? title.slice(0, -(name.length + 3)).trim() : title;
  return doc.toLowerCase() === name.toLowerCase() ? '' : doc;
}

function stepLabel(step) {
  if (step.kind === 'open_url') {
    if (!step.title) return `Opened ${step.label || 'a page'}`;
    const host = pageHost(step);
    return `Opened “${step.title}”${host ? ` · ${host}` : ''}${step.resume ? ` from ${timeLabel(step.resume)}` : ''}`;
  }
  if (step.kind === 'open_app') return `Open ${appName(step.app)}`;
  return ({ click: `Click ${step.label || step.target?.name || 'control'}`, set_text: 'Enter text', press_enter: 'Press Enter' })[step.kind] || step.kind;
}

const APPS = [[/whatsapp/i, 'whatsapp', 'W'], [/brave/i, 'brave', 'B'], [/chrome/i, 'chrome', 'C'], [/excel/i, 'excel', 'X'], [/winword/i, 'word', 'W']];
function appStyle(apps = []) {
  for (const [pattern, token, letter] of APPS) if (apps.some((app) => pattern.test(app))) return { token, letter };
  const name = (apps.find((app) => app !== '*') || 'Desktop').replace(/\.exe$/i, '');
  return { token: 'other', letter: name[0].toUpperCase() };
}

// Static, trusted icon markup; never built from recorded text.
const STEP_ICONS = {
  open_app: '<path d="M3 4.5h10v8H3z"/><path d="M3 7h10"/>',
  click: '<path d="M5 3l7 5-3 1 2 4-1.5.7-2-4L5 11z"/>',
  set_text: '<path d="M3 5V3.5h10V5M8 3.5v9M6.5 12.5h3"/>',
  press_enter: '<path d="M12.5 4v4.5h-8M7 6l-2.5 2.5L7 11"/>',
  open_url: '<circle cx="8" cy="8" r="5.5"/><path d="M2.5 8h11M8 2.5c1.8 2 1.8 9 0 11M8 2.5c-1.8 2-1.8 9 0 11"/>',
};

function appToken(app) {
  const match = APPS.find(([pattern]) => pattern.test(app || ''));
  return match ? match[1] : 'other';
}

function appDots(apps = []) {
  const dots = element('span', 'app-dots');
  for (const app of apps.slice(0, 4)) {
    const dot = element('i');
    dot.style.setProperty('--app', `var(--${app === '*' ? 'other' : appToken(app)})`);
    dots.append(dot);
  }
  return dots;
}

function element(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

// Routines are grouped by what they do so different kinds don't blur together.
const GROUPS = [
  { id: 'whatsapp', title: 'WhatsApp sends', icon: 'chat', color: 'whatsapp' },
  { id: 'browser', title: 'Browsing', icon: 'globe', color: 'brave' },
  { id: 'office', title: 'Office', icon: 'doc', color: 'word' },
  { id: 'chats', title: 'WhatsApp, no send', icon: 'chat', color: 'other' },
  { id: 'apps', title: 'Other apps', icon: 'window', color: 'other' },
  { id: 'empty', title: 'Empty recordings', icon: 'spark', color: 'other' },
];
// Static, trusted icon markup; never built from recorded text.
const GROUP_ICONS = {
  chat: '<path d="M3 12.5V4.5A1.5 1.5 0 0 1 4.5 3h7A1.5 1.5 0 0 1 13 4.5v5a1.5 1.5 0 0 1-1.5 1.5H6l-3 2.5z"/>',
  globe: '<circle cx="8" cy="8" r="5.5"/><path d="M2.5 8h11M8 2.5c1.8 2 1.8 9 0 11M8 2.5c-1.8 2-1.8 9 0 11"/>',
  doc: '<path d="M4 2.5h5.5L12 5v8.5H4z"/><path d="M9.5 2.5V5H12M6 8.2h4M6 10.7h4"/>',
  spark: '<path d="M8 2.5l1.3 3.6 3.7 1.4-3.7 1.4L8 12.5 6.7 8.9 3 7.5l3.7-1.4z"/>',
  window: '<rect x="2.5" y="3.5" width="11" height="9" rx="1.5"/><path d="M2.5 6.2h11"/>',
};
const icon = (name) => `<svg viewBox="0 0 16 16" aria-hidden="true">${GROUP_ICONS[name]}</svg>`;

// Group by what actually happened in the recording, not by the apps picked beforehand.
function groupOf(workflow) {
  const used = workflow.used || [];
  if (workflow.flow?.format === 'digitalsoul.excel-flow.v1' || workflow.flow?.kind === 'excel' || used.some((app) => /excel/i.test(app))) return 'office';
  if (workflow.can_run) return 'whatsapp';
  if (!workflow.step_count) return 'empty';
  if (used.some((app) => /chrome|brave|msedge|firefox/i.test(app))) return 'browser';
  if (used.some((app) => /excel|winword|powerpnt/i.test(app))) return 'office';
  if (used.some((app) => /whatsapp/i.test(app))) return 'chats';
  return 'apps';
}

function statusOf(workflow) {
  if (workflow.flow?.format === 'digitalsoul.excel-flow.v1' || workflow.flow?.kind === 'excel') return ['Ready to fill', 'ok'];
  if (workflow.can_run) return ['Ready to send', 'ok'];
  if (!workflow.step_count) return ['Empty', 'muted'];
  if (workflow.replay_error && !workflow.summary?.length) return ['Needs a re-record', 'warn'];
  return ['Replayable', 'info'];
}

function summaryLine(workflow) {
  const line = element('span', 'card-line');
  const pieces = workflow.summary || [];
  if (!pieces.length) {
    line.append(element('span', 'sum-empty', workflow.step_count ? `${workflow.step_count} steps, nothing typed or clicked by name` : 'Nothing was captured'));
    return line;
  }
  for (const piece of pieces) {
    if (piece.kind === 'to') line.append(element('span', 'sum-to', `→ ${piece.text}`));
    else if (piece.kind === 'message' || piece.kind === 'typed') line.append(element('q', `sum-${piece.kind}`, piece.text));
    else if (piece.kind === 'url') line.append(element('span', 'sum-url', piece.text));
    else line.append(element('span', 'sum-click', piece.text));
  }
  return line;
}

function bump(id, value) {
  const badge = $(id);
  if (badge.textContent === String(value)) return;
  badge.textContent = value;
  badge.classList.remove('bump');
  void badge.offsetWidth;
  badge.classList.add('bump');
}

function renderWorkflows() {
  const rawFilter = $('search').value.toLowerCase().trim();
  const tokens = rawFilter.split(/\s+/).filter(Boolean);
  const items = state.workflows.filter((workflow) => {
    if (!rawFilter) return true;
    const searchable = [
      workflow.name,
      workflow.intent,
      workflow.description,
      workflow.recipient,
      workflow.message,
      ...(workflow.apps || []).map((a) => a.replace(/\.exe$/i, '')),
      ...(workflow.parameters || []).map((p) => `${p.name} ${p.label || ''} ${p.example || ''}`),
      ...(workflow.steps || []).map((s) => `${s.action} ${s.target || ''} ${s.text || ''} ${s.explanation || ''}`)
    ].filter(Boolean).join(' ').toLowerCase();
    return searchable.includes(rawFilter) || tokens.every((t) => searchable.includes(t));
  });
  bump('workflow-count', state.workflows.length);
  $('empty').hidden = state.workflows.length > 0;
  const list = $('workflow-list');
  list.replaceChildren();
  if (!items.length && state.workflows.length) list.append(element('p', '', 'Nothing matches that. Try another word?'));
  let order = 0;
  for (const group of GROUPS) {
    const members = items.filter((workflow) => groupOf(workflow) === group.id);
    if (!members.length) continue;
    const section = element('section', `group group-${group.id}`);
    section.style.setProperty('--app', `var(--${group.color})`);
    const head = element('h3', 'group-head');
    const mark = element('span', 'group-icon');
    mark.innerHTML = icon(group.icon);
    head.append(mark, element('span', '', group.title), element('span', 'group-count', String(members.length)));
    section.append(head);
    for (const workflow of members) {
      const card = element('button', `workflow-card group-card-${group.id}`);
      card.style.setProperty('--i', String(order++));
      const used = workflow.used?.length ? workflow.used : (workflow.apps || []).filter((app) => app !== '*');
      card.style.setProperty('--app', `var(--${used.length ? appToken(used[0]) : 'other'})`);
      const badgeIcon = element('span', 'workflow-icon');
      badgeIcon.innerHTML = icon(group.icon);
      const info = element('span', 'workflow-info');
      const title = element('span', 'card-title');
      const [label, tone] = statusOf(workflow);
      title.append(element('strong', '', workflow.name), element('span', `status tone-${tone}`, label));
      const line = summaryLine(workflow);
      const facts = element('span', 'facts');
      facts.append(appDots(used));
      facts.append(element('span', '', `${workflow.step_count} steps`));
      if (workflow.duration) facts.append(element('span', '', shortDuration(workflow.duration)));
      facts.append(element('span', '', ago(workflow.created_at)));
      info.append(title, line);
      card.append(badgeIcon, info, facts, element('span', 'card-arrow', '›'));
      card.addEventListener('pointermove', (event) => {
        const box = card.getBoundingClientRect();
        card.style.setProperty('--glow-x', `${event.clientX - box.left}px`);
        card.style.setProperty('--glow-y', `${event.clientY - box.top}px`);
      });
      card.addEventListener('click', () => action(() => showDetail(workflow.id)));
      section.append(card);
    }
    list.append(section);
  }
}

function renderJobs() {
  bump('job-count', state.jobs.filter((job) => ['pending', 'preparing', 'dispatching'].includes(job.state)).length);
  $('job-list').replaceChildren();
  if (!state.jobs.length) $('job-list').append(element('p', 'hint', 'No scheduled messages or replay attempts yet.'));
  const labels = { pending: ['Scheduled', 'info'], preparing: ['Preparing', 'info'], dispatching: ['Sending', 'info'], attempted_unverified: ['Sent · check WhatsApp', 'ok'], uncertain: ['Uncertain', 'warn'], blocked: ['Blocked', 'bad'], missed: ['Missed', 'warn'], cancelled: ['Cancelled', 'muted'] };
  for (const job of [...state.jobs].reverse()) {
    const card = element('article', 'job-card');
    const body = element('div');
    const [label, tone] = labels[job.state] || [job.state, 'muted'];
    body.append(element('span', `status tone-${tone}`, label), element('strong', '', job.detail || 'WhatsApp message'), element('p', '', `${ago(job.due * 1000)} · ${new Date(job.due * 1000).toLocaleString()}`));
    card.append(body);
    if (['pending', 'preparing'].includes(job.state)) {
      const cancel = element('button', 'secondary', 'Cancel');
      cancel.addEventListener('click', () => action(async () => { await rpc('cancel', { id: job.id }); await refresh(); }));
      card.append(cancel);
    }
    $('job-list').append(card);
  }
}

function renderPatterns() {
  bump('pattern-count', state.patterns.length);
  if ($('skills-stat-badge')) {
    $('skills-stat-badge').textContent = `${state.patterns.length} Skill${state.patterns.length === 1 ? '' : 's'} Available`;
  }
  $('pattern-empty').hidden = state.patterns.length > 0;
  const list = $('pattern-list');
  list.replaceChildren();
  if (!state.patterns.length) return;

  for (const pat of state.patterns) {
    const card = element('article', 'pattern-card');
    const header = element('div', 'pattern-header');
    const badgeText = pat.is_demonstration ? '🧠 Learned Skill' : `🧠 Reinforced × ${pat.support}`;
    header.append(
      element('strong', 'pattern-title', pat.name),
      element('span', 'pattern-badge', badgeText),
      element('span', 'pattern-consistency', `${Math.round(pat.alignment * 100)}% consistency`)
    );

    const desc = element('p', 'pattern-intent', pat.intent || 'Learned pattern across sessions');
    const paramsBox = element('div', 'pattern-params-preview');
    if (pat.parameters && pat.parameters.length) {
      paramsBox.append(element('small', 'params-label', 'Learned dynamic variables:'));
      for (const p of pat.parameters) {
        paramsBox.append(element('span', 'param-chip', `{{${p.name}}}: ${p.default || 'any'}`));
      }
    } else {
      paramsBox.append(element('small', 'params-label', `${pat.step_count} static steps verified.`));
    }

    const actions = element('div', 'pattern-card-actions');
    const runBtn = element('button', 'primary mini-btn', '▶ Run with variables');
    runBtn.addEventListener('click', () => openPatternWorkflow(pat));

    const exportBtn = element('button', 'secondary mini-btn', '📄 SKILL.md');
    exportBtn.title = 'Export to Microsoft SKILL.md format';
    exportBtn.addEventListener('click', () => action(async () => {
      const res = await rpc('export_skill', { id: pat.id });
      try { await navigator.clipboard.writeText(res.skill_markdown); } catch {}
      notice('Copied standard SKILL.md definition to clipboard!', 'success');
    }));

    const auditBtn = element('button', 'secondary mini-btn', '🛡️ Audit');
    auditBtn.title = 'Audit skill quality, resilience & privacy';
    auditBtn.addEventListener('click', () => action(async () => {
      const rep = await rpc('evaluate_skill', { id: pat.id });
      const summary = `Skill Quality: Grade ${rep.grade} (${rep.score}/100)\n\nLocators: ${rep.dimension_scores.locators}/25 · Guardrails: ${rep.dimension_scores.guardrails}/25\nParams: ${rep.dimension_scores.parameters}/25 · Privacy: ${rep.dimension_scores.privacy}/25\n\nStrengths:\n• ${rep.strengths.join('\n• ')}\n\nTips:\n• ${rep.improvement_tips.join('\n• ')}`;
      alert(summary);
    }));

    const delBtn = element('button', 'text-button danger-text', 'Forget');
    delBtn.addEventListener('click', () => action(async () => {
      if (!confirmDialog(`Forget learned pattern “${pat.name}”?`)) return;
      await rpc('learning_delete', { id: pat.id });
      await refresh();
      notice('Pattern removed.');
    }));

    actions.append(delBtn, auditBtn, exportBtn, runBtn);
    card.append(header, desc, paramsBox, actions);
    list.append(card);
  }
}

function renderUserMemory() {
  const grid = $('user-memory-grid');
  if (!grid) return;
  grid.replaceChildren();

  const entries = Object.values(state.user_memory || {});
  if (!entries.length) {
    const empty = element('div', 'user-memory-item');
    empty.style.gridColumn = '1 / -1';
    empty.style.textAlign = 'center';
    empty.style.padding = '14px';
    empty.style.color = 'var(--text-3)';
    empty.append(element('p', '', '🧠 DigitalSoul is monitoring. Facts, preferences, and contacts you use in workflows will appear here automatically.'));
    grid.append(empty);
    return;
  }

  const catIcons = {
    identity: '👤',
    commerce: '🛍️',
    messaging: '💬',
    spreadsheet: '📊',
    workflow_habit: '⚡',
  };

  for (const fact of entries) {
    const card = element('div', 'user-memory-item');
    const top = element('div', 'memory-item-top');
    const icon = catIcons[fact.category] || '💡';

    const lbl = element('span', 'memory-item-label', `${icon} ${fact.label || fact.key}`);

    const actions = element('div', 'memory-item-actions');
    const editBtn = element('button', 'memory-mini-btn', '✏️');
    editBtn.title = `Edit "${fact.label || fact.key}"`;
    editBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      action(async () => {
        const newVal = promptDialog(`Edit remembered value for "${fact.label || fact.key}":`, fact.value);
        if (newVal === null || newVal.trim() === '') return;
        await rpc('gform_memory_save', { profile: { [fact.key]: newVal.trim() } });
        await refresh();
        notice(`Updated "${fact.label || fact.key}" in your memory!`, 'success');
      });
    });

    const delBtn = element('button', 'memory-mini-btn del', '✕');
    delBtn.title = `Forget "${fact.label || fact.key}"`;
    delBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      action(async () => {
        if (!confirmDialog(`Forget remembered fact "${fact.label || fact.key}"?`)) return;
        await rpc('gform_memory_save', { profile: { [fact.key]: '' } });
        await refresh();
        notice(`Removed "${fact.label || fact.key}" from memory.`);
      });
    });

    actions.append(editBtn, delBtn);
    top.append(lbl, actions);

    const val = element('div', 'memory-item-val', fact.value);
    const badge = element('div', 'memory-item-badge');
    const srcText = fact.source && fact.source.startsWith('learned:') ? 'Auto-learned' : (fact.usage_count > 1 ? `Used × ${fact.usage_count}` : 'Verified');
    badge.append(element('span', '', `● ${srcText}`));

    card.append(top, val, badge);
    grid.append(card);
  }
}

function openPatternWorkflow(pat) {
  view('approve');
  $('approve-whatsapp-view').hidden = true;
  $('approve-shopify-view').hidden = true;
  $('approve-excel-view').hidden = true;
  $('approve-gform-view').hidden = true;
  $('approve-pattern-view').hidden = false;

  $('pattern-run-title').textContent = `Run “${pat.name}”`;
  $('pattern-run-desc').textContent = `${pat.intent} · Learned from ${pat.support} demonstrations (${Math.round(pat.alignment * 100)}% consistency)`;

  const container = $('pattern-params-container');
  container.replaceChildren();
  const stepsList = element('ol');
  for (const step of pat.skeleton || []) stepsList.append(element('li', '', stepLabel(step)));
  container.append(stepsList);

  if (pat.parameters && pat.parameters.length) {
    for (const p of pat.parameters) {
      const label = element('label');
      label.append(element('span', '', `${p.label || p.name} ({{${p.name}}})`));
      const input = element('input');
      input.name = p.name;
      // Pre-fill from remembered user memory if available
      let prefilled = p.default || '';
      if (!prefilled || prefilled.startsWith('{{')) {
        const pNorm = p.name.toLowerCase().replace(/[^a-z0-9]/g, '');
        for (const [mKey, mData] of Object.entries(state.user_memory || {})) {
          const mNorm = mKey.toLowerCase().replace(/[^a-z0-9]/g, '');
          if (pNorm.includes(mNorm) || mNorm.includes(pNorm) || (p.label && p.label.toLowerCase().includes(mKey.toLowerCase()))) {
            prefilled = mData.value;
            break;
          }
        }
      }
      input.value = prefilled;
      input.placeholder = `e.g. ${prefilled || 'Enter value'}`;
      label.append(input);
      container.append(label);
    }
  } else {
    container.append(element('p', 'hint', `All ${pat.step_count} steps are deterministic and ready to replay.`));
  }

  $('pattern-form').onsubmit = (event) => {
    event.preventDefault();
    action(async () => {
      const formParams = {};
      container.querySelectorAll('input').forEach(inp => {
        formParams[inp.name] = inp.value;
      });
      notice(`Executing learned pattern “${pat.name}” with adapted inputs...`);
      await expanded(false);
      const res = await rpc('learning_execute', { id: pat.id, parameters: formParams, approved_digest: pat.approved_digest });
      await expanded(true);
      if (res && res.started) {
        notice(`✅ Successfully dispatched learned pattern “${pat.name}”! (${res.steps_count} steps)`, 'success');
      }
    });
  };
}

async function refresh() {
  const result = await rpc('list');
  state.workflows = result.workflows;
  state.jobs = result.jobs;
  try {
    const patResult = await rpc('learning_patterns');
    state.patterns = patResult.patterns || [];
    state.user_memory = patResult.user_memory || {};
    state.recent_routines = patResult.recent_routines || [];
  } catch {}
  renderWorkflows();
  renderPatterns();
  renderUserMemory();
  renderJobs();
}

async function showDetail(id) {
  const detail = await rpc('detail', { id });
  state.detail = detail;
  view('detail');
  $('detail-title').textContent = detail.name;
  const meta = $('detail-meta');
  meta.replaceChildren();
  for (const app of [...new Set(detail.steps.map((step) => step.app).filter(Boolean))]) {
    const chip = element('span', 'fact-chip');
    const dot = element('i');
    dot.style.setProperty('--app', `var(--${appToken(app)})`);
    chip.append(dot, element('span', '', appName(app)));
    meta.append(chip);
  }
  meta.append(element('span', 'fact-chip', `${detail.steps.length} steps`), element('span', 'fact-chip', shortDuration(detail.duration)), element('span', 'fact-chip', ago(detail.created_at)));
  renderUnderstanding(detail.understanding);
  renderFlow(detail.flow);
  const isExcelWorkflow = detail.flow?.format === 'digitalsoul.excel-flow.v1' || detail.flow?.kind === 'excel' || (detail.used || []).some(a => /excel/i.test(a));
  if (detail.flow?.kind === 'shopify') {
    $('detail-warning').textContent = 'A Shopify product routine was recognized. Review decoded and AI-generated fields before running with browser-use in Edge.';
    $('review-run').hidden = false;
    $('review-run').disabled = state.recording || state.running;
    $('replay-steps').hidden = true;
  } else if (isExcelWorkflow) {
    $('detail-warning').textContent = 'Excel spreadsheet routine recognized. Review the cells and values before updating your workbook.';
    $('review-run').hidden = false;
    $('review-run').disabled = state.recording || state.running;
    $('replay-steps').hidden = !detail.steps.length;
    $('replay-steps').disabled = state.recording || state.running;
  } else if (detail.flow) {
    $('detail-warning').textContent = 'WhatsApp send recognized. Check the recipient and message before replaying; it sends a real message.';
    $('review-run').disabled = state.recording || state.running;
    $('review-run').hidden = false;
    $('replay-steps').hidden = !detail.steps.length;
    $('replay-steps').disabled = state.recording || state.running;
    $('replay-steps').textContent = '▶ Replay with WhatsApp adapter';
  } else {
    $('detail-warning').textContent = detail.replay_error || 'Replay repeats these steps in order, including any messages or posts. It stops at the first step it cannot find.';
    $('review-run').hidden = true;
    $('review-run').disabled = true;
    $('replay-steps').hidden = !detail.steps.length;
    $('replay-steps').disabled = state.recording || state.running;
    $('replay-steps').textContent = 'Replay steps';
  }
  $('step-list').replaceChildren();
  let lastApp = null;
  for (const [index, step] of detail.steps.entries()) {
    // Name the app once when it changes instead of under every step.
    if (step.app && step.app !== lastApp) {
      const header = element('li', 'segment');
      header.style.setProperty('--app', `var(--${appToken(step.app)})`);
      header.style.setProperty('--i', String(index));
      const label = element('span', 'segment-label');
      label.append(element('i'), element('span', '', `In ${appName(step.app)}`));
      header.append(label);
      $('step-list').append(header);
      lastApp = step.app;
      if (step.kind === 'open_app') continue;
    }
    const row = element('li', `kind-${step.kind}`);
    row.style.setProperty('--i', String(index));
    const node = element('span', 'step-node');
    node.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true">${STEP_ICONS[step.kind] || STEP_ICONS.click}</svg>`;
    const body = element('div', 'step-body');
    if (step.kind === 'click') {
      body.append(element('span', 'verb', 'Clicked'), element('span', 'target-pill', step.label || step.target?.name || 'a control'));
    } else if (step.kind === 'set_text') {
      body.append(element('span', 'verb', 'Typed'), element('q', 'typed', step.value));
    } else if (step.kind === 'open_url') {
      body.append(element('span', 'verb', 'Opened'), element('span', 'link-pill', step.title ? `“${step.title}”` : step.label || step.url));
      if (step.title && pageHost(step)) body.append(element('span', 'step-quiet', `· ${pageHost(step)}`));
      if (step.resume) body.append(element('span', 'resume-pill', `from ${timeLabel(step.resume)}`));
    } else if (step.kind === 'press_enter') {
      body.append(element('span', 'verb', 'Pressed'), element('kbd', 'keycap', 'Enter ⏎'));
    } else {
      body.append(element('span', 'verb', stepLabel(step)));
      if (step.kind === 'open_app' && documentName(step)) body.append(element('span', 'step-quiet', documentName(step)));
    }
    const explanation = detail.understanding?.explanations?.[index];
    if (explanation) {
      body.append(element('p', 'step-explanation', explanation));
    }
    row.append(node, body, element('time', '', timeLabel(step.at || 0)));
    $('step-list').append(row);
  }
  if (!detail.steps.length) $('step-list').append(element('p', 'hint', 'No allowed-app actions were observed in this recording.'));
}

function renderUnderstanding(understanding) {
  const card = $('ai-understanding');
  if (!card) return;
  if (!understanding || !understanding.intent) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  card.replaceChildren();
  const head = element('div', 'understanding-head');
  head.innerHTML = `<svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M8 1a5 5 0 0 0-5 5c0 1.8.9 3.3 2.3 4.2V12c0 .6.4 1 1 1h3.4c.6 0 1-.4 1-1v-1.8A5 5 0 0 0 13 6a5 5 0 0 0-5-5zm-1.5 13a.5.5 0 0 0 0 1h3a.5.5 0 0 0 0-1h-3z"/></svg><span>Routine Understanding</span>`;
  const intent = element('div', 'understanding-intent', understanding.intent);
  card.append(head, intent);
  if (understanding.parameters?.length) {
    const params = element('div', 'understanding-params');
    for (const p of understanding.parameters) {
      const pill = element('span', 'param-pill');
      pill.append(element('strong', '', `${p.name}: `), document.createTextNode(p.value));
      params.append(pill);
    }
    card.append(params);
  }
}

// A WhatsApp routine reads as who and what, not as raw clicks.
function renderFlow(flow) {
  const card = $('flow-summary');
  card.hidden = !flow;
  card.replaceChildren();
  if (!flow) return;
  const row = (label, ...content) => {
    const item = element('div', 'flow-row');
    const value = element('div');
    value.append(...content);
    item.append(element('span', '', label), value);
    card.append(item);
  };
  row('Searched', element('q', '', flow.search));
  row('Chat', element('strong', '', flow.recipient));
  row('Message', element('q', '', flow.message));
  const sent = element('strong', '', flow.send?.kind === 'button' ? 'Send button' : 'Enter');
  row('Sent with', sent, ...(flow.unsent ? [element('em', '', 'typed only; replay will send it')] : []));
}

function newRecording() {
  if (state.recording || state.running) { notice('Stop the active recording or wait for the replay first.'); return; }
  view('create');
  $('record-form').elements.name.focus();
}

$('toggle').addEventListener('click', async () => {
  if (dragged) { dragged = false; return; }
  // Opening the panel must remain available while a backend request is pending.
  try {
    await expanded(!state.expanded);
    if (state.expanded && !state.pending) syncTheme();
  } catch (error) { notice(String(error?.message || error)); }
});

// Keep the compact notch visible during recording; only hide if explicitly commanded outside recording.
const hideNotch = (hidden) => {
  if (hidden && state.recording) return Promise.resolve();
  return tauri?.core.invoke('set_notch_hidden', { hidden }).catch(() => {});
};

function cheer() {
  $('notch').classList.remove('cheer');
  void $('notch').offsetWidth;
  $('notch').classList.add('cheer');
  setTimeout(() => $('notch').classList.remove('cheer'), 1300);
}

// The ghost turns to look at you (2.5D) and leans in, wide-eyed, as the cursor comes near.
function lookAt(x, y) {
  roam.cursor = [x, y];
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  for (const ghost of document.querySelectorAll('.ghost')) {
    const box = ghost.getBoundingClientRect();
    if (!box.width) continue;
    const dx = x - (box.left + box.width / 2);
    const dy = y - (box.top + box.height / 2);
    const distance = Math.hypot(dx, dy) || 1;
    const near = Math.max(0, Math.min(1, 1 - (distance - 30) / 320));
    const turn = Math.min(1, distance / 120);
    ghost.style.setProperty('--ry', `${(dx / distance) * turn * 26 * (0.35 + near)}deg`);
    ghost.style.setProperty('--rx', `${(-dy / distance) * turn * 18 * (0.35 + near)}deg`);
    ghost.style.setProperty('--fx', `${(dx / distance) * turn * 1.6}px`);
    ghost.style.setProperty('--fy', `${(dy / distance) * turn * 1.2}px`);
    ghost.style.setProperty('--near', near.toFixed(2));
    ghost.classList.toggle('near', near > 0.72);
  }
}
// The desktop-cursor poll (watchCursor) only steps in while the webview gets no pointer events,
// so two sources never alternate and make the eyes jitter.
let lastPointer = 0;
document.addEventListener('pointermove', (event) => { lastPointer = performance.now(); lookAt(event.clientX, event.clientY); });

// Every so often the visible ghost does a little trick. Which trick and when come from a fixed
// pseudo-random sequence: deterministic, but it never feels regular.
const TRICKS = ['twirl', 'wave', 'peek', 'wink', 'glance', 'yawn', 'hop'];
const sequence = (n) => { const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453; return x - Math.floor(x); };
let trickCount = 0;

function scheduleTrick() {
  setTimeout(() => {
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    // No tricks mid hand-off: the ghost flying home must land as itself.
    if (!calm && !state.recording && !state.running && !document.hidden && !$('notch').classList.contains('homing')) {
      const ghost = $('notch').classList.contains('roaming') ? roam.el?.querySelector('.ghost-float') : $('toggle').querySelector('.ghost-float');
      const roaming = $('notch').classList.contains('roaming');
      const pool = roaming ? [...TRICKS, 'circle', 'blow', 'circle'] : TRICKS;
      const trick = pool[Math.floor(sequence(trickCount * 2) * pool.length)];
      if (roaming && trick === 'circle' && !roam.act && !roam.fleeing) {
        // Loop toward the middle of the panel so the circle never runs into a wall.
        const box = $('notch').getBoundingClientRect();
        const toMiddle = (box.left + box.width / 2 - roam.p[0]) * roam.v[1] - (box.top + box.height / 2 - roam.p[1]) * roam.v[0];
        roam.act = { kind: 'circle', until: roam.time + 3.4, dir: toMiddle > 0 ? -1 : 1 };
      } else if (roaming && trick === 'blow' && !roam.act) {
        roam.act = { kind: 'pause', until: roam.time + 1.8 };
        roam.el.classList.add('blowing');
        setTimeout(() => roam.el?.classList.remove('blowing'), 1800);
      } else if (ghost && !['circle', 'blow'].includes(trick) && !ghost.className.includes('trick-')) {
        ghost.classList.add(`trick-${trick}`);
        setTimeout(() => ghost.classList.remove(`trick-${trick}`), 1900);
      }
    }
    trickCount += 1;
    scheduleTrick();
  }, 6000 + sequence(trickCount * 2 + 1) * 9000);
}
scheduleTrick();

// The ghost puffs up when you click and grows a little, curious, while you hover controls.
// Sizes stay small enough that the header ghost never touches the banner's clipped edge, and
// hover changes settle for a moment first, so crossing gaps between controls doesn't make it pulse.
let curious = 1;
let puffTimer = 0;
let curiousTimer = 0;
const LIVE_GHOSTS = '#toggle .ghost-float, .roamer .ghost-float';
const ghostSize = (size) => document.querySelectorAll(LIVE_GHOSTS).forEach((ghost) => ghost.style.setProperty('--size', size));
document.addEventListener('pointerdown', () => {
  document.querySelectorAll(LIVE_GHOSTS).forEach((ghost) => ghost.style.setProperty('--size', ghost.closest('.roamer') ? 1.28 : 1.18));
  clearTimeout(puffTimer);
  puffTimer = setTimeout(() => { puffTimer = 0; ghostSize(curious); }, 380);
});
document.addEventListener('pointerover', (event) => {
  const next = event.target.closest('button, .workflow-card, .chip, input, textarea, select') ? 1.1 : 1;
  clearTimeout(curiousTimer);
  curiousTimer = setTimeout(() => {
    curious = next;
    if (!puffTimer) ghostSize(curious);  // don't cut a click-puff short
  }, 140);
});

// When the panel is open the ghost leaves the header to roam. From afar it drifts after you;
// when you come close it gets shy and darts off sideways. It never blocks a click.
const roam = { el: null, p: [0, 0], prev: [0, 0], v: [0, 0], cursor: null, side: 1, fleeing: false, out: false, frame: 0, time: 0, last: 0, carry: 0, timer: 0, handoff: 0, act: null };
const ROAM_DT = 1 / 60;

// Deterministic "randomness": a few smooth waves at unrelated (irrational-ratio) speeds never
// visibly repeat, so the drift looks random while the same cursor path always gives the same flight.
const drift = (t, phase) => Math.sin(t * 1.13 + phase) + 0.6 * Math.sin(t * 1.13 * Math.SQRT2 + phase * 2.1) + 0.35 * Math.sin(t * 1.13 * 1.618 + phase * 0.7);

// Deterministic waypoints that slowly sweep the whole panel (tanh keeps them inside).
function wanderPoint(box, t) {
  return [
    box.left + box.width * (0.5 + 0.4 * Math.tanh(drift(t * 0.19, 1.3))),
    box.top + 66 + (box.height - 88) * (0.5 + 0.42 * Math.tanh(drift(t * 0.15, 4.1))),
  ];
}

function startRoam() {
  // The panel may have been closed again before this delayed start fired.
  if (roam.frame || !$('notch').classList.contains('open') || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (!roam.el) {
    // A clean copy of the header ghost: no half-played trick, puff or hover state carried over.
    const twin = $('toggle').querySelector('.ghost-float').cloneNode(true);
    twin.className = 'ghost-float';
    twin.removeAttribute('style');
    twin.querySelector('.ghost').classList.remove('near');
    twin.querySelector('.ghost').removeAttribute('style');
    roam.el = element('div', 'roamer');
    roam.el.setAttribute('aria-hidden', 'true');
    roam.el.append(element('i', 'wind'), element('i', 'wind'), element('i', 'gust'), element('i', 'gust'), element('i', 'gust'), twin);
    $('notch').append(roam.el);
  }
  clearTimeout(roam.handoff);
  $('notch').classList.remove('homing');
  roam.el.classList.remove('returning', 'blowing');
  const home = $('toggle').querySelector('.ghost').getBoundingClientRect();
  roam.p = [home.left + home.width / 2, home.top + home.height / 2];
  roam.prev = roam.p;
  roam.v = [0.5, 1.1];
  roam.time = 0;
  roam.carry = 0;
  roam.act = null;
  roam.fleeing = false;
  roam.out = false;
  roam.last = performance.now();
  paintRoam();  // start exactly on the header ghost, then hand over
  $('notch').classList.add('roaming');
  const tick = (now) => {
    // Fixed time step: identical motion on fast and slow displays.
    roam.carry += Math.min(now - roam.last, 100) / 1000;
    roam.last = now;
    while (roam.carry >= ROAM_DT) {
      roam.prev = roam.p;
      stepRoam();
      roam.carry -= ROAM_DT;
    }
    paintRoam();
    roam.frame = requestAnimationFrame(tick);
  };
  roam.frame = requestAnimationFrame(tick);
}

// On close, the ghost glides back to its spot in the header, then hands off to the header ghost.
function ghostHome() {
  if (!roam.frame) return;
  cancelAnimationFrame(roam.frame);
  roam.frame = 0;
  const box = $('notch').getBoundingClientRect();
  const home = $('toggle').querySelector('.ghost').getBoundingClientRect();
  // The header slides to the banner's centred spot while closing; aim for where it ends up.
  const shift = Math.max(0, box.width / 2 - 210);
  roam.el.classList.remove('blowing');
  roam.el.classList.add('returning');
  $('notch').classList.add('homing');  // reopen its spot in the header before it lands
  roam.el.style.setProperty('--wind', '0');
  roam.el.style.setProperty('--lean', '0deg');
  roam.el.style.transform = `translate(${home.left - box.left + shift + home.width / 2 - 17}px, ${home.top - box.top + home.height / 2 - 17}px)`;
  // Hand back to the header ghost once it has landed: one crossfade, never two ghosts at once.
  clearTimeout(roam.handoff);
  roam.handoff = setTimeout(() => $('notch').classList.remove('roaming', 'homing'), 380);
}

function stopRoam() {
  cancelAnimationFrame(roam.frame);
  roam.frame = 0;
  $('notch').classList.remove('roaming');
}

function stepRoam() {
  const box = $('notch').getBoundingClientRect();
  roam.time += ROAM_DT;
  const t = roam.time;
  let [px, py] = roam.p;
  let [vx, vy] = roam.v;
  let ax = 0;
  let ay = 0;
  if (roam.cursor) {
    const dx = px - roam.cursor[0];
    const dy = py - roam.cursor[1];
    const distance = Math.hypot(dx, dy) || 1;
    if (distance < 130) {
      // Too close: flee away and to the side it is already drifting toward (sign of the cross
      // product), so the dodge is decided by the motion, not by chance.
      if (!roam.fleeing) {
        const cross = vx * dy - vy * dx;
        roam.side = cross === 0 ? Math.sign(drift(t, 0.5)) || 1 : Math.sign(cross);
        roam.fleeing = true;
      }
      const push = (1 - distance / 130) * 0.55;
      ax += (dx / distance) * push - (dy / distance) * roam.side * push * 0.9;
      ay += (dy / distance) * push + (dx / distance) * roam.side * push * 0.9;
    } else {
      // Far: mostly wander its own waypoints, only loosely curious about where you are.
      roam.fleeing = false;
      const [wx, wy] = wanderPoint(box, t);
      ax += (wx * 0.7 + (roam.cursor[0] + (dx / distance) * 170) * 0.3 - px) * 0.0009;
      ay += (wy * 0.7 + (roam.cursor[1] + (dy / distance) * 170) * 0.3 - py) * 0.0009;
    }
  } else {
    const [wx, wy] = wanderPoint(box, t);
    ax += (wx - px) * 0.0009;
    ay += (wy - py) * 0.0009;
  }
  ax += drift(t * 0.6, 0) * 0.012;
  ay += drift(t * 0.52, 2.4) * 0.012;
  if (roam.act && (t > roam.act.until || roam.fleeing)) roam.act = null;
  const looping = roam.act?.kind === 'circle';
  if (roam.act?.kind === 'pause') { ax *= 0.1; ay *= 0.1; vx *= 0.94; vy *= 0.94; }
  const field = document.activeElement?.closest?.('input, textarea, select');
  if (field && !roam.fleeing) {
    // Keep clear of the field being typed into: drift above or below it, never across the text.
    const rect = field.getBoundingClientRect();
    if (px > rect.left - 30 && px < rect.right + 30 && py > rect.top - 40 && py < rect.bottom + 40) {
      ay += (py < (rect.top + rect.bottom) / 2 ? -1 : 1) * 0.08;
    }
  }
  const edge = 70;
  const room = [box.left + 22 + edge, box.right - 22 - edge, box.top + 66 + edge, box.bottom - 22 - edge];
  if (px < room[0]) ax += (room[0] - px) * 0.004;
  if (px > room[1]) ax -= (px - room[1]) * 0.004;
  if (py < room[2]) ay += (room[2] - py) * 0.004;
  if (py > room[3]) ay -= (py - room[3]) * 0.004;
  // Close under the header the ceiling pushes back hard, so a chase from below bends sideways early.
  if (roam.out && py < box.top + 130) ay += (box.top + 130 - py) * 0.012;
  // Steer: the heading turns at a capped rate (quicker when fleeing), so paths bend into arcs.
  const desired = [(vx + ax) * 0.95, (vy + ay) * 0.95];
  const heading = Math.atan2(vy, vx);
  let turn = Math.atan2(desired[1], desired[0]) - heading;
  turn = Math.atan2(Math.sin(turn), Math.cos(turn));
  const limit = roam.fleeing ? 0.075 : 0.03;
  // Loop-the-loop: one full turn at a steady rate and speed, a clean circle about 100 px across.
  const newHeading = looping ? heading + roam.act.dir * (Math.PI * 2) * ROAM_DT / 3.4 : heading + Math.max(-limit, Math.min(limit, turn));
  const speed = Math.hypot(vx, vy);
  // Speed eases toward its target, so it never lurches when a flee starts or ends.
  const pace = speed + ((looping ? 1.55 : Math.min(roam.fleeing ? 2.8 : 2.1, Math.hypot(...desired))) - speed) * (looping ? 0.08 : 0.1);
  vx = Math.cos(newHeading) * pace;
  vy = Math.sin(newHeading) * pace;
  px += vx;
  py += vy;
  const margin = 22;
  const bounds = [box.left + margin, box.right - margin, box.top + 66, box.bottom - margin];
  // Soft walls: the steering pushes it away before it gets there, so it arcs off edges instead of bouncing.
  // Outside the room (e.g. just leaving the header) it eases in rather than snapping.
  px += (Math.min(bounds[1], Math.max(bounds[0], px)) - px) * 0.05;
  py += (Math.min(bounds[3], Math.max(bounds[2], py)) - py) * 0.05;
  // Never past the panel's sides or bottom, even at full flee speed. The top stays soft only for the
  // first exit from the header; after that it never flies back over the header and its buttons.
  px = Math.min(box.right - 18, Math.max(box.left + 18, px));
  py = Math.min(box.bottom - 18, py);
  if (py >= box.top + 74) roam.out = true;
  if (roam.out) py = Math.max(box.top + 74, py);
  roam.p = [px, py];
  roam.v = [vx, vy];
}

function paintRoam() {
  const box = $('notch').getBoundingClientRect();
  // Paint between the last two fixed steps: no frozen or doubled frames on 60, 120 or 144 Hz screens.
  const blend = Math.min(1, roam.carry / ROAM_DT);
  const px = roam.prev[0] + (roam.p[0] - roam.prev[0]) * blend;
  const py = roam.prev[1] + (roam.p[1] - roam.prev[1]) * blend;
  const [vx, vy] = roam.v;
  const speed = Math.hypot(vx, vy);
  roam.el.style.transform = `translate(${px - box.left - 17}px, ${py - box.top - 17}px)`;
  roam.el.style.setProperty('--lean', `${Math.max(-1, Math.min(1, vx / 5)) * 14}deg`);
  // Wind streaks point opposite to travel and grow with speed.
  roam.el.style.setProperty('--wind-angle', `${Math.atan2(-vy, -vx)}rad`);
  roam.el.style.setProperty('--wind', Math.min(1, Math.max(0, (speed - 0.35) / 2.2)).toFixed(2));
}

// Outside the window the webview gets no pointer events, so follow the desktop cursor too.
function watchCursor() {
  const api = tauri?.window;
  if (!api?.cursorPosition || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const win = api.getCurrentWindow();
  let busyPoll = false;
  setInterval(async () => {
    if (busyPoll || document.hidden || performance.now() - lastPointer < 300) return;
    busyPoll = true;
    try {
      const [cursor, origin, scale] = await Promise.all([api.cursorPosition(), win.outerPosition(), win.scaleFactor()]);
      lookAt((cursor.x - origin.x) / scale, (cursor.y - origin.y) / scale);
    } catch { /* permission or platform without cursor position: pointer events still work */ }
    busyPoll = false;
  }, 70);
}

// Drag the banner anywhere; a plain click still opens it.
let dragged = false;
$('notch').querySelector('.topbar').addEventListener('pointerdown', (event) => {
  if (!tauri || event.button !== 0 || event.target.closest('.top-actions')) return;
  const start = [event.screenX, event.screenY];
  const cleanup = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', cleanup); };
  const move = async (next) => {
    if (Math.hypot(next.screenX - start[0], next.screenY - start[1]) < 4) return;
    cleanup();
    dragged = true;
    try { await tauri.window.getCurrentWindow().startDragging(); } catch { dragged = false; }
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', cleanup);
});

// Clicking anywhere outside DigitalSoul folds the dropdown back up.
let dialogOpen = false;
// Focus can bounce out and straight back (accessibility tools, voice control); only a real
// loss of focus counts as clicking outside.
window.addEventListener('blur', () => {
  setTimeout(() => {
    if (!document.hasFocus() && state.expanded && !state.pending && !dialogOpen) action(() => expanded(false));
  }, 180);
});

// Local voice commands (Jarvis).
let voiceOn = false;
let voicePending = false;
let voiceFeedback = '';
function voiceState(on, label) {
  voiceOn = on;
  if (!on) $('voice-mic').classList.remove('hearing');
  if (on) voiceFeedback = '';
  $('voice-mic').setAttribute('aria-pressed', String(on));
  $('voice-mic').setAttribute('aria-label', on ? 'Stop local voice commands' : 'Start local voice commands');
  $('voice-status').textContent = label || voiceFeedback || (on ? 'Listening locally · say Open WhatsApp' : 'Mic off · local faster-whisper');
  if (!state.recording && !state.running) {
    $('compact-label').textContent = voiceFeedback || (on ? (label ? 'Loading voice…' : 'Listening · say Hey Jarvis') : 'Boo! I remember your routines.');
  }
}
function compactVoice(message, label) {
  voiceFeedback = label || message;
  $('compact-label').textContent = voiceFeedback;
  $('voice-status').textContent = message;
  $('voice-mic').title = message;
  notice(message);
}
async function voiceResult(result) {
  if (!result) return;
  if (result.kind === 'signal') {
    $('voice-mic').classList.toggle('hearing', !!result.active);
    if (result.quiet_seconds >= 8) {
      compactVoice('No microphone sound detected. Check your input device or mute switch.', 'Mic is quiet · check input');
    }
    return;
  }
  if (result.kind === 'listening') {
    // Preserve the command result until the next utterance starts.
    if (voiceFeedback === 'Transcribing your voice...') voiceState(true);
    $('voice-status').textContent = result.message;
    return;
  }
  if (result.kind === 'heard') {
    $('voice-text').value = result.text;
    $('voice-mic').title = `Heard: ${result.text}`;
    const addView = $('add-workflow-view');
    if (addView && !addView.hidden && $('ai-workflow-prompt')) {
      $('ai-workflow-prompt').value = result.text;
      if ($('ai-workflow-status')) $('ai-workflow-status').textContent = `Heard: "${result.text}"`;
    }
  } else if (result.kind === 'ignored') {
    compactVoice('Say Hey Jarvis first, or click the mic off and on for a fresh command.', 'Say Hey Jarvis to wake');
  } else if (result.kind === 'learned_workflow') {
    const pattern = await rpc('learning_detail', { id: result.id });
    await expanded(true);
    openPatternWorkflow(pattern);
  } else if (result.kind === 'workflow') {
    await showDetail(result.id);
    await expanded(true);
    compactVoice(`Ready to run "${result.name}". Review the steps and approve replay.`, `Ready: ${result.name}`);
  } else if (['add_workflow', 'shopify', 'excel', 'whatsapp', 'workflow_created'].includes(result.kind)) {
    await handleSynthesizedWorkflow(result);
  } else if (result.message) {
    const setup = ['dependencies_missing', 'model_missing'].includes(result.code) || /install voice|download the local/i.test(result.message);
    compactVoice(result.message, setup ? 'Voice setup needed · expand for help' : result.message);
  }
}
$('voice-mic').addEventListener('click', async () => {
  if (voicePending) return;
  voicePending = true;
  try {
    await expanded(false);
    if (voiceOn) { await rpc('voice_stop'); voiceFeedback = ''; voiceState(false); }
    else {
      voiceState(true, 'Loading local voice model… click mic to stop');
      await rpc('voice_start');
    }
  } catch (error) {
    voiceState(false);
    compactVoice(String(error?.message || error), 'Voice unavailable · expand for help');
  } finally { voicePending = false; }
});
$('voice-form').addEventListener('submit', (event) => {
  event.preventDefault();
  action(async () => voiceResult(await rpc('voice_command', { text: $('voice-text').value })));
});

$('collapse').addEventListener('click', () => expanded(false).catch(error => notice(String(error?.message || error))));
for (const id of ['new-recording', 'empty-record']) $(id)?.addEventListener('click', newRecording);
for (const id of ['cancel-create', 'back']) $(id).addEventListener('click', () => view('library'));
document.querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', () => view(button.dataset.tab)));
$('search').addEventListener('input', renderWorkflows);
$('search').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    const query = $('search').value.trim();
    if (!query) return;
    action(async () => voiceResult(await rpc('voice_command', { text: query })));
  }
});
$('refresh').addEventListener('click', () => action(refresh));

const form = $('record-form');
const everythingChip = form.querySelector('input[value="*"]');
const appChips = form.querySelectorAll('input[name="app"]:not([value="*"])');
const whatsappChip = form.querySelector('input[value="WhatsApp.exe"]');
const syncWhatsappRow = () => { form.querySelector('.switch-row').hidden = !whatsappChip?.checked; };
form.querySelectorAll('input[name="app"]').forEach((chip) => chip.addEventListener('change', syncWhatsappRow));
everythingChip?.addEventListener('change', () => {
  if (everythingChip.checked) {
    appChips.forEach((chip) => { chip.checked = false; });
    if (form.elements.whatsapp) form.elements.whatsapp.checked = false;
  }
});
appChips.forEach((chip) => {
  chip.addEventListener('change', () => {
    if (chip.checked && everythingChip) everythingChip.checked = false;
    syncWhatsappRow();
  });
});
syncWhatsappRow();

$('record-form').addEventListener('submit', (event) => {
  event.preventDefault();
  action(async () => {
    const values = new FormData(event.target);
    const rawApps = [...values.getAll('app'), ...values.get('apps').split(',').map((app) => app.trim()).filter(Boolean)];
    if (!rawApps.length) throw new Error('Choose at least one app to observe.');
    const isEverything = rawApps.includes('*');
    const apps = isEverything ? ['*'] : rawApps.map((a) => (a === '*' || a.toLowerCase().endsWith('.exe') ? a : `${a}.exe`));
    const options = { name: values.get('name').trim(), apps };
    if (!isEverything && values.has('whatsapp') && apps.some((app) => /whatsapp/i.test(app))) options.whatsapp = true;
    await rpc('start', options);
    $('live-name').textContent = options.name;
    $('live-count').textContent = '0 steps';
    $('live-last').textContent = 'Open an allowed app to begin.';
    $('live-time').textContent = $('compact-time').textContent = '00:00';
    setRecording(true);
    view('library');
    await expanded(false);
  });
});

async function stopRecording() {
  const value = await rpc('stop');
  await hideNotch(false);
  setRecording(false);
  await refresh();
  await expanded(true);
  if (value) await showDetail(value.id);
}
for (const id of ['stop-recording', 'compact-stop']) $(id).addEventListener('click', () => action(stopRecording));
function openShopifyWorkflow(prefill = {}) {
  view('approve');
  $('approve-whatsapp-view').hidden = true;
  $('approve-shopify-view').hidden = false;
  $('approve-excel-view').hidden = true;
  $('approve-gform-view').hidden = true;
  $('shopify-progress').textContent = '';
  $('shopify-title').value = prefill.title || 'Vintage Washed Oversized T-Shirt';
  $('shopify-url').value = prefill.store_url || 'https://admin.shopify.com/store/gzn0vr-qn/products/new';
  $('shopify-price').value = prefill.price || '29.99';
  $('shopify-compare-at').value = prefill.compare_at_price || '';
  $('shopify-cost').value = prefill.cost_per_item || '';
  $('shopify-sku').value = prefill.sku || '';
  $('shopify-qty').value = prefill.inventory_quantity ?? 50;
  $('shopify-category').value = prefill.category || '';
  $('shopify-tags').value = prefill.tags || '';
  $('shopify-status').value = prefill.status || '';
  $('shopify-desc').value = prefill.description || '<p>Experience premium comfort with our 100% organic cotton oversized tee.</p>';
}
$('new-shopify')?.addEventListener('click', () => openShopifyWorkflow());

function openExcelWorkflow(prefill = {}) {
  view('approve');
  $('approve-whatsapp-view').hidden = true;
  $('approve-shopify-view').hidden = true;
  $('approve-excel-view').hidden = false;
  $('approve-gform-view').hidden = true;
  $('excel-workbook').value = prefill.workbook || 'Active Excel Workbook';
  $('excel-sheet').value = prefill.sheet || 'Sheet1';
  $('excel-topic').value = prefill.topic || prefill.title || 'Monthly Expenses';
  $('excel-headers').value = prefill.headers || 'Date, Item, Category, Amount';
  if (prefill.rows) {
    $('excel-rows').value = typeof prefill.rows === 'string' ? prefill.rows : prefill.rows.map(r => r.join(', ')).join('\n');
  } else if (prefill.cells) {
    $('excel-rows').value = prefill.cells.map(c => `${c.cell}, ${c.value}`).join('\n');
  } else {
    $('excel-rows').value = '2026-10-22, Social Ads, Marketing, 450.00\n2026-10-22, Software License, Tech, 120.00';
  }
  $('excel-formula').value = prefill.formula || '=SUM(D2:D3)';

  rpc('excel_inspect', {}).then((status) => {
    if (status && status.active) {
      if (!prefill.workbook && status.workbook) $('excel-workbook').value = status.workbook;
      if (!prefill.sheet && status.sheet) $('excel-sheet').value = status.sheet;
      if (!prefill.headers && status.headers) $('excel-headers').value = status.headers;
      notice(`📊 Connected to open Excel: ${status.workbook} · ${status.sheet} (${status.rows_count} rows)`);
    }
  }).catch(() => {});
}
$('new-excel')?.addEventListener('click', () => openExcelWorkflow());

let currentGFormQuestions = [];

async function inspectGForm(url) {
  $('gform-progress').textContent = '🔍 Inspecting form and matching remembered profile...';
  try {
    const res = await rpc('gform_inspect', { url });
    if (!res) throw new Error('Could not read form.');

    if (res.title) {
      $('gform-title').textContent = res.title;
      $('gform-subtitle').textContent = `Total questions: ${res.total_questions} · Auto-filled: ${res.known_fields.length} · New: ${res.new_fields.length}`;
    }

    $('gform-known-count').textContent = res.known_fields.length;
    $('gform-new-count').textContent = res.new_fields.length;

    const knownCont = $('gform-known-container');
    const newCont = $('gform-new-container');
    knownCont.replaceChildren();
    newCont.replaceChildren();

    currentGFormQuestions = [];

    // Render known fields
    if (res.known_fields && res.known_fields.length) {
      $('gform-known-header').hidden = false;
      for (const item of res.known_fields) {
        currentGFormQuestions.push(item);
        const card = element('div', 'gform-field-card');
        const lbl = element('label');
        lbl.textContent = item.title;
        const inp = element('input');
        inp.name = item.title;
        inp.value = item.suggested_value || '';
        inp.dataset.title = item.title;
        inp.dataset.key = item.matched_key || '';
        card.append(lbl, inp);
        knownCont.append(card);
      }
    } else {
      $('gform-known-header').hidden = true;
    }

    // Render new fields
    if (res.new_fields && res.new_fields.length) {
      $('gform-new-header').hidden = false;
      for (const item of res.new_fields) {
        currentGFormQuestions.push(item);
        const card = element('div', 'gform-field-card new');
        const lbl = element('label');
        lbl.textContent = item.title;
        const inp = element('input');
        inp.name = item.title;
        inp.placeholder = item.options ? `e.g. ${item.options.slice(0, 2).join(' or ')}` : 'Enter answer (will be remembered)';
        // Never invent an answer for a new field. The person must provide it
        // once; a successfully filled value is then remembered locally.
        inp.value = item.suggested_value || '';
        inp.dataset.title = item.title;
        card.append(lbl, inp);
        newCont.append(card);
      }
    } else {
      $('gform-new-header').hidden = true;
    }

    $('gform-progress').textContent = `✨ Mapped ${res.known_fields.length} known answers. ${res.new_fields.length} new fields ready to review.`;
  } catch (err) {
    $('gform-progress').textContent = String(err?.message || err);
  }
}

function openGFormWorkflow(prefill = {}) {
  view('approve');
  $('approve-whatsapp-view').hidden = true;
  $('approve-shopify-view').hidden = true;
  $('approve-excel-view').hidden = true;
  $('approve-pattern-view').hidden = true;
  $('approve-gform-view').hidden = false;

  $('gform-progress').textContent = '';
  const url = prefill.url || 'https://docs.google.com/forms/d/e/1FAIpQLScIxD14Pyc183PbDLHcS9N-nbQRRYSncqY0TP3g7qSsfWgnKQ/viewform';
  $('gform-url').value = url;
  inspectGForm(url);
}
$('new-gform')?.addEventListener('click', () => openGFormWorkflow());
$('gform-inspect-btn')?.addEventListener('click', () => inspectGForm($('gform-url').value));

$('gform-form')?.addEventListener('submit', (event) => {
  event.preventDefault();
  if (state.pending) {
    $('gform-progress').textContent = 'A request is already running.';
    return;
  }
  action(async () => {
    const url = $('gform-url').value;
    const saveMemory = $('gform-remember').checked;

    // Gather all inputs
    const items = [];
    const allInputs = $('gform-fields-section').querySelectorAll('input');
    allInputs.forEach(inp => {
      if (inp.dataset.title) {
        items.push({
          title: inp.dataset.title,
          value: inp.value,
          key: inp.dataset.key || ''
        });
      }
    });

    $('gform-progress').textContent = 'Opening browser to fill Google Form...';
    $('compact-label').textContent = 'Google Forms · filling…';
    notice(`Automating Google Form with ${items.length} answers...`);

    try {
      const res = await rpc('gform_run', {
        url,
        items,
        click_submit: false,
        save_memory: saveMemory,
        headless: false
      });
      await expanded(true);
      if (res && res.ok) {
        $('gform-progress').textContent = `✅ Successfully filled ${(res.filled_steps || []).length} form fields! Review the form in your browser before manual Submit.`;
        notice(`Form filled: ${(res.filled_steps || []).length} fields filled for review. Answers remembered!`, 'success');
      } else {
        $('gform-progress').textContent = `Filled ${(res?.filled_steps || []).length} fields. ${(res?.errors || []).join('; ')}`;
      }
    } catch (err) {
      $('gform-progress').textContent = String(err?.message || err);
      throw err;
    } finally {
      $('compact-label').textContent = IDLE_LABEL;
    }
  });
});

async function handleSynthesizedWorkflow(result) {
  if (!result) return;
  if (result.kind === 'error') {
    notice(result.message || 'Could not synthesize workflow.');
    if ($('ai-workflow-status')) $('ai-workflow-status').textContent = result.message || 'Synthesis failed.';
    return;
  }
  await expanded(true);
  if (result.kind === 'shopify') {
    openShopifyWorkflow(result.product || {});
    notice(result.message || `✨ Created Shopify product workflow: ${result.name}`);
    compactVoice(result.message || `Ready: ${result.name}`, `Shopify: ${result.name}`);
  } else if (result.kind === 'excel') {
    openExcelWorkflow(result.excel || {});
    notice(result.message || `✨ Created Excel workflow: ${result.name}`);
    compactVoice(result.message || `Ready: ${result.name}`, `Excel: ${result.name}`);
  } else if (result.kind === 'gform') {
    openGFormWorkflow(result.form || {});
    notice(result.message || `✨ Created Google Form workflow`);
    compactVoice(result.message || `Ready: Google Form`, `Form: Google Form`);
  } else if (result.kind === 'whatsapp') {
    state.detail = result.workflow || { id: result.id, flow: result.flow, steps: result.workflow?.steps || [] };
    await refresh();
    view('approve');
    $('approve-whatsapp-view').hidden = false;
    $('approve-shopify-view').hidden = true;
    $('approve-excel-view').hidden = true;
    $('approve-pattern-view').hidden = true;
    $('run-recipient').value = result.flow?.recipient || '';
    $('run-message').value = result.flow?.message || '';
    $('approve-send').checked = true;
    notice(result.message || `✨ Created WhatsApp workflow for ${result.flow?.recipient}`);
    compactVoice(result.message || `Ready: ${result.name}`, `WhatsApp: ${result.flow?.recipient}`);
  } else if (result.kind === 'workflow_created') {
    await refresh();
    if (result.id) {
      await expanded(true);
      await showDetail(result.id);
      notice(result.message || `✨ Created routine "${result.name}" with ${result.steps?.length || 0} steps!`, 'success');
      compactVoice(`Created "${result.name}". Expand notch to review steps.`, `Ready: ${result.name}`);
    }
  } else if (result.kind === 'learned_workflow') {
    const pattern = await rpc('learning_detail', { id: result.id });
    await expanded(true);
    openPatternWorkflow(pattern);
  } else if (result.kind === 'workflow') {
    await showDetail(result.id);
    await expanded(true);
    compactVoice(`Ready to run "${result.name}". Review the steps and approve replay.`, `Ready: ${result.name}`);
  } else if (result.message) {
    notice(result.message);
  }
}

function openAddWorkflow(initialPrompt = '') {
  view('add-workflow-view');
  if (initialPrompt && $('ai-workflow-prompt')) $('ai-workflow-prompt').value = initialPrompt;
  $('ai-workflow-prompt')?.focus();
  if ($('ai-workflow-status')) $('ai-workflow-status').textContent = 'Type what you want to automate, or click Dictate to speak.';
}
$('new-ai-workflow')?.addEventListener('click', () => openAddWorkflow());
$('empty-ai-workflow')?.addEventListener('click', () => openAddWorkflow());
$('cancel-ai-workflow')?.addEventListener('click', () => view('library'));

document.querySelectorAll('#add-workflow-view .chip-suggestion').forEach((btn) => {
  btn.addEventListener('click', () => {
    if ($('ai-workflow-prompt')) {
      $('ai-workflow-prompt').value = btn.dataset.prompt || '';
      $('ai-workflow-prompt').focus();
    }
  });
});

$('add-workflow-form')?.addEventListener('submit', (event) => {
  event.preventDefault();
  const prompt = $('ai-workflow-prompt')?.value.trim();
  if (!prompt) {
    notice('Please type or dictate a workflow instruction.');
    return;
  }
  action(async () => {
    notice('✨ Synthesizing workflow with AI...');
    if ($('ai-workflow-status')) $('ai-workflow-status').textContent = 'Synthesizing workflow actions...';
    const res = await rpc('workflow_synthesize', { prompt });
    await handleSynthesizedWorkflow(res);
  });
});

let speechRec = null;
let isDictating = false;

function toggleDictation() {
  const micBtn = $('ai-workflow-mic');
  const label = $('ai-mic-label');
  const status = $('ai-workflow-status');

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (isDictating) {
    isDictating = false;
    micBtn?.classList.remove('listening');
    if (label) label.textContent = 'Dictate';
    if (speechRec) {
      try { speechRec.stop(); } catch {}
      speechRec = null;
    }
    if (status) status.textContent = 'Dictation stopped. Click "Synthesize & Add Workflow" or continue typing.';
    return;
  }

  if (SpeechRecognition) {
    isDictating = true;
    micBtn?.classList.add('listening');
    if (label) label.textContent = 'Stop';
    if (status) status.textContent = '🎙️ Listening... speak your workflow prompt now.';

    const rec = new SpeechRecognition();
    speechRec = rec;
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = 'en-US';

    rec.onresult = (evt) => {
      let transcript = '';
      for (let i = evt.resultIndex; i < evt.results.length; ++i) {
        transcript += evt.results[i][0].transcript;
      }
      if (transcript.trim()) {
        const current = $('ai-workflow-prompt').value;
        if (!current || evt.resultIndex === 0) {
          $('ai-workflow-prompt').value = transcript.trim();
        } else {
          $('ai-workflow-prompt').value = current + ' ' + transcript.trim();
        }
        if (status) status.textContent = `🎙️ Heard: "${transcript.trim()}"`;
      }
    };

    rec.onerror = (evt) => {
      console.warn('SpeechRecognition error:', evt.error);
      if (status) status.textContent = `Mic note: ${evt.error}. You can also type or use the top Jarvis mic.`;
      toggleDictation();
    };

    rec.onend = () => {
      if (isDictating) {
        try { rec.start(); } catch { toggleDictation(); }
      }
    };

    try {
      rec.start();
    } catch (err) {
      console.warn('Could not start web speech:', err);
      fallbackToNativeVoice();
    }
  } else {
    fallbackToNativeVoice();
  }
}

async function fallbackToNativeVoice() {
  const micBtn = $('ai-workflow-mic');
  const label = $('ai-mic-label');
  const status = $('ai-workflow-status');

  try {
    if (voiceOn) {
      await rpc('voice_stop');
      voiceState(false);
      micBtn?.classList.remove('listening');
      if (label) label.textContent = 'Dictate';
      if (status) status.textContent = 'Voice dictation paused.';
    } else {
      micBtn?.classList.add('listening');
      if (label) label.textContent = 'Listening…';
      if (status) status.textContent = '🎙️ Local Whisper active. Speak your workflow instruction!';
      voiceState(true, 'Dictating workflow… click mic to stop');
      await rpc('voice_start');
    }
  } catch (err) {
    micBtn?.classList.remove('listening');
    if (label) label.textContent = 'Dictate';
    if (status) status.textContent = `Local mic: ${err?.message || err}. You can also type your workflow above.`;
  }
}

$('ai-workflow-mic')?.addEventListener('click', toggleDictation);

$('excel-ai-btn')?.addEventListener('click', () => action(async () => {
  const topic = $('excel-topic').value.trim() || $('excel-workbook').value.trim() || 'Expenses';
  notice('✨ AI is structuring spreadsheet rows and formulas...');
  const gen = await rpc('excel_generate', { topic });
  if (gen) {
    $('excel-headers').value = gen.headers || $('excel-headers').value;
    $('excel-rows').value = gen.rows || $('excel-rows').value;
    $('excel-formula').value = gen.formula || $('excel-formula').value;
    if (gen.sheet) $('excel-sheet').value = gen.sheet;
    notice('✨ Generated rows and formula for Excel! Review before running.');
  }
}));

$('excel-form')?.addEventListener('submit', (event) => {
  event.preventDefault();
  action(async () => {
    const payload = {
      workbook: $('excel-workbook').value,
      sheet: $('excel-sheet').value,
      headers: $('excel-headers').value,
      rows: $('excel-rows').value,
      formula: $('excel-formula').value,
      open_window: $('excel-open-window').checked,
    };
    notice('Updating Excel spreadsheet...');
    const result = await rpc('excel_run', payload);
    if (result && result.ok) {
      notice(`✅ ${result.detail || 'Excel spreadsheet updated successfully!'}`);
    } else {
      notice(`Excel run: ${result?.error || result?.detail || JSON.stringify(result)}`);
    }
  });
});

$('shopify-ai-btn')?.addEventListener('click', () => action(async () => {
  const title = $('shopify-title').value.trim();
  if (!title) {
    notice('Please enter a product title first so AI can generate the fields.');
    $('shopify-title').focus();
    return;
  }
  notice('✨ AI is analyzing title and generating product details...');
  const generated = await rpc('shopify_generate', {
    title,
    price: $('shopify-price').value,
    category: $('shopify-category').value,
    store_url: $('shopify-url').value,
  });
  if (generated) {
    $('shopify-desc').value = generated.description || $('shopify-desc').value;
    $('shopify-price').value = generated.price || $('shopify-price').value;
    $('shopify-compare-at').value = generated.compare_at_price || $('shopify-compare-at').value;
    $('shopify-cost').value = generated.cost_per_item || $('shopify-cost').value;
    $('shopify-sku').value = generated.sku || $('shopify-sku').value;
    $('shopify-qty').value = generated.inventory_quantity ?? $('shopify-qty').value;
    $('shopify-category').value = generated.category || $('shopify-category').value;
    $('shopify-tags').value = generated.tags || $('shopify-tags').value;
    $('shopify-status').value = generated.status || 'Active';
    notice('✨ Product fields generated by AI! Review before browser-use runs.');
  }
}));

$('shopify-form')?.addEventListener('submit', (event) => {
  event.preventDefault();
  if (state.pending) {
    $('shopify-progress').textContent = 'A request is already running. Wait for it or cancel the Shopify run.';
    return;
  }
  action(async () => {
    const isTest = $('shopify-test-mode').checked;
    const storeUrl = isTest ? 'http://127.0.0.1:7777/api/v1/shopify/test-form' : $('shopify-url').value;
    const product = {
      title: $('shopify-title').value,
      description: $('shopify-desc').value,
      price: $('shopify-price').value,
      compare_at_price: $('shopify-compare-at').value,
      cost_per_item: $('shopify-cost').value,
      sku: $('shopify-sku').value,
      inventory_quantity: Number($('shopify-qty').value),
      category: $('shopify-category').value,
      tags: $('shopify-tags').value,
      status: $('shopify-status').value,
      store_url: storeUrl,
    };
    notice(`Connecting to your Edge for "${product.title}". Allow Edge's debugging prompt if shown; keep this app open.`);
    $('shopify-progress').textContent = 'Connecting to your open Edge. Allow its debugging prompt if shown.';
    $('compact-label').textContent = 'Shopify · connecting to Edge…';
    $('shopify-cancel').hidden = false;
    let report;
    try {
      report = await rpc('shopify_run', { product, click_save: isTest, headless: false });
    } catch (error) {
      $('shopify-progress').textContent = String(error?.message || error);
      throw error;
    } finally {
      $('shopify-cancel').hidden = true;
      $('compact-label').textContent = IDLE_LABEL;
    }
    await expanded(true);
    if (report && report.ok) {
      $('shopify-progress').textContent = `${(report.filled_steps || []).length} fields filled. Review the product in Edge; live Save was not clicked.`;
      notice(`${report.status === 'saved' ? 'Saved the practice product' : 'Filled your Edge form for review'}: "${product.title}" (${(report.filled_steps || []).length} fields filled)`);
    } else {
      $('shopify-progress').textContent = `Filled ${(report?.filled_steps || []).length} fields. ${(report?.errors || ['The run did not finish.']).join('; ')}`;
      notice(`Shopify run completed: ${JSON.stringify(report?.errors || report)}`);
    }
  });
});
$('shopify-form')?.addEventListener('invalid', (event) => {
  $('shopify-progress').textContent = `Check ${event.target.id?.replace('shopify-', '').replaceAll('-', ' ') || 'the highlighted field'}: ${event.target.validationMessage}`;
}, true);
$('shopify-cancel')?.addEventListener('click', async () => {
  try {
    await rpc('shopify_cancel');
    notice('Stopping Shopify. Any filled fields remain unsaved.');
  } catch (error) { notice(String(error?.message || error)); }
});

$('review-run').addEventListener('click', () => {
  const detail = state.detail;
  const flow = detail?.flow;
  view('approve');
  if (flow?.kind === 'shopify') {
    openShopifyWorkflow(flow.product || { title: detail.name });
  } else if (flow?.kind === 'gform') {
    openGFormWorkflow(flow.form || {});
  } else if (flow?.format === 'digitalsoul.excel-flow.v1' || flow?.kind === 'excel' || (detail?.used || []).some(a => /excel/i.test(a))) {
    openExcelWorkflow({
      workbook: flow?.workbook || detail.name,
      sheet: flow?.sheet || 'Sheet1',
      title: detail.name,
      cells: flow?.cells,
    });
  } else if (flow) {
    $('approve-whatsapp-view').hidden = false;
    $('approve-shopify-view').hidden = true;
    $('approve-excel-view').hidden = true;
    $('approve-gform-view').hidden = true;
    $('run-recipient').value = flow.recipient;
    $('confirm-run').textContent = $('run-mode').value === 'later' ? 'Approve & schedule' : 'Approve & run';
    $('run-message').value = flow.message;
    $('approve-send').checked = true;
  } else {
    openShopifyWorkflow({ title: detail.name });
  }
});
$('cancel-run').addEventListener('click', () => view('detail'));
$('replay-steps').addEventListener('click', () => action(async () => {
  const detail = state.detail;
  if (!detail || !detail.steps?.length) return;
  await expanded(false);
  const flow = detail.flow;
  await rpc('replay_steps', {
    id: detail.id,
    approved_digest: detail.steps_digest,
    message: flow?.message,
    approved_recipient: flow?.recipient
  });
}));
$('run-mode').addEventListener('change', () => {
  const later = $('run-mode').value === 'later';
  $('schedule-label').hidden = !later;
  $('run-at').required = later;
  $('confirm-run').textContent = later ? 'Approve & schedule' : 'Approve & run';
});
$('run-form').addEventListener('submit', (event) => {
  event.preventDefault();
  action(async () => {
    $('approve-send').checked = true;
    const later = $('run-mode').value === 'later';
    const recipient = $('run-recipient').value.trim();
    if (!recipient) throw new Error('Who should I send it to?');
    const payload = { id: state.detail.id, message: $('run-message').value, approved_recipient: recipient };
    if (later) {
      const at = new Date($('run-at').value);
      if (!Number.isFinite(at.getTime()) || at <= new Date()) throw new Error('Choose a future local date and time.');
      payload.at = at.toISOString();
    }
    // Minimize the notch immediately so it gets out of the way for replay
    await expanded(false);
    await rpc(later ? 'schedule' : 'replay', payload);
    view('activity');
    await refresh();
    if (later) {
      notice('Scheduled locally. Keep DigitalSoul open and Windows awake/unlocked.');
    }
  });
});
function confirmDialog(message) {
  dialogOpen = true;
  try { return window.confirm(message); } finally { setTimeout(() => { dialogOpen = false; }, 300); }
}
function promptDialog(message, defaultValue = '') {
  dialogOpen = true;
  try { return window.prompt(message, defaultValue); } finally { setTimeout(() => { dialogOpen = false; }, 300); }
}

$('stop-all').addEventListener('click', () => action(async () => { await rpc('stop_jobs'); await refresh(); notice('Desktop stop is latched. Pending jobs were cancelled; an invocation already started cannot be undone.'); }));
$('resume-all').addEventListener('click', () => action(async () => { await rpc('resume_jobs'); notice('Desktop worker re-armed. Cancelled jobs stay cancelled.'); }));
$('delete-workflow').addEventListener('click', () => action(async () => {
  if (!confirmDialog(`Delete “${state.detail.name}” and its recorded steps?`)) return;
  await rpc('delete', { id: state.detail.id });
  state.detail = null;
  view('library');
  await refresh();
}));
$('quit').addEventListener('click', () => action(() => tauri.core.invoke('quit_app')));
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && state.expanded) action(() => expanded(false));
  if (event.ctrlKey && (event.code === 'Space' || event.key === ' ')) {
    event.preventDefault();
    triggerScreenScan();
  }
});

let currentScreenProposal = null;

function escapeHtml(str) {
  return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function triggerScreenScan(activeWindow = true) {
  await expanded(true);
  notice('📸 Scanning screen / active window & analyzing with AI...', 'info');
  busy(true);
  try {
    const proposal = await rpc('screen_scan', { active_window_only: activeWindow, cleanup_image: true });
    if (!proposal || !proposal.ok) {
      notice(proposal?.error || proposal?.summary || 'Screen scan failed or was denied.');
      return;
    }
    currentScreenProposal = proposal;
    openScreenProposal(proposal);
  } catch (err) {
    notice(String(err?.message || err));
  } finally {
    busy(false);
  }
}

function openScreenProposal(proposal) {
  view('approve');
  $('approve-whatsapp-view').hidden = true;
  $('approve-shopify-view').hidden = true;
  $('approve-excel-view').hidden = true;
  $('approve-pattern-view').hidden = true;
  $('approve-screen-view').hidden = false;

  const modeBadge = $('screen-processing-mode');
  if (modeBadge) {
    modeBadge.textContent = proposal.processing_mode || 'Local (WinRT OCR)';
    modeBadge.className = 'privacy-badge' + (proposal.processing_mode?.includes('Cloud') ? ' cloud' : '');
  }

  $('screen-proposal-title').textContent = proposal.title || 'Screen Workflow Proposal';
  $('screen-proposal-summary').textContent = proposal.summary || 'Extracted business information from screen.';

  const container = $('screen-extracted-container');
  if (!container) return;
  container.innerHTML = '';

  const ext = proposal.extracted_data || {};
  const kind = proposal.kind;

  if (kind === 'shopify') {
    container.innerHTML = `
      <div class="screen-data-card">
        <div class="screen-data-title app-brave">🛍️ Extracted Shopify Product Details (Editable)</div>
        <div class="screen-field-row"><label>Product Title</label><input id="screen-edit-title" value="${escapeHtml(ext.title || '')}" required></div>
        <div class="shop-grid">
          <div class="screen-field-row"><label>Price ($)</label><input id="screen-edit-price" type="number" step="0.01" value="${ext.price || '29.99'}"></div>
          <div class="screen-field-row"><label>SKU</label><input id="screen-edit-sku" value="${escapeHtml(ext.sku || '')}"></div>
        </div>
        <div class="screen-field-row"><label>Description</label><textarea id="screen-edit-desc" rows="2">${escapeHtml(ext.description || '')}</textarea></div>
      </div>
    `;
  } else if (kind === 'excel') {
    container.innerHTML = `
      <div class="screen-data-card">
        <div class="screen-data-title app-excel">📊 Extracted Spreadsheet & Table (Editable)</div>
        <div class="screen-field-row"><label>Spreadsheet Topic</label><input id="screen-edit-topic" value="${escapeHtml(ext.topic || 'Screen Data')}"></div>
        <div class="screen-field-row"><label>Columns (Headers)</label><input id="screen-edit-headers" value="${escapeHtml(Array.isArray(ext.headers) ? ext.headers.join(', ') : (ext.headers || ''))}"></div>
        <div class="screen-field-row"><label>Rows (CSV Format)</label><textarea id="screen-edit-rows" rows="3">${escapeHtml(ext.rows_csv || '')}</textarea></div>
        <div class="screen-field-row"><label>Summary Formula</label><input id="screen-edit-formula" value="${escapeHtml(ext.formula || '=SUM(...)')}"></div>
      </div>
    `;
  } else if (kind === 'whatsapp') {
    container.innerHTML = `
      <div class="screen-data-card">
        <div class="screen-data-title app-whatsapp">💬 Extracted Communication (Editable)</div>
        <div class="screen-field-row"><label>Recipient</label><input id="screen-edit-recipient" value="${escapeHtml(ext.recipient || '')}" required></div>
        <div class="screen-field-row"><label>Message</label><textarea id="screen-edit-message" rows="3" required>${escapeHtml(ext.message || '')}</textarea></div>
      </div>
    `;
  } else {
    container.innerHTML = `
      <div class="screen-data-card">
        <div class="screen-data-title app-info">🖥️ Extracted Routine Steps (Editable)</div>
        <div class="screen-field-row"><label>Routine Name</label><input id="screen-edit-name" value="${escapeHtml(proposal.title || 'Screen Routine')}"></div>
        <div class="screen-field-row"><label>Summary / Notes</label><textarea id="screen-edit-summary" rows="3">${escapeHtml(ext.text_snippet || proposal.summary || '')}</textarea></div>
      </div>
    `;
  }
}

$('screen-form')?.addEventListener('submit', (event) => {
  event.preventDefault();
  if (!currentScreenProposal) return;
  const p = currentScreenProposal;
  action(async () => {
    if (p.kind === 'shopify') {
      const updatedProduct = {
        ...(p.extracted_data || {}),
        title: $('screen-edit-title')?.value || p.extracted_data?.title,
        price: $('screen-edit-price')?.value || p.extracted_data?.price,
        sku: $('screen-edit-sku')?.value || p.extracted_data?.sku,
        description: $('screen-edit-desc')?.value || p.extracted_data?.description,
      };
      openShopifyWorkflow(updatedProduct);
      notice('Approved screen product! Review fields and run in Edge.');
    } else if (p.kind === 'excel') {
      const updatedExcel = {
        ...(p.extracted_data || {}),
        topic: $('screen-edit-topic')?.value || p.extracted_data?.topic,
        headers: $('screen-edit-headers')?.value || p.extracted_data?.headers,
        rows: $('screen-edit-rows')?.value || p.extracted_data?.rows_csv,
        formula: $('screen-edit-formula')?.value || p.extracted_data?.formula,
      };
      openExcelWorkflow(updatedExcel);
      notice('Approved screen spreadsheet! Review and update Excel.');
    } else if (p.kind === 'whatsapp') {
      const recipient = $('screen-edit-recipient')?.value.trim() || p.extracted_data?.recipient;
      const message = $('screen-edit-message')?.value.trim() || p.extracted_data?.message;
      view('approve');
      $('approve-screen-view').hidden = true;
      $('approve-whatsapp-view').hidden = false;
      $('run-recipient').value = recipient;
      $('run-message').value = message;
      $('approve-send').checked = true;
      notice('Approved communication! Confirm recipient and send.');
    } else {
      notice(`Approved routine "${p.title}".`);
      view('library');
    }
  });
});

$('screen-edit-btn')?.addEventListener('click', () => {
  if (!currentScreenProposal) return;
  const p = currentScreenProposal;
  openAddWorkflow(`Automate ${p.title}: ${p.summary}`);
});

$('scan-screen-btn')?.addEventListener('click', () => triggerScreenScan());

// Cross-App Chain Orchestrator: Process today's supplier drop
function openChainWorkflow(prefill = {}) {
  view('approve');
  $('approve-whatsapp-view').hidden = true;
  $('approve-shopify-view').hidden = true;
  $('approve-excel-view').hidden = true;
  $('approve-pattern-view').hidden = true;
  $('approve-screen-view').hidden = true;
  $('approve-chain-view').hidden = false;

  if (prefill.workbook) $('chain-workbook').value = prefill.workbook;
  if (prefill.store_url) $('chain-store-url').value = prefill.store_url;
  if (prefill.recipient) $('chain-recipient').value = prefill.recipient;

  $('chain-status-badge').textContent = 'Ready';
  $('chain-status-badge').className = 'privacy-badge';
  $('chain-report-container').hidden = true;

  const defaultSteps = [
    { name: "1. Read Supplier Rows (Excel/CSV)", app: "excel", status: "pending" },
    { name: "2. Validate Fields & Calculate Totals", app: "validator", status: "pending" },
    { name: "3. Stage Products in Shopify (browser-use)", app: "shopify", status: "pending" },
    { name: "4. Verify Staged Details Against Source", app: "verifier", status: "pending" },
    { name: "5. Notify Store Manager (WhatsApp)", app: "whatsapp", status: "pending" },
  ];
  renderChainSteps(defaultSteps);
}

function renderChainSteps(steps = []) {
  const container = $('chain-steps-container');
  if (!container) return;
  container.innerHTML = '';
  steps.forEach((s) => {
    const card = document.createElement('div');
    card.className = `chain-step-card ${s.status}`;
    const tone = s.status === 'completed' ? 'ok' : s.status === 'failed' ? 'bad' : s.status === 'blocked' ? 'warn' : 'muted';
    card.innerHTML = `
      <div class="chain-step-name">
        <strong>${escapeHtml(s.name)}</strong>
        <span class="chain-step-app">[${escapeHtml(s.app)}]</span>
      </div>
      <div class="chain-step-state">
        ${s.error ? `<span class="chain-step-error">${escapeHtml(s.error)}</span>` : ''}
        ${s.setup_requirement ? `<span class="chain-step-setup">${escapeHtml(s.setup_requirement)}</span>` : ''}
        <span class="chain-step-status tone-${tone}">${escapeHtml(s.status)}</span>
      </div>
    `;
    container.appendChild(card);
  });
}

function renderChainReport(report) {
  const repEl = $('chain-report-container');
  if (!repEl) return;
  repEl.hidden = false;
  repEl.innerHTML = `
    <div class="chain-report-head">
      <span>Execution Report: ${escapeHtml(report.status.toUpperCase())}</span>
      <span>${report.duration_seconds}s</span>
    </div>
    <div class="chain-report-body">${escapeHtml(report.summary)}</div>
  `;
}

$('chain-drop-btn')?.addEventListener('click', () => openChainWorkflow());

$('chain-preview-btn')?.addEventListener('click', () => action(async () => {
  notice('⚡ Preparing dry-run preview of supplier drop chain...', 'info');
  busy(true);
  try {
    const report = await rpc('chain_plan', {
      workbook_path: $('chain-workbook').value.trim(),
      store_url: $('chain-store-url').value.trim(),
      recipient: $('chain-recipient').value.trim(),
      dry_run: true,
      approved: false,
    });
    $('chain-status-badge').textContent = 'Preview';
    $('chain-status-badge').className = 'privacy-badge cloud';
    renderChainSteps(report.steps);
    renderChainReport(report);
    notice('Dry-run preview generated! Check details above before execution.');
  } catch (err) {
    notice(String(err?.message || err));
  } finally {
    busy(false);
  }
}));

$('chain-form')?.addEventListener('submit', (event) => {
  event.preventDefault();
  action(async () => {
    notice('⚡ Executing cross-app supplier drop chain...', 'info');
    busy(true);
    try {
      const report = await rpc('chain_run', {
        workbook_path: $('chain-workbook').value.trim(),
        store_url: $('chain-store-url').value.trim(),
        recipient: $('chain-recipient').value.trim(),
        dry_run: false,
        approved: true,
      });
      $('chain-status-badge').textContent = report.status;
      $('chain-status-badge').className = 'privacy-badge ' + (report.status === 'succeeded' ? '' : 'cloud');
      renderChainSteps(report.steps);
      renderChainReport(report);
      notice(`Chain execution finished: ${report.status.toUpperCase()} (${report.duration_seconds}s)`);
    } catch (err) {
      notice(String(err?.message || err));
    } finally {
      busy(false);
    }
  });
});

async function boot() {
  if (!tauri) {
    await expanded(true);
    notice('Browser preview only. Launch with npm run dev for native recording and saved workflows.');
    return;
  }
  await tauri.event.listen('desktop-event', async ({ payload }) => {
    try {
      if (payload.event === 'voice') {
        if (payload.kind === 'ready') voiceState(true);
        else if (['stopped', 'error', 'sleep'].includes(payload.kind)) voiceState(false);
        await voiceResult(payload);
      } else if (payload.event === 'recording_progress') {
        $('live-count').textContent = `${payload.step_count} steps`;
        $('live-time').textContent = $('compact-time').textContent = timeLabel(payload.elapsed);
        $('live-last').textContent = payload.last_step ? stepLabel(payload.last_step) : 'Open an allowed app to begin.';
      } else if (payload.event === 'recording_saved') {
        await hideNotch(false);
        setRecording(false);
        await refresh();
        await expanded(true);
        await showDetail(payload.recording.id);
        cheer();
      } else if (payload.event === 'pattern_learned') {
        if (payload.user_memory) state.user_memory = payload.user_memory;
        await refresh();
        cheer();
        notice(`🧠 Pattern Intelligence: ${payload.result?.detail || 'Learned new skills and workflow facts!'}`, 'success');
      } else if (payload.event === 'replay_started') {
        state.running = true;
        $('notch').classList.add('replaying');
        $('compact-label').textContent = payload.steps ? 'Doing it for you… F8 to stop' : 'Sending it for you…';
        $('quit').disabled = true;
      } else if (payload.event === 'replay_finished') {
        state.running = false;
        $('quit').disabled = false;
        $('notch').classList.remove('replaying');
        $('compact-label').textContent = IDLE_LABEL;
        await refresh();
        await expanded(true);
        if (payload.steps && state.detail) await showDetail(state.detail.id);
        else view('activity');
        notice(payload.job?.detail || 'Replay ended. Check its status and WhatsApp before retrying.', payload.job?.ok ? 'success' : '');
      } else if (payload.event === 'error') {
        await hideNotch(false);
        await expanded(true);
        notice(payload.message);
      } else if (payload.event === 'disconnected') {
        await hideNotch(false);
        state.connected = false;
        $('connection').className = 'connection error';
        await expanded(true);
        notice('Local backend disconnected. Reopen DigitalSoul; no new actions will be started.');
      }
    } catch (error) { notice(String(error?.message || error)); }
  });
  applyAccent((await rpc('hello')).accent);
  watchCursor();
  state.connected = true;
  $('connection').className = 'connection online';
  $('connection').title = 'Local desktop backend connected';
  $('hermes-sync-btn')?.addEventListener('click', () => action(async () => {
    notice('Loading skills from Hermes Agent...', 'info');
    const data = await rpc('list_hermes_skills');
    const skills = data?.skills || [];
    const listStr = skills.slice(0, 10).map(s => `• ${s.name} (${s.category})`).join('\n');
    const choice = promptDialog(`Available skills from Hermes Agent:\n${listStr}\n\nEnter skill name to copy & import (e.g. computer-use, youtube-content, blocked-page-recovery, systematic-debugging):`, 'computer-use');
    if (!choice || !choice.trim()) return;
    notice(`Copying & importing '${choice.trim()}' from Hermes...`, 'info');
    const res = await rpc('import_hermes_skill', { skill_name: choice.trim() });
    await refresh();
    notice(`Imported Hermes skill "${choice.trim()}" into learned patterns!`, 'success');
  }));
  $('import-skill-btn')?.addEventListener('click', () => action(async () => {
    const md = promptDialog('Paste standard Microsoft or Hermes SKILL.md specification:');
    if (!md || !md.trim()) return;
    const res = await rpc('import_skill', { skill_markdown: md.trim() });
    await refresh();
    notice(`Imported skill "${res.name}" with ${res.step_count} verified steps.`, 'success');
  }));
  $('add-memory-btn')?.addEventListener('click', () => action(async () => {
    const keyName = promptDialog("Enter fact or preference name (e.g. Full Name, Shopify Store URL, Manager Name, Shipping Address):");
    if (!keyName || !keyName.trim()) return;
    const val = promptDialog(`Enter value for "${keyName.trim()}":`);
    if (!val || !val.trim()) return;
    const cleanKey = keyName.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_');
    await rpc('gform_memory_save', { profile: { [cleanKey]: val.trim() } });
    await refresh();
    notice(`Saved "${keyName.trim()}" to your memory! DigitalSoul will use it across workflows.`, 'success');
  }));
  $('test-hud-btn')?.addEventListener('click', () => action(async () => {
    notice('Live Ghost Pilot HUD demo engaged! Watch the ghost flight overlay.', 'success');
    await rpc('hud_demo');
  }));
  $('self-heal-btn')?.addEventListener('click', () => action(async () => {
    notice('Self-Healing demo engaged: simulating recoverable obstacle & verified recovery.', 'success');
    await rpc('self_heal_demo', { obstacle: 'cookie' });
  }));
  await refresh();
}

boot().catch(async (error) => {
  $('connection').className = 'connection error';
  await expanded(true).catch(() => {});
  notice(String(error?.message || error));
});
