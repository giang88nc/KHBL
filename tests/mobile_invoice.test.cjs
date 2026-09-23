const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function setup(cash){
const input={value:'0',events:{},focus(){},addEventListener(k,f){this.events[k]=f;},setCustomValidity(v){this.error=v;}};
const action=()=>({addEventListener(k,fn){this.click=fn;}}),exact=action(),clear=action();
const hidden={value:'0'},buttons=[],label={},change={};
const f={dataset:{cash:String(cash)},elements:{tender:hidden},querySelector:s=>({'[data-tender]':input,'[data-quick]':{append:b=>buttons.push(b)},'[data-change-label]':label,'[data-change]':change,'[data-tender-exact]':exact,'[data-tender-clear]':clear}[s]||null),querySelectorAll:()=>[]};
vm.runInNewContext(fs.readFileSync('static/js/mobile_invoice.js','utf8'),{document:{getElementById:()=>f,createElement:()=>({addEventListener(k,fn){this.click=fn;}})}});
return {input,hidden,buttons,label,change,exact,clear};
}
const {input,hidden,buttons,label,change,exact,clear}=setup(4321000);
assert.deepEqual(buttons.map(b=>b.textContent),['4.330.000','4.350.000','4.400.000','4.500.000','4.600.000']);
buttons[3].click();assert.equal(hidden.value,'4500000');assert.equal(change.textContent,'179.000đ');
input.value='4000000';input.events.input();assert.equal(label.textContent,'Còn thiếu');assert.equal(change.textContent,'321.000đ');
exact.click();assert.equal(hidden.value,'4321000');assert.equal(change.textContent,'0đ');
clear.click();assert.equal(input.value,'');assert.equal(hidden.value,'0');assert.equal(change.textContent,'4.321.000đ');
const b=setup(41000000);assert.ok(b.buttons.some(x=>x.textContent==='41.100.000'));
for(const button of b.buttons){button.click();assert.ok(Number(b.hidden.value)>41000000&&Number(b.hidden.value)<=41500000);}
const zero=setup(0);assert.equal(zero.buttons.length,0);assert.equal(zero.exact.click,undefined);assert.equal(zero.clear.click,undefined);
console.log('PASS: suggestions, inline exact/clear, formatting, change and zero-cash guard');
