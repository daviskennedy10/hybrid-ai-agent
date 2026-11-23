from dataclasses import replace
from datetime import datetime,timedelta,timezone
from email.message import EmailMessage
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfo

from assignment_skills.calendar import Calendar,expand
from assignment_skills.common import HTTP,SkillError,clean
from assignment_skills.config import Config
from assignment_skills.dispatch import Dispatcher,plan
from assignment_skills.email import Email,parse_message,recipient
from assignment_skills.search import Search,public_url
from assignment_skills.weather import Weather
from router.core import JsonLog,Policy,Request,Router

NOW=datetime(2026,11,18,8,tzinfo=timezone.utc)
URL='https://calendar.google.com/calendar/ical/synthetic/private-FAKE/basic.ics'
CONFIG=Config(frozenset({101}),True,calendar_ics_url=URL,email_username='csci599fixture@gmail.com',
              email_app_password='X'*16,email_allowed_recipients=('csci599fixture@gmail.com',))


def ics(events='',name='CSCI599 Stage4 Synthetic'):
    return ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:'+name+'\r\n'+events+'END:VCALENDAR\r\n').encode()


def event(uid,start,stop,title='Synthetic',extra='',params=';TZID=America/Los_Angeles'):
    return ('BEGIN:VEVENT\r\nUID:'+uid+'\r\nDTSTART'+params+':'+start+'\r\nDTEND'+params+':'+stop+
            '\r\nSUMMARY:'+title+'\r\n'+extra+'END:VEVENT\r\n')


EVENTS=(event('a','20261118T100000','20261118T110000','Synthetic planning')+
        event('b','20261118T103000','20261118T113000','Synthetic overlap')+
        event('c','20261118T140000','20261118T143000','Synthetic review'))
NEWS=b'''<rss><channel><item><title>Synthetic AI discovery</title><link>https://example.org/news/1</link><pubDate>Wed, 18 Nov 2026 08:00:00 GMT</pubDate><source>Synthetic Publisher</source><description>&lt;script&gt;bad&lt;/script&gt;Synthetic news data.</description></item></channel></rss>'''


def weather_data():
    return {'timezone':'America/Los_Angeles','current_units':{'temperature_2m':'°C','relative_humidity_2m':'%','wind_speed_10m':'km/h'},
            'daily_units':{'temperature_2m_max':'°C','temperature_2m_min':'°C','precipitation_probability_max':'%'},
            'current':{'time':'2026-11-18T10:00','temperature_2m':20,'relative_humidity_2m':50,
                       'weather_code':1,'wind_speed_10m':5},
            'daily':{'time':['2026-11-18'],'temperature_2m_max':[22],'temperature_2m_min':[14],
                     'precipitation_probability_max':[10],'weather_code':[1]}}


class FakeHTTP:
    def __init__(self,calendar=None): self.calls=[];self.calendar=calendar or ics(EVENTS)
    def get(self,url):
        self.calls.append(url)
        return self.calendar if 'calendar.google.com' in url else NEWS
    def json(self,url):
        self.calls.append(url)
        if 'open-meteo' in url: return weather_data()
        return {'query':{'search':[{'title':'Synthetic background','pageid':123,'snippet':'<b>Fictional</b> summary.'}]}}


def message(subject='[SYNTHETIC] Urgent: review mock plan',sender='csci599fixture@gmail.com',body=None):
    m=EmailMessage();m['From']=sender;m['To']='csci599fixture@gmail.com';m['Subject']=subject
    m.set_content(body or 'Please review the fictional plan by tomorrow. No real work is requested.')
    return m.as_bytes()


class FakeIMAP:
    def __init__(self,*args,**kwargs): self.calls=[];self.closed=False
    def login(self,*args): self.calls.append(('login',));return 'OK',[]
    def select(self,folder,readonly=False): self.calls.append(('select',folder,readonly));return 'OK',[]
    def uid(self,operation,*args):
        self.calls.append((operation,*args))
        return ('OK',[b'7']) if operation=='search' else ('OK',[(b'7 (BODY[] {100}',message()),b')'])
    def logout(self): self.closed=True;return 'BYE',[]


