"""Bounded read-only network access and literal external-text handling."""
import json
import re
import socket
from html import unescape
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class SkillError(Exception):
    KINDS = {'not_configured','forbidden','invalid_request','unavailable','timeout','invalid_response',
             'unsupported_calendar','wrong_calendar','recipient_blocked','sending_disabled','private_search_blocked'}
    def __init__(self, kind):
        self.kind = kind if kind in self.KINDS else 'unavailable'
        super().__init__(self.kind)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Reader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in {'script','style'}:
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag in {'script','style'}:
            self.hidden = max(0, self.hidden-1)
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean(value, limit=1000):
    parser = Reader()
    parser.feed(str(value)[:20000])
    text = unescape(' '.join(parser.parts))
    text = ''.join(c for c in text if c in '\n\t' or (c.isprintable() and c not in '\u202a\u202b\u202d\u202e\u2066\u2067\u2068\u2069'))
    return re.sub(r'\s+', ' ', text).strip()[:limit]


class HTTP:
    HOSTS = {'calendar.google.com','news.google.com','en.wikipedia.org','api.open-meteo.com','wttr.in'}
    def get(self, url):
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or parsed.hostname not in self.HOSTS or parsed.username
                or parsed.password or parsed.port not in {None,443} or parsed.fragment):
            raise SkillError('invalid_request')
        for attempt in range(2):
            try:
                opener = build_opener(ProxyHandler({}), NoRedirect())
                with opener.open(Request(url, headers={'User-Agent':'CSCI599Assignment4/1.0 (synthetic educational tools)'}), timeout=8) as response:
                    body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise SkillError('invalid_response')
                return body
            except HTTPError as exc:
                if exc.code >= 500 and attempt == 0:
                    continue
                raise SkillError('unavailable') from None
            except (TimeoutError, socket.timeout):
                if attempt:
                    raise SkillError('timeout') from None
            except (URLError, OSError, HTTPException) as exc:
                if attempt:
                    reason=exc.reason if isinstance(exc,URLError) else exc
                    raise SkillError('timeout' if isinstance(reason,TimeoutError) else 'unavailable') from None
        raise SkillError('unavailable')

    def json(self, url):
        try:
            result = json.loads(self.get(url))
        except (ValueError, UnicodeError):
            raise SkillError('invalid_response') from None
        if not isinstance(result, dict):
            raise SkillError('invalid_response')
        return result
