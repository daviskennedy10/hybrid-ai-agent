"""Dedicated synthetic mailbox: read-only IMAP, extractive summaries, unsent drafts."""
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
import imaplib
import hashlib
import json
import re
import ssl

from assignment_skills.common import SkillError,clean


def recipient(value,allowlist):
    if not isinstance(value,str) or any(c in value for c in '\r\n,;'):
        raise SkillError('recipient_blocked')
    _,address=parseaddr(value)
    if not re.fullmatch(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',address):
        raise SkillError('recipient_blocked')
    if address.casefold() not in {a.casefold() for a in allowlist}:
        raise SkillError('recipient_blocked')
    return address


def parse_message(raw,uid):
    message=BytesParser(policy=policy.default).parsebytes(raw)
    subject=clean(str(message.get('Subject','')),200)
    if not subject.startswith('[SYNTHETIC]'): raise SkillError('forbidden')
    body=''
    for part in message.walk():
        if part.get_content_disposition()=='attachment' or part.get_content_type() not in {'text/plain','text/html'}:
            continue
        try:
            content=part.get_content()
            if not isinstance(content,str): continue
            excerpt=clean(content,6000)
        except (LookupError,UnicodeError,ValueError):
            raise SkillError('invalid_response') from None
        if part.get_content_type()=='text/plain': body=excerpt;break
        if not body: body=excerpt
    summary=' '.join(re.split(r'(?<=[.!?])\s+',body)[:3])[:800]
    markers=[word for word in ['urgent','asap','deadline','action required'] if word in (subject+' '+body).casefold()]
    if str(message.get('Importance','')).casefold()=='high' or str(message.get('X-Priority','')).startswith(('1','2')):
        markers.append('high priority header')
    identity=hashlib.sha256(json.dumps([str(message.get(h,'')) for h in ('Message-ID','Date','From','Subject')]+[str(uid)]).encode()).hexdigest()
    return {'identity':identity,'uid':str(uid),'subject':subject,'sender':clean(str(message.get('From','')),200),
            'reply_to':str(message.get('Reply-To',message.get('From',''))),
            'summary':summary or '(no readable text body)','urgent':bool(markers),'urgency_markers':markers,
            'summary_method':'extractive; first three sentences; no model or embedded instruction execution'}


class Email:
    def __init__(self,config,factory=None):
        self.config=config
        self.factory=factory or imaplib.IMAP4_SSL
    def unread(self):
        self.config.email_ready()
        client=None
        try:
            client=self.factory('imap.gmail.com',993,ssl_context=ssl.create_default_context(),timeout=8)
            client.login(self.config.email_username,self.config.email_app_password.replace(' ',''))
            status,_=client.select('INBOX',readonly=True)
            if status!='OK': raise SkillError('unavailable')
            status,data=client.uid('search',None,'UNSEEN','SUBJECT','"[SYNTHETIC]"')
            if status!='OK' or not isinstance(data,list) or len(data)!=1 or not isinstance(data[0],bytes):
                raise SkillError('invalid_response')
            ids=data[0].split()
            if any(not re.fullmatch(rb'\d{1,10}',uid) for uid in ids): raise SkillError('invalid_response')
            messages=[]
            for uid in ids[-10:][::-1]:
                status,parts=client.uid('fetch',uid,'(BODY.PEEK[]<0.65536>)')
                if status!='OK' or not isinstance(parts,list): raise SkillError('invalid_response')
                chunks=[p[1] for p in parts if isinstance(p,tuple) and len(p)==2 and isinstance(p[1],bytes)]
                if len(chunks)!=1 or len(chunks[0])>=65536: raise SkillError('invalid_response')
                messages.append(parse_message(chunks[0],uid.decode('ascii')))
            return messages
        except imaplib.IMAP4.error:
            raise SkillError('unavailable') from None
        except (OSError,TimeoutError):
            raise SkillError('unavailable') from None
        finally:
            if client is not None:
                try: client.logout()
                except (OSError,imaplib.IMAP4.error): pass

    def run(self,operation,uid=None,to=None):
        if operation=='send': return self.send(to=to)
        if operation not in {'unread','summarize','urgent','draft'}: raise SkillError('forbidden')
        if operation=='draft' and to is not None:
            recipient(to,self.config.email_allowed_recipients)  # reject unapproved recipients before network access
        if operation=='draft' and not re.fullmatch(r'\d{1,10}',str(uid)):
            raise SkillError('invalid_request')
        messages=self.unread()
        if operation=='draft':
            message=next((m for m in messages if m['uid']==str(uid)),None)
            if message is None: raise SkillError('invalid_request')
            target=recipient(to or message['reply_to'],self.config.email_allowed_recipients)
            return {'source':'dedicated Gmail IMAP EXAMINE/PEEK','draft':{
                'to':target,'subject':'Re: '+message['subject'],
                'body':'Thank you for your message about "'+message['subject']+'". I will review the details and follow up.\n\n[Review and edit this synthetic draft before any manual use.]',
                'sent':False,'requires_review':True,'sending_enabled':False,'source_uid':message['uid']}}
        if operation=='urgent': messages=[m for m in messages if m['urgent']]
        for message in messages: message.pop('reply_to',None)
        return {'source':'dedicated Gmail IMAP EXAMINE/PEEK','messages':messages,'sending_enabled':False}

    def send(self,*,to=None,approved=False,source='telegram',**kwargs):
        # Unconditional capability boundary: even human/LLM approval or a scheduled caller cannot send.
        # No SMTP or write-capable mail transport exists in this application.
        raise SkillError('sending_disabled')
