(function(){
 const f=document.getElementById('mobile-invoice-form');if(!f||f.dataset.bound)return;f.dataset.bound='1';
 const cash=Number(f.dataset.cash),input=f.querySelector('[data-tender]'),hidden=f.elements.tender;
 const fmt=n=>n.toLocaleString('vi-VN');
 function update(){
   if(!/^[\d.\s]*$/.test(input.value)){input.setCustomValidity('Chỉ nhập số tiền nguyên, không âm.');return;}
   const n=Number(input.value.replace(/[.\s]/g,'')||0);
   if(!Number.isSafeInteger(n)||n<0){input.setCustomValidity('Số tiền không hợp lệ.');return;}
   input.setCustomValidity('');hidden.value=String(n);input.value=fmt(n);
   f.querySelector('[data-change-label]').textContent=n<cash?'Còn thiếu':'Tiền trả lại';
   f.querySelector('[data-change]').textContent=fmt(Math.abs(n-cash))+'đ';
 }
 input.addEventListener('input',update);
 if(cash>0){
   f.querySelector('[data-tender-exact]').addEventListener('click',()=>{input.value=String(cash);update();});
   f.querySelector('[data-tender-clear]').addEventListener('click',()=>{input.value='';update();input.value='';input.focus();});
   const rounded=[10000,50000,100000,200000,500000].map(step=>Math.ceil(cash/step)*step);
   if(cash%100000===0)rounded.push(cash+100000);
   rounded.push(Math.ceil(cash/200000)*200000+200000);
   const values=[...new Set(rounded.filter(n=>n>cash&&n-cash<=500000))];
   values.sort((a,b)=>a-b);
   for(const n of values){const b=document.createElement('button');b.type='button';b.textContent=fmt(n);b.addEventListener('click',()=>{input.value=String(n);update();});f.querySelector('[data-quick]').append(b);}
   update();
 }else input.value=fmt(Number(hidden.value)||0);
 const wedding=f.querySelector('[data-wedding-row]');
 function toggle(){
  if(wedding){const on=!!f.querySelector('[name=is_wedding]')?.checked;wedding.hidden=!on;f.elements.wedding.disabled=!on;}
  const online=f.querySelector('[data-online-row]');if(online){const on=[...f.querySelectorAll('[name=channel]')].some(c=>c.checked&&c.value==='online');online.hidden=!on;online.querySelectorAll('input').forEach(c=>{c.disabled=!on;if(c.name==='social')c.required=on;});}
 }
 f.querySelectorAll('[name=channel],[name=is_wedding]').forEach(c=>c.addEventListener('change',toggle));toggle();
})();
