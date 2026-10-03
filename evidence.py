"""Reviewed market evidence, explicit scope, and dated link availability.

A matching price sign is never used to invent or select a news catalyst.
Unreviewed RSS headlines remain reading material, not causal assertions.
"""
from __future__ import annotations
import json, re, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
HKT = ZoneInfo('Asia/Hong_Kong')
UA = 'Mozilla/5.0'
CACHE_FILE = DATA / 'link-checks.json'
try:
    CHECKS = json.loads(CACHE_FILE.read_text())
except (OSError, ValueError):
    CHECKS = {}
# Links that opened from an ordinary connection. Some publishers refuse a hosting provider's address.
try:
    VERIFIED = json.loads((DATA / 'link-verified.json').read_text())
except (OSError, ValueError):
    VERIFIED = {}

class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.hidden=0; self.title=False; self.title_parts=[]
    def handle_starttag(self,t,a):
        if t in ('script','style'): self.hidden+=1
        if t=='title': self.title=True
    def handle_endtag(self,t):
        if t in ('script','style'): self.hidden=max(0,self.hidden-1)
        if t=='title':self.title=False
    def handle_data(self,s):
        if self.title:self.title_parts.append(s)
        if not self.hidden and s.strip():self.parts.append(s.strip())

def readable_html(body: bytes) -> bool:
    if body.startswith(b'%PDF'):return True
    p=Text();p.feed(body.decode('utf-8','replace'))
    title=' '.join(p.title_parts).lower(); text=' '.join(p.parts)
    if any(x in title for x in ('just a moment','access denied','page not found','robot check','404 not found','subscribe to read')):return False
    if len(text)<400:return False
    if re.search(r'"isAccessibleForFree"\s*:\s*(?:false|"false")',body.decode('utf-8','replace'),re.I):return False
    return True

def check_link(item, force=False):
    url=item.get('link','');now=time.time();old=CHECKS.get(url,{})
    # A recorded browser read is distinct from a server HTTP check. Expire it.
    if not force and old.get('method')=='browser' and old.get('ok') and now-old.get('checked',0)<7*86400:return old
    ttl=1800 if old.get('ok') else 600
    if not force and now-old.get('checked',0)<ttl:return old
    result={'ok':False,'checked':now,'method':'http'}
    if not url.startswith('https://'):
        result['detail']='HTTPS article required';CHECKS[url]=result;return result
    try:
        req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/pdf'})
        with urllib.request.urlopen(req,timeout=8) as resp:
            body=resp.read(2500000)
            result.update(ok=resp.status==200 and readable_html(body),status=resp.status)
            if result['ok']:result['okAt']=now
            # Do not persist signed/expiring redirects such as EIA's access URL.
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        result['detail']=str(e)[:120]
        # A local DNS or timeout failure says nothing about the page; keep a success from the last two days.
        transient=isinstance(e,TimeoutError) or 'nodename nor servname' in str(e) or 'timed out' in str(e) or 'Temporary failure' in str(e)
        if transient and old.get('ok') and now-old.get('okAt',old.get('checked',0))<2*86400:
            result.update(ok=True,okAt=old.get('okAt',old.get('checked')),status=old.get('status'))
    seen=VERIFIED.get(url)
    if not result['ok'] and seen and result.get('status') not in (404,410) and 'HTTP Error 404' not in result.get('detail','') and now-(seen.get('okAt') or 0)<7*86400:
        result.update(ok=True,okAt=seen.get('okAt'),status=seen.get('status'),method='verified')
    CHECKS[url]=result
    return result

def save_checks():
    CACHE_FILE.write_text(json.dumps(CHECKS,ensure_ascii=False,indent=2))

def catalog():
    try: items=json.loads((DATA/'evidence-news.json').read_text())['items']
    except (OSError, ValueError, KeyError):return []
    out=[]
    for raw in items:
        n=dict(raw)
        try:dt=datetime.fromisoformat(n['date']).replace(tzinfo=HKT)
        except (ValueError,KeyError):continue
        n.update(ts=dt.timestamp(),time=dt.isoformat(),dateOnly=True)
        out.append(n)
    return out

