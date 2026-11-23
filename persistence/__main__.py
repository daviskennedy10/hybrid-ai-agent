"""Local seed export only. Never uploads to GCS or provisions an account/resource."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from agent_memory.store import Store
from persistence.bundle import pack,database
from telegram_bot.config import ROOT,Settings


def main():
    parser=argparse.ArgumentParser(description='Export validated synthetic state to a new local file; stop the bot for cross-store consistency')
    parser.add_argument('--export-local',required=True)
    args=parser.parse_args()
    try:
        from telegram_bot.__main__ import instance_lock
        with instance_lock(ROOT/'router/logs/telegram.lock'):
            settings=Settings.load()
            target=Path(args.export_local)
            if target.exists() or target.is_symlink(): raise ValueError('Destination exists')
            logs=ROOT/'router/logs'
            files={'scheduler.sqlite3':database(logs/'proactive-state/state.sqlite3'),
                   'activity.sqlite3':database(logs/'activity/activity.sqlite3')}
            files.update(Store(logs/'memory',settings.allowed_users).export())
            body=pack(files);fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream: stream.write(body);stream.flush();os.fsync(stream.fileno())
            print(json.dumps({'exported':True,'bytes':len(body),'cloud_upload_performed':False}));return 0
    except Exception:
        print('Local export failed; ensure the bot is stopped, state exists and destination is new.');return 2


if __name__=='__main__': raise SystemExit(main())
