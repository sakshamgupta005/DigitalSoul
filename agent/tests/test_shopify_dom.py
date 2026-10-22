"""Opt-in browser-use DOM integration against synthetic product forms."""
import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agent.ghost.api.shopify_routes import SHOPIFY_POLARIS_HTML
from agent.ghost.browser.shopify_actor import FILL_SCRIPT


@pytest.mark.skipif(not os.environ.get("DIGITALSOUL_TEST_CDP_URL"), reason="Requires an explicit local test-browser CDP endpoint")
def test_live_shadow_title_revealed_fields_and_status_picker():
    async def check():
        from browser_use import Browser
        browser = Browser(cdp_url=os.environ["DIGITALSOUL_TEST_CDP_URL"], keep_alive=True,
                          enable_default_extensions=False, auto_download_pdfs=False)
        page = None
        try:
            await browser.start()
            page = await browser.new_page()
            await page.evaluate("(html) => {document.open();document.write(html);document.close();}", SHOPIFY_POLARIS_HTML)
            await page.evaluate('''() => {
                const title=document.querySelector('#product-title');
                const host=document.createElement('s-internal-text-field');
                title.before(host); host.attachShadow({mode:'open'}).append(title);
                for (const [id,text] of [['pricing-compare-at','Compare-at'],['pricing-cost','Cost per item'],['inventory-sku','SKU']]) {
                    const input=document.getElementById(id); input.style.display='none';
                    const button=document.createElement('button');button.type='button';button.textContent=text;
                    button.onclick=()=>{input.style.display='block';};input.before(button);
                }
                const status=document.querySelector('#product-status');status.remove();
                const field=document.createElement('s-internal-single-picker-field');field.setAttribute('label','Status');
                field.textContent='Active';field.style.display='block';
                const popup=document.createElement('div');popup.style.display='none';
                for(const value of ['Active','Draft']) {
                    const option=document.createElement('s-internal-picker-option');option.setAttribute('value',value.toUpperCase());
                    const button=document.createElement('button');button.type='button';button.textContent=value;
                    button.onclick=()=>{field.textContent=value;popup.style.display='none';};option.append(button);popup.append(option);
                }
                field.onclick=()=>{popup.style.display='block';};document.body.append(field,popup);
                const vendor=document.createElement('s-internal-single-picker-field');
                document.querySelector('#product-vendor')?.remove();
                vendor.setAttribute('label','Vendor');vendor.setAttribute('commandfor','fixture-vendor-popup');
                vendor.style.display='block';vendor.textContent='None';
                const vendorPopup=document.createElement('s-popover');vendorPopup.id='fixture-vendor-popup';
                vendorPopup.style.display='none';
                const search=document.createElement('input');search.type='search';vendorPopup.append(search);
                const vendorOption=document.createElement('s-internal-picker-option');vendorOption.textContent='Fixture vendor';
                vendorOption.style.display='block';vendorPopup.append(vendorOption);
                vendorOption.onclick=()=>{vendor.textContent='Fixture vendor';vendorPopup.style.display='none';};
                vendor.onclick=()=>{vendorPopup.style.display='block';};document.body.append(vendor,vendorPopup);
            }''')
            product = {"title": "Synthetic shadow product", "description": "<p>Fixture description.</p>",
                       "price": "89.00", "compare_at_price": "99.00", "cost_per_item": "30.00",
                       "sku": "TEST-001", "inventory_quantity": 0, "category": "Fixture category",
                       "vendor": "Fixture vendor", "tags": "fixture", "status": "Draft"}
            report = json.loads(await page.evaluate(FILL_SCRIPT, product))
            assert report["ok"], report
            assert {step["field"] for step in report["steps"]} == set(product)
            actual = json.loads(await page.evaluate('''() => JSON.stringify({
                title:document.querySelector('s-internal-text-field').shadowRoot.querySelector('input').value,
                price:document.querySelector('[name="price"]').value,
                sku:document.querySelector('[name="sku"]').value,
                quantity:document.querySelector('[name="quantity"]').value,
                status:document.querySelector('s-internal-single-picker-field').textContent
            })'''))
            assert actual == {"title": product["title"], "price": "89.00", "sku": "TEST-001", "quantity": "0", "status": "Draft"}
        finally:
            if page:
                await browser.close_page(page)
            await browser.stop()
    asyncio.run(check())


@pytest.mark.skipif(not os.environ.get("DIGITALSOUL_TEST_CDP_URL"), reason="Requires an explicit local test-browser CDP endpoint")
def test_live_notch_request_fills_product_through_real_stdio_bridge(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = SHOPIFY_POLARIS_HTML.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = dict(os.environ, DIGITALSOUL_EDGE_CDP_URL=os.environ['DIGITALSOUL_TEST_CDP_URL'],
               ANONYMIZED_TELEMETRY='false')
    diagnostic = "import faulthandler,runpy; faulthandler.dump_traceback_later(12); runpy.run_module('agent.ghost.desktop.notch_bridge',run_name='__main__')"
    process = subprocess.Popen([sys.executable, '-u', '-c', diagnostic, str(tmp_path)],
                               cwd=Path(__file__).resolve().parents[2], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', env=env)
    replies = queue.Queue()
    stdout_lines, stderr_lines = [], []
    def read():
        for line in process.stdout:
            stdout_lines.append(line)
            try:
                replies.put(json.loads(line))
            except ValueError:
                pass
    threading.Thread(target=read, daemon=True).start()
    def read_errors():
        for line in process.stderr:
            stderr_lines.append(line)
    threading.Thread(target=read_errors, daemon=True).start()
    product = {'title':'Notch bridge test - unsaved', 'description':'<p>Synthetic product.</p>',
               'price':'19.00', 'compare_at_price':'29.00', 'cost_per_item':'8.00', 'sku':'NOTCH-TEST',
               'inventory_quantity':0, 'category':'Fixture category', 'tags':'fixture', 'status':'Draft',
               'store_url':f'http://127.0.0.1:{server.server_port}/api/v1/shopify/test-form'}
    try:
        for message in [{'id':'fill','method':'shopify_run','payload':{'product':product,'click_save':False,'headless':False}},
                        {'id':'responsive','method':'hello'}]:
            process.stdin.write(json.dumps(message)+'\n')
        process.stdin.flush()
        results = []
        while not any(r.get('id') == 'fill' for r in results):
            try:
                results.append(replies.get(timeout=35))
            except queue.Empty:
                raise AssertionError({'responses':results, 'stdout':stdout_lines[-8:], 'stderr':stderr_lines[-55:]})
        fill = next(r for r in results if r.get('id') == 'fill')
        assert fill['ok'], fill
        assert fill['result']['ok'], fill
        assert len(fill['result']['filled_steps']) == 10
        assert fill['result']['save'] == {}
        assert next(i for i,r in enumerate(results) if r.get('id') == 'responsive') < next(i for i,r in enumerate(results) if r.get('id') == 'fill')
    finally:
        process.stdin.write('{"method":"shutdown"}\n')
        process.stdin.flush()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        server.shutdown()
        server.server_close()