def enrich(rows, incoming=None):
    """Merge reviewed context with dated direct feeds, retain the rolling week."""
    now=datetime.now(HKT); cutoff=now.date()-timedelta(days=6)
    reviewed=[n for n in catalog() if (n.get('baseline') or n['date']>=cutoff.isoformat()) and n['date']<=now.date().isoformat()]
    # A publication can disappear from a short RSS feed without disappearing from the week.
    archive_file=DATA/'news-week.json'
    try: archive=json.loads(archive_file.read_text())
    except (OSError,ValueError):archive=[]
    incoming=list(incoming or []) + [n for r in rows for n in r.get('headlines',[]) if not n.get('reviewed')] + archive
    by_url={}
    for n in incoming:
        ts=n.get('ts')
        if isinstance(ts,(int,float)) and datetime.fromtimestamp(ts,HKT).date()>=cutoff and ts<=now.timestamp() and n.get('link','').startswith('https://'):
            prev=by_url.get(n['link'])
            if prev and len(prev.get('body') or '') > len(n.get('body') or ''):
                n=dict(n); n['body']=prev['body']
            by_url[n['link']]=n
    feed=list(by_url.values())[:160]
    candidates={n['link']:n for n in reviewed+feed}
    with ThreadPoolExecutor(max_workers=12) as pool:
        results=list(pool.map(check_link,candidates.values()))
    good={u for u,c in zip(candidates,results) if c.get('ok')}
    archive_file.write_text(json.dumps([n for n in feed if n['link'] in good],ensure_ascii=False))
    save_checks()
    for r in rows:
        pool_reviewed=[dict(n) for n in reviewed if r['id'] in n.get('assets',[]) and n['link'] in good]
        matched=[n for n in reviewed if n['link'] in good and not n.get('baseline') and show_reviewed(n, r['id'])]
        extra=[n for n in feed if n['link'] in good and content_about(r['id'], n, strict=True)]
        seen=set();news=[]
        for n in matched+extra:
            if n.get('baseline') or n.get('link') not in good or n['link'] in seen:continue
            seen.add(n['link']);n=dict(n)
            if not n.get('reviewed'):n['scope']='Mentioned in the article'
            n['checkedAt']=datetime.fromtimestamp(CHECKS[n['link']]['checked'],HKT).isoformat()
            n['linkMethod']=CHECKS[n['link']]['method']
            news.append(n)
        news.sort(key=lambda n:(n.get('date') or datetime.fromtimestamp(n['ts'],HKT).date().isoformat(), bool(n.get('effects',{}).get(r['id'])),bool(n.get('reviewed'))),reverse=True)
        r['headlines']=news[:8];r['_evidence']=pool_reviewed
        r['newsWindow']=f"{cutoff:%d %b}–{now:%d %b}"
        r['newsAsOf']=max((n.get('ts',0) for n in news),default=None)
        r['verification']={'status':'Not independently matched','text':'Source shown; the second-source return has not been numerically matched for the same instrument and time.'}
        comparison={'nky':('nikkei25',1.30,0.011),'hsi':('hsi25',-1.0,0.051),'spx':('wrap25',0.5,0.051),'dji':('wrap25',0.9,0.051)}.get(r['id'])
        if comparison and r.get('quoteTime') and datetime.fromtimestamp(r['quoteTime'],ZoneInfo('America/New_York') if r['id'] in ('spx','dji') else HKT).date().isoformat()=='2026-09-25':
            nid,reported,tol=comparison;n=next((n for n in matched if n['id']==nid),None)
            if n and isinstance(r.get('dayPct'),(float,int)):
                agrees=abs(r['dayPct']-reported)<=tol
                r['verification']={'status':'Rounded return corroborated' if agrees else 'Return discrepancy','text':f"25 Sep: second-source report {reported:+.2f}%; displayed feed {r['dayPct']:+.2f}%. Comparison allows the report’s rounding; it is not a tick-by-tick match.",'source':ref(n)}
        if r['id']=='us10y' and any(n['id']=='wrap25' for n in matched) and r.get('quoteTime') and datetime.fromtimestamp(r['quoteTime'],ZoneInfo('America/New_York')).date().isoformat()=='2026-09-25':
            r['verification']={'status':'Time / benchmark discrepancy','text':'Bloomberg’s later closing report gives 5.16% and −4 bp; this CBOE/Yahoo observation differs. Do not combine these as the same close.','source':ref(next(n for n in matched if n['id']=='wrap25'))}
        r['reading']=analysis(r, r.get('dayPct'), period='latest')
        r['explanation']=r['reading']['explanation']
        r['next']=r['reading']['next']
        r['summary']=r['reading']['observed']
        r.pop('_pool',None)

