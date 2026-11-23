"""Deterministic skill selection. External content cannot select tools or actions."""
from dataclasses import dataclass,field
from datetime import datetime,timezone
import re
from assignment_skills.common import SkillError,HTTP
from assignment_skills.calendar import Calendar
from assignment_skills.email import Email
from assignment_skills.search import Search
from assignment_skills.weather import Weather


@dataclass(frozen=True)
class Plan:
    name: str
    operation: str
    args: dict=field(default_factory=dict)


def plan(text):
    lower=text.casefold().strip()
    if lower.startswith('/search') or re.match(r'^(?:search(?: the)? web|web search)\b',lower):
        query=re.sub(r'^(?:/search|search(?: the)? web(?: for)?|web search)\s*','',text,flags=re.I).strip()
        return Plan('web_search','search',{'query':query})
    if lower.startswith('/calendar') or re.search(r'\bcalendar\b|\btoday.?s schedule\b|\bfree time slots?\b|\boverlapping events\b',lower):
        operation='upcoming' if 'upcoming' in lower else 'free' if re.search(r'\bfree\b',lower) else 'overlaps' if 'overlap' in lower else 'day'
        if re.search(r'\b(create|add|delete|remove|cancel|move|update)\b',lower): operation='blocked'
        day='tomorrow' if 'tomorrow' in lower else 'today'
        match=re.search(r'\b\d{4}-\d{2}-\d{2}\b',text)
        if match: day=match[0]
        duration=re.search(r'\b(\d+)\s*(?:minutes?|min)\b',lower)
        return Plan('calendar',operation,{'day':day,'minutes':int(duration[1]) if duration else 30})
    if lower.startswith('/email') or (re.search(r'\b(emails?|mailbox|unread)\b',lower) and re.search(r'\b(list|show|summari[sz]e|identify|prepare|draft|read|send|delete|unread)\b',lower)):
        operation='send' if re.search(r'\bsend\b',lower) else 'draft' if 'draft' in lower else 'urgent' if 'urgent' in lower else 'summarize' if re.search(r'summari[sz]',lower) else 'unread'
        if re.search(r'\b(delete|archive|mark|forward)\b',lower): operation='blocked'
        args={}
        if operation=='draft':
            uid=re.search(r'\b(?:uid|email|message|draft)\s+#?(\d{1,10})\b',lower)
            if uid: args['uid']=uid[1]
            to=re.search(r'\bto\s+([^\s<>]+@[^\s<>]+)',text,re.I)
            if to: args['to']=to[1]
        return Plan('email',operation,args)
    if lower.startswith('/weather') or re.search(r'\b(weather|forecast)\b',lower):
        location=re.search(r'\b(?:for|in)\s+(.+?)[?.!]*$',text,re.I)
        return Plan('weather','current' if 'current' in lower else 'forecast',{'location':location[1].strip() if location else None})
    return None


def render(name,operation,data):
    if name=='calendar':
        lines=['Calendar: '+data['date']+' ('+data['timezone']+')']
        if operation=='free':
            lines += ['Free: '+s['start']+' — '+s['end'] for s in data['free_slots'][:20]] or ['No free working-hour slots.']
        elif operation=='overlaps':
            lines += ['Overlap: '+e['first']+' / '+e['second']+' at '+e['start'] for e in data['overlaps'][:20]] or ['No overlaps.']
        else:
            lines += [e['start']+' — '+e['end']+': '+e['title']+(' [all day]' if e['all_day'] else '') for e in data['events'][:20]] or ['No events.']
        return '\n'.join(lines)+'\nSource: dedicated Google Calendar (read only).'
    if name=='email':
        if 'draft' in data:
            d=data['draft']
            return 'UNSENT DRAFT — review only; application sending is disabled.\nTo: '+d['to']+'\nSubject: '+d['subject']+'\n\n'+d['body']
        messages=data['messages']
        lines=['Synthetic unread email ('+str(len(messages))+'; read-only):']
        for m in messages:
            lines.append('UID '+m['uid']+' | '+m['subject']+'\nFrom: '+m['sender'])
            if operation!='unread': lines.append('Extractive summary: '+m['summary'])
            lines.append('Urgency: '+(', '.join(m['urgency_markers']) if m['urgent'] else 'no urgency markers found'))
        if not messages: lines.append('No matching unread synthetic messages.')
        return '\n'.join(lines)+'\nDraft command: /email draft <UID>. No email is ever sent.'
    if name=='web_search':
        from assignment_skills.presentation import search_reply
        return search_reply(data)
    current=data['current']
    lines=['Weather: '+data['location']+' ('+data['timezone']+'); fictitious test profile.',
           'Current '+current['time']+': '+str(current['temperature_c'])+' °C; humidity '+str(current['humidity_pct'])+'%; wind '+str(current['wind_kmh'])+' km/h.']
    if operation!='current':
        lines += [d['date']+': '+str(d['low_c'])+' to '+str(d['high_c'])+' °C; rain probability '+str(d['precipitation_probability_pct'])+'%' for d in data['daily']]
    note=' (fallback: Open-Meteo '+('timed out' if data.get('primary_failure')=='timeout' else 'unavailable')+')' if data.get('provider_fallback') else ''
    return '\n'.join(lines)+'\nSource: '+data['source']+' '+data['source_url']+note


