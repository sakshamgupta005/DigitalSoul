const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run the actual voice handlers with native IPC and DOM surfaces substituted.
function harness(fail = false) {
  const nodes = new Map();
  const sizes = [];
  const methods = [];
  const details = [];
  const context = {
    state: { recording: false, running: false },
    $: (id) => {
      if (!nodes.has(id)) nodes.set(id, {
        textContent: '', attributes: {}, handlers: {},
        setAttribute(name, value) { this.attributes[name] = value; },
        addEventListener(name, fn) { this.handlers[name] = fn; },
      });
      return nodes.get(id);
    },
    expanded: async (value) => sizes.push(value),
    rpc: async (method) => { methods.push(method); if (fail) throw new Error('Backend unavailable'); },
    notice: () => {},
    showDetail: async (id) => details.push(id),
  };
  const source = fs.readFileSync(path.join(__dirname, '../ui/app.js'), 'utf8');
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('let voiceOn = false;'), source.indexOf("$('voice-form').addEventListener")), context);
  return { context, nodes, sizes, methods, details, click: () => nodes.get('voice-mic').handlers.click(),
    event: (result) => context.voiceResult(result) };
}

test('mic loading, error, and wake stay compact', async () => {
  const ui = harness();
  await ui.click();
  ui.context.voiceState(false);
  await ui.event({ kind: 'error', code: 'dependencies_missing', message: 'Run setup-voice.ps1' });
  ui.context.voiceState(false); // Worker exit must preserve the error status.
  assert.match(ui.nodes.get('compact-label').textContent, /Voice setup needed/);
  await ui.event({ kind: 'wake', message: "I'm listening" });
  assert.deepEqual(ui.sizes, [false]);
  assert.deepEqual(ui.methods, ['voice_start']);
});

test('mic RPC failure stays compact', async () => {
  const ui = harness(true);
  await ui.click();
  assert.deepEqual(ui.sizes, [false]);
  assert.match(ui.nodes.get('compact-label').textContent, /Voice unavailable/);
});

test('matched workflow waits for manual expansion and review', async () => {
  const ui = harness();
  await ui.click();
  await ui.event({ kind: 'workflow', id: 'saved-1', name: 'Morning music' });
  assert.deepEqual(ui.details, ['saved-1']);
  assert.deepEqual(ui.sizes, [false]);
  assert.deepEqual(ui.methods, ['voice_start']); // No replay approval from speech.
  assert.equal(ui.nodes.get('compact-label').textContent, 'Ready: Morning music');
});

test('heard speech and app launch feedback stay inside notch', async () => {
  const ui = harness();
  await ui.click();
  await ui.event({ kind: 'heard', text: 'Open WhatsApp' });
  assert.equal(ui.nodes.get('voice-text').value, 'Open WhatsApp');
  await ui.event({ kind: 'app_opened', app: 'whatsapp', message: 'Opening WhatsApp' });
  ui.context.voiceState(false);
  assert.equal(ui.nodes.get('compact-label').textContent, 'Opening WhatsApp');
  assert.deepEqual(ui.sizes, [false]);
  assert.deepEqual(ui.methods, ['voice_start']);
});
