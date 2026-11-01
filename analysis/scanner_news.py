"""Direct, dated news context. No generated buy calls and no price-alert gate."""
import re
import time
from datetime import datetime,timezone
from urllib.parse import urlencode,urlsplit
from analysis.scanner_reference_data import ET


def article(row,provider,kind,ticker,received):
    symbols=row.get('tickers',[]) if provider=='Massive' else [row.get('symbol')]
    if ticker not in symbols:return None
    title=row.get('title');raw=row.get('published_utc') if provider=='Massive' else row.get('publishedDate')
    if not title or not raw:raise ValueError('Headline or publication date missing')
    dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
    assumed=dt.tzinfo is None
    if assumed:dt=dt.replace(tzinfo=ET)
    published=dt.timestamp()
    if published>received+60:raise ValueError('Future-dated news item')
    url=row.get('article_url') if provider=='Massive' else row.get('url')
    parsed=urlsplit(url or '')
    if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username:url=None
    publisher=row.get('publisher') or row.get('site') or provider
    if isinstance(publisher,dict):publisher=publisher.get('name') or provider
    return {'headline':title,'url':url,'provider':provider,'publisher':publisher,'kind':kind,
        'published_at':published,'published_raw':raw,'publication_timezone_note':'Naive FMP date interpreted as America/New_York' if assumed else 'Provider timezone explicit',
        'first_seen_at':received,'catalyst_assessment':'unclassified; read original source'}


class NewsContext:
    def __init__(self,provider):
        self.provider=provider;self.rows={};self.first_seen={}

    def fetch(self,ticker):
        if not re.fullmatch(r'[A-Z0-9.\-]{1,16}',ticker):raise ValueError('Invalid ticker')
        items=[];errors={};coverage={};invalid=0
        endpoints=[('Massive','article','https://api.massive.com/v2/reference/news?'+urlencode({'ticker':ticker,'limit':20,'order':'desc','sort':'published_utc'}),'massive'),
            ('FMP','article','https://financialmodelingprep.com/stable/news/stock?'+urlencode({'symbols':ticker,'limit':20}),'fmp'),
            ('FMP','press_release','https://financialmodelingprep.com/stable/news/press-releases?'+urlencode({'symbols':ticker,'limit':20}),'fmp')]
        for provider,kind,url,credential in endpoints:
            source=provider+' '+kind
            try:
                data=self.provider.get(url,credential);received=time.time()
                rows=data.get('results',[]) if provider=='Massive' else data
                if not isinstance(rows,list):raise ValueError('Unexpected news response')
                coverage[source]={'checked_at':received,'returned':len(rows),'possibly_more':len(rows)>=20 or bool(isinstance(data,dict) and data.get('next_url'))}
                for row in rows:
                    try:
                        item=article(row,provider,kind,ticker,received)
                        if not item:continue
                        key=(provider,item['url'],item['headline'],item['published_raw'])
                        item['first_seen_at']=self.first_seen.setdefault(key,received)
                        items.append(item)
                    except (ValueError,TypeError,AttributeError):invalid+=1
            except Exception as exc:errors[source]=str(exc)
        now=time.time()
        self.first_seen={k:v for k,v in self.first_seen.items() if now-v<7*86400}
        # Retain distinct provider observations; syndicated copies are explicitly possible.
        unique={(r['provider'],r['url'],r['headline'],r['published_raw']):r for r in items}
        items=sorted(unique.values(),key=lambda r:r['published_at'],reverse=True)
        result={'ticker':ticker,'checked_at':now,'status':'partial' if errors and coverage else 'unavailable' if errors else 'available',
            'articles':items[:5],'omitted_count':max(0,len(items)-5),'invalid_items':invalid,
            'sources':coverage,'errors':errors,'refresh_seconds':60,
            'note':'Selected-symbol REST context, not a breaking-news wire. Massive documents hourly news updates. FMP delivery latency is unverified. Syndicated articles may repeat. First seen is this process observation, not original publication.'}
        self.rows[ticker]=result
        if len(self.rows)>100:
            oldest=min(self.rows,key=lambda t:self.rows[t]['checked_at']);del self.rows[oldest]
        return result

    def reading(self,ticker,now):
        result=self.rows.get(ticker)
        if not result:return {'status':'unavailable','articles':[],'reason':'Select this stock to load provider news'}
        if result['checked_at']>now:return {'status':'unavailable','articles':[],'reason':'News lookup had not completed at this time'}
        return {**result,'stale':now-result['checked_at']>90,'age_seconds':now-result['checked_at']}
