(function () {
  'use strict';
  var root = document.getElementById('qr-learning');
  if (!root) return;
  var form = document.getElementById('qrl-form'), raw = document.getElementById('qrl-raw');
  var output = document.getElementById('qrl-output'), scope = document.getElementById('qrl-scope');
  var status = document.getElementById('qrl-status'), comparison = document.getElementById('qrl-comparison');
  var list = document.getElementById('qrl-rules'), count = document.getElementById('qrl-count');
  var state = JSON.parse(document.getElementById('qrl-initial-state').textContent);
  state.drafts=state.drafts||[];
  var canTeach = root.dataset.canTeach === '1', busy = false, serial = 0, editId = '', draftId = '';
  var libraryMode='waiting', libraryModal=document.getElementById('qrl-library-modal');
  var undo = [], fragments = [], fragmentIndex = -1;
  var fragmentBox = document.getElementById('qrl-fragments'), fragmentList = document.getElementById('qrl-fragment-list');
  var undoButton = document.getElementById('qrl-undo');
  var names = {text:'Họ tên và địa chỉ', ho_ten:'Họ tên', dia_chi:'Địa chỉ', qr:'Toàn bộ QR'};
  function message(text, error) { status.textContent = text; status.classList.toggle('is-error', !!error); }
  function lock(value) {
    busy = value;
    root.querySelectorAll('button,textarea,select').forEach(function (el) {
      el.disabled = value || (el.dataset.write === '1' && !canTeach) || (el.id === 'qrl-apply' && !canTeach);
    });
  }
  async function request(url, data) {
    var options = {credentials:'same-origin', cache:'no-store'};
    if (data) {
      options.method = 'POST';
      options.headers = {'Content-Type':'application/json', 'X-CSRFToken':form.querySelector('[name="csrfmiddlewaretoken"]').value};
      options.body = JSON.stringify(data);
    }
    var response = await fetch(url, options);
    if (response.redirected || response.status === 401) throw new Error('Phiên đăng nhập đã hết. Đăng nhập lại rồi mở công cụ.');
    var result;
    try { result = await response.json(); } catch (_) { throw new Error('Máy chủ chưa trả được kết quả. Dữ liệu đang nhập được giữ lại.'); }
    if (!response.ok) throw new Error(result.error || 'Chưa thực hiện được. Hãy thử lại.');
    return result;
  }
  function payload() { return {raw:raw.value, output:output.value, scope:scope.value, revision:state.revision, edit_id:editId, draft_id:draftId}; }
  function remember() {
    keepFragment();
    undo.push({input:payload(), fragments:fragments.map(function (p) { return Object.assign({},p); }), index:fragmentIndex});
    if (undo.length>10) undo.shift();undoButton.hidden=false;
  }
  function keepFragment() {
    if (fragmentIndex>=0 && fragments[fragmentIndex]) Object.assign(fragments[fragmentIndex],payload());
  }
  function clearTools() { undo=[];fragments=[];fragmentIndex=-1;editId='';draftId='';undoButton.hidden=true;renderFragments(); }
  function renderFragments() {
    fragmentList.replaceChildren();fragmentBox.hidden=!fragments.length;
    fragments.forEach(function (pair,index) {
      var b=document.createElement('button');b.type='button';b.className='khbl-btn khbl-btn--outline khbl-btn--sm';
      b.textContent=(index+1)+'. '+pair.output;b.setAttribute('aria-pressed',String(index===fragmentIndex));
      b.addEventListener('click',function () {
        if (busy) return;keepFragment();selectFragment(index);message('Đang sửa đoạn '+(index+1)+' / '+fragments.length+'. Chỉ Áp dụng mới lưu mẫu.');
      });fragmentList.appendChild(b);
    });
  }
  function selectFragment(index) {
    var pair=fragments[index];serial++;fragmentIndex=index;editId=pair.edit_id||'';draftId=pair.draft_id||draftId;
    raw.value=pair.raw;output.value=pair.output;scope.value=pair.scope;comparison.hidden=true;
    document.getElementById('qrl-form-title').textContent='Sửa đoạn còn lỗi';renderFragments();output.focus();
  }
  function notifyLearning() {
    try { localStorage.setItem('khbl-qr-learning-revision', String(state.revision) + ':' + Date.now()); } catch (_) {}
  }
  function button(text, action) {
    var b = document.createElement('button'); b.type='button';b.textContent=text;
    b.className='khbl-btn khbl-btn--outline khbl-btn--sm'; b.dataset.write='1'; b.disabled=!canTeach;
    b.addEventListener('click',action);return b;
  }
  function closeLibrary() { libraryModal.hidden=true; }
  function openLibrary(mode) {
    libraryMode=mode;render();libraryModal.hidden=false;document.getElementById('qrl-close-library').focus();
  }
  function render() {
    var active=state.rules.filter(function (r) { return r.active; }).length;
    count.textContent=active+'/'+state.rules.length;
    document.getElementById('qrl-draft-count').textContent=state.drafts.length;
    document.getElementById('qrl-modal-draft-count').textContent=state.drafts.length;
    document.getElementById('qrl-modal-rule-count').textContent=active+'/'+state.rules.length;
    document.getElementById('qrl-library-subtitle').textContent=libraryMode==='waiting'?'Danh sách CHỜ xử lý':'Danh sách ĐÃ HỌC';
    document.getElementById('qrl-tab-waiting').setAttribute('aria-pressed',String(libraryMode==='waiting'));
    document.getElementById('qrl-tab-learned').setAttribute('aria-pressed',String(libraryMode==='learned'));
    list.replaceChildren();
    if (libraryMode==='waiting') {
      if (!state.drafts.length) { var waitingEmpty=document.createElement('p');waitingEmpty.className='qrl-empty';waitingEmpty.textContent='Không có RAW đang chờ xử lý.';list.appendChild(waitingEmpty); }
      state.drafts.slice().reverse().forEach(function (draft) {
        var row=document.createElement('div');row.className='qrl-rule';
        var col=document.createElement('div'), label=document.createElement('small'), text=document.createElement('p');
        label.textContent=names[draft.scope]+' · CHỜ';text.textContent=draft.raw;text.title=draft.raw;
        col.appendChild(label);col.appendChild(text);row.appendChild(col);
        var actions=document.createElement('div');actions.className='qrl-rule-actions';
        actions.appendChild(button('Xử lý',function () {
          if (busy) return;clearTools();serial++;draftId=draft.id;raw.value=draft.raw;output.value='';scope.value=draft.scope;
          comparison.hidden=true;document.getElementById('qrl-form-title').textContent='Xử lý mẫu CHỜ';
          closeLibrary();message('Đã mở RAW từ danh sách CHỜ. Bấm Dịch, đối chiếu rồi Áp dụng để chuyển sang ĐÃ HỌC.');raw.focus();form.scrollIntoView({block:'start',behavior:'smooth'});
        }));
        row.appendChild(actions);list.appendChild(row);
      });
      return;
    }
    if (!state.rules.length) { var empty=document.createElement('p');empty.className='qrl-empty';empty.textContent='Chưa có mẫu được dạy.';list.appendChild(empty); }
    state.rules.slice().reverse().forEach(function (rule) {
      var row=document.createElement('div');row.className='qrl-rule' + (rule.active?'':' is-off');
      var col=document.createElement('div'), label=document.createElement('small'), text=document.createElement('p');
      label.textContent=names[rule.scope] + (rule.active?' · Đang dùng':' · Đã tắt');
      text.textContent=rule.raw+' → '+rule.output;text.title=text.textContent;col.appendChild(label);col.appendChild(text);row.appendChild(col);
      var actions=document.createElement('div');actions.className='qrl-rule-actions';
      actions.appendChild(button('Sửa',function () {
        if (busy) return;clearTools();serial++;editId=rule.id;raw.value=rule.raw;output.value=rule.output;scope.value=rule.scope;
        comparison.hidden=true;document.getElementById('qrl-form-title').textContent='Sửa mẫu đã học';
        closeLibrary();message('Đang sửa mẫu. Bấm Áp dụng để lưu kết quả mới.');raw.focus();form.scrollIntoView({block:'start',behavior:'smooth'});
      }));
      actions.appendChild(button(rule.active?'Tạm tắt':'Bật lại',async function () {
        if (busy) return;lock(true);serial++;
        try {
          var result=await request(form.dataset.saveUrl,{edit_id:rule.id,active:!rule.active,revision:state.revision});
          state=result;render();notifyLearning();message(rule.active?'Đã tạm tắt mẫu.':'Đã bật lại mẫu.');
        } catch (error) { message(error.message,true); } finally { lock(false); }
      }));
      row.appendChild(actions);list.appendChild(row);
    });
  }
  form.addEventListener('input',function () { serial++;comparison.hidden=true; });
  document.getElementById('qrl-new').addEventListener('click',function () {
    if (busy) return;clearTools();serial++;form.reset();comparison.hidden=true;
    document.getElementById('qrl-form-title').textContent='Thêm mẫu mới';message('');raw.focus();
  });
  undoButton.addEventListener('click',function () {
    if (busy || !undo.length) return;
    var previous=undo.pop();serial++;raw.value=previous.input.raw;output.value=previous.input.output;
    scope.value=previous.input.scope;editId=previous.input.edit_id;draftId=previous.input.draft_id||'';
    fragments=previous.fragments;fragmentIndex=previous.index;comparison.hidden=true;
    undoButton.hidden=!undo.length;renderFragments();
    document.getElementById('qrl-form-title').textContent=editId?'Sửa mẫu đã học':'Thêm mẫu mới';
    message('Đã khôi phục nội dung nhập trước đó. Mẫu đã lưu không thay đổi.');
  });
  async function transform(action) {
    if (busy) return;
    if (!raw.value.trim() || (action==='filter' && !output.value.trim())) {
      message(action==='filter'?'Cần có cả RAW và Kết quả đúng. Bấm Dịch trước nếu ô kết quả còn trống.':'Nhập RAW trước khi bấm Dịch.',true);return;
    }
    var input=payload(), call=++serial;lock(true);comparison.hidden=true;
    message(action==='translate'?'Đang dịch RAW…':'Đang lọc và ghép các đoạn RAW…');
    try {
      var result=await request(action==='translate'?form.dataset.translateUrl:form.dataset.filterUrl,input);
      if (call!==serial) return;
      if (action==='translate') {
        remember();output.value=result.output;keepFragment();renderFragments();
        message('Đã điền gợi ý tiếng Việt. Đối chiếu và sửa trước khi Áp dụng. Chưa lưu mẫu.'+(result.warnings.length?'\n'+result.warnings.join(' · '):''));
      } else if (!result.segments.length) {
        message('Không thấy dấu hiệu lỗi mã ký tự; giữ nguyên hai ô. Chữ đúng mã nhưng sai nội dung vẫn cần đối chiếu.');
      } else {
        var pendingDraft=draftId;remember();fragments=result.segments.map(function (pair) { return Object.assign({draft_id:pendingDraft},pair); });fragmentIndex=-1;selectFragment(0);
        message('Đã giữ '+fragments.length+' cặp đoạn cần sửa. Chọn từng đoạn để sửa và Áp dụng.'+
          (fragments.some(function (p) { return p.alignment==='context'; })?' Có đoạn được giữ rộng hơn để không ghép nhầm RAW.':'')+'\n'+result.warnings.join(' · '));
      }
    } catch (error) { if (call===serial) message(error.message,true); } finally { lock(false); }
  }
  document.getElementById('qrl-translate').addEventListener('click',function () { return transform('translate'); });
  document.getElementById('qrl-filter').addEventListener('click',function () { return transform('filter'); });
  document.getElementById('qrl-save-draft').addEventListener('click',async function () {
    if (busy || !canTeach) return;
    if (!raw.value.trim()) { message('Nhập RAW trước khi Lưu nháp.',true);return; }
    var call=++serial;lock(true);message('Đang lưu RAW vào danh sách CHỜ…');
    try {
      var result=await request(form.dataset.draftUrl,{raw:raw.value,scope:scope.value,revision:state.revision});
      if (call!==serial) return;state=result;state.drafts=state.drafts||[];draftId=result.draft_id||draftId;render();message(result.message);
    } catch (error) { if(call===serial) message(error.message,true); } finally { lock(false); }
  });
  document.getElementById('qrl-preview').addEventListener('click',async function () {
    if (busy || !form.reportValidity()) return;
    var call=++serial, input=payload();message('Đang xem thử…');
    try {
      var result=await request(form.dataset.previewUrl,input);
      if (call!==serial) return;
      document.getElementById('qrl-before').textContent=result.before;
      document.getElementById('qrl-after').textContent=result.after;comparison.hidden=false;
      message('Xem thử trong phạm vi: ' + result.scope + '. Chưa lưu mẫu.');
    } catch (error) { if (call===serial) message(error.message,true); }
  });
  form.addEventListener('submit',async function (event) {
    event.preventDefault();if (busy || !canTeach || !form.reportValidity()) return;
    var input=payload();serial++;lock(true);message('Đang lưu mẫu…');
    try {
      var result=await request(form.dataset.saveUrl,input);state=result;state.drafts=state.drafts||[];render();notifyLearning();
      // Stay on the saved pair so the user can inspect it and deliberately edit.
      var saved=state.rules.find(function (r) { return r.id===result.applied_id; });
      editId=saved?saved.id:'';draftId='';if(saved){scope.value=saved.scope;raw.value=saved.raw;output.value=saved.output;}
      keepFragment();renderFragments();
      message(result.message);document.getElementById('qrl-form-title').textContent='Mẫu đã học';
    } catch (error) { message(error.message,true); } finally { lock(false); }
  });
  document.getElementById('qrl-reload').addEventListener('click',async function () {
    if (busy) return;serial++;lock(true);
    try { state=await request(form.dataset.listUrl);state.drafts=state.drafts||[];render();message('Đã tải lại danh sách. Nội dung đang nhập được giữ nguyên.'); }
    catch(error) { message(error.message,true); } finally { lock(false); }
  });
  document.getElementById('qrl-open-waiting').addEventListener('click',function () { openLibrary('waiting'); });
  document.getElementById('qrl-open-learned').addEventListener('click',function () { openLibrary('learned'); });
  document.getElementById('qrl-tab-waiting').addEventListener('click',function () { libraryMode='waiting';render(); });
  document.getElementById('qrl-tab-learned').addEventListener('click',function () { libraryMode='learned';render(); });
  document.getElementById('qrl-close-library').addEventListener('click',closeLibrary);
  libraryModal.addEventListener('click',function (event) { if(event.target===libraryModal) closeLibrary(); });
  document.addEventListener('keydown',function (event) { if(event.key==='Escape' && !libraryModal.hidden) closeLibrary(); });
  render();
})();
