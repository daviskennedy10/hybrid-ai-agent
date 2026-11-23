"""Operator-owned scheduling metadata; never credentials or executable job text."""
from dataclasses import dataclass,field
from datetime import datetime,time,timedelta,timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'secrets/schedule.json'


def aware(value):
    result=datetime.fromisoformat(value)
    if result.tzinfo is None: raise ValueError('Timezone offset required')
    return result.astimezone(timezone.utc)


def local_due(day,hhmm,zone):
    """First fold on fall-back; first valid minute after a spring-forward gap."""
    naive=datetime.combine(day,time.fromisoformat(hhmm))
    for offset in range(181):
        candidate=(naive+timedelta(minutes=offset)).replace(tzinfo=zone,fold=0)
        if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)==candidate.replace(tzinfo=None):
            return candidate.astimezone(timezone.utc)
    raise ValueError('No valid local schedule instant')


@dataclass(frozen=True)
class Schedule:
    enabled: bool=False
    recipient_user_id: int | None=field(default=None,repr=False)
    timezone: str='America/Los_Angeles'
    morning_time: str='08:00'
    evening_time: str='18:00'
    tick_seconds: int=5
    alert_poll_seconds: int=120
    event_lead_minutes: int=30
    conflict_horizon_hours: int=48
    catchup_minutes: int=120
    max_notifications_per_poll: int=5
    max_delivery_attempts: int=3
    retry_seconds: int=30
    test_jobs: tuple=()
    verification: dict=field(default_factory=dict)

    @classmethod
    def load(cls,path=PATH):
        if not Path(path).exists(): return cls()
        try:
            raw=json.loads(Path(path).read_text())
            version=raw.pop('version')
            if type(version) is not int or version!=1: raise ValueError
            obj=cls(**raw);obj.validate();return obj
        except (OSError,ValueError,TypeError,KeyError):
            raise ValueError('Invalid proactive configuration; see docs/STAGE5.md') from None

    def validate(self):
        ZoneInfo(self.timezone)
        if type(self.enabled) is not bool: raise ValueError('enabled must be boolean')
        if self.enabled and (type(self.recipient_user_id) is not int or self.recipient_user_id<=0):
            raise ValueError('An explicit recipient is required')
        for value in (self.morning_time,self.evening_time):
            if not isinstance(value,str) or len(value)!=5 or time.fromisoformat(value).strftime('%H:%M')!=value:
                raise ValueError('Use HH:MM schedules')
        bounds={'tick_seconds':(1,60),'alert_poll_seconds':(10,3600),'event_lead_minutes':(1,2880),
                'conflict_horizon_hours':(1,168),'catchup_minutes':(1,360),'max_notifications_per_poll':(1,10),
                'max_delivery_attempts':(1,5),'retry_seconds':(1,600)}
        for name,(low,high) in bounds.items():
            value=getattr(self,name)
            if type(value) is not int or not low<=value<=high: raise ValueError('Invalid scheduling bound')
        if not isinstance(self.test_jobs,(list,tuple)) or len(self.test_jobs)>2: raise ValueError('At most two controlled test runs')
        for job in self.test_jobs:
            if (not isinstance(job,dict) or set(job)!={'id','kind','due_at'} or job['kind'] not in {'morning','evening'}
                    or not isinstance(job['id'],str) or len(job['id'])!=32 or any(c not in '0123456789abcdef' for c in job['id'])):
                raise ValueError('Invalid controlled test run')
            aware(job['due_at'])
        if not isinstance(self.verification,dict): raise ValueError('Invalid verification configuration')
        if self.verification:
            if (not isinstance(self.verification,dict) or set(self.verification)!={'until','lead_minutes','poll_seconds'}
                    or type(self.verification['lead_minutes']) is not int or not 1<=self.verification['lead_minutes']<=2880
                    or type(self.verification['poll_seconds']) is not int or not 10<=self.verification['poll_seconds']<=60):
                raise ValueError('Invalid temporary verification window')
            aware(self.verification['until'])

    def authorize(self,telegram_users,skill_config):
        self.validate()
        if not self.enabled: return
        if (self.recipient_user_id not in telegram_users or self.recipient_user_id not in skill_config.allowed_user_ids
                or self.timezone!=skill_config.profile['timezone']):
            raise ValueError('Proactive recipient/timezone must match the authorized assignment profile')
        skill_config.calendar_ready();skill_config.email_ready()

    def effective(self,now):
        if self.verification and now<=aware(self.verification['until']):
            return self.verification['lead_minutes'],self.verification['poll_seconds']
        return self.event_lead_minutes,self.alert_poll_seconds
