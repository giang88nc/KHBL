"""Render synthetic receipts for visual QA; no PMV or application database access."""
import datetime as dt
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.test_prices')
import django
django.setup()
import segno
from django.template.loader import render_to_string
from apps.pos import deposit_print_layout as layout

root = Path(__file__).resolve().parents[1]
out = root / 'logs' / 'deposit-print-qa'
out.mkdir(parents=True, exist_ok=True)
items = [dict(ProductDesc='Nhẫn nam chạm rồng', ProductCode='SP001', GoldCode='24K',
              Size='23', GoldWeight=6.303, unit='chỉ', SL=1),
         dict(ProductDesc='Nhẫn nữ theo mẫu khách', ProductCode='', GoldCode='18K',
              Size='12', GoldWeight=0.8, unit='chỉ', SL=1)]
base = dict(pk='TDC260900000006',receipt_qr=segno.make('TDC260900000006',micro=False).svg_data_uri(),
    h=dict(CustID='TEST',CustName='Nguyễn Minh Anh',Phone='0901234567',Address='1276 Kha Vạn Cân, P. Linh Xuân, TP. HCM',
           TrnDate=dt.date(2026,9,12),TienCoc=1500000,CashPay=1000000,CardPay=500000),
    work=dict(promise_date=dt.date(2026,9,30),responsible='Trần Ngọc Phụng',estimate=15000000),
    view_lines=items,view_notes=[],deposit_amount_words='Một triệu năm trăm nghìn đồng.',
    deposit_print_calibration=layout.css(layout.defaults()))
for name, overrides in [('normal',{'view_lines':items+[dict(items[0],ProductCode='SP003',ProductDesc='Vòng hoa sen')]}), ('many',{'view_lines':items*8,'view_notes':[
    dict(display_date=dt.date(2026,9,12),text='Khách chốt mẫu, hẹn thử size trước khi nhận hàng.')]*4}),
    ('empty',{'view_lines':[]}), ('overflow',{'view_lines':items*50})]:
    html=render_to_string('pos/dat_coc_print.html',{**base,**overrides})
    html=html.replace('/static/',(root/'static').as_uri()+'/')
    (out/f'{name}.html').write_text(html,encoding='utf-8')
print(out)
config_html=render_to_string('pos/deposit_print_config.html',dict(blocks=layout.BLOCKS,csrf_token='fixture-only',
    request=SimpleNamespace(user=SimpleNamespace(username='QA',get_full_name=lambda:'QA',is_authenticated=False),path='/he-thong/mau-in-coc/'),
    layout=layout.defaults(),defaults=layout.defaults(),preview_url='/sample/?configure=1',trn_id=''))
(out/'config.html').write_text(config_html,encoding='utf-8')
