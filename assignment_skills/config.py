"""Explicit assignment-only account configuration; no credential discovery."""
from dataclasses import dataclass, field
import json
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from assignment_skills.common import SkillError

ROOT=Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
    allowed_user_ids: frozenset
    assignment_only_confirmed: bool = False
    calendar_name: str = 'CSCI599 Stage4 Synthetic'
    calendar_ics_url: str = field(default='', repr=False)
    email_username: str = field(default='', repr=False)
    email_app_password: str = field(default='', repr=False)
    email_allowed_recipients: tuple = field(default=(), repr=False)
    profile: dict = field(default_factory=lambda: {'name':'Fictional Casey Test','location':'Los Angeles',
                          'latitude':34.0522,'longitude':-118.2437,'timezone':'America/Los_Angeles'})

    @classmethod
    def load(cls, path=ROOT/'secrets/skills.json', fallback_users=None):
        try:
            if not Path(path).exists() and fallback_users:
                config=cls(frozenset(fallback_users))
                config.validate()
                return config
            data=json.loads(Path(path).read_text())
            if data.pop('version') != 1:
                raise ValueError
            data['allowed_user_ids']=frozenset(data['allowed_user_ids'])
            config=cls(**data)
            config.validate()
            return config
        except (OSError,ValueError,TypeError,KeyError):
            raise SkillError('not_configured') from None

    def validate(self):
        if not self.allowed_user_ids or len(self.allowed_user_ids)>64 or any(type(i) is not int or i<=0 for i in self.allowed_user_ids):
            raise ValueError('Invalid skill principals')
        if type(self.assignment_only_confirmed) is not bool or self.calendar_name != 'CSCI599 Stage4 Synthetic':
            raise ValueError('Assignment-only configuration required')
        p=self.profile
        if (set(p)!={'name','location','latitude','longitude','timezone'} or not p['name'].startswith('Fictional ')
                or type(p['latitude']) not in {int,float} or not -90<=p['latitude']<=90
                or type(p['longitude']) not in {int,float} or not -180<=p['longitude']<=180
                or not isinstance(p['location'],str) or not 1<=len(p['location'])<=80):
            raise ValueError('Invalid fictitious weather profile')
        ZoneInfo(p['timezone'])
        if (not isinstance(self.email_allowed_recipients,(list,tuple)) or len(self.email_allowed_recipients)>10
                or any(not isinstance(a,str) or any(c in a for c in '\r\n,;<> ') or '@' not in a for a in self.email_allowed_recipients)):
            raise ValueError('Invalid recipient allowlist')
        if self.calendar_ics_url:
            u=urlparse(self.calendar_ics_url)
            if (u.scheme!='https' or u.hostname!='calendar.google.com' or u.username or u.password
                    or u.port not in {None,443} or not u.path.startswith('/calendar/ical/')
                    or '/private-' not in u.path or not u.path.endswith('/basic.ics') or u.query or u.fragment):
                raise ValueError('Only private Google Calendar iCal feeds supported')

    def calendar_ready(self):
        if not self.assignment_only_confirmed or not self.calendar_ics_url:
            raise SkillError('not_configured')

    def email_ready(self):
        import re
        if (not self.assignment_only_confirmed or not re.fullmatch(r'csci599[a-zA-Z0-9.+_-]*@gmail\.com',self.email_username)
                or not re.fullmatch(r'[a-zA-Z0-9]{16}',self.email_app_password.replace(' ',''))
                or not self.email_allowed_recipients):
            raise SkillError('not_configured')
