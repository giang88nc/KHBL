const {chromium} = require(process.env.QA_NODE_MODULES + '/playwright');
const path = require('path');
const {pathToFileURL} = require('url');
const fs = require('fs');
(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const page = await browser.newPage({viewport:{width:1000,height:1100}});
  const results=[];
  for (const name of ['normal','many','empty','overflow']) {
    await page.goto(pathToFileURL(path.resolve('logs/deposit-print-qa/'+name+'.html')).href);
    await page.waitForFunction(() => document.body.dataset.printReady==='true');
    const result=await page.evaluate(() => ({
      ready:document.body.dataset.printReady,
      status:document.getElementById('print-status').textContent,
      pages:document.querySelectorAll('.deposit-paper').length,
      rows:document.querySelectorAll('[data-customer-items] tr').length,
      disabled:document.getElementById('print-receipt').disabled,
      identical:[...document.querySelectorAll('.deposit-paper')].every(p=>{
        const copies=[...p.querySelectorAll('[data-store-copy]')];
        return copies[0].textContent === copies[1].textContent;
      }),
      fits:[...document.querySelectorAll('[data-fit-area]')].every(p=>p.scrollHeight<=p.clientHeight+1),
    }));
    results.push({name,...result});
    if (result.ready!=='true'||result.disabled||!result.identical) throw Error(JSON.stringify(result));
    if (['normal','empty'].includes(name)&&(result.ready!=='true'||!result.fits)) throw Error(JSON.stringify(result));
    if (name==='overflow'&&(result.pages<2||result.rows!==100)) throw Error('Oversized receipt must keep all rows across preview pages');
    if (name==='many' && result.rows!==16) throw Error('Lost product rows');
    if(result.pages>1){
      await page.evaluate(()=>{window.print=()=>{window.printCalled=true;};});
      await page.click('#print-receipt');
      if(!await page.evaluate(()=>window.printCalled))throw Error('Overflow receipt print is blocked');
      await page.emulateMedia({media:'print'});
      if(await page.locator('[data-continuation]:visible').count())throw Error('Continuation pages must not print');
      const pdf=await page.pdf({format:'A5',path:`logs/deposit-print-qa/${name}-first-page.pdf`,printBackground:true});
      if((pdf.toString('latin1').match(/\/Type\s*\/Page\b/g)||[]).length!==1)throw Error('Overflow receipt output must contain exactly page one');
      if(!await page.locator('#receipt-pages').isVisible())throw Error('First page is hidden');
      await page.emulateMedia({media:'screen'});
    }
    if (name==='normal') {
      if (result.pages!==1) throw Error('Three standard products should fit one sheet');
      const customer = await page.locator('.customer-copy').innerText();
      if (!customer.includes('******4567') || customer.includes('0901234567')) throw Error('Customer phone must be masked');
      if (!(await page.locator('[data-store-copy]').first().innerText()).includes('0901234567')) throw Error('Store keeps full phone');
      await page.locator('.deposit-paper').first().screenshot({path:'logs/deposit-print-qa/preview.png'});
      await page.emulateMedia({media:'print'});
      await page.evaluate(()=>window.dispatchEvent(new Event('beforeprint')));
      if(await page.locator('body').getAttribute('data-print-ready')!=='true'||!await page.locator('#receipt-pages').isVisible())throw Error('Print preflight hid a valid receipt');
      const bg=await page.locator('.deposit-paper').first().evaluate(p=>getComputedStyle(p).backgroundImage);
      if(bg!=='none') throw Error('Preprinted background must not be printed');
      await page.locator('.deposit-paper').first().screenshot({path:'logs/deposit-print-qa/ink-only.png'});
      const pdf=await page.pdf({format:'A5',path:'logs/deposit-print-qa/normal.pdf',printBackground:true});
      const count=(pdf.toString('latin1').match(/\/Type\s*\/Page\b/g)||[]).length;
      if(count!==1)throw Error(`Expected one PDF page, got ${count}`);
      const fields=await page.evaluate(()=>({signature:!!document.querySelector('.customer-signatures'),
        below:document.querySelector('.customer-employee').getBoundingClientRect().top>=document.querySelector('.customer-reminder').getBoundingClientRect().bottom,
        stores:[...document.querySelectorAll('.store-copy')].every(el=>el.querySelector('.store-deposit').textContent.includes('1.500.000')&&el.querySelector('.store-employee').textContent.includes('Trần Ngọc Phụng'))}));
      if(fields.signature||!fields.below||!fields.stores)throw Error(JSON.stringify(fields));
      await page.emulateMedia({media:'screen'});
    }
  }
  console.log(JSON.stringify(results,null,2));
  let saved=null;
  await page.route('http://deposit-preview.test/**',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(req.method()==='POST'){
      saved=req.postDataJSON().layout;
      return route.fulfill({json:{ok:true,layout:saved}});
    }
    if(url.pathname.startsWith('/static/')){
      const file=path.resolve('.'+url.pathname);
      if(!file.startsWith(path.resolve('static')+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:''});
      return route.fulfill({path:file});
    }
    const name=url.pathname==='/sample/'?'normal':'config';
    const body=fs.readFileSync('logs/deposit-print-qa/'+name+'.html','utf8').replaceAll(pathToFileURL(path.resolve('static')).href+'/','/static/');
    return route.fulfill({contentType:'text/html',body});
  });
  await page.setViewportSize({width:1600,height:1100});
  await page.goto('http://deposit-preview.test/he-thong/mau-in-coc/');
  await page.waitForFunction(()=>!document.getElementById('dc-layout-print').disabled);
  const frame=page.frameLocator('#dc-layout-frame');
  const top=page.locator('[data-key="employee"] [data-p="top"]');
  await top.fill('58');
  await frame.locator('.customer-employee').evaluate(el=>new Promise((resolve,reject)=>{
    const start=Date.now();const poll=()=>{if(el.style.top==='58%'||Math.abs(el.offsetTop/el.closest('.deposit-paper').offsetHeight*100-58)<.2)resolve();else if(Date.now()-start>2000)reject(Error('Preview did not update'));else requestAnimationFrame(poll);};poll();
  }));
  await page.click('#dc-layout-save');
  await page.waitForFunction(()=>document.getElementById('toast-root').textContent.includes('Đã lưu mẫu'));
  if(saved?.employee.top!==58)throw Error('Configuration did not post current layout');
  await page.click('#dc-layout-reset');
  if(await top.inputValue()!=='57.5')throw Error('Reset did not restore defaults');
  await page.waitForFunction(()=>!document.getElementById('dc-layout-print').disabled);
  await page.screenshot({path:'logs/deposit-print-qa/config.png',fullPage:true});
  await page.locator('[data-key="items"] [data-p="h"]').fill('1');
  await page.waitForFunction(()=>document.getElementById('dc-layout-status').textContent.includes('Chỉ in trang 1'));
  if(await page.locator('#dc-layout-print').isDisabled())throw Error('Config print must remain available for overflow');
  console.log('Config iframe, live positioning, save payload, reset: OK');
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
