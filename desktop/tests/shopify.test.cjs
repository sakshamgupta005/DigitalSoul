const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../ui/app.js'), 'utf8');

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
      if (!nodes.has(id)) nodes.set(id, {value: '', hidden: true, handlers: {},
        addEventListener(name, fn) { this.handlers[name] = fn; }});
      return nodes.get(id);
    },
    expanded: async value => { sizes.push(value); context.state.expanded = value; },
    rpc: async (method, payload) => {
      calls.push({method, payload});
      if (method === 'shopify_run') return await new Promise(resolve => { complete = resolve; });
    },
    action: fn => { context.state.pending = true; running = fn().finally(() => {context.state.pending = false;}); },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("$('toggle').addEventListener"), source.indexOf('// While recording the banner')), context);
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
