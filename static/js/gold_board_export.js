(() => {
  'use strict';
  const board = document.querySelector('[data-gold-board]');
  const ready = (async () => {
    await document.fonts.ready;
    await Promise.all([...board.querySelectorAll('img')].map(img => img.decode()));
    return {width:board.getBoundingClientRect().width,height:board.getBoundingClientRect().height,rows:Number(document.body.dataset.rowCount)};
  })();
  // Giữ đúng pipeline html2canvas 1.4.1 của trang mẫu: chụp lớn rồi thu về ~950k pixel.
  window.goldBoard = {ready, async exportPNG() {
    const {width, height, rows} = await ready;
    if (!rows) throw new Error('Chưa có loại vàng được ghim để xuất ảnh.');
    if (typeof window.html2canvas !== 'function') throw new Error('Chưa tải được bộ xuất ảnh. Hãy mở lại bảng.');
    const targetScale = Math.min(1.4, Math.sqrt(950000 / (width * height)));
    const source = await window.html2canvas(board, {
      backgroundColor:'#8f0808', scale:2,
      useCORS:true, logging:false, imageTimeout:15000, foreignObjectRendering:false,
      async onclone(doc) {
        const clone = doc.querySelector('[data-gold-board]');
        clone.style.boxShadow = 'none';
        clone.style.background = '#980909';
        await doc.fonts.ready;
      }
    });
    const output = document.createElement('canvas');
    output.width = Math.max(1, Math.round(width * targetScale));
    output.height = Math.max(1, Math.round(height * targetScale));
    const ctx = output.getContext('2d', {alpha:false});
    ctx.fillStyle = '#8f0808'; ctx.fillRect(0,0,output.width,output.height);
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(source,0,0,source.width,source.height,0,0,output.width,output.height);
    const blob = await new Promise(resolve => output.toBlob(resolve,'image/png',1));
    if (!blob) throw new Error('Không tạo được ảnh PNG. Vui lòng thử lại.');
    return blob;
  }};
})();
