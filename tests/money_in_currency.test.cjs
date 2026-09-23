const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function input(name,value){return {dataset:{money:name},value,selectionStart:value.length,handlers:{},addEventListener(k,fn){this.handlers[k]=fn;},setSelectionRange(a){this.selectionStart=a;},setCustomValidity(s){this.error=s;}};}
const bank=input('bank_amount','1000000'),cash=input('cash_amount','0'),display={},words={};
const elements={bank_amount:{value:'1000000'},cash_amount:{value:'0'},bank_id:{options:[],selectedOptions:[],addEventListener(){}}};
const form={dataset:{total:'1000000'},elements,querySelectorAll(s){return s==='[data-money]'?[bank,cash]:[];},querySelector(s){return s==='.transfer-bank'?{querySelector(){return {};}}:s==='[data-amount-display]'?display:words;}};
vm.runInNewContext(fs.readFileSync('static/js/money_flow_transfer.js','utf8'),{document:{getElementById(){return form;},querySelector(){return null;}},localStorage:{getItem(){return null;}}});
assert.equal(bank.value,'1.000.000');
function type(el,value){el.value=value;el.selectionStart=value.length;el.handlers.input();}
type(bank,'250000');assert.equal(bank.value,'250.000');assert.equal(cash.value,'750.000');assert.equal(elements.bank_amount.value,'250000');assert.equal(elements.cash_amount.value,'750000');
type(cash,'125.000');assert.equal(bank.value,'875.000');assert.equal(elements.bank_amount.value,'875000');
type(bank,'1000001');assert.equal(bank.value,'1.000.000');assert.equal(cash.value,'0');assert.equal(bank.error,'');
type(cash,'2000000');assert.equal(cash.value,'1.000.000');assert.equal(bank.value,'0');
type(cash,'');assert.equal(cash.value,'0');assert.equal(bank.value,'1.000.000');
type(bank,'-10');assert.ok(bank.error);
type(bank,'0');assert.ok(bank.error);
type(bank,'1000000');assert.equal(bank.error,'');assert.equal(cash.value,'0');
console.log('PASS: currency formatting, canonical payload, two-way split, invalid amounts');
