'use strict';

const $ = (id) => document.getElementById(id);
const state = { expanded: false, connected: false, recording: false, running: false, workflows: [], jobs: [], detail: null, pending: false };
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
    if (tauri) await tauri.core.invoke('set_notch_expanded', { expanded: true });
    $('panel').hidden = false;
    requestAnimationFrame(() => { $('notch').classList.add('open'); movePill(); });
    setTimeout(startRoam, 560);
  } else {
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
  for (const id of ['library', 'activity', 'detail', 'create', 'approve']) $(id).hidden = id !== name;
  const tab = name === 'activity' ? 'activity' : 'library';
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
  for (const id of ['new-recording', 'empty-record', 'stop-recording', 'compact-stop', 'confirm-run']) $(id).disabled = value;
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
  $('new-recording').disabled = value || state.running;
}

const APP_NAMES = { 'whatsapp.exe': 'WhatsApp', 'whatsapp.root.exe': 'WhatsApp', 'brave.exe': 'Brave', 'chrome.exe': 'Chrome', 'msedge.exe': 'Edge', 'excel.exe': 'Excel', 'winword.exe': 'Word', 'powerpnt.exe': 'PowerPoint', 'explorer.exe': 'File Explorer', 'file explorer': 'File Explorer', '*': 'Everything' };
function appName(app) {
  if (!app) return 'Desktop';
  const name = APP_NAMES[app.toLowerCase()] || app.replace(/\.exe$/i, '');
  return name[0].toUpperCase() + name.slice(1);
}

function stepLabel(step) {
  if (step.kind === 'open_app') {
    const name = appName(step.app);
    return step.title ? `Open ${name} (${step.title})` : `Open ${name}`;
  }
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
  if (workflow.can_run) return 'whatsapp';
  if (!workflow.step_count) return 'empty';
  if (used.some((app) => /chrome|brave|msedge|firefox/i.test(app))) return 'browser';
  if (used.some((app) => /excel|winword|powerpnt/i.test(app))) return 'office';
  if (used.some((app) => /whatsapp/i.test(app))) return 'chats';
  return 'apps';
}

function statusOf(workflow) {
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
  const filter = $('search').value.toLowerCase();
  const items = state.workflows.filter((workflow) => `${workflow.name} ${workflow.recipient || ''}`.toLowerCase().includes(filter));
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

async function refresh() {
  const result = await rpc('list');
  state.workflows = result.workflows;
  state.jobs = result.jobs;
  renderWorkflows();
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
  renderFlow(detail.flow);
  $('detail-warning').textContent = detail.flow
    ? 'WhatsApp send recognized. Check the recipient and message before replaying; it sends a real message.'
    : 'Replay repeats these steps in order, including any messages or posts. It stops at the first step it cannot find.';
  $('review-run').disabled = !detail.flow || state.recording || state.running;
  // Recordings that are not a WhatsApp send routine replay step by step instead.
  $('review-run').hidden = !detail.flow && detail.steps.length > 0;
  $('replay-steps').hidden = !$('review-run').hidden;
  $('replay-steps').disabled = state.recording || state.running;
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
    } else if (step.kind === 'press_enter') {
      body.append(element('span', 'verb', 'Pressed'), element('kbd', 'keycap', 'Enter ⏎'));
    } else {
      body.append(element('span', 'verb', stepLabel(step)));
    }
    row.append(node, body, element('time', '', timeLabel(step.at || 0)));
    $('step-list').append(row);
  }
  if (!detail.steps.length) $('step-list').append(element('p', 'hint', 'No allowed-app actions were observed in this recording.'));
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

$('toggle').addEventListener('click', () => {
  if (dragged) { dragged = false; return; }
  action(async () => { await expanded(!state.expanded); if (state.expanded) syncTheme(); });
});

function cheer() {
  $('notch').classList.remove('cheer');
  void $('notch').offsetWidth;
  $('notch').classList.add('cheer');
  setTimeout(() => $('notch').classList.remove('cheer'), 1300);
}

