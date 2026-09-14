"""Popup đặt hàng: trọng lượng chỉ/gram, giá được ký và tổng tiền tính lại bằng Decimal."""
from decimal import Decimal
from apps.pmv import money as M
from django.core import signing
from django.utils import timezone
from django.core.validators import DecimalValidator
from . import services as S


def keep_weight_precision(data,initial):
    data=data.copy()
    for index,old in enumerate(initial):
        prefix=f'items-{index}-'
        if data.get(prefix+'Mode')=='stock': continue
        if data.get(prefix+'GoldCode')!=old.get('GoldCode') or data.get(prefix+'Mode')!=old.get('Mode'): continue
        try:
            shown=Decimal(data.get(prefix+'GoldWeight','').replace(',','.'))
            if shown==old['GoldWeight'].quantize(Decimal('.001')):
                data[prefix+'GoldWeight']=str(old['GoldWeight'])
        except Exception: pass
    return data


def compact_data(c,post,old_lines):
    """Hàng sẵn luôn dùng thông tin kho; trường ẩn không được sửa lịch sử món cũ."""
    from . import deposit_orders as O
    data=post.copy(); errors={}
    try: count=min(max(int(post.get('items-TOTAL_FORMS',0)),0),60)
    except ValueError: count=0
    for i in range(count):
        prefix=f'items-{i}-'
        if post.get(prefix+'DELETE'): continue
        old=O.item_info(old_lines[i]) if i<len(old_lines) else {}
        data[prefix+'Notes']=old.get('Notes') or ''
        data[prefix+'SL']=str(old.get('SL') or 1)
        if post.get(prefix+'Mode')!='stock':
            data[prefix+'ProductCode']='Khách đặt'
            data[prefix+'DiamondWeight']=post.get(prefix+'DiamondWeight','0') if old else '0'
            continue
        code=post.get(prefix+'ProductCode','').strip()[:80]
        rows=c.query("SELECT p.ProductCode,p.ProductDesc,p.GoldCode,p.TotalWeight,p.DiamondWeight,p.RingSize,p.TaskPrice,g.WeightUnit "
            "FROM T_PRODUCT p WITH (NOLOCK) LEFT JOIN I_GOLD g WITH (NOLOCK) ON g.GoldCode=p.GoldCode WHERE p.Status='I' AND p.ProductCode=?",(code,))
        if len(rows)!=1:
            errors[i]='Mã không đúng hoặc hàng không còn sẵn trong kho. Quét/nhập lại mã.'; continue
        row=rows[0]; scale=Decimal(100) if row['WeightUnit']=='L' else Decimal(1)
        values={k:row[k] for k in ('ProductCode','ProductDesc','GoldCode')}
        values.update(TotalWeight=Decimal(row.get('TotalWeight') or 0)/scale,DiamondWeight=Decimal(row.get('DiamondWeight') or 0)/scale,
                      TaskPrice=Decimal(row.get('TaskPrice') or 0)*1000,Size=row.get('RingSize') or '')
        values['GoldWeight']=values['TotalWeight']-values['DiamondWeight']
        for key in ('TotalWeight','DiamondWeight','GoldWeight'): values[key]=values[key].quantize(Decimal('.00000001'))
        values['TaskPrice']=values['TaskPrice'].quantize(Decimal('.001'))
        # MoneyInput đọc chuỗi Việt ở phiên form mới.
        if post.get('money_format')=='vi': values['TaskPrice']=format(values['TaskPrice'],'f').replace('.',',')
        for key,value in values.items(): data[prefix+key]=str(value)
    return data,errors


def price_context(c,mobile=False,stored=None):
    gold=c.query('SELECT GoldCode,GoldDesc,WeightUnit,PriceUnit FROM I_GOLD WITH (NOLOCK) ORDER BY GoldCode')
    factor_rows=c.query('SELECT dbo.fun_GetHS() factor')
    factor=Decimal(str(factor_rows[0]['factor'])) if factor_rows and factor_rows[0].get('factor') else Decimal(1)
    mysql=S.gia_mysql()
    rounding='1000' if str(c.sys_param('QuyCachLamTronVND')).startswith('3@') else '1'
    result={}
    saved=(stored or {}).get('rates',{})
    for g in gold:
        code=g['GoldCode']; unit='g' if g.get('WeightUnit')=='G' else 'chỉ'
        price_unit='g' if g.get('PriceUnit')=='G' else 'chỉ'
        price=mysql.get(code,{}).get('SellRate')
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
