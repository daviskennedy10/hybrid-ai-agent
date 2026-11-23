"""Read-only Google iCalendar queries with recurrence, local-day and busy-slot logic."""
from dataclasses import dataclass
from datetime import date,datetime,time,timedelta,timezone
import re
import hashlib
from zoneinfo import ZoneInfo
from dateutil.rrule import rrulestr

from assignment_skills.common import SkillError,clean

UTC=timezone.utc


@dataclass(frozen=True)
class Event:
    uid: str
    title: str
    start: datetime
    end: datetime
    all_day: bool=False
    transparent: bool=False


def instant(value, params, zone):
    if params.get('VALUE')=='DATE' or re.fullmatch(r'\d{8}',value):
        return datetime.combine(datetime.strptime(value,'%Y%m%d').date(),time(),zone),True
    tz=UTC if value.endswith('Z') else ZoneInfo(params.get('TZID',str(zone)))
    parsed=datetime.strptime(value.removesuffix('Z'),'%Y%m%dT%H%M%S').replace(tzinfo=tz)
    if parsed.astimezone(UTC).astimezone(tz).replace(tzinfo=None)!=parsed.replace(tzinfo=None):
        raise SkillError('unsupported_calendar')
    return parsed,False


def fields(raw):
    try:
        text=raw.decode('utf-8-sig').replace('\r\n','\n')
    except UnicodeError:
        raise SkillError('invalid_response') from None
    text=re.sub(r'\n[ \t]','',text)
    events=[]; current=None; calendar_name=None;stack=[];roots=0
    for line in text.splitlines():
        if line.startswith('BEGIN:'):
            component=line[6:]
            if component=='VCALENDAR':
                if stack: raise SkillError('invalid_response')
                roots+=1
            if component=='VEVENT':
                if stack!=['VCALENDAR'] or current is not None: raise SkillError('invalid_response')
                current={}
            stack.append(component)
            if len(stack)>8: raise SkillError('unsupported_calendar')
        elif line.startswith('END:'):
            component=line[4:]
            if not stack or stack.pop()!=component: raise SkillError('invalid_response')
            if component=='VEVENT':
                events.append(current);current=None
                if len(events)>500: raise SkillError('unsupported_calendar')
        elif ':' in line:
            key,value=line.split(':',1)
            parts=key.split(';'); name=parts[0].upper()
            params=dict(p.split('=',1) for p in parts[1:] if '=' in p)
            params={k.upper():v.strip('"') for k,v in params.items()}
            if stack and stack[-1]=='VEVENT':
                current.setdefault(name,[]).append((value,params))
                if name in {'UID','DTSTART','DTEND','SUMMARY','STATUS','RRULE','RECURRENCE-ID'} and len(current[name])>1:
                    raise SkillError('invalid_response')
            elif stack==['VCALENDAR'] and name=='X-WR-CALNAME':
                if calendar_name is not None: raise SkillError('invalid_response')
                calendar_name=value
    if stack or roots!=1 or current is not None:
        raise SkillError('invalid_response')
    return calendar_name,events


