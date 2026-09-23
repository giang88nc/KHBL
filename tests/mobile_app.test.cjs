const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const flush=()=>new Promise(setImmediate);
function element(extra={}){return {hidden:false,textContent:'',dataset:{},events:{},addEventListener(name,fn){this.events[name]=fn;},...extra};}

async function faceid(standalone){
  const button=element(),open=element({hidden:true}),check=element(),status=element();
  const wait=element({hidden:true,querySelector:s=>s.includes('status')?status:s.includes('open')?open:check});
  const form=element({dataset:{pending:'0'},action:'/banle/mobile/auth/start/',querySelector:()=>button});
  const document=element({hidden:false,querySelector:()=>open,getElementById:id=>id==='mobile-faceid-start'?form:id==='mobile-faceid-wait'?wait:null});
  const win=element({matchMedia:()=>({matches:standalone})});
  const timers=[],calls=[],redirects=[];let result='pending';
  vm.runInNewContext(fs.readFileSync('static/js/mobile_faceid.js','utf8'),{
    document,window:win,navigator:{standalone:false},location:{replace:u=>redirects.push(u)},URL,Date,AbortController,
    FormData:class{set(){}},setTimeout:(fn,ms)=>{const t={fn,ms};timers.push(t);return t;},clearTimeout:t=>{if(t)t.cancelled=true;},
    fetch:async(url)=>{calls.push(url);return {ok:true,status:200,json:async()=>url.includes('start')?{authorize:'https://unopposable-parheliacal-waylon.ngrok-free.dev/banle/mobile/?ticket=t&app=1',created:Date.now()/1000}:{status:result}};}
  });
  await flush();let prevented=false;
  if(!standalone){await form.events.submit({preventDefault(){prevented=true;}});assert.equal(prevented,false);assert.equal(calls.length,0);return;}
  assert.equal(form.hidden,true);assert.equal(open.hidden,false);
  assert.match(open.href,/app=1/);assert.equal(calls.length,1);
  open.events.click({preventDefault(){prevented=true;}});
  assert.equal(prevented,false,'one native tap goes directly to the already prepared HTTPS URL');
  assert.equal(calls.length,1,'click must not fetch a new authorization URL or require another tap');
  document.hidden=true;await check.events.click();assert.equal(calls.length,1,'hidden app must not poll');
  document.hidden=false;await check.events.click();assert.equal(calls.length,2);
  result='success';await check.events.click();assert.deepEqual(redirects,['/banle/mobile/dashboard/']);
  await check.events.click();assert.equal(calls.length,3,'success stops polling');
}

async function connectivity(){
  const message=element(),retry=element(),banner=element({querySelector:s=>s.includes('message')?message:retry});
  const document=element({hidden:false,getElementById:()=>banner,documentElement:{style:{setProperty(){}},classList:{toggle(){}}}});
  const win=element();let healthy=false,calls=0;
  vm.runInNewContext(fs.readFileSync('static/js/mobile_app.js','utf8'),{
    document,window:win,navigator:{onLine:true},AbortController,
    setTimeout:()=>1,clearTimeout(){},fetch:async()=>{calls++;if(!healthy)throw Error('network');return {ok:true,json:async()=>({service:'khbl-mobile',ok:true})};}
  });
  await flush();assert.equal(banner.hidden,false);assert.match(message.textContent,/máy chủ KHBL/);
  healthy=true;await retry.events.click();assert.equal(banner.hidden,true);
  const before=calls;document.hidden=true;await retry.events.click();assert.equal(calls,before);
}

async function sharing(secure){
  const node=()=>element({style:{},setAttribute(){},append(){},remove(){},focus(){}});
  const save=node(),share=node();save.download='QR-test.png';
  let observer,shares=0,captured=null,previewCount=0;
  const qr={decode:async()=>{}};
  const frame={after(){}};
  const modal={querySelector:s=>s.includes('save')?save:s.includes('share')?share:null,append(){previewCount++;}};
  const panel={isConnected:true,closest:()=>modal,querySelector:s=>s.includes('frame')?frame:qr};
  const root={querySelector:()=>panel};
  vm.runInNewContext(fs.readFileSync('static/js/mobile_qr_share.js','utf8'),{
    document:{getElementById:()=>root,createElement:()=>node(),fonts:{ready:Promise.resolve()},addEventListener(){},removeEventListener(){}},
    window:{isSecureContext:secure,html2canvas:async target=>{captured=target;return {toBlob:fn=>fn({}),toDataURL:()=> 'data:image/png;base64,FRAME'};}},
    navigator:{userAgent:'iPhone',canShare:()=>true,share:async()=>{shares++;}},
    File:class{constructor(parts,name){this.name=name;}},
    MutationObserver:class{constructor(fn){observer=fn;}observe(){}disconnect(){}}
  });
  await flush();assert.equal(captured,frame,'must capture full frame, not QR img');
  assert.equal(share.hidden,!secure);
  await save.events.click({preventDefault(){}});
  if(secure)assert.equal(shares,1);else assert.equal(previewCount,1,'HTTP iPhone opens save-image preview');
  panel.querySelector=()=>null;observer();assert.equal(share.hidden,true);assert.equal(save.hidden,true);
}

(async()=>{await faceid(false);await faceid(true);await connectivity();await sharing(false);await sharing(true);console.log('PASS: standalone auth, separate-cookie protocol UI, hidden/finished polling, connectivity recovery, HTTP/HTTPS QR sharing fallback');})().catch(e=>{console.error(e);process.exitCode=1;});
