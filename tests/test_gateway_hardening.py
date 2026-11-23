"""Real HTTP handlers over memory sockets: no production, sockets or inference."""
import base64
import io
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from operations.gateway import Handler, same_origin
from operations.gateway_limits import AccessLimits

PASSWORD='synthetic-review-canary-'+'x'*43
AUTH='Basic '+base64.b64encode(('grader:'+PASSWORD).encode()).decode()

class Socket:
    def __init__(self,raw):self.raw=io.BytesIO(raw);self.output=bytearray()
    def makefile(self,*args,**kwargs):return self.raw
    def sendall(self,data):self.output.extend(data)
    def settimeout(self,*args):pass

class GatewayHardeningTests(unittest.TestCase):
    def setUp(self):
        self.events=[]
        self.router=Mock()
        self.router.run.return_value={'success':True,'response':'Synthetic answer','request_id':'a'*32}
        self.router.activity=None
        self.server=SimpleNamespace(config={'password':PASSWORD,'principal_id':101},router=self.router,
            inference_slots=threading.BoundedSemaphore(1),access_limits=AccessLimits(),
            health_path='/missing/synthetic-health',audit=self.events.append)

    def call(self,method='POST',path='/ask',auth=AUTH,body=b'{"text":"Hello!"}',extra=()):
        headers=[('Host','assistant.example'),('Content-Type','application/json'),('Content-Length',str(len(body)))]
        if auth is not None:headers.append(('Authorization',auth))
        headers.extend(extra)
        raw=(method+' '+path+' HTTP/1.0\r\n'+''.join(k+': '+v+'\r\n' for k,v in headers)+'\r\n').encode()+body
        socket=Socket(raw);Handler(socket,('127.0.0.1',1),self.server)
        head,_,payload=bytes(socket.output).partition(b'\r\n\r\n')
        return int(head.split()[1]),head,payload

    def test_missing_wrong_malformed_and_duplicate_passwords_never_dispatch(self):
        for auth,extra in [(None,()),('Basic incorrect',()),('Bearer incorrect',()),('Basic !!!',()),
                           (AUTH,(('Authorization',AUTH),))]:
            with self.subTest(auth_kind=auth is not None):
                status,head,body=self.call(auth=auth,extra=extra)
                self.assertEqual(status,401)
                self.assertNotIn(PASSWORD.encode(),head+body)
        self.router.run.assert_not_called()

    def test_bad_password_flood_does_not_lock_out_correct_password(self):
        for i in range(200):
            self.assertEqual(self.call(auth='Basic incorrect')[0],401 if i<10 else 429)
        self.router.run.assert_not_called()
        self.assertEqual(len(self.server.access_limits.failures),10)
        self.assertEqual(len(self.events),10)
        self.assertEqual(self.call()[0],200)
        self.router.run.assert_called_once()

    def test_invalid_requests_never_consume_valid_request_budget(self):
        for _ in range(20):self.call(auth=None)
        self.assertEqual(len(self.server.access_limits.requests),0)
        self.assertEqual(self.call()[0],200)

    def test_valid_request_limit_remains_independent_and_bounded(self):
        for _ in range(30):self.assertEqual(self.call()[0],200)
        self.assertEqual(self.call()[0],429)
        self.assertEqual(self.router.run.call_count,30)

    def test_malformed_origin_is_controlled_and_never_dispatches(self):
        for origin in ['https://[broken','https://assistant.example:bad','https://assistant.example:99999',
                       'null','','https://user@assistant.example','https://assistant.example/path',
                       'https://assistant.example?','http://assistant.example','https://evil.example']:
            with self.subTest(origin=origin):
                self.assertEqual(self.call(extra=(('Origin',origin),))[0],403)
        self.router.run.assert_not_called()

    def test_duplicate_origins_and_hosts_are_rejected(self):
        for extra in [(('Origin','https://assistant.example'),('Origin','https://assistant.example')),
                      (('Origin','https://assistant.example'),('Host','evil.example'))]:
            self.assertEqual(self.call(extra=extra)[0],403)
        self.router.run.assert_not_called()

    def test_equivalent_https_origin_and_nonbrowser_requests_work(self):
        for origin in ['https://assistant.example','https://ASSISTANT.EXAMPLE:443']:
            self.assertEqual(self.call(extra=(('Origin',origin),))[0],200)
        self.assertEqual(self.call()[0],200)

    def test_control_characters_and_bad_ports_in_origin_helper(self):
        for origin,host in [('https://assistant.example\n','assistant.example'),
                            ('https://assistant.example','assistant.example:0'),
                            ('https://assistant.example','assistant.example/path')]:
            self.assertFalse(same_origin(origin,host))

    def test_unpaired_unicode_surrogates_are_rejected_before_router(self):
        for value in ['\ud800','\udfff','prefix\ud800suffix']:
            self.assertEqual(self.call(body=json.dumps({'text':value}).encode())[0],400)
        self.router.run.assert_not_called()

    def test_valid_unicode_and_escaped_pairs_are_supported(self):
        for body in [json.dumps({'text':'Synthetic café 🚀'}).encode(),b'{"text":"\\ud83d\\ude80"}']:
            self.assertEqual(self.call(body=body)[0],200)
        self.assertEqual(self.router.run.call_count,2)

    def test_no_server_or_python_fingerprint_on_success_and_rejections(self):
        for kwargs in [{},{'auth':None},{'body':b'invalid'},{'extra':(('Origin','https://[broken'),)},
                       {'method':'TRACE'},{'method':'GET','path':'/startupz'},
                       {'method':'GET','path':'/healthz'},{'method':'GET','path':'/admin'}]:
            _,head,body=self.call(**kwargs)
            self.assertNotIn(b'\r\nserver:',head.lower())
            self.assertNotIn(b'python',head.lower()+body.lower())
            self.assertNotIn(b'basehttp',head.lower()+body.lower())
            self.assertIn(b'Date:',head)

    def test_logs_and_errors_do_not_reflect_headers_or_prompt(self):
        secret_text='synthetic-body-canary'
        self.call(body=json.dumps({'text':secret_text}).encode())
        self.call(auth='Basic incorrect',extra=(('Origin','https://secret-header-canary'),))
        logged=json.dumps(self.events)
        for text in [secret_text,PASSWORD,AUTH,'secret-header-canary']:
            self.assertNotIn(text,logged)

    def test_administrative_storage_and_model_paths_never_dispatch(self):
        for path in ['/admin','/metrics','/debug','/secrets','/snapshots','/v1/chat/completions','/api/pull']:
            self.assertEqual(self.call(method='GET',path=path,auth=None)[0],401)
            self.assertEqual(self.call(method='GET',path=path)[0],404)
        self.router.run.assert_not_called()

if __name__=='__main__':unittest.main()
