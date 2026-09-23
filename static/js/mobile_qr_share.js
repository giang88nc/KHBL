/* Export the entire visible QR frame locally. Never upload banking details. */
(function(){
  const root=document.getElementById('modal-root'),panel=root?.querySelector('.transfer-panel--qr');
  const modal=panel?.closest('.khbl-modal'),save=modal?.querySelector('[data-qr-save]');
  if(!save || save.dataset.bound)return;
  save.dataset.bound='1';
  const frame=panel.querySelector('.transfer-qr-frame'),qr=panel.querySelector('img.transfer-qr');
  const share=modal.querySelector('[data-qr-share]');
  if(!qr || !frame){save.hidden=true;if(share)share.hidden=true;return;}
  let file=null,dataUrl='',preparing=null,preview=null,disposed=false;
  const ios=/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
  const note=document.createElement('p');note.className='qr-export-status';note.setAttribute('role','status');frame.after(note);
  const valid=()=>!disposed&&panel.isConnected&&!!panel.querySelector('img.transfer-qr');
  const supported=()=>!!file&&window.isSecureContext&&!!navigator.share&&!!navigator.canShare&&navigator.canShare({files:[file]});
  function library(){
    if(window.html2canvas)return Promise.resolve();
    if(!window.kh2CanvasReady)window.kh2CanvasReady=new Promise((resolve,reject)=>{
      const script=document.createElement('script');script.src='/static/js/vendor/html2canvas-1.4.1.min.js';
      script.onload=resolve;script.onerror=()=>{window.kh2CanvasReady=null;reject(Error('Không tải được bộ chụp ảnh. Bấm Lưu hình để thử lại.'));};
      document.head.append(script);
    });
    return window.kh2CanvasReady;
  }
  async function prepare(){
    if(file)return;
    if(preparing)return preparing;
    preparing=(async()=>{
      note.textContent='Đang chuẩn bị ảnh QR…';
      await library();await document.fonts.ready;await qr.decode();
      if(!valid())return;
      const canvas=await window.html2canvas(frame,{scale:3,backgroundColor:'#ffffff',logging:false,
        imageTimeout:15000,useCORS:false,onclone(doc){
          const style=doc.createElement('style');
          style.textContent='.transfer-qr-frame::before,.transfer-qr-frame::after{display:none!important}.transfer-qr-frame{animation:none!important;box-shadow:none!important;background:white!important}.transfer-qr-frame *{animation:none!important;transition:none!important}';
          doc.head.append(style);
        }});
      if(!valid())return;
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
      if(!blob)throw Error('Chưa tạo được ảnh. Bấm Lưu hình để thử lại.');
      dataUrl=canvas.toDataURL('image/png');
      file=new File([blob],save.download||'KH2-QR.png',{type:'image/png'});
      if(share)share.hidden=!supported();
      note.textContent='';
    })().finally(()=>{preparing=null;});
    return preparing;
  }
  function closePreview(){
    if(preview){preview.remove();preview=null;document.removeEventListener('keydown',escape);save.focus();}
  }
  function escape(event){if(event.key==='Escape')closePreview();}
  function showPreview(){
    if(!valid()||!file)return;
    closePreview();
    preview=document.createElement('section');preview.className='qr-export-preview';
    preview.setAttribute('role','dialog');preview.setAttribute('aria-modal','true');preview.setAttribute('aria-label','Lưu ảnh QR');
    const title=document.createElement('h2');title.textContent='Lưu ảnh QR';
    const hint=document.createElement('p');hint.textContent='Nhấn giữ ảnh bên dưới → Lưu vào Ảnh. Ảnh gồm thông tin ngân hàng và mã QR.';
    const image=document.createElement('img');image.src=dataUrl;image.alt='Ảnh đầy đủ thông tin ngân hàng và mã QR';
    const close=document.createElement('button');close.type='button';close.textContent='Xong';close.addEventListener('click',closePreview);
    preview.append(title,hint,image,close);modal.append(preview);close.focus();document.addEventListener('keydown',escape);
  }
  async function exportImage(event){
    event.preventDefault();
    if(!valid())return;
    if(!file){
      note.textContent='Đang tạo ảnh…';
      try{await prepare();if(valid())showPreview();}catch(error){note.textContent=error.message;}
      return;
    }
    if(supported()){
      try{await navigator.share({files:[file],title:'KH2 · Thông tin chuyển khoản'});}
      catch(error){if(error.name!=='AbortError')showPreview();}
    }else if(ios){showPreview();}
    else{
      const a=document.createElement('a');a.href=dataUrl;a.download=file.name;document.body.append(a);a.click();a.remove();
      note.textContent='Đã yêu cầu tải ảnh đầy đủ. Kiểm tra mục Tải về.';
    }
  }
  save.addEventListener('click',exportImage);share?.addEventListener('click',exportImage);
  if(share)share.hidden=true;
  prepare().catch(error=>{if(valid())note.textContent=error.message;});
  const observer=new MutationObserver(()=>{
    if(!valid()){disposed=true;closePreview();file=null;dataUrl='';save.hidden=true;if(share)share.hidden=true;note.remove();observer.disconnect();}
  });
  observer.observe(root,{childList:true,subtree:true});
})();
