"""Read actual assignment skills through Router.run and assemble source-limited text."""
import asyncio
from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
from html import escape
from zoneinfo import ZoneInfo
from assignment_skills.common import clean
from router.core import Request
from telegram_bot.messages import HTMLReply
from proactive.state import digest

UTC=timezone.utc


def instant(value):
    result=datetime.fromisoformat(value)
    if result.tzinfo is None: raise ValueError('Aware event time required')
    return result.astimezone(UTC)


def notification_text(lines):
    """One Telegram message: no partial multi-chunk retry ambiguity, all data escaped."""
    body='\n'.join(lines)
    suffix='\n[Additional entries omitted; use the skill commands for details.]'
    limit=3500-len(escape(suffix).encode())
    text=''
    for char in body:
        candidate=text+escape(char)
        if len(candidate.encode())>limit: return HTMLReply((text+escape(suffix),))
        text=candidate
    return HTMLReply((text,))


def conflicts(events,now,horizon):
    selected=[e for e in events[:100] if e.get('busy',True) and instant(e['end'])>now]
    found=[]
    for i,a in enumerate(selected):
        for b in selected[i+1:]:
            start=max(instant(a['start']),instant(b['start']))
            stop=min(instant(a['end']),instant(b['end']))
            if start<stop and start<=horizon:
                found.append((a,b,start,stop))
                if len(found)>=100: return found
    return found


@dataclass
class Reading:
    ok: bool
    data: dict
    request_id: str | None
    failure: str | None=None