// The ghost turns to look at you (2.5D) and leans in, wide-eyed, as the cursor comes near.
function lookAt(x, y) {
  roam.cursor = [x, y];
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
document.addEventListener('pointermove', (event) => lookAt(event.clientX, event.clientY));

// Every so often the visible ghost does a little trick. Which trick and when come from a fixed
// pseudo-random sequence: deterministic, but it never feels regular.
const TRICKS = ['twirl', 'wave', 'peek', 'wink', 'glance', 'yawn', 'hop'];
const sequence = (n) => { const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453; return x - Math.floor(x); };
let trickCount = 0;

function scheduleTrick() {
  setTimeout(() => {
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!calm && !state.recording && !state.running && !document.hidden) {
      const ghost = $('notch').classList.contains('roaming') ? roam.el?.querySelector('.ghost-float') : $('toggle').querySelector('.ghost-float');
      const trick = TRICKS[Math.floor(sequence(trickCount * 2) * TRICKS.length)];
      if (ghost && !ghost.className.includes('trick-')) {
        ghost.classList.add(`trick-${trick}`);
        setTimeout(() => ghost.classList.remove(`trick-${trick}`), 1500);
      }
    }
    trickCount += 1;
    scheduleTrick();
  }, 6000 + sequence(trickCount * 2 + 1) * 9000);
}
scheduleTrick();

// The ghost puffs up when you click and grows a little, curious, while you hover controls.
let curious = 1;
let puffTimer = 0;
const ghostSize = (size) => document.querySelectorAll('.ghost-float').forEach((ghost) => ghost.style.setProperty('--size', size));
document.addEventListener('pointerdown', () => {
  ghostSize(1.45);
  clearTimeout(puffTimer);
  puffTimer = setTimeout(() => { puffTimer = 0; ghostSize(curious); }, 260);
});
document.addEventListener('pointerover', (event) => {
  curious = event.target.closest('button, .workflow-card, .chip, input, textarea, select') ? 1.18 : 1;
  if (!puffTimer) ghostSize(curious);  // don't cut a click-puff short
});