# Reviewed stories may appear only on the instruments they actually discuss.
# A market-wide oil wrap is not a Bitcoin, gold, or Nikkei headline.
DISPLAY_ON={
 'nikkei25':{'nky'},'hsi25':{'hsi'},
 'ecb24':{'eur','sx5e','de10y'},'ecbfci':{'eur','sx5e','de10y'},
 'snb24':{'chf'},'rba22':{'aud'},
 'fed21':{'eur','jpy','gbp','chf','aud','cny','us10y','us30y'},
 'yen25':{'jpy'},'china20':{'cny'},'trade25':{'hsi','cny'},
 'gas25':{'ng'},'gas24':{'ng'},'storage24':{'ng'},
 'grains25':{'wh'},'grains24':{'wh'},
 'bonds25':{'us10y','us30y'},'gold25':{'au','ag'},'copper25':{'cu'},
 'wrap25':{'wti','us10y','us30y'},
 'wrap24':{'wti','us10y','us30y'},
 'wrap23':{'us10y','us30y'},
}
TITLE_RE={
 'spx':r"\b(s&p\s*500|s&p500)\b",'ndx':r"\b(nasdaq-100|nasdaq)\b",'dji':r"\bdow(\s+jones)?\b",
 'nky':r"\bnikkei\b",'hsi':r"\bhang seng\b",'sx5e':r"\b(stoxx|euro\s*stoxx)\b",
 'eur':r"\b(eur/?usd|euro)\b|\becb\b|\beuropean central bank\b",
 'jpy':r"\b(usd/?jpy|yen)\b|\bbank of japan\b|\bboj\b",
 'gbp':r"\b(gbp/?usd|sterling|pound sterling)\b|\bbank of england\b",
 'chf':r"\b(swiss franc|usd/?chf)\b|\bsnb\b|\bswiss national bank\b",
 'aud':r"\b(aud/?usd|australian dollar)\b|\breserve bank of australia\b|\brba\b",
 'cny':r"\b(usd/?cny|yuan|renminbi)\b|\bloan prime\b|\bpeople.?s bank of china\b",
 'wti':r"\b(wti|brent|crude oil|oil prices?)\b",
 'ng':r"\bnatural gas\b",'au':r"\bgold\b",'ag':r"\bsilver\b",'cu':r"\bcopper\b",'wh':r"\bwheat\b",
 'us10y':r"\b(10-year (?:treasury|yield|note|bond)s?|treasury yields?|treasuries)\b",
 'us30y':r"\b(30-year (?:treasury|yield|note|bond)s?)\b",
 'de10y':r"\b(german bund|bund yield|german 10)",
 'jp10y':r"\b(jgb|japanese government bond)",
 'gb10y':r"\bgilts?\b",
 'ca10y':r"\b(canada 10|canadian (bond|yield))",
 'btc':r"\bbitcoin\b",'eth':r"\bethereum\b",'sol':r"\bsolana\b",
 'xrp':r"\b(xrp|ripple)\b",'bnb':r"\bbinance coin\b|\bbnb\b",'doge':r"\bdogecoin\b",
}
OFF_TOPIC=re.compile(r"\b(pope|asylum|first lady|peng liyuan|manchester city|man city|football|soccer|thucydides|heat wave|heat-related)\b",re.I)

def show_reviewed(n, asset_id):
    if asset_id in (n.get('effects') or {}):
        return True
    return asset_id in DISPLAY_ON.get(n.get('id'), ())

def story_text(n):
    return "\n".join(x for x in (n.get('title'), n.get('body'), n.get('excerpt')) if x)

