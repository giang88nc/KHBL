"""Popup đặt hàng: trọng lượng chỉ/gram, giá được ký và tổng tiền tính lại bằng Decimal."""
from decimal import Decimal
from apps.pmv import money as M
from django.core import signing
from django.utils import timezone
from django.core.validators import DecimalValidator
from . import services as S


def price_context(c,mobile=False,stored=None):
    gold=c.query('SELECT GoldCode,GoldDesc,WeightUnit,PriceUnit FROM I_GOLD WITH (NOLOCK) ORDER BY GoldCode')
    factor_rows=c.query('SELECT dbo.fun_GetHS() factor')
    factor=Decimal(str(factor_rows[0]['factor'])) if factor_rows and factor_rows[0].get('factor') else Decimal(1)
    raw=c.query('SELECT GoldCcy,SellRate FROM I_XRATE WITH (NOLOCK) WHERE ShopID=?',(S.XRATE_SHOP,))
    rates={r['GoldCcy']:r['SellRate'] for r in raw if 'GoldCcy' in r}
    mysql=S.gia_mysql()
    rounding='1000' if str(c.sys_param('QuyCachLamTronVND')).startswith('3@') else '1'
    result={}
    saved=(stored or {}).get('rates',{})
    for g in gold:
        code=g['GoldCode']; unit='g' if g.get('WeightUnit')=='G' else 'chỉ'
        price_unit='g' if g.get('PriceUnit')=='G' else 'chỉ'
        price=mysql.get(code,{}).get('SellRate',rates.get(code))
        rate=Decimal(str(price))*1000 if price is not None else None
        if code in saved and saved[code].get('unit')==unit: rate=saved[code].get('rate')
        if price_unit!=unit: rate=None
        result[code]={'rate':format(Decimal(rate),'f') if rate is not None else None,'unit':unit,'round_unit':saved.get(code,{}).get('round_unit',rounding),
            'native_per_display':str(Decimal(1) if mobile or unit=='g' else Decimal(100)/factor),
            'name':g.get('GoldDesc') or code}
    payload={'target':c.target,'mobile':mobile,'rates':result,'at':(stored or {}).get('at') or timezone.now().isoformat()}
    return payload,signing.dumps(payload,salt='dc-pricing')


def calculate(items,rates):
    grouped={}; lines=[]; total=Decimal(0); gold_total=Decimal(0); tasks=Decimal(0); missing=[]
    for index,row in enumerate(items):
        if not row or row.get('DELETE'): continue
        code=row['GoldCode']; meta=rates.get(code)
        if not meta: raise ValueError('Loại vàng không còn trong bảng giá của phiên này. Mở lại phiếu.')
        quantity=Decimal(row.get('SL') or 1); weight=Decimal(row['GoldWeight'])*quantity
        task=Decimal(row.get('TaskPrice') or 0)*quantity
        rate=Decimal(meta['rate']) if meta.get('rate') is not None else None
        known=rate is not None and rate>0 or weight==0
        amount=M.round_vnd(weight*(rate or 0),quantum=meta.get('round_unit','1')) if known else None
        if not known: missing.append(code)
        subtotal=M.round_vnd(weight*(rate or 0)+task,quantum=meta.get('round_unit','1')) if known else None
        group=grouped.setdefault(code,{'code':code,'unit':meta['unit'],'weight':Decimal(0),'rate':rate,'amount':Decimal(0),'known':True})
        group['weight']+=weight; group['amount']+=amount or 0; group['known']=group['known'] and known
        tasks+=(subtotal-amount) if known else task; gold_total+=amount or 0; total+=subtotal or 0
        lines.append({'index':index,'code':code,'quantity':int(quantity),'gold_weight':str(weight),'gold_amount':str(amount) if amount is not None else None,
                      'amount':str(subtotal) if subtotal is not None else None,'task':str(task)})
    return {'total':None if missing else total,'gold_total':None if missing else gold_total,'tasks':tasks,'missing':sorted(set(missing)),
            'groups':list(grouped.values()),'lines':lines}


def native_lines(items,rates):
    converted=[]
    for row in items:
        row=dict(row)
        if row and not row.get('DELETE'):
            scale=Decimal(rates[row['GoldCode']]['native_per_display'])
            for key in ('GoldWeight','DiamondWeight','TotalWeight'):
                row[key]=(row[key]*scale).quantize(Decimal('0.00000001'))
                try: DecimalValidator(19,8)(row[key])
                except Exception as exc: raise ValueError('Trọng lượng vượt giới hạn lưu của PMV.') from exc
        converted.append(row)
    return converted
