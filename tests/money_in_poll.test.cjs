const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const code=fs.readFileSync('static/js/money_flow_transfer.js','utf8');
async function run(state){
  const timers=[];let calls=0,refresh=0;
  const small={textContent:''},status={textContent:'',querySelector:()=>small};
  const frame={replaceChildren(){},append(){}};
  const panel={querySelector:s=>s==='.transfer-processing'?status:frame,closest:()=>({querySelector:()=>({})})};
  const form={dataset:{pawnPoll:'/test'},isConnected:true,closest:()=>panel};
  const document={getElementById:id=>id==='mf-payment-form'?null:{},querySelector:s=>s.includes('data-pawn-poll')?form:null,
                  createElement:()=>({style:{}}),dispatchEvent(){refresh++;}};
  vm.runInNewContext(code,{document,location:{pathname:'/banle/mobile/money-in/'},Number,Date,AbortController,FormData:class{},CustomEvent:class{},
    MutationObserver:class{observe(){} disconnect(){}},
    setTimeout:(fn,ms)=>{const t={fn,ms};timers.push(t);return t;},clearTimeout:t=>{t.cancelled=true;},
    fetch:async()=>{calls++;return {status:200,json:async()=>({status:state,received:'1000'})};}});
  await new Promise(setImmediate);
  return {form,timers,status,get calls(){return calls;},get refresh(){return refresh;}};
}
(async()=>{
  const pending=await run('waiting');
  assert.equal(pending.calls,1);
  const retry=pending.timers.find(t=>t.ms===1000);assert.ok(retry);
  pending.form.isConnected=false;retry.fn();await new Promise(setImmediate);
  assert.equal(pending.calls,1,'closed popup must not issue another request');
  const done=await run('success');
  assert.match(done.status.textContent,/THÀNH CÔNG/);
  assert.equal(done.refresh,1);
  assert.equal(done.timers.filter(t=>t.ms===1000).length,0,'success stops polling');
  console.log('PASS: 1-second polling, stop-on-close, stop-on-success, list refresh');
})().catch(e=>{console.error(e);process.exitCode=1;});