def content_about(asset_id, n, strict=False):
    """Match the asset in the article text, not only in the headline.

    strict: a passing mention is not enough; the headline must name the asset
    or the article text must discuss it at least twice.
    """
    text=story_text(n)
    title=n.get('title') or ''
    pat=TITLE_RE.get(asset_id)
    if not pat or not text or OFF_TOPIC.search(title):
        return False
    if not strict:
        return bool(re.search(pat, text, re.I))
    if re.search(pat, title, re.I):
        return True
    return len(re.findall(pat, "\n".join(x for x in (n.get('body'), n.get('excerpt')) if x), re.I))>=2

def title_about(asset_id, title):
    return content_about(asset_id, {'title': title})

def own_mechanism(row):
    name=row['label']
    if row['cls']=='crypto':
        return f"Higher bond yields make a non-yielding token like {name} less attractive, and risk appetite in stocks tends to carry over to crypto."
    if row['cls']=='equities':
        return f"Higher bond yields lower the value of future company profits, so {name} tends to fall when yields jump and rise when oil and inflation fears ease."
    if row['cls']=='bonds':
        return f"{name} rises when investors expect more inflation or more central-bank hikes, and falls when those fears ease; a higher yield means a lower bond price."
    if row['id'] in ('au','ag'):
        return f"{name} pays no interest, so it gets harder to hold when bond yields rise and easier when they fall. A stronger dollar also makes it dearer for foreign buyers."
    if row['id']=='cu':
        return "Copper follows growth expectations, Chinese demand and the dollar; higher yields and a firm dollar weigh on it."
    if row['id']=='wh':
        return "Wheat follows export sales, the harvest and Black Sea supply."
    if row['id']=='ng':
        return "Natural gas follows storage, production, weather and pipelines."
    if row['id']=='wti':
        return "Oil follows how much crude is available and how much buyers need. A shipping threat can add a premium; signs that tankers will get through can take it off."
    return f"{name} responds to the same rate, oil and dollar forces as the rest of the market."
PAIRS={'eur':('ECB','Fed','EUR','USD'),'jpy':('Fed','BOJ','USD','JPY'),'gbp':('BoE','Fed','GBP','USD'),'chf':('Fed','SNB','USD','CHF'),'aud':('RBA','Fed','AUD','USD'),'cny':('Fed','PBOC','USD','CNY')}
BANK_NAMES={'Fed':'Federal Reserve','ECB':'European Central Bank','BOJ':'Bank of Japan','BoE':'Bank of England','SNB':'Swiss National Bank','RBA':'Reserve Bank of Australia','PBOC':"People’s Bank of China"}

def ref(n):
    return {k:n.get(k) for k in ('title','source','link','date','scope','checkedAt','linkMethod')}

def policy_rows(row,end):
    pair=PAIRS.get(row['id'])
    if not pair:return []
    start=end-timedelta(days=6);out=[]
    for bank,ccy in zip(pair[:2],pair[2:]):
        found=[n for n in row.get('_evidence',[]) if n.get('bank')==bank and n['date']<=end.isoformat()]
        found.sort(key=lambda n:n['date'],reverse=True)
        fresh=[n for n in found if n['date']>=start.isoformat() and not n.get('baseline')]
        n=(fresh or found or [None])[0]
        out.append(dict(bank=BANK_NAMES[bank],currency=ccy,status='Recent evidence' if fresh else 'Earlier baseline' if n else 'Evidence gap',
                        text=n['excerpt'] if n else 'No verified monetary-policy update in this dashboard’s collected sources for this window. Do not infer a policy change from the exchange rate.',source=ref(n) if n else None))
    return out