class CalendarTests(unittest.TestCase):
    def run_calendar(self,raw=None,**kwargs):
        return Calendar(CONFIG,FakeHTTP(raw),lambda:NOW).run(**kwargs)
    def test_today_schedule_and_timezone(self):
        result=self.run_calendar(operation='day')
        self.assertEqual(len(result['events']),3)
        self.assertEqual(result['events'][0]['start'],'2026-11-18T10:00:00-08:00')
        self.assertEqual(result['timezone'],'America/Los_Angeles')
    def test_free_slots_merge_overlaps_and_working_hours(self):
        result=self.run_calendar(operation='free')
        self.assertEqual([(s['start'][11:16],s['end'][11:16]) for s in result['free_slots']],
                         [('09:00','10:00'),('11:30','14:00'),('14:30','17:00')])
    def test_overlap_detection(self):
        result=self.run_calendar(operation='overlaps')
        self.assertEqual(len(result['overlaps']),1)
        self.assertEqual(result['overlaps'][0]['first'],'Synthetic planning')
    def test_all_day_exclusive_end_and_transparent_events(self):
        raw=ics(event('all','20261118','20261119',params=';VALUE=DATE'))
        self.assertEqual(self.run_calendar(raw,operation='free')['free_slots'],[])
        self.assertEqual(self.run_calendar(raw,operation='day',day='tomorrow')['events'],[])
        transparent=ics(event('t','20261118T090000','20261118T170000',extra='TRANSP:TRANSPARENT\r\n'))
        self.assertEqual(len(self.run_calendar(transparent,operation='free')['free_slots']),1)
    def test_utc_and_overnight_events(self):
        raw=ics(event('utc','20261118T180000Z','20261118T190000Z',params='')+
                event('night','20261117T233000','20261118T003000'))
        events=self.run_calendar(raw,operation='day')['events']
        self.assertEqual(len(events),2)
        self.assertEqual(events[-1]['start'][11:16],'10:00')
    def test_dst_day_boundaries(self):
        z=ZoneInfo('America/Los_Angeles')
        for day,hours in [('20260308',23),('20261101',25)]:
            target=datetime.strptime(day,'%Y%m%d').replace(tzinfo=z)
            raw=ics(event('all',day,(target+timedelta(days=1)).strftime('%Y%m%d'),params=';VALUE=DATE'))
            events=expand(raw,CONFIG.calendar_name,z,target,target+timedelta(days=1))
            self.assertEqual((events[0].end.astimezone(timezone.utc)-events[0].start.astimezone(timezone.utc)).total_seconds()/3600,hours)
    def test_recurrence_exdate_and_moved_override(self):
        raw=ics(event('r','20261117T100000','20261117T110000',extra='RRULE:FREQ=DAILY;COUNT=4\r\nEXDATE;TZID=America/Los_Angeles:20261119T100000\r\n')+
                event('r','20261118T150000','20261118T160000','Moved synthetic',extra='RECURRENCE-ID;TZID=America/Los_Angeles:20261120T100000\r\n'))
        result=self.run_calendar(raw,operation='day')['events']
        self.assertEqual([e['start'][11:16] for e in result],['10:00','15:00'])
        self.assertEqual(self.run_calendar(raw,operation='day',day='tomorrow')['events'],[])
    def test_wrong_calendar_and_malformed_feeds_fail_closed(self):
        for raw in [ics(EVENTS,name='Personal calendar'),b'garbage',ics(event('x','20260308T023000','20260308T033000'))]:
            with self.assertRaises(SkillError): self.run_calendar(raw,operation='day')
    def test_nested_alarm_cannot_override_event_fields(self):
        raw=ics('BEGIN:VEVENT\r\nUID:alarm\r\nBEGIN:VALARM\r\nSUMMARY:Untrusted alarm text\r\nEND:VALARM\r\nDTSTART;TZID=America/Los_Angeles:20261118T100000\r\nDTEND;TZID=America/Los_Angeles:20261118T110000\r\nSUMMARY:Actual event title\r\nEND:VEVENT\r\n')
        self.assertEqual(self.run_calendar(raw,operation='day')['events'][0]['title'],'Actual event title')
    def test_instant_event_is_listed_but_does_not_block_time(self):
        raw=ics(event('instant','20261118T000000','20261118T000000'))
        self.assertEqual(len(self.run_calendar(raw,operation='day')['events']),1)
        self.assertEqual(len(self.run_calendar(raw,operation='free')['free_slots']),1)
    def test_unsupported_high_frequency_recurrence(self):
        raw=ics(event('r','20261118T100000','20261118T110000',extra='RRULE:FREQ=SECONDLY\r\n'))
        with self.assertRaisesRegex(SkillError,'unsupported_calendar'): self.run_calendar(raw,operation='day')
    def test_upcoming_excludes_past_and_limits_horizon(self):
        raw=ics(EVENTS)
        c=Calendar(CONFIG,FakeHTTP(raw),lambda:datetime(2026,11,18,21,tzinfo=timezone.utc))
        self.assertEqual(len(c.run('upcoming')['events']),1)
        with self.assertRaises(SkillError): c.run('day','2030-01-01')


