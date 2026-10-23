"""Browser-use integration fixture: no external form or submitted response."""
import asyncio
import os
from urllib.parse import quote
import pytest
from agent.ghost.browser.gform_fields import operate

HTML = '''<html><title>Controlled Form</title><body>
<div role="listitem" data-item-id="name"><div role="heading">Full name</div><input required></div>
<div role="listitem" data-item-id="skills"><div role="heading">Skills</div>
<button role="checkbox" aria-label="Python" aria-checked="false" onclick="this.setAttribute('aria-checked',this.getAttribute('aria-checked')==='true'?'false':'true')">Python</button>
<button role="checkbox" aria-label="Java" aria-checked="true" onclick="this.setAttribute('aria-checked',this.getAttribute('aria-checked')==='true'?'false':'true')">Java</button></div>
<div role="listitem" data-item-id="choice"><div role="heading">Color</div><select><option>Red</option><option>Blue</option></select></div>
<div role="listitem" data-item-id="duplicate1"><div role="heading">Name</div><input></div>
<div role="listitem" data-item-id="duplicate2"><div role="heading">Name</div><input></div>
<button>Submit</button></body></html>'''

@pytest.mark.skipif(os.environ.get('DIGITALSOUL_GFORM_LIVE') != '1', reason='Explicit local browser integration')
def test_real_browser_field_readback():
    from browser_use import Browser
    from agent.ghost.drivers.browser import browser_executable
    async def run():
        browser = Browser(headless=True, executable_path=browser_executable(), use_cloud=False,
                          enable_default_extensions=False, auto_download_pdfs=False)
        await browser.start()
        try:
            page=await browser.new_page()
            await page.goto('data:text/html,'+quote(HTML))
            state=await operate(page,'inspect')
            assert len(state['questions'])==5
            result=await operate(page,'fill',signature=state['signature'],items=[
                {'id':'name','title':'Full name','value':'Test Person'},
                {'id':'skills','title':'Skills','value':['Python']},
                {'id':'choice','title':'Color','value':'Blue'}])
            assert len(result['steps'])==3
            fresh=await operate(page,'inspect')
            assert fresh['values'][:3]==[['Test Person'],['Python'],'Blue']
            with pytest.raises(Exception):
                await operate(page,'fill',signature=fresh['signature'],items=[{'title':'Name','value':'Wrong'}])
            assert (await operate(page,'inspect'))['values'][3:]==[[''],['']]
            with pytest.raises(Exception):
                await operate(page,'fill',signature='stale',items=[])
        finally: await browser.kill()
    asyncio.run(run())