def analysis(row,pct,day=None,period='daily'):
    """Use only reviewed dated evidence. Price direction is used for a consistency check."""
    end=day or datetime.now(HKT).date()
    if isinstance(end,str):end=date.fromisoformat(end)
    if day is None and row.get('quoteTime') and row.get('cls')!='crypto':
        zone=ZoneInfo('America/New_York') if row.get('cls') in ('fx','commodities') or row.get('region')=='US' else HKT
        end=datetime.fromtimestamp(row['quoteTime'],zone).date()
    start=end-timedelta(days=6)
    # a reviewed next-morning report on this day's close may be dated one day later
    next_day=lambda n: (end+timedelta(days=1)).isoformat()>=n['date'] and end.isoformat() in ((n.get('effects') or {}).get(row['id']) or {}).get('days',[])
    pool=[n for n in row.get('_evidence',[]) if not n.get('baseline') and start.isoformat()<=n['date'] and (n['date']<=end.isoformat() or next_day(n))]
    pool.sort(key=lambda n:n['date'],reverse=True)
    by={n['id']:n for n in pool}; banks=policy_rows(row,end)
    direct=[n for n in pool if row['id'] in n.get('effects',{})]
    weekly_only=lambda n: n['effects'][row['id']].get('weekly')
    if period=='weekly':
        direct=[n for n in direct if weekly_only(n)] or direct
    else:
        direct=[n for n in direct if not weekly_only(n)]
        same=[n for n in direct if end.isoformat() in (n['effects'][row['id']].get('days') or [n['date']])]
        direct=same or direct
    used=[];expected=0;confidence='Low';label='Context only';mechanism='';outlook='';fact=''
    # Weekly gas rise has its own supply evidence; Friday profit-taking is a counterpoint.
    if row['id']=='ng' and period=='weekly' and 'gas24' in by:
        direct=[by['gas24']]+[n for n in direct if n['id']!='gas24']
    if direct:
        n=direct[0];e=n['effects'][row['id']];used=[n]
        fact=n['excerpt'];mechanism=e['mechanism'];outlook=e['outlook'];expected=e['sign'];label='Reported asset driver';confidence='Medium'
        if row['id']=='au':
            mechanism+=' Oil is in this argument only because the gold report links cheaper oil to lower rate fears. Gold and crude are not the same market.'
        if row['id']=='ng' and period=='weekly' and 'gas25' in by:
            fact=by['gas24']['excerpt']+' '+by['gas25']['excerpt']
            mechanism='Thursday’s rise to $3.2 followed a 53 bcf storage build, well under the 76 bcf five-year average, and output heading toward an 11-week low of 108.4 bcfd in Louisiana and West Virginia. Friday’s fall to $3.15 was profit-taking after TC Energy’s Columbia Gas line in West Virginia declared force majeure and produced the largest one-day gain since January. The weekly gain is what is left after that Friday sale.'
            outlook='The weekly rise holds if the Columbia Gas force majeure stays in place and the storage surplus keeps shrinking from 95 Bcf. It fades if the fault is repaired, Louisiana and West Virginia output recovers, or the weather turns milder. The next EIA storage release is the check.'
            used.append(by['gas25'])
        if row['id']=='nky' and period=='weekly' and 'nikkei25' in by and 'nky24' in by:
            fact=('Japan traded only on Thursday and Friday after the Silver Week holiday from 19 to 23 September, when the Philadelphia Semiconductor Index rose more than 8% after Meta’s new AI agent and a new Alibaba AI chip. '
                  'On Thursday the Nikkei rose 0.76% to 65,513.99 as those chip shares caught up, but Topix fell 0.39% and bank shares weakened as bond yields rose.')
            mechanism=('Friday was a different session: the Nikkei rose another 1.30% to 66,364.20 and Topix rose 1.31%, with Tokyo Electron up 4.82% and Advantest up 2.84% as the largest contributors, and bank shares up 4.08% ahead of Monday’s interim-dividend deadline. '
                       'The weekly gain adds those two sessions, a narrow chip catch-up on Thursday and a broader rise in chips and banks on Friday.')
            outlook='The rise can continue if Tokyo Electron and Advantest keep attracting buyers after the holiday catch-up. It weakens if that chip buying fades once Monday’s dividend cutoff passes, or if a drop like Friday’s fall in SoftBank spreads.'
            used=[by['nky24'], by['nikkei25']]
            expected=1
    elif row['cls']=='fx':
        fresh=[b for b in banks if b['status']=='Recent evidence']
        fact=' '.join(f"{b['currency']}: {b['text']}" for b in banks if b.get('text'))
        if not fact:fact='Both central banks were checked in the collected evidence; no usable policy update was available for this window.'
        used=[n for n in pool if any(b.get('source',{} ) and b['source']['link']==n['link'] for b in banks)]
        base,quote=PAIRS[row['id']][2:]
        mechanism=f"A rise in expected {base} interest rates relative to {quote} can favour {base} and lift this pair. The reverse can lower it. What matters is the surprise relative to expectations, not just the current rate level."
        outlook=f"No firm one-way call. A wider expected rate advantage for {base} points higher; an advantage shifting toward {quote} points lower. New inflation data or either bank’s guidance can change that comparison."
        if row['id']=='chf' and 'snb24' in by and any(n.get('bank')=='Fed' for n in pool):
            mechanism='The Fed’s tightening message contrasts with the SNB’s zero rate. A larger expected US interest return can attract dollars and support USD/CHF, although safe-haven franc demand can offset it.';expected=1
            outlook='Conditional upward pressure on USD/CHF if the US rate advantage widens. New Swiss intervention signals or stronger safe-haven demand could reverse it.'
        elif row['id'] in ('gbp','cny') and any(n.get('bank')=='Fed' for n in pool):
            expected=-1 if row['id']=='gbp' else 1
            mechanism='The recent Fed message favours higher US interest returns. With no equivalent new tightening decision verified on the other side, this can support the dollar. It is a policy backdrop, not proof of the session’s cause.'
        if row['id']=='aud' and 'rba22' in by:
            fact=(by['rba22']['excerpt']+' '+(next((n['excerpt'] for n in pool if n.get('bank')=='Fed'), ''))) .strip()
            mechanism='A less urgent Australian tightening case alongside stronger US rate expectations can reduce the appeal of AUD relative to USD. This points toward a lower AUD/USD, but commodity demand can offset it.'
            expected=-1
            outlook='Conditional downward pressure if the US rate advantage grows. A stronger RBA inflation warning or improving Chinese commodity demand would weaken that view.'
        elif row['id']=='eur' and 'ecb24' in by:
            fact=(by['ecb24']['excerpt']+' '+(next((n['excerpt'] for n in pool if n.get('bank')=='Fed'), ''))) .strip()
            mechanism='Both banks face inflation pressure, so tightening alone cannot explain EUR/USD. The pair tends to rise when expected euro rates improve relative to US rates, and fall when the US advantage grows.'
        label='Two-central-bank comparison'
    else:
        name=row['label']
        specific=[n for n in pool if show_reviewed(n, row['id']) and not str(n.get('id','')).startswith('wrap')]
        specific.sort(key=lambda n: n['date'], reverse=True)
        wrap=next((n for n in pool if str(n.get('id','')).startswith('wrap') and show_reviewed(n, row['id'])), None)
        any_wrap=next((n for n in pool if str(n.get('id','')).startswith('wrap')), None)
        if row['cls']=='crypto' or (not specific and not wrap):
            label='Market context'
            if any_wrap:
                used=[any_wrap];fact=any_wrap['excerpt']
            mechanism=own_mechanism(row)
            outlook=f"Direction for {name} follows the same market forces: watch Treasury yields, oil and the dollar."
        elif row['id']=='ag' and any(n['id']=='gold25' for n in specific):
            used=[n for n in specific if n['id']=='gold25'][:1]
            fact=used[0]['excerpt']
            label='Gold context, not a silver report'
            mechanism='This note is about gold. Silver can move with gold, but it also depends on industrial use, so the gold note does not prove why silver moved. Oil is not treated as a silver story.'
            outlook='No directional call for silver from the gold note. A silver call needs a report that names silver, plus a separate check on factory demand.'
        elif specific and row['cls']=='bonds':
            n=specific[0];used=[n];fact=n['excerpt'];label='Bond-market evidence'
            mechanism=f"{name} is a yield, not a share price. Investors demand more yield when they worry about inflation or extra borrowing, and accept less when that worry fades. One manager’s view, or one central-bank comment, is an argument about that yield. It is not proof of why the printed change happened."
            outlook=f"No one-way call on {name}. The yield can fall if inflation pressure eases and buyers return, and rise if inflation or new borrowing returns. The next inflation print is the check."
        elif specific and row['id']=='sx5e':
            n=specific[0];used=[n];fact=n['excerpt'];label='Euro-area policy background'
            mechanism=f"{name} tracks large euro-area companies. ECB inflation and rate news can change what investors will pay for their future profits. The bulletin is regional background. It does not, by itself, prove the day’s move, and an oil headline is not the cause."
            outlook=f"No firm call on {name} from this bulletin. A call needs earnings or a policy surprise that names European shares."
        elif wrap and row['id']=='wti':
            used=[wrap];fact=wrap['excerpt'];soft=wrap['id']=='wrap25';label='Oil-market report'
            mechanism='If traders expect more oil to pass through Hormuz, they need less insurance against a shortage, and the extra premium in the price can fall. Threats to shipping do the opposite: buyers pay more for the barrels they can get. Talks are not the same thing as tankers actually moving.'
            expected=-1 if soft else 1
            outlook='The price leans down if shipping really normalises, and up if talks fail or supply is interrupted again. Watch tanker flows, not only political statements.'
        elif wrap and row['cls']=='bonds':
            used=[wrap];fact=wrap['excerpt'];soft=wrap['id']=='wrap25';label='Rates background'
            if 'bonds25' in by:used.append(by['bonds25'])
            mechanism=f"If investors expect inflation to stay high, they demand a higher yield on {name}. A higher yield means existing bonds are worth less. If oil prices fall and that inflation fear eases, the yield can fall back. This is a bond argument, not a reason to borrow an equity or crypto headline."
            expected=0 if soft else 1
            outlook=f"No one-way call on {name}. The yield rises again if inflation or government borrowing pressure returns, and falls if that pressure fades. Check the next inflation print before treating the move as settled."
        else:
            label='Market context'
            src=wrap or any_wrap
            if src:
                used=[src];fact=src['excerpt']
            mechanism=own_mechanism(row)
            outlook=f"Direction for {name} follows the same market forces: watch Treasury yields, oil and the dollar."
    if period=='weekly' and row['cls']=='bonds' and 'wrap23' in by and 'wrap25' in by and any(str(n.get('id','')).startswith('wrap') for n in used):
        fact=by['wrap23']['excerpt']+' '+by['wrap25']['excerpt']+f" For {row['label']}, those are the two sides of the week: midweek inflation pressure, then Friday’s oil pullback. The weekly yield change nets them."
        used=[by['wrap23'],by['wrap25']]+[n for n in used if not str(n.get('id','')).startswith('wrap')]
        expected=0
    if period=='weekly' and row['id']=='wti' and 'wrap25' in by and 'wrap24' in by:
        fact=by['wrap24']['excerpt']+' '+by['wrap25']['excerpt']+' The weekly oil change nets those two sessions: Brent near $107 on the way up, then around $104 after the Hormuz proposal.'
        used=[by['wrap24'],by['wrap25']]
        expected=0
    if not fact:
        mechanism=mechanism or own_mechanism(row)
        outlook=outlook or f"Direction for {row['label']} follows the same market forces: watch Treasury yields, oil and the dollar."
    if row['cls']=='fx' and direct:
        mechanism+=' Central-bank policy remains a separate channel; compare both sides below.'
    number=f"{pct:+.2f}%" if isinstance(pct,(int,float)) else 'not available'
    noun='yield change' if row.get('kind')=='yield' else 'price change'
    observed=f"Observed {noun}: {number}. "
    if row['cls']=='fx' and isinstance(pct,(int,float)):
        base,quote=PAIRS[row['id']][2:];observed+=f"This means {base} {'strengthened' if pct>0 else 'weakened' if pct<0 else 'was unchanged'} against {quote}. "
    if expected and isinstance(pct,(int,float)) and pct!=0:
        if (pct>0)==(expected>0):
            observed+='The direction agrees with the mechanism above. Agreement is not a measurement of how large that cause was.'
        else:
            observed+='The direction goes against this one explanation, so the cause is not settled. Another force, or a different timestamp, is still in play.'
            confidence='Low'
    else:observed+='The sources do not say how much of this move they explain, so the cause stays open.'
    if row.get('kind')=='yield':observed+=' This percentage is a change in the yield, not a bond-price return.'
    lead=((used[0].get('effects') or {}).get(row['id']) or {}) if used else {}
    points=[p for p in lead.get('points') or [] if p.get('text')]
    next_points=[p for p in lead.get('nextPoints') or [] if p.get('text')]
    refs={n['link']:ref(n) for n in used}
    for n in used:
        for extra in n.get('also') or []:
            if extra.get('link'):refs.setdefault(extra['link'],{k:extra.get(k) for k in ('title','source','link','date')})
    # Bullet summaries cite their own articles; the policy baselines are only for the prose fallback.
    for b in banks if not points else []:
        if b.get('source'):refs[b['source']['link']]=b['source']
    if row['id']=='us10y' and end.isoformat()=='2026-09-25' and 'wrap25' in by:
        observed+=' Cross-source discrepancy: Bloomberg’s later closing wrap reports a 4 bp decline to 5.16%, while this CBOE/Yahoo observation shows a different move. Timing and instrument comparability remain unresolved; do not treat them as a verified match.'
        refs[by['wrap25']['link']]=ref(by['wrap25']);confidence='Low'
    # Morning-meeting summary: market facts, then how they caused this percentage move.
    def _clip(text, n=2):
        parts=re.split(r'(?<=[.!?])\s+', (text or '').strip())
        parts=[p.strip() for p in parts if p.strip()]
        return ' '.join(parts[:n])
    explanation=_clip(fact, 2)
    cause=_clip(mechanism, 2)
    if cause: explanation=(explanation+' '+cause).strip()
    whats_next=_clip(outlook, 2)
    if points:explanation=' '.join(p['text'] for p in points)
    if next_points:whats_next=' '.join(p['text'] for p in next_points)
    return dict(driver=label,catalyst=fact,mechanism=mechanism,observed=observed,next=whats_next,explanation=explanation,
                points=points,nextPoints=next_points,
                confidence=confidence,confidenceMeaning='Qualitative confidence in this explanation; not a forecast probability or backtested accuracy.',
                evidence=list(refs.values()),banks=banks,evidenceThrough=end.isoformat(),period=period)