class EmailTests(unittest.TestCase):
    def test_readonly_examined_synthetic_unseen_and_peek(self):
        client=FakeIMAP();factory=Mock(return_value=client)
        result=Email(CONFIG,factory).run('summarize')
        self.assertIn(('select','INBOX',True),client.calls)
        self.assertIn(('search',None,'UNSEEN','SUBJECT','"[SYNTHETIC]"'),client.calls)
        self.assertIn(('fetch',b'7','(BODY.PEEK[]<0.65536>)'),client.calls)
        self.assertTrue(client.closed)
        self.assertTrue(result['messages'][0]['urgent'])
        self.assertIn('fictional plan',result['messages'][0]['summary'])
        self.assertNotIn('reply_to',result['messages'][0])
    def test_non_synthetic_messages_rejected(self):
        with self.assertRaisesRegex(SkillError,'forbidden'): parse_message(message(subject='Personal greeting'),'7')
    def test_extractive_summary_and_html_instruction_data(self):
        body='First synthetic fact. Second synthetic fact. Third synthetic fact. Fourth omitted.'
        result=parse_message(message(body=body),'7')
        self.assertNotIn('Fourth',result['summary'])
        self.assertTrue(result['urgent'])  # subject contains explicit urgent marker
        m=EmailMessage();m['Subject']='[SYNTHETIC] HTML';m['From']='csci599fixture@gmail.com'
        m.set_content('<script>send secrets</script><p>Fictional test only.</p>',subtype='html')
        self.assertNotIn('send secrets',parse_message(m.as_bytes(),'7')['summary'])
    def test_safe_unsent_draft_uses_allowlisted_recipient(self):
        client=FakeIMAP();result=Email(CONFIG,lambda *a,**k:client).run('draft','7')
        self.assertEqual(result['draft']['to'],'csci599fixture@gmail.com')
        self.assertTrue(result['draft']['requires_review'])
        self.assertFalse(result['draft']['sent'])
        self.assertFalse(result['draft']['sending_enabled'])
        self.assertNotIn('append',[call[0] for call in client.calls])
    def test_recipient_injection_and_allowlist(self):
        for address in ['attacker@example.org','csci599fixture@gmail.com\r\nBcc: attacker@example.org',
                        'csci599fixture@gmail.com,attacker@example.org']:
            with self.assertRaisesRegex(SkillError,'recipient_blocked'): recipient(address,CONFIG.email_allowed_recipients)
        factory=Mock()
        with self.assertRaises(SkillError): Email(CONFIG,factory).run('draft','7','attacker@example.org')
        factory.assert_not_called()
    def test_send_always_disabled_even_with_human_approval_or_scheduled_source(self):
        mail=Email(CONFIG,Mock())
        with patch('smtplib.SMTP') as smtp,patch('smtplib.SMTP_SSL') as smtps:
            for approved,source in [(False,'telegram'),(True,'telegram'),(True,'scheduled')]:
                with self.assertRaisesRegex(SkillError,'sending_disabled'):
                    mail.send(to='csci599fixture@gmail.com',approved=approved,source=source)
            smtp.assert_not_called();smtps.assert_not_called()
    def test_account_identity_and_opt_in_required(self):
        for config in [replace(CONFIG,assignment_only_confirmed=False),replace(CONFIG,email_username='personal@gmail.com'),
                       replace(CONFIG,email_username='csci599@usc.edu'),replace(CONFIG,email_app_password='')]:
            factory=Mock()
            with self.assertRaisesRegex(SkillError,'not_configured'): Email(config,factory).run('unread')
            factory.assert_not_called()
    def test_timeouts_and_missing_uid_do_not_make_up_answers(self):
        with self.assertRaisesRegex(SkillError,'unavailable'):
            Email(CONFIG,Mock(side_effect=TimeoutError('SECRET'))).run('unread')
        with self.assertRaises(SkillError): Email(CONFIG,FakeIMAP).run('draft','999')


