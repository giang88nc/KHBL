(function(){
 const f=document.getElementById('standalone-qr-form');if(!f||f.dataset.bound)return;f.dataset.bound='1';
 const input=f.querySelector('[data-standalone-amount]'),select=f.elements.bank_id;
 const radios=[...f.querySelectorAll('[name=category]')];
 function words(n){
  if(!Number.isSafeInteger(n)||n<0)return 'Số tiền không hợp lệ';
  if(n===0)return 'Không đồng';
  const digits=['không','một','hai','ba','bốn','năm','sáu','bảy','tám','chín'],units=['','nghìn','triệu','tỷ','nghìn tỷ'],groups=[],out=[];
  while(n>0){groups.push(n%1000);n=Math.floor(n/1000);}
  for(let i=groups.length-1;i>=0;i--){let v=groups[i];if(!v)continue;const h=Math.floor(v/100),t=Math.floor(v/10)%10,u=v%10;let s=h||i<groups.length-1?digits[h]+' trăm ':'';
   if(t>1)s+=digits[t]+' mươi ';else if(t===1)s+='mười ';else if(u&&(h||i<groups.length-1))s+='lẻ ';
   if(u)s+=(u===1&&t>1?'mốt':u===5&&t>0?'lăm':digits[u])+' ';out.push(s+units[i]);}
  const s=out.join(' ').replace(/\s+/g,' ').trim()+' đồng';return s[0].toUpperCase()+s.slice(1);
 }
 function format(){
  if(!/^[\d.\s]*$/.test(input.value)){input.setCustomValidity('Chỉ nhập số tiền nguyên dương.');return;}
  const raw=input.value.replace(/[.\s]/g,''),n=Number(raw);
  input.setCustomValidity(raw&&Number.isSafeInteger(n)&&n>0&&n<=999999999999999?'':'Số tiền không hợp lệ.');
  f.elements.amount.value=raw;input.value=raw?n.toLocaleString('vi-VN'):'';
  f.querySelector('[data-manual-amount]').textContent=Number.isSafeInteger(n)?n.toLocaleString('vi-VN')+'đ':'—';
  f.querySelector('[data-manual-words]').textContent=words(n);
 }
 function bank(save){const o=select.selectedOptions[0],s=f.querySelector('.transfer-bank');s.querySelector('b').textContent=o?.dataset.bank||'';s.querySelector('strong').textContent=o?.dataset.account||'';s.querySelector('span').textContent=o?.dataset.owner||'';if(save)try{localStorage.setItem('khbl.qr.manual.bank',select.value);}catch(_){}}
 function category(auto){const checked=radios.find(r=>r.checked);if(!checked)return;
  f.querySelector('[data-manual-reference]').value=(/^\d+$/.test(f.dataset.referenceSuffix)?'KHBL':checked.value)+f.dataset.referenceSuffix;  // 22/09/2026: mã mới KHBL+10 số
  if(auto)select.value=[...select.options].find(o=>o.value&&o.dataset.type?.trim().toLowerCase()===checked.dataset.bankType)?.value||'';
  bank(false);
 }
 radios.forEach(r=>r.addEventListener('change',()=>category(true)));
 category(f.dataset.keepBank!=='1');
 input.addEventListener('input',format);select.addEventListener('change',()=>bank(true));bank(false);format();
})();
