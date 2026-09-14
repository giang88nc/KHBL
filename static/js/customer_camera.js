/* Chụp/cắt thường cục bộ; Tách CCCD dùng riêng API xử lý RAM, không lưu hồ sơ. */
(() => {
  'use strict';
  const dialog=document.getElementById('kh-local-camera');
  if(!dialog) return;
  const $=selector=>dialog.querySelector(selector), video=$('video'), preview=$('[data-local-preview]');
  const device=$('#kh-local-device'), message=$('[data-local-message]'), capture=$('[data-local-capture]');
  const retake=$('[data-local-retake]'), crop=$('[data-local-crop]'), save=$('[data-local-save]');
  const originalButton=$('[data-local-original]'), controls=$('[data-local-crop-controls]');
  const card=$('[data-local-card]'),cardDialog=document.getElementById('kh-local-card-dialog'),cardRoot=document.getElementById('kh-local-card-content');
  let cardSource=null,cardTicket=0,cardRequest=null;
  const edges=[...dialog.querySelectorAll('[data-crop-edge]')];
  let stream=null, session=0, photo=null, original=null, cropping=false, busy=false, drag=null;
  let rectangle={L:0,T:0,R:1,B:1};
  const downloads=new Set();

  function tell(text,error=false){message.textContent=text;message.classList.toggle('is-error',error);}
  function stop(){if(stream) stream.getTracks().forEach(track=>track.stop());stream=null;video.srcObject=null;}
  function state(){
    video.hidden=!!photo;preview.hidden=!photo;
    capture.hidden=!!photo;retake.hidden=!photo && !message.classList.contains('is-error');
    crop.hidden=!photo;save.hidden=!photo;originalButton.hidden=!photo || photo===original;
    card.hidden=!photo;card.disabled=cropping || busy;
    controls.hidden=!cropping;preview.classList.toggle('is-cropping',cropping);
    save.disabled=!photo || cropping || busy;crop.disabled=cropping || busy;
    retake.disabled=busy;originalButton.disabled=busy;device.disabled=!!photo || busy;
  }
  function close(){
    closeCard();
    session++;stop();photo=null;original=null;cropping=false;busy=false;drag=null;
    preview.width=1;preview.height=1;
    if(dialog.open) dialog.close();
  }
  function closeCard(){
    cardTicket++;cardRequest?.abort();cardRequest=null;cardSource=null;
    cardRoot?.querySelector('#th-cat-stage')?.dispatchEvent(new Event('khbl:crop-dispose'));
    if(cardDialog?.open) cardDialog.close();
    cardRoot?.replaceChildren();busy=false;state();
  }
  window.khblLocalCardClose=closeCard;
  cardDialog?.addEventListener('cancel',event=>{event.preventDefault();closeCard();});
  async function requestCard(mode,params={}){
    const url=new URL(dialog.dataset.cardUrl,location.origin);url.searchParams.set('mode',mode);
    Object.entries(params).forEach(([key,value])=>url.searchParams.set(key,value));
    cardRequest=new AbortController();
    const response=await fetch(url,{method:'POST',credentials:'same-origin',cache:'no-store',signal:cardRequest.signal,
      headers:{'Content-Type':'image/jpeg','X-CSRFToken':dialog.dataset.csrf},body:cardSource});
    if(response.redirected || response.status===403) throw new Error('Phiên đăng nhập / quyền không hợp lệ. Tải lại trang rồi thử lại.');
    if(!response.ok){const error=await response.json().catch(()=>({}));throw new Error(error.error || 'Chưa xử lý được ảnh. Vui lòng thử lại.');}
    return response;
  }
  async function cardJpeg(source){
    // Stay below the web server's RAM buffer; do not spill the upload to a temp file.
    for(const max of [1600,1280,1000]){
      const canvas=document.createElement('canvas'),scale=Math.min(1,max/Math.max(source.width,source.height));
      canvas.width=Math.round(source.width*scale);canvas.height=Math.round(source.height*scale);
      canvas.getContext('2d').drawImage(source,0,0,canvas.width,canvas.height);
      for(const quality of [.92,.82,.72,.62]){
        const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',quality));
        if(blob && blob.size<=350*1024) return blob;
      }
    }
    throw new Error('Ảnh quá nhiều chi tiết. Cắt gần vùng CCCD rồi thử lại.');
  }
  card?.addEventListener('click',async()=>{
    if(!photo || cropping || busy) return;
    const ticket=++cardTicket;busy=true;state();
    const loading=document.createElement('div');loading.className='kh-local-card-loading';
    const title=document.createElement('h2');title.textContent='✂ Đang nhận diện và tách CCCD…';
    const hint=document.createElement('p');hint.textContent='Xử lý tạm trong RAM máy chủ, không lưu ảnh. Có thể mất vài giây.';
    const cancel=document.createElement('button');cancel.type='button';cancel.className='khbl-btn';cancel.textContent='Đóng';cancel.onclick=closeCard;
    loading.append(title,hint,cancel);cardRoot.replaceChildren(loading);cardDialog.showModal();
    try{
      const source=await cardJpeg(photo);
      if(ticket!==cardTicket || !cardDialog.open) return;
      cardSource=source;
      if(!cardSource) throw new Error('Chưa tạo được ảnh để tách thẻ.');
      const response=await requestCard('preview');const html=await response.text();
      if(ticket!==cardTicket || !cardDialog.open) return;
      htmx.swap(cardRoot,html,{swapStyle:'innerHTML',settleDelay:0});
    }catch(error){
      if(ticket!==cardTicket) return;
      closeCard();tell(error.message || 'Không tách được CCCD.',true);
    }
  });
  cardRoot?.addEventListener('click',async event=>{
    const button=event.target.closest('[data-local-card-apply]');
    if(!button || button.disabled || !cardSource) return;
    const ticket=cardTicket;button.disabled=true;
    const errorBox=cardRoot.querySelector('[data-local-card-error]');errorBox.textContent='Đang áp dụng góc xoay và cắt bốn cạnh…';
    const params=Object.fromEntries([...cardRoot.querySelectorAll('#th-cat-form input[name]')].map(input=>[input.name,input.value]));
    try{
      const response=await requestCard('apply',params),blob=await response.blob();
      const image=await createImageBitmap(blob);
      if(ticket!==cardTicket || !dialog.open){image.close();return;}
      const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
      canvas.getContext('2d').drawImage(image,0,0);image.close();photo=canvas;cropping=false;
      closeCard();state();draw();tell(`Đã tách CCCD ${photo.width} × ${photo.height} px. Bấm LƯU VỀ MÁY để tải ảnh.`);
    }catch(error){if(ticket===cardTicket){errorBox.textContent=error.message || 'Chưa dùng được ảnh đã tách.';button.disabled=false;}}
  });
  function draw(){
    if(!photo) return;
    const ratio=Math.min(1,1400/photo.width);
    preview.width=Math.max(1,Math.round(photo.width*ratio));preview.height=Math.max(1,Math.round(photo.height*ratio));
    const ctx=preview.getContext('2d'), w=preview.width,h=preview.height;
    ctx.drawImage(photo,0,0,w,h);
    if(cropping){
      const {L,T,R,B}=rectangle;
      ctx.fillStyle='rgba(0,0,0,.55)';
      ctx.fillRect(0,0,w,h*T);ctx.fillRect(0,h*B,w,h*(1-B));
      ctx.fillRect(0,h*T,w*L,h*(B-T));ctx.fillRect(w*R,h*T,w*(1-R),h*(B-T));
      ctx.strokeStyle='#ffda78';ctx.lineWidth=Math.max(2,w/500);ctx.strokeRect(w*L,h*T,w*(R-L),h*(B-T));
      edges.forEach(input=>{input.value=String(Math.round(rectangle[input.dataset.cropEdge]*100));input.nextElementSibling.textContent=input.value+'%';});
    }
  }
  function cameraError(error){
    const texts={NotAllowedError:'Camera chưa được cấp quyền. Cho phép camera trong trình duyệt rồi bấm Chụp lại.',
      NotFoundError:'Không tìm thấy camera trên thiết bị.',NotReadableError:'Camera đang bận. Đóng ứng dụng dùng camera rồi thử lại.',
      NotSupportedError:'Trình duyệt này chưa hỗ trợ camera. Hãy mở trang bằng HTTPS trên Chrome, Edge hoặc Safari.'};
    stop();tell(texts[error?.name] || 'Không mở được camera. Bấm Chụp lại để thử lại.',true);state();capture.disabled=true;
  }
  async function openStream(){
    const ticket=++session;
    stop();photo=null;original=null;cropping=false;busy=false;drag=null;
    tell('Đang mở camera…');state();capture.disabled=true;device.disabled=true;
    if(!navigator.mediaDevices?.getUserMedia){cameraError({name:'NotSupportedError'});return;}
    try{
      const opened=await navigator.mediaDevices.getUserMedia({audio:false,video:{
        ...(device.value?{deviceId:{exact:device.value}}:{facingMode:{ideal:'environment'}}),
        width:{ideal:1920},height:{ideal:1080}}});
      if(ticket!==session || !dialog.open){opened.getTracks().forEach(track=>track.stop());return;}
      stream=opened;video.srcObject=opened;await video.play();
      if(ticket!==session || !dialog.open) return;
      capture.disabled=false;tell('Đặt hình ngay ngắn trong khung rồi bấm Chụp.');
      try{
        const available=await navigator.mediaDevices.enumerateDevices();
        if(ticket!==session || !dialog.open || photo) return;
        const current=opened.getVideoTracks()[0]?.getSettings().deviceId;
        device.replaceChildren();
        available.filter(item=>item.kind==='videoinput').forEach((item,i)=>device.add(new Option(item.label || `Camera ${i+1}`,item.deviceId)));
        if(current) device.value=current;
      }catch(_){/* Camera vẫn hoạt động nếu không liệt kê được thiết bị. */}
      device.disabled=false;
    }catch(error){if(ticket===session && dialog.open) cameraError(error);}
  }
  document.querySelectorAll('[data-local-camera-open]').forEach(button=>button.addEventListener('click',()=>{
    if(!dialog.open) dialog.showModal();openStream();
  }));
  dialog.querySelectorAll('[data-local-close]').forEach(button=>button.addEventListener('click',close));
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  dialog.addEventListener('close',()=>{if(stream || photo) close();});
  dialog.addEventListener('click',event=>{
    if(event.target!==dialog) return;
    const r=dialog.getBoundingClientRect();
    if(event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom) close();
  });
  device.addEventListener('change',openStream);retake.addEventListener('click',openStream);
  capture.addEventListener('click',()=>{
    if(!stream || !video.videoWidth || !video.videoHeight) return;
    const canvas=document.createElement('canvas'),scale=Math.min(1,4096/Math.max(video.videoWidth,video.videoHeight));
    canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
    canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
    photo=original=canvas;stop();capture.disabled=true;
    tell(`Ảnh ${photo.width} × ${photo.height} px. Có thể cắt hoặc lưu về máy.`);state();draw();
  });
  crop.addEventListener('click',()=>{
    if(!photo) return;
    rectangle={L:0.1,T:0.1,R:0.9,B:0.9};cropping=true;state();draw();
    tell('Chọn vùng cần giữ rồi bấm Áp dụng cắt.');
  });
  $('[data-local-crop-cancel]').addEventListener('click',()=>{cropping=false;drag=null;state();draw();tell('Đã hủy vùng cắt. Ảnh được giữ nguyên.');});
  edges.forEach(input=>input.addEventListener('input',()=>{
    const key=input.dataset.cropEdge, opposite={L:'R',R:'L',T:'B',B:'T'}[key],value=Number(input.value)/100;
    rectangle[key]=key==='L' || key==='T'?Math.min(value,rectangle[opposite]-.01):Math.max(value,rectangle[opposite]+.01);
    draw();
  }));
  const point=event=>{const r=preview.getBoundingClientRect();return {x:Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))};};
  preview.addEventListener('pointerdown',event=>{
    if(!cropping || !event.isPrimary || (event.pointerType==='mouse' && event.button!==0)) return;
    drag={...point(event),before:{...rectangle},id:event.pointerId};preview.setPointerCapture(event.pointerId);event.preventDefault();
  });
  preview.addEventListener('pointermove',event=>{
    if(!drag || drag.id!==event.pointerId) return;
    const p=point(event);rectangle={L:Math.min(drag.x,p.x),T:Math.min(drag.y,p.y),R:Math.max(drag.x,p.x),B:Math.max(drag.y,p.y)};draw();
  });
  function endDrag(event){
    if(!drag || drag.id!==event.pointerId) return;
    if(event.type==='pointercancel' || rectangle.R-rectangle.L<.01 || rectangle.B-rectangle.T<.01) rectangle=drag.before;
    drag=null;draw();
  }
  preview.addEventListener('pointerup',endDrag);preview.addEventListener('pointercancel',endDrag);
  $('[data-local-crop-apply]').addEventListener('click',()=>{
    if(!photo || !cropping) return;
    const x=Math.floor(rectangle.L*photo.width),y=Math.floor(rectangle.T*photo.height);
    const w=Math.floor(rectangle.R*photo.width)-x,h=Math.floor(rectangle.B*photo.height)-y;
    if(w<2 || h<2){tell('Vùng cắt quá nhỏ. Chọn lại vùng ảnh.',true);return;}
    const cut=document.createElement('canvas');cut.width=w;cut.height=h;
    cut.getContext('2d').drawImage(photo,x,y,w,h,0,0,w,h);photo=cut;cropping=false;drag=null;
    state();draw();tell(`Đã cắt: ${w} × ${h} px. Bấm LƯU VỀ MÁY để tải ảnh.`);
  });
  originalButton.addEventListener('click',()=>{photo=original;cropping=false;drag=null;state();draw();tell('Đã khôi phục ảnh gốc.');});
  save.addEventListener('click',()=>{
    if(!photo || cropping || busy) return;
    busy=true;state();const ticket=session;
    photo.toBlob(blob=>{
      if(ticket!==session || !dialog.open) return;
      busy=false;state();
      if(!blob){tell('Chưa tạo được tệp ảnh. Vui lòng thử lại.',true);return;}
      const now=new Date(),pad=n=>String(n).padStart(2,'0');
      const name=`Hinh-chup-${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}-${now.getMilliseconds()}.jpg`;
      const url=URL.createObjectURL(blob),link=document.createElement('a');downloads.add(url);
      link.href=url;link.download=name;document.body.append(link);link.click();link.remove();
      setTimeout(()=>{URL.revokeObjectURL(url);downloads.delete(url);},60000);
      tell('Đã tạo tệp tải xuống. Kiểm tra mục Tải xuống của trình duyệt.');
    },'image/jpeg',.94);
  });
  window.addEventListener('pagehide',()=>{close();downloads.forEach(url=>URL.revokeObjectURL(url));downloads.clear();});
})();