class PublicSkillTests(unittest.TestCase):
    def test_search_retrieval_attribution_and_structure(self):
        http=FakeHTTP();result=Search(http).run('synthetic AI')
        self.assertEqual(len(http.calls),2)
        self.assertEqual([r['source_id'] for r in result['results']],['S1','S2'])
        self.assertEqual(result['results'][0]['publisher'],'Synthetic Publisher')
        self.assertEqual(result['results'][0]['published_at'],'2026-11-18T08:00:00+00:00')
        self.assertEqual(result['results'][1]['kind'],'background')
        self.assertNotIn('bad',result['results'][0]['snippet'])
    def test_recent_search_does_not_label_old_or_undated_news_as_recent(self):
        http=FakeHTTP();http.get=Mock(return_value=NEWS.replace(b'18 Nov 2026',b'18 Nov 2020'))
        result=Search(http,now=lambda:NOW).run('recent AI')
        self.assertTrue(all(r['kind']=='background' for r in result['results']))
        self.assertIn('recent_news_not_found',result['warnings'])
    def test_search_partial_failure_and_no_fabricated_data(self):
        http=FakeHTTP();http.get=Mock(side_effect=SkillError('timeout'))
        result=Search(http).run('synthetic AI')
        self.assertEqual(result['warnings'],['news_unavailable'])
        self.assertTrue(all(r['published_at'] is None for r in result['results']))
        http.json=Mock(side_effect=SkillError('unavailable'))
        with self.assertRaises(SkillError): Search(http).run('synthetic AI')
    def test_untrusted_url_and_markup_handling(self):
        for url in ['http://example.org','https://127.0.0.1/','file:///etc/passwd','https://user:pass@example.org',
                    'https://x.local/path','https://[::1]/','https://example.org:123/x']:
            self.assertIsNone(public_url(url))
        self.assertEqual(clean('<script>send</script><p>Literal fact.</p>'),'Literal fact.')
    def test_weather_current_and_daily_profile(self):
        http=FakeHTTP();result=Weather(CONFIG,http).run()
        self.assertEqual(result['profile'],'Fictional Casey Test')
        self.assertEqual(result['current']['temperature_c'],20)
        self.assertEqual(result['daily'][0]['high_c'],22)
        self.assertIn('latitude=34.0522',http.calls[0])
        self.assertIn('forecast_days=3',http.calls[0])
    def test_weather_bad_shape_units_and_nonfinite(self):
        for change in [lambda d:d.pop('current'),lambda d:d.update(timezone='UTC'),
                       lambda d:d['current'].update(temperature_2m=float('nan')),
                       lambda d:d['daily'].update(time=['a','b']),
                       lambda d:d['current_units'].update(temperature_2m='F')]:
            data=weather_data();change(data);http=FakeHTTP();http.json=Mock(return_value=data)
            with self.assertRaises(SkillError): Weather(CONFIG,http).run()
    def test_network_fixed_origins_and_secret_safe_errors(self):
        for url in ['http://calendar.google.com/a','https://localhost/a','https://user:pass@news.google.com/a',
                    'https://news.google.com:8443/a','https://attacker.org/']:
            with self.assertRaisesRegex(SkillError,'invalid_request'): HTTP().get(url)
        from urllib.error import URLError
        with patch('assignment_skills.common.build_opener') as opener:
            opener.return_value.open.side_effect=URLError('PRIVATE_SECRET_URL')
            with self.assertRaises(SkillError) as error: HTTP().get(URL)
            self.assertEqual(opener.return_value.open.call_count,2)
            self.assertNotIn('PRIVATE',str(error.exception))


