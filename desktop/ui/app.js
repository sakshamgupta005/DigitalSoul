'use strict';

const $ = (id) => document.getElementById(id);
const state = { expanded: false, connected: false, recording: false, running: false, workflows: [], jobs: [], detail: null, pending: false };
const tauri = window.__TAURI__;
const timeLabel = (seconds) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;
const dateLabel = (date) => new Date(date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

function notice(message) {
  $('notice').textContent = message || '';
  $('notice').hidden = !message;
}

async function rpc(method, payload = {}) {
  if (!tauri) throw new Error('Open the Tauri app to record desktop steps. This browser view has no native backend.');
  return tauri.core.invoke('backend_request', { method, payload });
}

async function expanded(value) {
  if (tauri) await tauri.core.invoke('set_notch_expanded', { expanded: value });
  state.expanded = value;
  $('panel').hidden = !value;
  $('collapse').hidden = !value;
  $('toggle').setAttribute('aria-expanded', String(value));
  $('toggle').setAttribute('aria-label', value ? 'Collapse DigitalSoul' : 'Expand DigitalSoul');
}

function view(name) {
  for (const id of ['library', 'activity', 'detail', 'create', 'approve']) $(id).hidden = id !== name;
  document.querySelectorAll('[data-tab]').forEach((button) => button.classList.toggle('active', button.dataset.tab === name));
  notice('');
  $('panel').scrollTop = 0;
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
  $('compact-label').textContent = value ? 'Recording your routine' : 'Your work, remembered.';
  $('quit').disabled = value || state.running;
  $('new-recording').disabled = value || state.running;
}

function stepLabel(step) {
  return ({ open_app: `Open ${step.app || 'application'}`, click: `Click ${step.label || step.target?.name || 'control'}`, set_text: 'Enter text', press_enter: 'Press Enter' })[step.kind] || step.kind;
}

function element(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

function renderWorkflows() {
  const filter = $('search').value.toLowerCase();
  const items = state.workflows.filter((workflow) => workflow.name.toLowerCase().includes(filter));
  $('workflow-count').textContent = state.workflows.length;
  $('empty').hidden = state.workflows.length > 0;
  const list = $('workflow-list');
  list.replaceChildren();
  if (!items.length && state.workflows.length) list.append(element('p', '', 'No matching workflows.'));
  for (const workflow of items) {
    const card = element('button', 'workflow-card');
    card.append(element('span', 'workflow-icon', workflow.apps?.some((app) => /whatsapp/i.test(app)) ? '↗' : '⌘'));
    const info = element('span', 'workflow-info');
    info.append(element('strong', '', workflow.name), element('p', '', `${workflow.step_count} steps · ${workflow.apps?.join(', ') || 'Desktop'} · ${dateLabel(workflow.created_at)}`));
    card.append(info, element('span', `badge ${workflow.can_run ? '' : 'review'}`, workflow.can_run ? 'Ready to review' : 'Recorded steps'), element('span', 'card-arrow', '›'));
    card.addEventListener('click', () => action(() => showDetail(workflow.id)));
    list.append(card);
  }
}

function renderJobs() {
  $('job-count').textContent = state.jobs.filter((job) => ['pending', 'preparing', 'dispatching'].includes(job.state)).length;
  $('job-list').replaceChildren();
  if (!state.jobs.length) $('job-list').append(element('p', 'hint', 'No scheduled messages or replay attempts yet.'));
  const labels = { pending: 'Scheduled', preparing: 'Preparing replay', dispatching: 'Send in progress', attempted_unverified: 'Send attempted · check WhatsApp', uncertain: 'Uncertain · inspect WhatsApp', blocked: 'Blocked · check the desktop', missed: 'Missed time window', cancelled: 'Cancelled' };
  for (const job of [...state.jobs].reverse()) {
    const card = element('article', 'job-card');
    const body = element('div');
    body.append(element('strong', '', labels[job.state] || job.state), element('p', '', new Date(job.due * 1000).toLocaleString()), element('p', '', job.detail || job.id.slice(0, 8)));
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
  $('detail-meta').textContent = `${detail.steps.length} recorded steps · ${timeLabel(detail.duration)} · ${new Date(detail.created_at).toLocaleString()}`;
  $('detail-warning').textContent = detail.flow ? 'A WhatsApp routine was recognized. Review its recipient and message before replay. Recorded sends may already have sent a real message.' : (detail.replay_error || 'These desktop steps are saved as evidence. An app adapter is needed before they can be replayed.');
  $('review-run').disabled = !detail.flow || state.recording || state.running;
  $('step-list').replaceChildren();
  for (const step of detail.steps) {
    const row = element('li');
    const body = element('div');
    body.append(element('strong', '', stepLabel(step)), element('p', '', step.app || 'Desktop'));
    if (step.kind === 'set_text') body.append(element('code', '', step.value));
    row.append(body, element('time', '', timeLabel(step.at || 0)));
    $('step-list').append(row);
  }
  if (!detail.steps.length) $('step-list').append(element('p', 'hint', 'No allowed-app actions were observed in this recording.'));
}

function newRecording() {
  if (state.recording || state.running) { notice('Stop the active recording or wait for the replay first.'); return; }
  view('create');
  $('record-form').elements.name.focus();
}

$('toggle').addEventListener('click', () => action(() => expanded(!state.expanded)));
$('collapse').addEventListener('click', () => action(() => expanded(false)));
for (const id of ['new-recording', 'empty-record']) $(id).addEventListener('click', newRecording);
for (const id of ['cancel-create', 'back']) $(id).addEventListener('click', () => view('library'));
document.querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', () => view(button.dataset.tab)));
$('search').addEventListener('input', renderWorkflows);
$('refresh').addEventListener('click', () => action(refresh));
$('record-form').elements.whatsapp.addEventListener('change', (event) => { $('whatsapp-fields').hidden = !event.target.checked; });

$('record-form').addEventListener('submit', (event) => {
  event.preventDefault();
  action(async () => {
    const values = new FormData(event.target);
    const options = { name: values.get('name').trim(), apps: values.get('apps').split(',').map((app) => app.trim()).filter(Boolean) };
    if (values.has('whatsapp')) {
      options.recipient = values.get('recipient').trim();
      if (!options.recipient) throw new Error('Enter the contact name you will search for, or turn off WhatsApp replay.');
      options.chat_title = values.get('chat_title').trim() || options.recipient;
      options.message = values.get('message');
    }
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
  $('run-message').value = flow.message;
  $('approve-send').checked = false;
});
$('cancel-run').addEventListener('click', () => view('detail'));
$('run-mode').addEventListener('change', () => {
  const later = $('run-mode').value === 'later';
  $('schedule-label').hidden = !later;
  $('run-at').required = later;
  $('confirm-run').textContent = later ? 'Approve & schedule →' : 'Approve & run →';
});
$('run-form').addEventListener('submit', (event) => {
  event.preventDefault();
  action(async () => {
    if (!$('approve-send').checked) throw new Error('Review and approve the exact recipient and message first.');
    const later = $('run-mode').value === 'later';
    const payload = { id: state.detail.id, message: $('run-message').value, approved_recipient: $('run-recipient').value };
    if (later) {
      const at = new Date($('run-at').value);
      if (!Number.isFinite(at.getTime()) || at <= new Date()) throw new Error('Choose a future local date and time.');
      payload.at = at.toISOString();
    }
    await rpc(later ? 'schedule' : 'replay', payload);
    view('activity');
    await refresh();
    if (!later) await expanded(false);
    else notice('Scheduled locally. Keep DigitalSoul open and Windows awake/unlocked.');
  });
});
$('stop-all').addEventListener('click', () => action(async () => { await rpc('stop_jobs'); await refresh(); notice('Desktop stop is latched. Pending jobs were cancelled; an invocation already started cannot be undone.'); }));
$('resume-all').addEventListener('click', () => action(async () => { await rpc('resume_jobs'); notice('Desktop worker re-armed. Cancelled jobs stay cancelled.'); }));
$('delete-workflow').addEventListener('click', () => action(async () => {
  if (!window.confirm(`Delete “${state.detail.name}” and its recorded steps?`)) return;
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
      } else if (payload.event === 'replay_started') {
        state.running = true;
        $('compact-label').textContent = 'Replaying your workflow…';
        $('quit').disabled = true;
      } else if (payload.event === 'replay_finished') {
        state.running = false;
        $('quit').disabled = false;
        $('compact-label').textContent = 'Your work, remembered.';
        await refresh();
        await expanded(true);
        view('activity');
        notice(payload.job?.detail || 'Replay ended. Check its status and WhatsApp before retrying.');
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
  await rpc('hello');
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