def expand(raw, expected_name, zone, begin, end):
    name,records=fields(raw)
    if name!=expected_name: raise SkillError('wrong_calendar')
    result=[]; overrides={}; bases={}
    def get(record,key,default=''):
        return record.get(key,[(default,{})])[0][0]
    try:
        for record in records:
            if 'RECURRENCE-ID' in record:
                if 'RANGE' in record['RECURRENCE-ID'][0][1]: raise SkillError('unsupported_calendar')
                rid,_=instant(*record['RECURRENCE-ID'][0],zone)
                overrides[(get(record,'UID'),rid.astimezone(UTC))]=record
        for record in records:
            if 'RECURRENCE-ID' in record: continue
            if get(record,'STATUS')=='CANCELLED' and 'DTSTART' not in record: continue
            uid=get(record,'UID')
            if not uid or uid in bases or 'DTSTART' not in record: raise SkillError('invalid_response')
            start,all_day=instant(*record['DTSTART'][0],zone)
            if 'DTEND' in record:
                stop,_=instant(*record['DTEND'][0],zone)
                duration=stop-start
            elif 'DURATION' in record:
                match=re.fullmatch(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?',get(record,'DURATION'))
                if not match: raise SkillError('unsupported_calendar')
                d,h,m,s=(int(v or 0) for v in match.groups())
                duration=timedelta(days=d,hours=h,minutes=m,seconds=s)
            else: duration=timedelta(days=1) if all_day else timedelta()
            if duration<timedelta() or duration>timedelta(days=31): raise SkillError('unsupported_calendar')
            bases[uid]=(record,duration,all_day)
            occurrences=[start]
            if 'RRULE' in record:
                rule=get(record,'RRULE')
                rules=dict(part.split('=',1) for part in rule.split(';'))
                if (rules.get('FREQ') not in {'DAILY','WEEKLY','MONTHLY','YEARLY'}
                        or not set(rules)<={'FREQ','INTERVAL','COUNT','UNTIL','BYDAY','BYMONTHDAY','BYMONTH','WKST'}
                        or not 1<=int(rules.get('INTERVAL','1'))<=366
                        or start<begin-timedelta(days=3660)):
                    raise SkillError('unsupported_calendar')
                until=re.search(r'UNTIL=(\d{8})(?:;|$)',rule)
                if until:
                    until_dt=datetime.combine(datetime.strptime(until[1],'%Y%m%d').date(),time(23,59,59),start.tzinfo)
                    rule=rule.replace('UNTIL='+until[1],'UNTIL='+until_dt.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ'))
                recurring=rrulestr(rule,dtstart=start)
                # bounded expansion; iterate only inside the requested horizon.
                occurrences=[]
                for occurrence in recurring.xafter(begin-duration,inc=True):
                    if occurrence>=end: break
                    occurrences.append(occurrence)
                    if len(occurrences)>1000: raise SkillError('unsupported_calendar')
            excluded=set()
            for values,key in [(excluded,'EXDATE')]:
                for value,params in record.get(key,[]):
                    values.update(instant(v,params,zone)[0].astimezone(UTC) for v in value.split(','))
            for value,params in record.get('RDATE',[]):
                occurrences.extend(instant(v,params,zone)[0] for v in value.split(','))
            for occurrence in set(occurrences):
                if occurrence.astimezone(UTC) in excluded or get(record,'STATUS')=='CANCELLED': continue
                # RFC5545 omits nonexistent local recurring times.
                if occurrence.astimezone(UTC).astimezone(occurrence.tzinfo).replace(tzinfo=None)!=occurrence.replace(tzinfo=None): continue
                if (uid,occurrence.astimezone(UTC)) in overrides: continue
                actual,actual_end,actual_all=occurrence,occurrence+duration,all_day
                title=get(record,'SUMMARY','Untitled')
                if actual.astimezone(UTC)<end.astimezone(UTC) and (actual_end.astimezone(UTC)>begin.astimezone(UTC) or actual_end==actual and actual.astimezone(UTC)>=begin.astimezone(UTC)):
                    result.append(Event(uid,clean(title.replace('\\,',',').replace('\\n',' '),160),actual,actual_end,actual_all,
                                        get(record,'TRANSP')=='TRANSPARENT'))
                    if len(result)>1000: raise SkillError('unsupported_calendar')
        # Recurrence overrides are evaluated independently: a moved occurrence can
        # enter the query window even when its original recurrence date was outside it.
        for (uid,rid),override in overrides.items():
            if uid not in bases: raise SkillError('unsupported_calendar')
            base,duration,all_day=bases[uid]
            if get(override,'STATUS')=='CANCELLED' or get(base,'STATUS')=='CANCELLED': continue
            actual,actual_all=instant(*override['DTSTART'][0],zone)
            stop=instant(*override['DTEND'][0],zone)[0] if 'DTEND' in override else actual+duration
            if stop<actual: raise SkillError('invalid_response')
            if actual.astimezone(UTC)<end.astimezone(UTC) and stop.astimezone(UTC)>begin.astimezone(UTC):
                result.append(Event(uid,clean(get(override,'SUMMARY',get(base,'SUMMARY','Untitled')),160),actual,stop,
                                    actual_all,get(override,'TRANSP',get(base,'TRANSP'))=='TRANSPARENT'))
        if len(result)>1000: raise SkillError('unsupported_calendar')
        return sorted(result,key=lambda e:e.start.astimezone(UTC))
    except (ValueError,KeyError,TypeError,OverflowError):
        raise SkillError('unsupported_calendar') from None


class Calendar:
    def __init__(self,config,http,now=None):
        self.config,self.http=config,http
        self.now=now or (lambda:datetime.now(UTC))
    def run(self,operation,day='today',minutes=30):
        if operation not in {'day','upcoming','free','overlaps'}: raise SkillError('forbidden')
        self.config.calendar_ready()
        zone=ZoneInfo(self.config.profile['timezone'])
        today=self.now().astimezone(zone).date()
        target=today+timedelta(days=1) if day=='tomorrow' else today if day=='today' else date.fromisoformat(day)
        if abs((target-today).days)>90 or not 15<=minutes<=240: raise SkillError('invalid_request')
        begin=self.now().astimezone(zone) if operation=='upcoming' and target==today else datetime.combine(target,time(),zone)
        end=datetime.combine(target+timedelta(days=7 if operation=='upcoming' else 1),time(),zone)
        events=expand(self.http.get(self.config.calendar_ics_url),self.config.calendar_name,zone,begin,end)
        serialize=lambda e:{'event_id':hashlib.sha256(e.uid.encode()).hexdigest(),'title':e.title,'start':e.start.astimezone(zone).isoformat(),
                             'end':e.end.astimezone(zone).isoformat(),'all_day':e.all_day,'busy':not e.transparent}
        data={'date':str(target),'timezone':str(zone),'source':'dedicated Google Calendar iCal',
              'events':[serialize(e) for e in events]}
        busy=[e for e in events if not e.transparent and e.end.astimezone(UTC)>e.start.astimezone(UTC)]
        if operation=='overlaps':
            overlaps=[];count=0
            for i,a in enumerate(busy):
                for b in busy[i+1:]:
                    if max(a.start.astimezone(UTC),b.start.astimezone(UTC))<min(a.end.astimezone(UTC),b.end.astimezone(UTC)):
                        count+=1
                        if len(overlaps)<100:
                            overlaps.append({'first':a.title,'second':b.title,'start':max(a.start,b.start,key=lambda d:d.astimezone(UTC)).astimezone(zone).isoformat()})
            data['overlaps']=overlaps;data['overlap_count']=count
        if operation=='free':
            left=datetime.combine(target,time(9),zone).astimezone(UTC)
            right=datetime.combine(target,time(17),zone).astimezone(UTC)
            slots=[];cursor=left
            for e in busy:
                a,b=max(left,e.start.astimezone(UTC)),min(right,e.end.astimezone(UTC))
                if b<=left or a>=right: continue
                if a-cursor>=timedelta(minutes=minutes): slots.append((cursor,a))
                cursor=max(cursor,b)
            if right-cursor>=timedelta(minutes=minutes): slots.append((cursor,right))
            data['free_slots']=[{'start':a.astimezone(zone).isoformat(),'end':b.astimezone(zone).isoformat()} for a,b in slots]
        return data