class Dispatcher:
    def __init__(self,config,http=None,calendar=None,email=None):
        self.config=config
        http=http or HTTP()
        self.tools={'calendar':calendar or Calendar(config,http),'email':email or Email(config),
                    'web_search':Search(http),'weather':Weather(config,http)}
    def plan(self,text): return plan(text)
    def execute(self,request,selected):
        private=request.private or selected.name in {'calendar','email'}
        success=False;data=None;failure=None;provider_attempts=[];tool_invoked=False
        try:
            if type(request.principal_id) is not int or request.principal_id not in self.config.allowed_user_ids:
                raise SkillError('forbidden')
            if selected.operation=='blocked': raise SkillError('forbidden')
            if selected.name=='web_search' and private: raise SkillError('private_search_blocked')
            args=dict(selected.args)
            if selected.name=='weather':
                location=args.pop('location')
                if location and location.casefold()!=self.config.profile['location'].casefold(): raise SkillError('invalid_request')
            tool_invoked=True
            if selected.name=='web_search': data=self.tools[selected.name].run(**args)
            else: data=self.tools[selected.name].run(selected.operation,**args)
            data['retrieved_at']=datetime.now(timezone.utc).isoformat()
            response=render(selected.name,selected.operation,data)
            success=True
        except SkillError as exc:
            failure=exc.kind
            if selected.name=='weather': provider_attempts=getattr(exc,'provider_attempts',[])
        except (ValueError,TypeError,KeyError,OverflowError): failure='invalid_response'
        except Exception: failure='unavailable'  # no exception strings in responses/logs
        if not success:
            response={
                'not_configured':'This assignment-only account is not configured yet. Complete docs/STAGE4_SETUP.md locally; never paste credentials here.',
                'sending_disabled':'Email sending is disabled in application code. Only reading and unsent draft preparation are supported.',
                'recipient_blocked':'Draft recipient is not on the configured test-recipient allowlist.',
                'private_search_blocked':'Web search is held in a private conversation. Use /new to clear private context before making an explicitly public search.',
                'forbidden':'This skill operation is not permitted. Calendar/email access is read-only and requires an authorized skill principal.',
                'wrong_calendar':'Calendar identity mismatch. Only the dedicated CSCI599 Stage4 Synthetic calendar is accepted.',
                'invalid_request':'Unsupported skill arguments. Use /calendar today|tomorrow|upcoming|free|overlaps [YYYY-MM-DD], /email unread|summarize|urgent|draft <UID>, /search <query>, or /weather.',
            }.get(failure,'The external skill could not retrieve valid data. No model-generated answer was substituted.')
        count=0
        if success:
            count=len(data.get('events',data.get('messages',data.get('results',data.get('daily',[])))))
            if 'draft' in data: count=1
            elif selected.operation=='free': count=len(data['free_slots'])
            elif selected.operation=='overlaps': count=data['overlap_count']
        sources={'calendar':'google_calendar_ical','email':'gmail_imap_readonly','web_search':'google_news_wikipedia','weather':'open_meteo'}
        if success and selected.name=='weather':
            sources['weather']=data.get('provider_id','open_meteo')
        if not success and selected.name=='weather' and provider_attempts:
            sources['weather']=provider_attempts[-1]['provider']
        providers=[]
        if success:
            if selected.name=='web_search':
                if any(r['kind']=='news' for r in data['results']): providers.append('google_news')
                if any(r['kind']=='background' for r in data['results']): providers.append('wikipedia')
            else: providers.append(sources[selected.name])
        return {'success':success,'private':private,'status':'success' if success else 'held' if failure=='private_search_blocked' else 'failure',
                'failure':failure,'response':response,'skill_data':data,
                'skill_execution':{'tool_invoked':tool_invoked,'skill':selected.name,'operation':selected.operation,'source':sources[selected.name],
                                   'success':success,'result_count':count,'providers_succeeded':providers,'sending_enabled':False,
                                   'provider_fallback':bool(data.get('provider_fallback')) if success else len(provider_attempts)>1,
                                   'primary_failure':data.get('primary_failure') if success else (provider_attempts[0]['failure'] if provider_attempts else None),
                                   'provider_attempts':provider_attempts}}
