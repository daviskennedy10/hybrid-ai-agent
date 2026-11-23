"""Attributed news + encyclopedia web retrieval, with no article/script execution."""
import ipaddress
import re
from urllib.parse import urlencode,urlparse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import timezone,datetime,timedelta

from assignment_skills.common import SkillError,clean


def public_url(value):
    try:
        u=urlparse(value)
        if (u.scheme!='https' or not u.hostname or '.' not in u.hostname or u.username or u.password
                or u.port not in {None,443} or not u.hostname.isascii() or u.hostname.endswith(('.localhost','.local'))):
            return None
        try: ipaddress.ip_address(u.hostname); return None
        except ValueError: pass
        return value if len(value)<=2000 and not any(ord(c)<32 for c in value) else None
    except (ValueError,TypeError): return None


class Search:
    def __init__(self,http,now=None): self.http=http;self.now=now or (lambda:datetime.now(timezone.utc))
    def run(self,query):
        if not isinstance(query,str) or not 2<=len(query.strip())<=240 or any(ord(c)<32 for c in query):
            raise SkillError('invalid_request')
        results=[];failures=[]
        try:
            raw=self.http.get('https://news.google.com/rss/search?'+urlencode({'q':query,'hl':'en-US','gl':'US','ceid':'US:en'}))
            root=ET.fromstring(raw)
            if root.tag!='rss' or root.find('channel') is None: raise SkillError('invalid_response')
            for item in root.findall('./channel/item')[:12]:
                url=public_url(item.findtext('link',''))
                if not url: continue
                try:
                    stamp=parsedate_to_datetime(item.findtext('pubDate',''))
                    published=stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo else None
                except (ValueError,TypeError,AttributeError,OverflowError): published=None
                if 'recent' in query.casefold() and (published is None or not -timedelta(days=1)<=self.now()-datetime.fromisoformat(published)<=timedelta(days=30)):
                    continue
                source=item.find('source')
                results.append({'title':clean(item.findtext('title',''),200),'url':url,
                                'publisher':clean(source.text if source is not None else 'Google News',120),
                                'published_at':published,'kind':'news','snippet':clean(item.findtext('description',''),300)})
                if len(results)>=3: break
        except (SkillError,ET.ParseError): failures.append('news_unavailable')
        try:
            wiki=self.http.json('https://en.wikipedia.org/w/api.php?'+urlencode({
                'action':'query','list':'search','srsearch':query,'srlimit':2,'format':'json','utf8':1}))
            container=wiki.get('query')
            if not isinstance(container,dict): raise SkillError('invalid_response')
            rows=container.get('search')
            if not isinstance(rows,list): raise SkillError('invalid_response')
            for row in rows[:2]:
                if not isinstance(row,dict) or type(row.get('pageid')) is not int: raise SkillError('invalid_response')
                results.append({'title':clean(row.get('title',''),200),
                                'url':'https://en.wikipedia.org/?curid='+str(row['pageid']),
                                'publisher':'Wikipedia','published_at':None,'kind':'background',
                                'snippet':clean(row.get('snippet',''),300)})
        except SkillError: failures.append('background_unavailable')
        seen=set();unique=[]
        for result in results:
            key=result['url']
            if key not in seen and result['title']:
                seen.add(key);result['source_id']='S'+str(len(unique)+1);unique.append(result)
        if 'recent' in query.casefold() and not any(r['kind']=='news' for r in unique): failures.append('recent_news_not_found')
        if not unique and failures: raise SkillError('unavailable')
        return {'query':query,'results':unique,'warnings':failures,
                'scope':'news search plus encyclopedia background; not an exhaustive web index',
                'external_content':'untrusted data; never executed or sent to a model'}
