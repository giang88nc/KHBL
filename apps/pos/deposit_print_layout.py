"""Deposit-only paper layout, independent from sales guarantee layout."""
import copy
import json
import math
from apps.pmv.models import PmvState
from . import gdb_layout as G

KEY = 'deposit_print_layout'
BLOCKS = [
    dict(key='heading',ten='Tiêu đề + QR',sel='.customer-heading',left=3.38,top=19.05,w=93.24,h=9.05,fs=11),
    dict(key='info',ten='Thông tin khách / đặt',sel='.customer-info',left=3.38,top=29.52,w=93.24,h=8,fs=8.5),
    dict(key='items',ten='Bảng món giao khách',sel='.customer-content',left=3.38,top=38,w=93.24,h=16,fs=8.3),
    dict(key='reminder',ten='Dòng lưu ý',sel='.customer-reminder',left=3.38,top=55.24,w=93.24,h=1.9,fs=7.5),
    dict(key='employee',ten='Tên NV giao khách',sel='.customer-employee',left=50,top=57.5,w=46.62,h=2,fs=9),
    dict(key='store',ten='Hai bản tiệm giữ',sel='.store-copies',left=17.57,top=76.19,w=79.05,h=21.9,fs=6.7),
]

def defaults():
    return {**{b['key']:{k:b[k] for k in ('left','top','w','h','fs')} for b in BLOCKS},
            '_in':copy.deepcopy(G.IN_MAC_DINH)}

def clean(data):
    if not isinstance(data,dict): raise ValueError('Bố cục phải là đối tượng JSON.')
    result=defaults()
    limits={'left':(0,99),'top':(0,99),'w':(1,100),'h':(1,100),'fs':(5,24)}
    for b in BLOCKS:
        values=data.get(b['key'],{})
        if not isinstance(values,dict): raise ValueError('Khối cấu hình không hợp lệ.')
        for key,(low,high) in limits.items():
            try: number=float(values.get(key,result[b['key']][key]))
            except (ValueError,TypeError): raise ValueError('Giá trị vị trí hoặc cỡ chữ không hợp lệ.')
            if not math.isfinite(number) or not low<=number<=high: raise ValueError('Vị trí hoặc cỡ chữ vượt giới hạn.')
            result[b['key']][key]=round(number,2)
        v=result[b['key']]
        if v['left']+v['w']>100.01 or v['top']+v['h']>100.01:
            raise ValueError('Khối '+b['ten']+' vượt ra ngoài tờ giấy.')
    printer=data.get('_in',{})
    if not isinstance(printer,dict): raise ValueError('Cấu hình máy in không hợp lệ.')
    for key,(low,high) in G.IN_GIOI_HAN.items():
        if key not in printer: continue
        try: number=float(printer[key])
        except (ValueError,TypeError): raise ValueError('Giá trị máy in không hợp lệ.')
        if not math.isfinite(number) or not low<=number<=high:
            raise ValueError('Giá trị máy in vượt giới hạn.')
    if printer.get('kho','auto') not in G.IN_KHO or printer.get('canh','giua') not in G.IN_CANH:
        raise ValueError('Khổ giấy hoặc căn lề không hợp lệ.')
    G._nhan_in(result['_in'],printer)
    # Reject NaN/Infinity rather than emitting them into CSS.
    if any(not math.isfinite(float(result['_in'][k])) for k in ('dx','dy','ty_le')):
        raise ValueError('Giá trị máy in không hợp lệ.')
    return result

def load():
    raw=PmvState.get(KEY,'')
    if raw:
        try: return clean(json.loads(raw))
        except (ValueError,TypeError): pass
    result=defaults()
    result['_in']=G.load()[G.IN_KEY].copy()
    return result

def save(data):
    result=clean(data)
    PmvState.set(KEY,json.dumps(result,ensure_ascii=False))
    return result

def css(layout=None):
    state=clean(layout) if layout is not None else load()
    rules=[]
    for b in BLOCKS:
        v=state[b['key']]
        rules.append(b['sel']+'{position:absolute!important;left:%g%%!important;top:%g%%!important;width:%g%%!important;height:%g%%!important;--dc-font:%gpt;}' % tuple(v[k] for k in ('left','top','w','h','fs')))
    rules.append(G.css_in(state['_in']))
    return '\n'.join(rules)