// When the panel is open the ghost leaves the header to roam. From afar it drifts after you;
// when you come close it gets shy and darts off sideways. It never blocks a click.
const roam = { el: null, p: [0, 0], v: [0, 0], cursor: null, side: 1, fleeing: false, frame: 0, time: 0, last: 0, carry: 0 };
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
  if (roam.frame || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (!roam.el) {
    roam.el = element('div', 'roamer');
    roam.el.setAttribute('aria-hidden', 'true');
    roam.el.append(element('i', 'wind'), element('i', 'wind'), $('toggle').querySelector('.ghost-float').cloneNode(true));
    $('notch').append(roam.el);
  }
  const home = $('toggle').querySelector('.ghost').getBoundingClientRect();
  roam.p = [home.left + home.width / 2, home.top + home.height / 2];
  roam.v = [0.6, 1];
  roam.time = 0;
  roam.carry = 0;
  roam.last = performance.now();
  $('notch').classList.add('roaming');
  const tick = (now) => {
    // Fixed time step: identical motion on fast and slow displays.
    roam.carry += Math.min(now - roam.last, 100) / 1000;
    roam.last = now;
    while (roam.carry >= ROAM_DT) {
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
  roam.el.classList.add('returning');
  roam.el.style.setProperty('--wind', '0');
  roam.el.style.setProperty('--lean', '0deg');
  roam.el.style.transform = `translate(${home.left - box.left + shift + home.width / 2 - 17}px, ${home.top - box.top + home.height / 2 - 17}px)`;
  setTimeout(() => {
    $('notch').classList.remove('roaming');
    setTimeout(() => roam.el.classList.remove('returning'), 260);
  }, 300);
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
  vx = (vx + ax) * 0.95;
  vy = (vy + ay) * 0.95;
  // Unhurried: about 3.5px a frame at most.
  const speed = Math.hypot(vx, vy);
  if (speed > 3.5) { vx *= 3.5 / speed; vy *= 3.5 / speed; }
  px += vx;
  py += vy;
  const margin = 22;
  const bounds = [box.left + margin, box.right - margin, box.top + 66, box.bottom - margin];
  if (px < bounds[0] || px > bounds[1]) { vx *= -0.6; px = Math.min(bounds[1], Math.max(bounds[0], px)); }
  if (py < bounds[2] || py > bounds[3]) { vy *= -0.6; py = Math.min(bounds[3], Math.max(bounds[2], py)); }
  roam.p = [px, py];
  roam.v = [vx, vy];
}

function paintRoam() {
  const box = $('notch').getBoundingClientRect();
  const [px, py] = roam.p;
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
    if (busyPoll || document.hidden) return;
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
window.addEventListener('blur', () => {
  if (state.expanded && !state.pending && !dialogOpen) action(() => expanded(false));
});
$('collapse').addEventListener('click', () => action(() => expanded(false)));
for (const id of ['new-recording', 'empty-record']) $(id).addEventListener('click', newRecording);
for (const id of ['cancel-create', 'back']) $(id).addEventListener('click', () => view('library'));
document.querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', () => view(button.dataset.tab)));
$('search').addEventListener('input', renderWorkflows);
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
    const apps = [...values.getAll('app'), ...values.get('apps').split(',').map((app) => app.trim()).filter(Boolean)];
    if (!apps.length) throw new Error('Choose at least one app to observe.');
    const isEverything = apps.includes('*');
    const options = { name: values.get('name').trim(), apps: isEverything ? ['*'] : apps };
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
  setRecording(false);
  await refresh();
  await expanded(true);
  if (value) await showDetail(value.id);
}
for (const id of ['stop-recording', 'compact-stop']) $(id).addEventListener('click', () => action(stopRecording));
$('review-run').addEventListener('click', () => {
  const flow = state.detail?.flow;
  if (!flow) return;
  view('approve');
  $('run-recipient').value = flow.recipient;
  $('confirm-run').textContent = $('run-mode').value === 'later' ? 'Approve & schedule' : 'Approve & run';
  $('run-message').value = flow.message;
  $('approve-send').checked = true;
});
$('cancel-run').addEventListener('click', () => view('detail'));
$('replay-steps').addEventListener('click', () => action(async () => {
  const detail = state.detail;
  const apps = [...new Set(detail.steps.map((step) => step.app).filter(Boolean).map(appName))].join(', ') || 'your desktop';
  if (!confirmDialog(`Replay all ${detail.steps.length} steps in ${apps}?

DigitalSoul will click and type exactly as recorded, including sending messages again. Don't touch the mouse or keyboard while it runs. Press F8 to stop.`)) return;
  await expanded(false);
  await rpc('replay_steps', { id: detail.id, approved_digest: detail.steps_digest });
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
});

async function boot() {
  if (!tauri) {
    await expanded(true);
    notice('Browser preview only. Launch with npm run dev for native recording and saved workflows.');
    return;
  }
  await tauri.event.listen('desktop-event', async ({ payload }) => {
    try {
      if (payload.event === 'recording_progress') {
        $('live-count').textContent = `${payload.step_count} steps`;
        $('live-time').textContent = $('compact-time').textContent = timeLabel(payload.elapsed);
        $('live-last').textContent = payload.last_step ? stepLabel(payload.last_step) : 'Open an allowed app to begin.';
      } else if (payload.event === 'recording_saved') {
        setRecording(false);
        await refresh();
        await expanded(true);
        await showDetail(payload.recording.id);
        cheer();
        notice(`Got it! I learned ${payload.recording.steps.length} steps.`, 'success');
      } else if (payload.event === 'replay_started') {
        state.running = true;
        $('notch').classList.add('replaying');
        tauri.core.invoke('set_ghost_cursor', { on: true }).catch(() => {});
        $('compact-label').textContent = payload.steps ? 'Doing it for you… F8 to stop' : 'Sending it for you…';
        $('quit').disabled = true;
      } else if (payload.event === 'replay_finished') {
        state.running = false;
        $('quit').disabled = false;
        $('notch').classList.remove('replaying');
        tauri.core.invoke('set_ghost_cursor', { on: false }).catch(() => {});
        $('compact-label').textContent = IDLE_LABEL;
        await refresh();
        await expanded(true);
        if (payload.steps && state.detail) await showDetail(state.detail.id);
        else view('activity');
        notice(payload.job?.detail || 'Replay ended. Check its status and WhatsApp before retrying.', payload.job?.ok ? 'success' : '');
      } else if (payload.event === 'error') {
        await expanded(true);
        notice(payload.message);
      } else if (payload.event === 'disconnected') {
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
  await refresh();
}
boot().catch(async (error) => {
  $('connection').className = 'connection error';
  await expanded(true).catch(() => {});
  notice(String(error?.message || error));
});
