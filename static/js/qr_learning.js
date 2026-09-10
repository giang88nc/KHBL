(function () {
  'use strict';
  var root = document.getElementById('qr-learning');
  if (!root) return;
  var form = document.getElementById('qrl-form'), raw = document.getElementById('qrl-raw');
  var output = document.getElementById('qrl-output'), scope = document.getElementById('qrl-scope');
  var status = document.getElementById('qrl-status'), comparison = document.getElementById('qrl-comparison');
  var list = document.getElementById('qrl-rules'), count = document.getElementById('qrl-count');
  var state = JSON.parse(document.getElementById('qrl-initial-state').textContent);
  var canTeach = root.dataset.canTeach === '1', busy = false, serial = 0, editId = '';
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
  function payload() { return {raw:raw.value, output:output.value, scope:scope.value, revision:state.revision, edit_id:editId}; }
  function notifyLearning() {
    try { localStorage.setItem('khbl-qr-learning-revision', String(state.revision) + ':' + Date.now()); } catch (_) {}
  }
  function button(text, action) {
    var b = document.createElement('button'); b.type='button';b.textContent=text;
    b.className='khbl-btn khbl-btn--outline khbl-btn--sm'; b.dataset.write='1'; b.disabled=!canTeach;
    b.addEventListener('click',action);return b;
  }
  function render() {
    list.replaceChildren();count.textContent='(' + state.rules.filter(function (r) { return r.active; }).length + ' đang dùng / ' + state.rules.length + ')';
    if (!state.rules.length) { var empty=document.createElement('p');empty.className='qrl-empty';empty.textContent='Chưa có mẫu được dạy. Nhập cặp RAW và kết quả đúng ở trên.';list.appendChild(empty); }
    state.rules.slice().reverse().forEach(function (rule) {
      var row=document.createElement('div');row.className='qrl-rule' + (rule.active?'':' is-off');
      [[names[rule.scope] + (rule.active?' · Đang dùng':' · Đã tắt'),rule.raw],['Kết quả đúng',rule.output]].forEach(function (item) {
        var col=document.createElement('div'), label=document.createElement('small'), text=document.createElement('p');
        label.textContent=item[0];text.textContent=item[1];col.appendChild(label);col.appendChild(text);row.appendChild(col);
      });
      var actions=document.createElement('div');actions.className='qrl-rule-actions';
      actions.appendChild(button('Sửa',function () {
        if (busy) return;serial++;editId=rule.id;raw.value=rule.raw;output.value=rule.output;scope.value=rule.scope;
        comparison.hidden=true;document.getElementById('qrl-form-title').textContent='Sửa mẫu đã học';
        message('Đang sửa mẫu. Bấm Áp dụng để lưu kết quả mới.');raw.focus();form.scrollIntoView({block:'start',behavior:'smooth'});
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
    if (busy) return;serial++;editId='';form.reset();comparison.hidden=true;
    document.getElementById('qrl-form-title').textContent='Thêm mẫu mới';message('');raw.focus();
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
      var result=await request(form.dataset.saveUrl,input);state=result;render();notifyLearning();
      // Stay on the saved pair so the user can inspect it and deliberately edit.
      var saved=state.rules.find(function (r) { return r.id===result.applied_id; });
      editId=saved?saved.id:'';if(saved){scope.value=saved.scope;raw.value=saved.raw;output.value=saved.output;}
      message(result.message);document.getElementById('qrl-form-title').textContent='Mẫu đã học';
    } catch (error) { message(error.message,true); } finally { lock(false); }
  });
  document.getElementById('qrl-reload').addEventListener('click',async function () {
    if (busy) return;serial++;lock(true);
    try { state=await request(form.dataset.listUrl);render();message('Đã tải lại danh sách. Nội dung đang nhập được giữ nguyên.'); }
    catch(error) { message(error.message,true); } finally { lock(false); }
  });
  render();
})();