class Collector:
    def __init__(self,router,principal,audit): self.router,self.principal,self.audit=router,principal,audit

    async def read(self,skill,text,key,origin):
        selected=self.router.skill_dispatcher.plan(text) if self.router.skill_dispatcher else None
        if selected is None or selected.name!=skill or selected.operation not in {'day','upcoming','free','summarize','forecast'}:
            raise ValueError('Proactive work requires an explicit read-only skill plan')
        try:
            result=await asyncio.to_thread(self.router.run,Request(text,private=True,principal_id=self.principal))
        except Exception:
            result={'route':'tool','success':False,'failure':'internal_error'}
        if result.get('route')!='tool': raise ValueError('Proactive work cannot use an unplanned model path')
        ok=result.get('success') is True and isinstance(result.get('skill_data'),dict)
        failure=result.get('failure') if not ok else None
        self.audit({'message':'proactive_tool_read','notification_key':key,'origin':origin,
                    'skill':skill,'request_id':result.get('request_id'),'success':ok,'failure':failure})
        return Reading(ok,result.get('skill_data') if ok else {},result.get('request_id'),failure)

    async def assemble(self,kind,now,zone,key,origin):
        today=now.astimezone(zone).date();activity_counts=None;gap_count=None
        current=await self.read('calendar','/calendar '+str(today),key,origin)
        mail=await self.read('email','/email summarize',key,origin)
        reads=[current,mail]
        lines=[('Morning briefing' if kind=='morning' else 'End-of-day recap')+' — '+str(today)+' ('+str(zone)+')']
        events=current.data.get('events',[]) if current.ok else []
        lines.append("Today's scheduled entries (completion/attendance not verified):")
        if current.ok:
            lines.extend(self.event_lines(events,zone) or ['• No entries in the dedicated calendar.'])
            # For recaps include all recorded conflicts for the day, even if their time has passed.
            overlap=conflicts(events,datetime.combine(today,datetime.min.time(),zone).astimezone(UTC),now+timedelta(days=1))
            lines.append('Calendar conflicts: '+str(len(overlap))+'.')
            for a,b,_,_ in overlap[:2]: lines.append('• '+clean(a['title'],65)+' / '+clean(b['title'],65))
        else: lines.append('• Calendar unavailable; schedule/conflicts unknown.')
        if kind=='morning':
            gaps=await self.read('calendar','/calendar free '+str(today),key,origin);reads.append(gaps)
            if gaps.ok:
                slots=gaps.data.get('free_slots',[]);gap_count=len(slots)
                lines.append('Available calendar gaps (09:00–17:00; at least 30 minutes):')
                lines.extend(['• '+instant(slot['start']).astimezone(zone).strftime('%H:%M')+'–'+instant(slot['end']).astimezone(zone).strftime('%H:%M') for slot in slots[:3]] or ['• No qualifying free slots.'])
                if len(slots)>3: lines.append('• More slots available through /calendar free.')
            else: lines.append('Calendar gaps unavailable; no free time inferred.')
        messages=mail.data.get('messages',[]) if mail.ok else []
        urgent=[m for m in messages if m.get('urgent') is True]
        if mail.ok:
            lines.append('Unread synthetic email (up to 10 newest matches): '+str(len(messages))+'; urgency matches: '+str(len(urgent))+'.')
            for m in sorted(messages,key=lambda m:not m.get('urgent'))[:3]:
                lines.append('• '+('[URGENT] ' if m.get('urgent') else '')+clean(m['subject'],90))
                lines.append('  Summary: '+clean(m.get('summary',''),120))
        else: lines.append('Email unavailable; unread/urgent status unknown.')
        if kind=='morning':
            weather=await self.read('weather','/weather',key,origin);reads.append(weather)
            if weather.ok:
                w=weather.data;c=w['current']
                lines.append('Weather ('+clean(w['source'],30)+'): '+str(c['temperature_c'])+' °C; wind '+str(c['wind_kmh'])+' km/h.')
                forecast=next((d for d in w['daily'] if d['date']==str(today)),None)
                if forecast: lines.append('Today: '+str(forecast['low_c'])+'–'+str(forecast['high_c'])+' °C; max rain chance '+str(forecast['precipitation_probability_pct'])+'%.')
                else: lines.append('No forecast for today was supplied.')
                lines.append('Weather source time: '+clean(c['time'],85))
            else: lines.append('Weather unavailable; no conditions or forecast guessed.')
            attention=[]
            if current.ok and overlap: attention.append('review the listed scheduling conflicts')
            if mail.ok and urgent: attention.append('review '+str(len(urgent))+' urgent unread synthetic message(s)')
            usable=[slot for slot in gaps.data.get('free_slots',[]) if instant(slot['end'])>now+timedelta(minutes=30)] if gaps.ok else []
            if usable: attention.append('consider the '+instant(usable[0]['start']).astimezone(zone).strftime('%H:%M')+' calendar gap for synthetic preparation; no event was created')
            if not current.ok or not mail.ok or not weather.ok or not gaps.ok: attention.append('check unavailable sources manually')
            lines.append('Attention: '+('; '.join(attention) if attention else 'no attention markers in the retrieved data')+'.')
        else:
            tomorrow=await self.read('calendar','/calendar '+str(today+timedelta(days=1)),key,origin);reads.append(tomorrow)
            lines.append("Tomorrow's scheduled entries:")
            future=tomorrow.data.get('events',[]) if tomorrow.ok else []
            if tomorrow.ok:
                lines.extend(self.event_lines(future,zone) or ['• No entries in the dedicated calendar.'])
                future_conflicts=conflicts(future,now,now+timedelta(days=2))
                lines.append("Tomorrow's conflicts: "+str(len(future_conflicts))+'.')
            else: lines.append('• Tomorrow calendar unavailable; preparation details unknown.')
            actions=[]
            if future: actions.append('prepare for '+clean(future[0]['title'],65))
            if tomorrow.ok and future_conflicts: actions.append('resolve the listed time overlaps')
            if urgent: actions.append('review unread urgent synthetic email')
            if not all(r.ok for r in reads): actions.append('check unavailable sources manually')
            activity=getattr(self.router,'activity',None)
            if activity is not None:
                counts=await asyncio.to_thread(activity.summary,self.principal,today,zone);activity_counts=counts
                lines.append('Verified assistant activity through this snapshot:')
                lines.append('Requests handled: '+str(counts['request_handled'])+'; tools executed: '+str(counts['tool_success']+counts['tool_failure'])+' ('+str(counts['tool_success'])+' succeeded).')
                lines.append('Model replies generated: '+str(counts['model_success'])+'; memory writes: '+str(counts['memory_write'])+'.')
                lines.append('Notifications delivered: '+str(counts['notifications_delivered'])+'; ordinary replies delivered: '+str(counts['reply_delivered'])+'.')
                lines.append('Counts describe assistant operations, not completion of meetings or real-world assignments.')
            else: lines.append('Verified assistant activity: tracking unavailable for this run.')
            lines.append('Preparation: '+('; '.join(actions) if actions else 'no preparation markers in retrieved data')+'.')
        lines.append('Sources: assignment-only calendar/mail'+(' and public weather' if kind=='morning' else '')+'. Email sending is disabled.')
        return notification_text(lines),{'tool_successes':sum(r.ok for r in reads),'tool_failures':sum(not r.ok for r in reads),
                                         'quality':'complete' if all(r.ok for r in reads) else 'partial',
                                         'source_request_ids':[r.request_id for r in reads if r.ok],
                                         'activity_counts':activity_counts,'free_slot_count':gap_count}

    @staticmethod
    def event_lines(events,zone):
        result=[]
        for e in events[:5]:
            start=instant(e['start']).astimezone(zone);stop=instant(e['end']).astimezone(zone)
            result.append('• '+('All day' if e.get('all_day') else start.strftime('%H:%M')+'–'+stop.strftime('%H:%M'))+' '+clean(e['title'],85))
        if len(events)>5: result.append('• '+str(len(events)-5)+' more calendar entries; use /calendar for details.')
        return result

    async def alerts(self,now,zone,lead,horizon,key):
        cal=await self.read('calendar','/calendar upcoming',key,'scheduler')
        mail=await self.read('email','/email summarize',key,'scheduler')
        items=[]
        events=cal.data.get('events',[]) if cal.ok else []
        for e in events[:100]:
            start,stop=instant(e['start']),instant(e['end'])
            if not e.get('all_day') and now<start<=now+timedelta(minutes=lead):
                identity=digest('event',e['event_id'],start.isoformat())
                minutes=max(1,int((start-now).total_seconds()/60))
                items.append({'identity':identity,'kind':'event','expires':start,'source_request_ids':[cal.request_id],
                              'body':notification_text(['Upcoming synthetic calendar event',clean(e['title'],140),
                                  start.astimezone(zone).strftime('%Y-%m-%d %H:%M %Z')+' (in '+str(minutes)+' minutes).',
                                  'Lead window: '+str(lead)+' minutes. Source: dedicated Google Calendar.'])})
        for a,b,start,stop in conflicts(events,now,now+timedelta(hours=horizon)):
            ids=sorted([(e['event_id'],e['start'],e['end']) for e in [a,b]])
            items.append({'identity':digest('conflict',ids),'kind':'conflict','expires':stop,'source_request_ids':[cal.request_id],
                          'body':notification_text(['Synthetic calendar conflict',clean(a['title'],100)+' / '+clean(b['title'],100),
                               'Overlap: '+start.astimezone(zone).strftime('%Y-%m-%d %H:%M %Z')+'–'+stop.astimezone(zone).strftime('%H:%M %Z'),
                               'Review the dedicated calendar; no events were changed.'])})
        for m in mail.data.get('messages',[]) if mail.ok else []:
            if m.get('urgent') is True:
                items.append({'identity':digest('urgent',m['identity']),'kind':'urgent','expires':now+timedelta(days=1),'source_request_ids':[mail.request_id],
                              'body':notification_text(['Newly detected unread urgent synthetic email',clean(m['subject'],150),
                                    'Markers: '+', '.join(m['urgency_markers']),
                                    'Summary: '+clean(m.get('summary',''),240),
                                    'Source: dedicated read-only mailbox. No email was sent.'])})
        return sorted(items,key=lambda i:({'urgent':0,'conflict':1,'event':2}[i['kind']],i['expires']))
