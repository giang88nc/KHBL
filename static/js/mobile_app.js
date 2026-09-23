(function () {
  const banner=document.getElementById('mobile-connection');
  if(!banner) return;
  let checking=false,timer;
  function message(text){banner.hidden=!text;banner.querySelector('[data-connection-message]').textContent=text;}
  async function probe(){
    clearTimeout(timer);
    if(checking || document.hidden)return;
    checking=true;
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),5000);
    try{
      // navigator.onLine alone cannot determine whether the shop server is reachable.
      const response=await fetch('/banle/mobile/health/',{cache:'no-store',credentials:'omit',signal:controller.signal});
      const data=await response.json();
      if(!response.ok || data.service!=='khbl-mobile' || data.ok!==true)throw Error();
      message('');
    }catch(_){message(navigator.onLine===false?'Điện thoại đang ngoại tuyến. Bật Wi-Fi và kết nối mạng cửa hàng.':'Không kết nối được máy chủ KHBL. Kiểm tra Wi-Fi cửa hàng hoặc máy chủ; không gửi lại thanh toán khi chưa kiểm tra kết quả.');}
    finally{clearTimeout(timeout);checking=false;if(!document.hidden)timer=setTimeout(probe,30000);}
  }
  banner.querySelector('[data-connection-retry]').addEventListener('click',probe);
  window.addEventListener('online',probe);window.addEventListener('offline',probe);
  window.addEventListener('pageshow',probe);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(timer);else probe();});
  document.addEventListener('htmx:sendError',probe);document.addEventListener('htmx:timeout',probe);
  document.addEventListener('htmx:responseError',event=>{
    const code=event.detail.xhr.status;
    message(code===403?'Phiên đăng nhập hoặc quyền đã thay đổi. Mở lại KHBL để đăng nhập.':`Yêu cầu chưa hoàn tất (HTTP ${code}). Kiểm tra kết quả trước khi thao tác lại.`);
  });
  function viewport(){
    const v=window.visualViewport,root=document.documentElement;
    if(v && v.scale===1){root.style.setProperty('--app-height',v.height+'px');root.style.setProperty('--app-top',v.offsetTop+'px');}
    const editing=/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName||'');
    root.classList.toggle('app-keyboard-open',editing && !!v && window.innerHeight-v.height>120);
  }
  window.visualViewport?.addEventListener('resize',viewport);window.visualViewport?.addEventListener('scroll',viewport);
  document.addEventListener('focusin',viewport);document.addEventListener('focusout',()=>setTimeout(viewport,100));
  viewport();probe();
})();
