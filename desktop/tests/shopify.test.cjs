const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../ui/app.js'), 'utf8');

// A DOM element stand-in with the harmless methods app.js calls (ghost/animation code shares this slice).
function fakeNode() {
  return {value: '', hidden: true, handlers: {}, dataset: {}, textContent: '', children: [],
    addEventListener(name, fn) { this.handlers[name] = fn; }, removeEventListener() {},
    setAttribute() {}, removeAttribute() {}, getAttribute: () => null, focus() {}, blur() {},
    querySelector: () => fakeNode(), querySelectorAll: () => [], closest: () => null,
    append() {}, appendChild() {}, replaceChildren() {}, remove() {},
    classList: {add() {}, remove() {}, toggle() {}, contains: () => false},
    style: {setProperty() {}, removeProperty() {}},
    getBoundingClientRect: () => ({left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0})};
}

function harness() {
  const nodes = new Map();
  const sizes = [];
  const calls = [];
  let running;
  let complete;
  const context = {
    state: {pending: false, expanded: true}, dragged: false,
    IDLE_LABEL: 'Idle', notice: () => {}, syncTheme: () => {}, view: () => {},
    $: id => {
      if (!nodes.has(id)) nodes.set(id, fakeNode());
      return nodes.get(id);
    },
    expanded: async value => { sizes.push(value); context.state.expanded = value; },
    rpc: async (method, payload) => {
      calls.push({method, payload});
      if (method === 'shopify_run') return await new Promise(resolve => { complete = resolve; });
    },
    action: fn => { context.state.pending = true; running = fn().finally(() => {context.state.pending = false;}); },
    // Browser globals used by the ghost/animation code sharing this slice of app.js; inert stand-ins.
    tauri: undefined,
    document: {addEventListener() {}, querySelectorAll: () => [], querySelector: () => null, hasFocus: () => true, hidden: false},
    window: {addEventListener() {}, removeEventListener() {}, matchMedia: () => ({matches: false})},
    matchMedia: () => ({matches: false}),
    performance: {now: () => 0},
    requestAnimationFrame: () => 0, cancelAnimationFrame() {},
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
  };
  vm.createContext(context);
  // Just the toggle click handler: from its start to the first line that closes it at column 0.
  const toggle = source.indexOf("$('toggle').addEventListener");
  vm.runInContext(source.slice(toggle, source.indexOf('\n});', toggle) + 4), context);
  vm.runInContext(source.slice(source.indexOf('function openShopifyWorkflow'), source.indexOf("$('review-run').addEventListener")), context);
  return {context, nodes, sizes, calls, running: () => running,
    complete: result => complete(result)};
}

test('Shopify button opens its form and preserves zero inventory', () => {
  const ui = harness();
  ui.nodes.get('new-shopify').handlers.click();
  assert.equal(ui.nodes.get('approve-shopify-view').hidden, false);
  ui.context.openShopifyWorkflow({inventory_quantity: 0});
  assert.equal(ui.nodes.get('shopify-qty').value, 0);
});

test('notch opens while Shopify waits; cancel remains available and submission does not collapse it', async () => {
  const ui = harness();
  ui.context.openShopifyWorkflow();
  ui.nodes.get('shopify-form').handlers.submit({preventDefault() {}});
  assert.equal(ui.context.state.pending, true);
  assert.deepEqual(ui.sizes, []);
  assert.equal(ui.nodes.get('shopify-cancel').hidden, false);
  ui.context.state.expanded = false;
  await ui.nodes.get('toggle').handlers.click();
  assert.deepEqual(ui.sizes, [true]);
  await ui.nodes.get('shopify-cancel').handlers.click();
  assert.equal(ui.calls.at(-1).method, 'shopify_cancel');
  ui.complete({ok: false, errors: ['Connection declined']});
  await ui.running();
  assert.equal(ui.context.state.pending, false);
  assert.equal(ui.nodes.get('shopify-cancel').hidden, true);
});