def pulse(rows):
    by={r['id']:r for r in rows};gauges=[]
    specs=[('spx','US equities','Broad US large-company performance; not a global equity index.'),('eur','Relative currencies','EUR versus USD: measures this pair, not a trade-weighted dollar index.'),('us10y','Cost of money','A reference for long-term US borrowing costs. Yield up means existing bond price down.'),('wti','Energy costs','US crude supply and inflation exposure; a price rise can reflect supply trouble or strong demand.'),('btc','Crypto sentiment','The largest crypto benchmark; does not represent every token or all risk appetite.')]
    for id,role,why in specs:
        r=by.get(id,{})
        gauges.append(dict(id=id,cls=r.get('cls'),label=r.get('label',id),role=role,why=why,value=r.get('dayPct'),bp=r.get('dayBps'),asOf=r.get('quoteTime'),window='rolling 24h' if id=='btc' else 'latest available session'))
    alerts=[]
    for cls in ('equities','fx','commodities','bonds','crypto'):
        rr=[r for r in rows if r.get('cls')==cls and isinstance(r.get('dayBps') if cls=='bonds' else r.get('dayPct'),(int,float))]
        if not rr:continue
        r=max(rr,key=lambda r:abs(r.get('dayBps') if cls=='bonds' else r['dayPct']))
        alerts.append(dict(cls=cls,label=r['label'],value=r.get('dayBps') if cls=='bonds' else r['dayPct'],unit='bp' if cls=='bonds' else '%',window='rolling 24h' if cls=='crypto' else 'latest available observation',asOf=r.get('quoteTime')))
    return dict(mood='evidence',gauges=gauges,alerts=alerts,
        headline='Five lenses on the market — read the channels, then check the evidence.',
        how='These benchmarks are a compact starting point across the five asset classes, not a buy/sell score. A move can have several causes. Different exchanges close at different times, and crypto uses a rolling 24-hour window. Compare equivalent timestamps before drawing conclusions.',
        fresh='Price updates, news publication times and analysis review dates are separate. The source and timestamp on each card take priority over the page refresh clock.')
