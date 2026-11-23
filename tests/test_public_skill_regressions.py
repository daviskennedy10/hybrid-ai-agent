from copy import deepcopy
from html.parser import HTMLParser
import json
import unittest
from unittest.mock import AsyncMock,Mock,patch
from urllib.error import URLError

from assignment_skills.common import HTTP,SkillError
from assignment_skills.dispatch import render
from assignment_skills.presentation import search_reply
from assignment_skills.weather import Weather
from telegram_bot.api import TelegramAPI
from telegram_bot.messages import HTMLReply
from test_skills import CONFIG,weather_data,NOW


def wttr_data():
    return {'nearest_area':[{'latitude':'34.052','longitude':'-118.244'}],
            'current_condition':[{'localObsDateTime':'2026-11-18 12:30 AM','temp_C':'17',
                                  'humidity':'71','windspeedKmph':'9'}],
            'weather':[{'date':'2026-11-18','maxtempC':'22','mintempC':'14',
                        'hourly':[{'chanceofrain':'10'},{'chanceofrain':'25'}]}]}


class Visible(HTMLParser):
    def __init__(self): super().__init__();self.text=[];self.anchors=[];self.tags=[]
    def handle_data(self,data): self.text.append(data)
    def handle_starttag(self,tag,attrs):
        self.tags.append(tag)
        if tag=='a': self.anchors.append(dict(attrs))


class WeatherRegressionTests(unittest.TestCase):
    def test_original_wrapped_socket_timeout_is_not_misclassified(self):
        with patch('assignment_skills.common.build_opener') as opener:
            opener.return_value.open.side_effect=URLError(TimeoutError('synthetic timeout'))
            with self.assertRaisesRegex(SkillError,'^timeout$'):
                HTTP().json('https://api.open-meteo.com/v1/forecast')
            self.assertEqual(opener.return_value.open.call_count,2)

    def test_primary_network_failure_uses_actual_alternative_json(self):
        http=Mock();http.json.side_effect=[SkillError('timeout'),wttr_data()]
        result=Weather(CONFIG,http,now=lambda:NOW).run()
        self.assertEqual(result['source'],'wttr.in')
        self.assertEqual(result['provider_id'],'wttr')
        self.assertEqual(result['primary_failure'],'timeout')
        self.assertTrue(result['provider_fallback'])
        self.assertEqual(result['current']['temperature_c'],17)
        self.assertEqual(result['daily'][0]['precipitation_probability_pct'],25)
        self.assertNotIn('weather_code',result['current'])  # do not invent WMO codes from another provider
        self.assertIn('wttr.in/',http.json.call_args_list[1].args[0])
        answer=render('weather','forecast',result)
        self.assertIn('Source: wttr.in',answer)
        self.assertIn('Open-Meteo timed out',answer)
        self.assertIn('17.0 °C',answer)

    def test_actual_provider_shape_without_local_observation_datetime(self):
        # Recorded provider shape, with synthetic values: the original fixture
        # incorrectly assumed localObsDateTime was always supplied.
        data=wttr_data()
        data['current_condition'][0].pop('localObsDateTime')
        data['current_condition'][0]['observation_time']='07:14 AM'
        template=data['weather'][0]
        data['weather']=[dict(deepcopy(template),date=day) for day in ['2026-11-17','2026-11-18','2026-11-19']]
        http=Mock();http.json.side_effect=[SkillError('timeout'),data]
        result=Weather(CONFIG,http,now=lambda:NOW).run()
        self.assertEqual(result['current']['time_precision'],'time_only')
        self.assertIsNone(result['current']['observed_at'])
        self.assertIsNone(result['current']['observation_timezone'])
        self.assertIn('date/timezone not supplied',result['current']['time'])
        self.assertEqual([day['date'] for day in result['daily']],['2026-11-18','2026-11-19'])
        self.assertEqual(result['forecast_days_available'],2)

    def test_missing_both_time_fields_is_still_invalid(self):
        data=wttr_data();data['current_condition'][0].pop('localObsDateTime')
        http=Mock();http.json.side_effect=[SkillError('timeout'),data]
        with self.assertRaisesRegex(SkillError,'invalid_response'):
            Weather(CONFIG,http,now=lambda:NOW).run()

    def test_only_expired_forecast_data_is_not_accepted_as_future_weather(self):
        data=wttr_data();data['weather'][0]['date']='2020-01-01'
        http=Mock();http.json.side_effect=[SkillError('timeout'),data]
        with self.assertRaisesRegex(SkillError,'invalid_response'):
            Weather(CONFIG,http,now=lambda:NOW).run()

    def test_each_provider_failure_is_preserved_independently(self):
        from assignment_skills.dispatch import Dispatcher,plan
        from router.core import Request
        http=Mock();http.json.side_effect=[SkillError('timeout'),{}]
        result=Dispatcher(CONFIG,http).execute(Request('/weather',principal_id=101),
                                                plan('/weather'))
        self.assertEqual(result['failure'],'invalid_response')
        self.assertEqual(result['skill_execution']['source'],'wttr')
        self.assertTrue(result['skill_execution']['provider_fallback'])
        self.assertEqual(result['skill_execution']['primary_failure'],'timeout')
        self.assertEqual(result['skill_execution']['provider_attempts'],[
            {'provider':'open_meteo','failure':'timeout'},{'provider':'wttr','failure':'invalid_response'}])

    def test_valid_primary_response_does_not_change_provider(self):
        http=Mock();http.json.return_value=weather_data()
        result=Weather(CONFIG,http,now=lambda:NOW).run()
        self.assertEqual(result['provider_id'],'open_meteo')
        self.assertFalse(result['provider_fallback'])
        self.assertEqual(http.json.call_count,1)

    def test_validation_error_is_not_hidden_as_availability_fallback(self):
        data=weather_data();data['current_units']['temperature_2m']='incorrect'
        http=Mock();http.json.return_value=data
        with self.assertRaisesRegex(SkillError,'invalid_response'): Weather(CONFIG,http,now=lambda:NOW).run()
        self.assertEqual(http.json.call_count,1)

    def test_both_services_unavailable_never_return_estimated_weather(self):
        http=Mock();http.json.side_effect=SkillError('timeout')
        with self.assertRaisesRegex(SkillError,'timeout'): Weather(CONFIG,http,now=lambda:NOW).run()
        self.assertEqual(http.json.call_count,2)

    def test_alternative_schema_location_and_ranges_must_be_valid(self):
        for mutation in [lambda d:d['nearest_area'][0].update(latitude='0'),
                         lambda d:d['current_condition'][0].update(humidity='900'),
                         lambda d:d['weather'][0].update(hourly=[]),
                         lambda d:d['current_condition'][0].update(localObsDateTime='opaque payload')]:
            data=wttr_data();mutation(data)
            http=Mock();http.json.side_effect=[SkillError('timeout'),data]
            with self.assertRaisesRegex(SkillError,'invalid_response'): Weather(CONFIG,http,now=lambda:NOW).run()


