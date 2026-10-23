(data) => (async () => {
  const visible = e => e && e.getClientRects().length && !e.disabled && e.getAttribute('aria-hidden') !== 'true';
  const norm = s => String(s ?? '').replace(/\s+/g, ' ').trim();
  const label = e => norm(e.getAttribute('data-value') || e.getAttribute('aria-label') || e.innerText || e.value);
  const pause = () => new Promise(r => setTimeout(r, 120));
  const roots = [...document.querySelectorAll('[role="listitem"], .Qr7Oae')].filter(visible)
    .filter(e => e.querySelector('input, textarea, [role="radio"], [role="checkbox"], [role="listbox"], select'));
  const blocks = roots.filter(e => !roots.some(other => other !== e && e.contains(other)));
  const describe = (b,i) => {
    const input = b.querySelector('input[name^="entry."], input[name="emailAddress"]');
    const title = norm(b.querySelector('[role="heading"], .M7eMe')?.innerText || b.getAttribute('aria-label'));
    const radios = [...b.querySelectorAll('[role="radio"], input[type="radio"]')].filter(visible);
    const checks = [...b.querySelectorAll('[role="checkbox"], input[type="checkbox"]')].filter(visible);
    const text = [...b.querySelectorAll('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"]), textarea')].filter(visible);
    const grid = [...b.querySelectorAll('[role="row"]')].some(r=>r.querySelector('[role="radio"], [role="checkbox"]'));
    let type = b.querySelector('input[type="file"]') ? 'upload' : grid ? 'grid' : radios.length ? 'radio' : checks.length ? 'checkbox' : b.querySelector('[role="listbox"], select') ? 'dropdown' : text.length === 1 ? text[0].type || 'text' : 'unsupported';
    const id = b.getAttribute('data-item-id') || input?.name || '';
    return {id: id || 'title:'+title, title, type, required: !!b.querySelector('[aria-required="true"], [required]') || /\*\s*$/.test(title), options: [...radios,...checks,...b.querySelectorAll('[role="option"], option')].map(label), rows: grid ? [...b.querySelectorAll('[role="row"]')].filter(r=>r.querySelector('[role="radio"], [role="checkbox"]')).map(r=>({title:norm(r.getAttribute("aria-label") || r.querySelector('[role="rowheader"]')?.textContent), options:[...r.querySelectorAll('[role="radio"], [role="checkbox"]')].map(label), multiple:!!r.querySelector('[role="checkbox"]')})) : [], index:i};
  };
  const questions = blocks.map(describe);
  const buttons = [...document.querySelectorAll('[role="button"], button')].filter(visible);
  const button = name => buttons.filter(e => norm(e.innerText || e.getAttribute('aria-label')).toLowerCase() === name);
  const signature = JSON.stringify(questions.map(q=>[q.id,q.title,q.type,q.options]));
  const read = b => {
    const rows=[...b.querySelectorAll('[role="row"]')].filter(r=>r.querySelector('[role="radio"], [role="checkbox"]'));
    if(rows.length)return rows.map(r=>[norm(r.getAttribute('aria-label')||r.querySelector('[role="rowheader"]')?.textContent), [...r.querySelectorAll('[aria-checked="true"]')].map(label).sort()]);
    const choice=[...b.querySelectorAll('[role="radio"], [role="checkbox"], input[type="radio"], input[type="checkbox"]')].filter(visible);
    if(choice.length) return choice.filter(e=>e.checked || e.getAttribute('aria-checked')==='true').map(label).sort();
    const select=b.querySelector('select'); if(select)return select.selectedOptions[0]?.textContent.trim();
    const list=b.querySelector('[role="listbox"]'); if(list)return [...list.querySelectorAll('[role="option"]')].filter(e=>e.getAttribute('aria-selected')==='true').map(label);
    return [...b.querySelectorAll('input:not([type="hidden"]), textarea')].filter(visible).map(e=>e.value);
  };
  const snapshot = () => ({title:document.title, url:location.href, questions, signature, values:blocks.map(read), next:button('next').length===1 || button('continue').length===1, submit:button('submit').length===1, errors:[...document.querySelectorAll('[role="alert"], [aria-invalid="true"]')].filter(visible).map(e=>norm(e.innerText)||'Invalid answer'), confirmed:!!document.querySelector('.freebirdFormviewerViewResponseConfirmationMessage, .vHW8K') && /formResponse/.test(location.pathname)});
  if(data.operation==='inspect')return snapshot();
  if(data.signature !== signature)throw Error('Form changed; inspect and review again');
  if(data.operation==='next' || data.operation==='submit') {
    if(snapshot().errors.length)throw Error('Resolve form validation errors first');
    const matches=data.operation==='submit'?button('submit'):[...button('next'),...button('continue')];
    if(matches.length!==1)throw Error('Navigation control is missing or ambiguous');
    matches[0].click(); return {clicked:true};
  }
  if(data.operation!=='fill')throw Error('Unsupported operation');
  const reports=[];
  for(const item of data.items || []) {
    const matches=questions.filter(q=>item.id ? q.id===item.id : q.title===item.title);
    if(matches.length!==1)throw Error('Question missing or ambiguous: '+item.title);
    const q=matches[0], b=blocks[q.index], value=item.value;
    if(['upload','unsupported','file'].includes(q.type))throw Error('Manual input required: '+q.title+' ('+q.type+')');
    if(q.type==='grid') {
      if(!value || typeof value!=='object' || Array.isArray(value))throw Error('Grid answers required');
      const rows=[...b.querySelectorAll('[role="row"]')].filter(r=>r.querySelector('[role="radio"], [role="checkbox"]'));
      if(q.rows.some(r=>!r.title) || new Set(q.rows.map(r=>r.title)).size!==q.rows.length)throw Error('Grid row labels missing or ambiguous');
      for(let i=0;i<rows.length;i++) {
        const spec=q.rows[i], wanted=spec.multiple?value[spec.title]:[value[spec.title]];
        if(!Array.isArray(wanted) || wanted.some(v=>typeof v!=='string'))throw Error('Answer every grid row');
        const cs=[...rows[i].querySelectorAll('[role="radio"], [role="checkbox"]')];
        for(const v of wanted)if(cs.filter(e=>label(e)===v).length!==1)throw Error('Grid option missing or ambiguous');
        for(const e of cs){const selected=e.getAttribute('aria-checked')==='true', want=wanted.includes(label(e));if(selected!==want&&(spec.multiple||want))e.click();}
        await pause();
        const got=cs.filter(e=>e.getAttribute('aria-checked')==='true').map(label).sort();
        if(JSON.stringify(got)!==JSON.stringify([...new Set(wanted)].sort()))throw Error('Grid verification failed');
      }
    } else if(q.type==='radio' || q.type==='checkbox') {
      const wanted=q.type==='checkbox'?value:[value];
      if(!Array.isArray(wanted) || wanted.some(v=>typeof v!=='string'))throw Error('Checkbox answers must be a list: '+q.title);
      const cs=[...b.querySelectorAll(q.type==='radio'?'[role="radio"], input[type="radio"]':'[role="checkbox"], input[type="checkbox"]')].filter(visible);
      for(const v of wanted)if(cs.filter(e=>label(e)===v).length!==1)throw Error('Option missing or ambiguous: '+q.title);
      for(const e of cs) {
        const selected=!!e.checked || e.getAttribute('aria-checked')==='true', want=wanted.includes(label(e));
        if(selected!==want && (q.type==='checkbox'||want))e.click();
      }
      await pause();
      if(JSON.stringify(read(b))!==JSON.stringify([...new Set(wanted)].sort()))throw Error('Selection verification failed: '+q.title);
    } else if(q.type==='dropdown') {
      const native=b.querySelector('select');
      if(native){const opts=[...native.options].filter(e=>norm(e.textContent)===value);if(opts.length!==1)throw Error('Option missing or ambiguous');native.value=opts[0].value;native.dispatchEvent(new Event('change',{bubbles:true}));}
      else {b.querySelector('[role="listbox"]').click();await pause();const opts=[...b.querySelectorAll('[role="option"]')].filter(e=>visible(e)&&label(e)===value);if(opts.length!==1)throw Error('Option missing or ambiguous');opts[0].click();}
      await pause(); const got=read(b);if(!(got===value || Array.isArray(got)&&got.length===1&&got[0]===value))throw Error('Dropdown verification failed');
    } else {
      if(typeof value!=='string' && typeof value!=='number')throw Error('Text answer required');
      const inputs=[...b.querySelectorAll('input:not([type="hidden"]), textarea')].filter(visible);
      if(inputs.length!==1)throw Error('Composite field requires manual input: '+q.title);
      const input=inputs[0], proto=input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto,'value').set.call(input,String(value));
      for(const name of ['input','change','blur'])input.dispatchEvent(new Event(name,{bubbles:true}));
      await pause();if(input.value!==String(value)||!input.checkValidity())throw Error('Value verification failed: '+q.title);
    }
    reports.push({id:q.id, question:q.title, value, key:item.key||'', verified:true});
  }
  return {steps:reports, ...snapshot()};
})()
