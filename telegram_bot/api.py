"""Small Bot API client: fixed HTTPS origin, no redirects, no raw error output."""
import asyncio
import json
from http.client import HTTPException
import socket
from telegram_bot.messages import HTMLReply
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class TelegramError(Exception):
    def __init__(self, kind, retry_after=None):
        self.kind = kind
        self.retry_after = retry_after
        super().__init__(kind)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TelegramAPI:
    METHODS = {'getMe', 'getWebhookInfo', 'getUpdates', 'sendMessage'}

    def __init__(self, token, *, retries=2, sleep=asyncio.sleep):
        self._token = token
        self.retries = retries
        self.sleep = sleep

    def _post(self, method, payload, timeout):
        if method not in self.METHODS:
            raise TelegramError('invalid_method')
        request = Request('https://api.telegram.org/bot' + self._token + '/' + method,
                          data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        opener = build_opener(ProxyHandler({}), NoRedirect())
        try:
            try:
                response = opener.open(request, timeout=timeout)
            except HTTPError as exc:
                # Never include the request URL or Telegram's free-form description.
                response = exc
            with response:
                status = response.code
                body = response.read(2_000_001)
            if 500 <= status < 600:
                raise TelegramError('server_error')
            if len(body) > 2_000_000:
                raise TelegramError('invalid_response')
            try:
                data = json.loads(body)
            except (ValueError, UnicodeError):
                raise TelegramError('invalid_response') from None
            if not isinstance(data, dict) or type(data.get('ok')) is not bool:
                raise TelegramError('invalid_response')
            if not data['ok'] or status != 200:
                code = data.get('error_code', status)
                parameters = data.get('parameters')
                delay = parameters.get('retry_after') if isinstance(parameters, dict) else None
                if code == 429:
                    if type(delay) is not int or not 0 <= delay <= 60:
                        raise TelegramError('rate_limit_long')
                    raise TelegramError('rate_limit', delay)
                kind = {401: 'unauthorized_token', 403: 'forbidden', 409: 'conflict'}.get(code)
                raise TelegramError(kind or ('server_error' if type(code) is int and 500 <= code < 600 else 'api_error'))
            result = data.get('result')
            valid = isinstance(result, list) if method == 'getUpdates' else isinstance(result, dict)
            if not valid:
                raise TelegramError('invalid_response')
            return result
        except (URLError, TimeoutError, socket.timeout, OSError, HTTPException):
            raise TelegramError('transport') from None

    async def call(self, method, payload=None):
        for attempt in range(self.retries + 1):
            try:
                return await asyncio.to_thread(self._post, method, payload or {}, 30)
            except TelegramError as exc:
                # A send may have reached Telegram before a timeout/5xx. Do not replay
                # an ambiguous send; 429 is the only automatically replayed send error.
                transient = exc.kind in {'transport', 'server_error', 'rate_limit'}
                safe_retry = method != 'sendMessage' or exc.kind == 'rate_limit'
                if not transient or not safe_retry or attempt == self.retries:
                    raise
                await self.sleep(exc.retry_after if exc.kind == 'rate_limit' else min(2 ** attempt, 4))

    async def send(self, chat_id, text):
        # 1000 code points use <=2000 UTF-16 units, safely below Telegram's limit.
        if not isinstance(text, str) or not text.strip():
            raise TelegramError('invalid_message')
        html=isinstance(text,HTMLReply)
        if html:
            chunks=text.parts
        else:
            if len(text)>32000: text=text[:32000]+'\n[Response truncated.]'
            chunks=[text[i:i+1000] for i in range(0,len(text),1000)]
        for chunk in chunks:
            payload={'chat_id':chat_id,'text':chunk,'protect_content':True,
                     'link_preview_options':{'is_disabled':True}}
            if html: payload['parse_mode']='HTML'
            result=await self.call('sendMessage',payload)
            chat = result.get('chat')
            if (type(result.get('message_id')) is not int or not isinstance(chat, dict)
                    or chat.get('id') != chat_id):
                raise TelegramError('invalid_response')