class SearchFormattingRegressionTests(unittest.IsolatedAsyncioTestCase):
    def result(self,count=5):
        return {'results':[{'source_id':'S'+str(i),'title':'Synthetic AI headline '+str(i),
                           'publisher':'Synthetic Publisher','published_at':'2026-11-18T08:00:00+00:00',
                           'kind':'news','url':'https://news.google.com/rss/articles/'+('C'*1500)+str(i)+'?oc=5',
                           'snippet':'<b>Short source summary.</b> '+('A'*200)+' https://example.org/'+('B'*150)} for i in range(count)],
                'warnings':[]}

    async def test_long_encoded_targets_never_appear_in_visible_reply(self):
        reply=search_reply(self.result())
        self.assertIsInstance(reply,HTMLReply)
        visible=Visible();visible.feed(str(reply))
        text=' '.join(visible.text)
        self.assertNotIn('C'*60,text)
        self.assertNotIn('A'*60,text)
        self.assertNotIn('B'*60,text)
        self.assertNotIn('/rss/articles/',text)
        self.assertIn('Synthetic AI headline',text)
        self.assertIn('Short source summary',text)
        self.assertIn('Synthetic Publisher',text)
        self.assertEqual(len(visible.anchors),5)
        self.assertTrue(all(a['href'].startswith('https://news.google.com/') for a in visible.anchors))

    async def test_html_escaping_prevents_attribute_and_tag_injection(self):
        data=self.result(1)
        data['results'][0].update(title='<script>evil</script> A & B',publisher='Publisher <i>data</i>',
                                  url='https://example.org/path?x="onclick="evil&y=2',snippet='<img src=x onerror=evil>Safe excerpt.')
        reply=search_reply(data);visible=Visible();visible.feed(str(reply))
        self.assertTrue(set(visible.tags)<={'b','a'})
        self.assertNotIn('onclick',visible.anchors[0])
        self.assertEqual(set(visible.anchors[0]),{'href'})
        self.assertNotIn('evil',' '.join(visible.text))
        self.assertIn('A & B',' '.join(visible.text))

    async def test_delivery_chunks_are_bounded_complete_html_and_use_html_mode(self):
        reply=search_reply(self.result())
        api=TelegramAPI('123456789:SYNTHETIC_TOKEN_NOT_REAL')
        response={'message_id':1,'chat':{'id':101}}
        with patch.object(api,'call',new=AsyncMock(return_value=response)) as call:
            await api.send(101,reply)
        self.assertEqual(call.await_count,len(reply.parts))
        for invocation in call.await_args_list:
            payload=invocation.args[1]
            self.assertEqual(payload['parse_mode'],'HTML')
            self.assertLessEqual(len(payload['text'].encode('utf-8')),3500)
            self.assertEqual(payload['text'].count('<a '),payload['text'].count('</a>'))
            self.assertTrue(payload['link_preview_options']['is_disabled'])
        with patch.object(api,'call',new=AsyncMock(return_value=response)) as call:
            await api.send(101,'<script>ordinary model/user text remains literal</script>')
            self.assertNotIn('parse_mode',call.await_args.args[1])

    async def test_missing_summary_is_labelled_not_fabricated(self):
        data=self.result(1);data['results'][0]['snippet']=''
        self.assertIn('no article summary available',str(search_reply(data)))
