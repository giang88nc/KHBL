"""Mobile date routing: calendar months; source errors never look like empty data."""
import datetime as dt
from django.utils import timezone

def month_bounds(today=None):
    today=today or timezone.localdate();first=today.replace(day=1)
    return first,(first.replace(day=28)+dt.timedelta(days=4)).replace(day=1)-dt.timedelta(days=1)

def periods(d1,d2,unbounded=False):
    first,_=month_bounds()
    if unbounded:return [('hist',None,None),('kk',None,None)]
    result=[]
    if d1<first:result.append(('hist',d1,min(d2,first-dt.timedelta(days=1))))
    if d2>=first:result.append(('kk',max(d1,first),d2))
    return result

def prefer_live(rows,sql_prefix,reader):
    """Overlay existing KK invoices, including cancelled/reclassified ones."""
    indexed={str(r['TrnID']):r for r in rows}
    ids=[str(r['TrnID']) for r in rows if r.get('_source')=='hist']
    for start in range(0,len(ids),200):
        batch=ids[start:start+200]
        for r in reader(sql_prefix+' WHERE b.TrnID IN ('+','.join('?' for _ in batch)+')',tuple(batch),target='kk'):
            indexed[str(r['TrnID'])]={**r,'_source':'kk'}
    return list(indexed.values())
