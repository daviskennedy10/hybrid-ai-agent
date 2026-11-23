"""Concise, escaped source presentation; opaque URLs remain link targets only."""
from html import escape
import re
from urllib.parse import urlencode
from assignment_skills.common import clean
from assignment_skills.search import public_url
from telegram_bot.messages import HTMLReply


def excerpt(value,limit):
    text=clean(value,2000)
    text=re.sub(r'(?:https?://|www\.)\S+','',text,flags=re.I)
    text=re.sub(r'\b[A-Za-z0-9_+/=-]{60,}\b','',text)
    text=re.sub(r'\s+',' ',text).strip(' -|')
    return text[:limit].rstrip()+('…' if len(text)>limit else '')


def search_reply(data):
    header='<b>Web results</b> — news and reference sources'
    blocks=[]
    for row in data['results'][:5]:
        title=excerpt(row['title'],140) or 'Untitled result'
        publisher=excerpt(row['publisher'],60) or 'Source'
        if title.endswith(' - '+publisher): title=title[:-(len(publisher)+3)]
        summary=excerpt(row.get('snippet',''),160)
        if summary.casefold().startswith(title.casefold()): summary=summary[len(title):].strip(' -|')
        if not summary or summary.casefold()==publisher.casefold(): summary='Source supplied a headline only; no article summary available.'
        url=public_url(row['url'])
        if not url: continue
        date=(row.get('published_at') or '')[:10]
        kind='News' if row.get('kind')=='news' else 'Background'
        metadata=publisher+' · '+kind+(' · '+date if date else '')
        link_label='Read article' if row.get('kind')=='news' else 'Read source'
        def block(target,label):
            return ('<b>'+escape(title)+'</b>\n'+escape(summary)+'\n'+escape(metadata)+
                    '\n<a href="'+escape(target,quote=True)+'">'+escape(label)+'</a>')
        rendered=block(url,link_label)
        if len(rendered.encode('utf-8'))>3400:
            # Never truncate an opaque URL into a broken or misleading article link.
            target='https://news.google.com/search?'+urlencode({'q':title[:80]})
            rendered=block(target,'Find this title on Google News')
        blocks.append(rendered)
    if not blocks: blocks=['No source results retrieved.']
    warnings=data.get('warnings') or []
    notes=[]
    if 'news_unavailable' in warnings: notes.append('News retrieval unavailable.')
    if 'background_unavailable' in warnings: notes.append('Reference retrieval unavailable.')
    if 'recent_news_not_found' in warnings: notes.append('No dated news from the last 30 days was verified.')
    # Every block stays intact and each outgoing fragment is <=3500 UTF-8 bytes,
    # which is also below Telegram's 4096 parsed UTF-16-unit limit.
    parts=[];current=header
    for item in blocks:
        candidate=current+'\n\n'+item
        if len(candidate.encode('utf-8'))>3500:
            parts.append(current);current=item
        else: current=candidate
    if notes:
        note=' '.join(notes)
        if len((current+'\n\n'+note).encode('utf-8'))>3500:
            parts.append(current);current=note
        else: current+='\n\n'+note
    parts.append(current)
    return HTMLReply(parts)