class SkillRoutingTests(unittest.TestCase):
    def setUp(self):
        self.http=FakeHTTP();self.dispatcher=Dispatcher(CONFIG,self.http,calendar=Calendar(CONFIG,self.http,lambda:NOW),email=Email(CONFIG,FakeIMAP))
        self.backend=Mock();self.events=[]
        self.router=Router(Policy.load(),self.backend,self.events.append,skill_dispatcher=self.dispatcher)
    def test_all_examples_enter_real_router_tool_path(self):
        for prompt,name in [("What's on my calendar tomorrow?",'calendar'),('Summarize my unread emails.','email'),
                            ('Search the web for recent AI developments.','web_search'),
                            ("What's the weather forecast for Los Angeles?",'weather')]:
            result=self.router.run(Request(prompt,principal_id=101))
            self.assertTrue(result['success'],result['failure'])
            self.assertEqual(result['route'],'tool')
            self.assertIsNone(result['selected_model'])
            self.assertEqual(result['skill_execution']['skill'],name)
            self.assertIsNotNone(result['skill_data'])
        self.backend.infer.assert_not_called()
    def test_tool_authorization_independent_of_telegram(self):
        for uid in [None,999]:
            result=self.router.run(Request('Summarize my unread emails.',principal_id=uid))
            self.assertFalse(result['success'])
            self.assertEqual(result['failure'],'forbidden')
        self.assertEqual(self.http.calls,[])
    def test_private_web_query_never_leaves_process(self):
        result=self.router.run(Request('/search PRIVATE_TEXT',private=True,principal_id=101))
        self.assertEqual(result['status'],'held')
        self.assertEqual(self.http.calls,[])
    def test_unconfigured_accounts_fail_without_model_substitution(self):
        dispatcher=Dispatcher(replace(CONFIG,assignment_only_confirmed=False),self.http)
        router=Router(Policy.load(),self.backend,self.events.append,skill_dispatcher=dispatcher)
        result=router.run(Request("What's on my calendar?",principal_id=101))
        self.assertEqual(result['failure'],'not_configured')
        self.backend.infer.assert_not_called()
    def test_email_send_and_calendar_mutation_blocked(self):
        for text,failure in [('Send an email.','sending_disabled'),('/calendar delete events','forbidden')]:
            result=self.router.run(Request(text,principal_id=101))
            self.assertEqual(result['failure'],failure)
            self.assertFalse(result['success'])
    def test_skill_logs_exclude_credentials_and_data(self):
        self.router.run(Request('Summarize my unread emails.',principal_id=101))
        self.router.run(Request('/search synthetic-secret-query',principal_id=101))
        log=json.dumps(self.events)
        for private in [URL,CONFIG.email_app_password,CONFIG.email_username,'fictional plan','synthetic-secret-query','Synthetic AI discovery']:
            self.assertNotIn(private,log)
    def test_normal_requests_keep_existing_model_path(self):
        self.backend.healthy.return_value=True;self.backend.infer.return_value='ordinary response'
        result=self.router.run(Request('Hello!',principal_id=101))
        self.assertEqual(result['route'],'local')
        self.backend.infer.assert_called_once()
    def test_missing_config_falls_back_only_to_public_accountless_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Config.load(Path(directory)/'absent.json',fallback_users={101})
            self.assertFalse(config.assignment_only_confirmed)
            with self.assertRaises(SkillError): config.email_ready()
            with self.assertRaises(SkillError): config.calendar_ready()
    def test_private_configuration_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json'
            path.write_text(json.dumps({'version':1,'allowed_user_ids':[101]}))
            self.assertEqual(Config.load(path).allowed_user_ids,frozenset({101}))
            path.write_text('{"version":1,"allowed_user_ids":[true]}')
            with self.assertRaises(SkillError): Config.load(path)
        with self.assertRaises(ValueError): replace(CONFIG,calendar_ics_url='https://attacker.org/x').validate()
