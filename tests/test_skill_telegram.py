from dataclasses import replace
import unittest
from unittest.mock import Mock
from assignment_skills.dispatch import Dispatcher
from assignment_skills.calendar import Calendar
from assignment_skills.email import Email
from router.core import Router,Policy
from telegram_bot.service import Bot
from test_telegram import FakeAPI,Settings,TOKEN,update
from test_skills import CONFIG,FakeHTTP,FakeIMAP,NOW


class TelegramSkillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        http=FakeHTTP()
        dispatcher=Dispatcher(CONFIG,http,calendar=Calendar(CONFIG,http,lambda:NOW),email=Email(CONFIG,FakeIMAP))
        self.backend=Mock();self.routes=[];self.events=[];self.api=FakeAPI()
        self.router=Router(Policy.load(),self.backend,self.routes.append,skill_dispatcher=dispatcher)
        self.bot=Bot(Settings(TOKEN,frozenset({101,202}),False),self.api,self.router,self.events.append)
    async def test_four_tools_through_authorized_telegram(self):
        for i,text in enumerate(['/calendar tomorrow','/email summarize','/new','/search recent AI','/weather'],1):
            await self.bot.handle_update(update(i,text=text))
        self.assertEqual([e['skill_execution']['skill'] for e in self.routes],['calendar','email','web_search','weather'])
        self.assertTrue(all(e['success'] for e in self.routes))
        self.assertTrue(all(e['route']=='tool' for e in self.routes))
        self.assertTrue(all(e['tool_success'] for e in self.events if e.get('skill_execution')))
        self.assertTrue(all(not e['model_success'] for e in self.events if e.get('skill_execution')))
        self.backend.infer.assert_not_called()
    async def test_rejected_sender_cannot_execute_tools(self):
        await self.bot.handle_update(update(user=999,text='/email unread'))
        self.assertEqual(self.routes,[])
        self.assertEqual(self.api.sent,[])
    async def test_secondary_skill_allowlist_enforced(self):
        await self.bot.handle_update(update(user=202,text='/calendar today'))
        self.assertEqual(self.routes[-1]['failure'],'forbidden')
        self.assertEqual(self.events[-1]['status'],'skill_failed')
    async def test_private_data_never_enters_later_model_history(self):
        await self.bot.handle_update(update(1,text='/email summarize'))
        self.assertTrue(self.bot.sessions[101].private)
        self.assertNotIn('fictional plan',str(self.bot.sessions[101].history))
        self.backend.healthy.return_value=True;self.backend.infer.return_value='Local answer.'
        await self.bot.handle_update(update(2,text='Hello!'))
        route,request=self.backend.infer.call_args.args
        self.assertEqual(route,'local')
        self.assertTrue(request.private)
        self.assertNotIn('fictional plan',str(request.history))
    async def test_send_disabled_notice_and_no_duplicate_skill_calls(self):
        incoming=update(1,text='/email send')
        await self.bot.handle_update(incoming);await self.bot.handle_update(incoming)
        self.assertEqual(len(self.routes),1)
        self.assertIn('sending is disabled',self.api.sent[0][1])
    async def test_skill_evidence_requires_actual_success_and_delivery(self):
        from assignment_skills.verify_logs import verify
        for i,text in enumerate(['/calendar today','/email summarize','/new','/search recent AI','/weather'],1):
            await self.bot.handle_update(update(i,text=text))
        report=verify(self.events,self.routes)
        self.assertTrue(all(report['verified_skills'].values()))
        self.routes[0]['success']=False
        self.assertFalse(verify(self.events,self.routes)['verified_skills']['calendar'])
        self.assertFalse(any(verify(self.events,[])['verified_skills'].values()))
    async def test_private_search_hold_uses_accurate_skill_notice(self):
        await self.bot.handle_update(update(1,text='/private'))
        await self.bot.handle_update(update(2,text='/search recent AI'))
        self.assertIn('Web search is held',self.api.sent[-1][1])
        self.assertEqual(self.events[-1]['status'],'held')
