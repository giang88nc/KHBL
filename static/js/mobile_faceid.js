/* Keep the original Home Screen app session; never copy an auth cookie or verifier to JS. */
(function () {
  const form=document.getElementById('mobile-faceid-start');
  if(!form) return;
  const wait=document.getElementById('mobile-faceid-wait'),status=wait.querySelector('[data-faceid-status]');
  const open=document.querySelector('[data-faceid-open]'),check=wait.querySelector('[data-faceid-check]');
  const standalone=window.matchMedia('(display-mode: standalone)').matches || navigator.standalone===true;
  let pending=false,launched=false,busy=false,timer=null,refreshTimer=null,created=0,deadline=0;
  const button=form.querySelector('button');
  function show(message){wait.hidden=false;status.textContent=message;}
  function stop(message){pending=false;launched=false;clearTimeout(timer);clearTimeout(refreshTimer);form.hidden=false;button.disabled=false;open.hidden=true;check.hidden=true;show(message);}
  async function post(url,body){
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),12000);
    try{
      const response=await fetch(url,{method:'POST',credentials:'same-origin',body,signal:controller.signal});
      const data=await response.json().catch(()=>({error:'Không thể hoàn tất yêu cầu. Mở lại trang đăng nhập và thử lại.'}));
      return {response,data};
    }finally{clearTimeout(timeout);}
  }
  async function poll(){
    clearTimeout(timer);
    if(!pending || !launched || busy || document.hidden) return;
    if(Date.now()>deadline){stop('Phiên xác thực hết hạn. Bấm FACE ID để bắt đầu lại.');return;}
    busy=true;
    try{
      const {response,data}=await post('/banle/mobile/auth/result/',new FormData(form));
      if(response.ok && data.status==='success'){pending=false;location.replace('/banle/mobile/dashboard/');return;}
      if(response.status===403 && data.redirect==='/banle/mobile/auth/notice/'){pending=false;location.replace(data.redirect);return;}
      if(response.status===400 || response.status===403){stop(data.error);return;}
      show(response.ok?'Đang xác thực…':data.error);
      check.hidden=response.ok;
    }catch(_){show('Mất kết nối. Vui lòng thử lại.');check.hidden=false;}
    finally{busy=false;if(pending && !document.hidden)timer=setTimeout(poll,2500);}
  }
  function ready(authorize,stamp){
    const target=new URL(authorize);
    if(target.origin!=='https://unopposable-parheliacal-waylon.ngrok-free.dev' || target.pathname!=='/banle/mobile/')throw Error('Liên kết xác thực không hợp lệ.');
    created=Number(stamp)*1000 || Date.now();deadline=created+240000;
    pending=true;form.hidden=true;button.disabled=false;open.href=target.href;
    try{launched=sessionStorage.getItem('kh2.faceid.opened')===String(created);}catch(_){}
    open.hidden=launched;wait.hidden=!launched;check.hidden=true;
    if(launched){show('Đang xác thực…');return;}
    // Refresh an unused ticket before its three-minute expiry, only while visible.
    clearTimeout(refreshTimer);
    refreshTimer=setTimeout(()=>{if(!document.hidden && !launched)prepare();},Math.max(0,created+150000-Date.now()));
  }
  async function prepare(){
    if(busy || document.hidden)return;
    busy=true;button.disabled=true;form.hidden=false;open.hidden=true;wait.hidden=true;
    try{
      const body=new FormData(form);body.set('mode','app');
      const {response,data}=await post(form.action,body);
      if(!response.ok || !data.authorize)throw Error(data.error || 'Chưa mở được Face ID. Vui lòng thử lại.');
      launched=false;ready(data.authorize,data.created);
    }catch(error){stop(error.message);}
    finally{busy=false;}
  }
  form.addEventListener('submit',event=>{
    if(!standalone)return; // Normal-browser POST/redirect flow stays unchanged.
    event.preventDefault();prepare();
  });
  open.addEventListener('click',event=>{
    if(Date.now()>created+175000){event.preventDefault();prepare();return;}
    // Native link navigation runs in the original user gesture: no async popup or extra tap.
    launched=true;clearTimeout(refreshTimer);
    try{sessionStorage.setItem('kh2.faceid.opened',String(created));}catch(_){}
    show('Đang xác thực…');
    setTimeout(()=>{open.hidden=true;poll();},100);
  });
  check.addEventListener('click',poll);
  function resume(){if(!standalone)return;if(launched)poll();else if(!pending || Date.now()>created+150000)prepare();}
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(timer);else resume();});
  window.addEventListener('pageshow',resume);
  window.addEventListener('focus',resume);
  if(standalone){
    const authorize=JSON.parse(document.getElementById('mobile-faceid-authorize')?.textContent || 'null');
    const stamp=JSON.parse(document.getElementById('mobile-faceid-created')?.textContent || '0');
    if(authorize && Number(stamp)*1000+240000>Date.now()){
      try{ready(authorize,stamp);if(launched)poll();else resume();}catch(error){stop(error.message);}
    }else prepare();
  }
})();
