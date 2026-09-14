// Isolated browser QA with synthetic camera; no customer page, database or upload API.
const {chromium}=require(process.env.QA_NODE_MODULES+'/playwright');
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict');
const root=path.resolve('.'),output=path.join(root,'logs/customer-camera-qa');
const requests=[];
const server=http.createServer((req,res)=>{
  requests.push({method:req.method,url:req.url});
  if(req.method==='POST' && req.url.startsWith('/banle/khach-hang/chup-hinh/tach-cccd/')){
    const parts=[];req.on('data',part=>parts.push(part));req.on('end',()=>{
      requests.at(-1).bytes=Buffer.concat(parts).length;
      requests.at(-1).type=req.headers['content-type'];
      const apply=req.url.includes('mode=apply');
      res.setHeader('Content-Type',apply?'image/jpeg':'text/html; charset=utf-8');
      res.end(fs.readFileSync(path.join(output,apply?'card-rotated.jpg':'card-popup.html')));
    });return;
  }
  const name=req.url==='/'?path.join(output,'fixture.html'):path.resolve(root,'.'+req.url.split('?')[0]);
  if(req.method!=='GET' || (!name.startsWith(root+path.sep))){res.writeHead(403);res.end();return;}
  if(!fs.existsSync(name)){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.html')?'text/html; charset=utf-8':'application/octet-stream');
  res.end(fs.readFileSync(name));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({channel:'chrome',headless:true,args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']});
  try{
    const context=await browser.newContext({viewport:{width:1000,height:1000},permissions:['camera'],acceptDownloads:true});
    const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>{
      window.testStreams=[];
      const real=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia=async options=>{const s=await real(options);window.testStreams.push(s);return s;};
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const click=selector=>page.locator(selector).click();
    await click('[data-local-camera-open]');await page.waitForFunction(()=>!document.querySelector('[data-local-capture]').disabled);
    await click('[data-local-capture]');
    assert.equal(await page.evaluate(()=>window.testStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))),true);
    await click('[data-local-crop]');
    const box=await page.locator('[data-local-preview]').boundingBox();
    await page.mouse.move(box.x+box.width*.2,box.y+box.height*.2);await page.mouse.down();
    await page.mouse.move(box.x+box.width*.8,box.y+box.height*.8,{steps:5});await page.mouse.up();
    assert.equal(await page.locator('[data-local-save]').isDisabled(),true);
    await page.screenshot({path:path.join(output,'crop-desktop.png')});
    await click('[data-local-crop-apply]');
    const dimensions=(await page.locator('[data-local-message]').innerText()).match(/(\d+) × (\d+)/).slice(1).map(Number);
    const downloaded=page.waitForEvent('download');await click('[data-local-save]');
    const download=await downloaded;assert.match(download.suggestedFilename(),/^Hinh-chup-\d{8}-\d{6}-\d+\.jpg$/);
    await download.saveAs(path.join(output,'synthetic-cropped.jpg'));
    const data=[...fs.readFileSync(path.join(output,'synthetic-cropped.jpg'))];
    assert.deepEqual(await page.evaluate(async bytes=>{const img=await createImageBitmap(new Blob([new Uint8Array(bytes)],{type:'image/jpeg'}));return [img.width,img.height];},data),dimensions);
    await click('[data-local-original]');await click('[data-local-crop]');await click('[data-local-crop-cancel]');
    assert.equal(await page.locator('[data-local-save]').isEnabled(),true);
    await page.setViewportSize({width:390,height:844});await click('[data-local-crop]');
    await page.locator('[data-crop-edge=L]').fill('25');await page.locator('[data-crop-edge=L]').dispatchEvent('input');
    await page.screenshot({path:path.join(output,'crop-mobile.png')});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#kh-local-camera').isVisible(),false);
    await click('[data-local-camera-open]');await page.waitForFunction(()=>!document.querySelector('[data-local-capture]').disabled);
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>window.testStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))),true);
    const beforeCard=requests.filter(r=>r.method==='POST').length;assert.equal(beforeCard,0);
    await click('[data-local-camera-open]');await page.waitForFunction(()=>!document.querySelector('[data-local-capture]').disabled);
    await click('[data-local-capture]');await click('[data-local-card]');
    await page.locator('[data-local-card-apply]').waitFor();
    await page.setViewportSize({width:1100,height:950});
    await page.locator('[data-xoay="90"]').click();
    assert.equal(await page.locator('#th-cat-goc').inputValue(),'90');
    await page.waitForFunction(()=>{const t=getComputedStyle(document.getElementById('th-cat-khung')).transform;return t.startsWith('matrix(0, 1, -1, 0,');});
    await page.screenshot({path:path.join(output,'card-skill-popup.png')});
    await click('[data-local-card-apply]');
    await page.waitForFunction(()=>!document.querySelector('#kh-local-card-dialog').open);
    assert.match(await page.locator('[data-local-message]').innerText(),/738 × 1170/);
    assert.equal(await page.locator('[data-local-save]').isEnabled(),true);
    await page.keyboard.press('Escape');
    await page.evaluate(()=>{navigator.mediaDevices.getUserMedia=()=>Promise.reject(new DOMException('Denied','NotAllowedError'));});
    await click('[data-local-camera-open]');await page.waitForFunction(()=>document.querySelector('[data-local-message]').classList.contains('is-error'));
    assert.equal(await page.locator('[data-local-retake]').isVisible(),true);
    assert.equal(await page.locator('[data-local-capture]').isDisabled(),true);
    assert.deepEqual(errors,[]);
    const posts=requests.filter(r=>r.method==='POST');assert.equal(posts.length,2);
    assert.equal(posts.every(r=>r.type==='image/jpeg' && r.bytes>0),true);
    assert.equal(posts[0].bytes,posts[1].bytes);
    assert.equal(requests.some(r=>!/^\/(static\/|banle\/khach-hang\/chup-hinh\/tach-cccd\/|$|favicon.ico)/.test(r.url)),false);
    console.log(JSON.stringify({camera:'synthetic',cropDimensions:dimensions,download:download.suggestedFilename(),cameraTracksStopped:true,mobileOverflow:false,permissionErrorHandled:true,ordinaryCropUploads:beforeCard,explicitCardProcessingRequests:posts.length,cardRotation:'738x1170',pageErrors:errors},null,2));
    await context.close();
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
