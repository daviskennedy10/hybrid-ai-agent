"""Stage 7 container supervisor; no provisioning or credential refresh."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from operations.credentials import expiry_status

ROOT = Path(__file__).resolve().parents[1]
SOCKET = '/tmp/a3-tailscaled.sock'


def checks(root=ROOT):
    from telegram_bot.config import Settings
    from assignment_skills.config import Config
    from proactive.config import Schedule
    from operations.gateway import load
    if os.geteuid() == 0 or os.environ.get('A3_REQUIRE_GCS') != '1':
        raise ValueError('Non-root durable cloud mode required')
    from operations.audit import isolation_report
    if sys.platform=='linux' and isolation_report()['effective_capabilities_zero'] is not True:
        raise ValueError('No effective Linux capabilities permitted')
    settings = Settings.load(root / 'secrets/telegram.env')
    skills = Config.load(root / 'secrets/skills.json')
    schedule = Schedule.load(root / 'secrets/schedule.json')
    schedule.authorize(settings.allowed_users, skills)
    if not schedule.enabled: raise ValueError('Cloud proactive scheduling must be configured')
    load(root / 'secrets/gateway.json', settings.allowed_users & skills.allowed_user_ids)
    c = json.loads((root / 'secrets/persistence.json').read_text())
    if c.get('mode') != 'gcs' or c.get('bootstrap') is not False or c.get('lease_seconds') != 120:
        raise ValueError('Initialize and verify durable state before deployment')
    if importlib.util.find_spec('google.cloud.storage') is None:
        raise ValueError('Cloud persistence dependency missing')
    profile = Path(os.environ['HERMES_HOME'])
    if profile == Path.home() / '.hermes':
        raise ValueError('Dedicated cloud OAuth profile required')
    status = expiry_status(json.loads((profile / 'auth.json').read_text()))
    if (status['minimum_remaining_seconds'] or 0) < 54 * 3600:
        raise ValueError('Separate cloud OAuth must cover the evidence window plus margin')
    key = (root / 'secrets/tailscale.key').read_text().strip()
    if not key.startswith('tskey-auth-') or len(key) > 512 or any(x.isspace() for x in key):
        raise ValueError('Tagged Tailscale authentication required')
    # Values never leave this function. Runtime still independently validates all
    # account, destination, model and persistence configuration before polling.
    return True


def wait_for_previous_lease(root=ROOT, clock=time.time, sleep=time.sleep):
    from persistence.coordinator import Coordinator
    from persistence.objects import GCSObjects
    c=json.loads((root / 'secrets/persistence.json').read_text())
    objects=GCSObjects(c['bucket'],prefix=c['namespace'])
    coordinator=Coordinator(objects,lease_seconds=120)
    deadline=clock()+135
    while True:
        raw,_=objects.get('head')
        head=coordinator._read(raw)
        if head['snapshot'] is None: raise ValueError('Durable namespace was not initialized')
        if head['until']<=clock(): return
        if clock()>=deadline: raise ValueError('Another durable owner remains active')
        sleep(min(5,max(0.1,head['until']-clock())))


def start_tailnet():
    daemon = subprocess.Popen([
        'tailscaled', '--tun=userspace-networking', '--state=mem:',
        '--statedir=/tmp/a3-tailscale', '--socket=' + SOCKET,
        '--outbound-http-proxy-listen=127.0.0.1:1055'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            if daemon.poll() is not None:
                raise ValueError('Private network daemon stopped')
            if Path(SOCKET).exists():
                break
            time.sleep(0.2)
        subprocess.run([
            'tailscale', '--socket=' + SOCKET, 'up',
            '--auth-key=file:' + str(ROOT / 'secrets/tailscale.key'),
            '--hostname=a3-cloud-agent', '--advertise-tags=tag:a3-cloud',
            '--accept-dns=false', '--accept-routes=false', '--timeout=45s'],
            check=True, timeout=50, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return daemon
    except BaseException:
        daemon.terminate()
        daemon.wait(timeout=10)
        raise


def main():
    children = []
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
        # Telegram drains/checkpoints before private transport goes away.
        if len(children) == 3 and children[-1].poll() is None:
            children[-1].send_signal(signal.SIGTERM)
    try:
        checks()
        wait_for_previous_lease()
        children.append(start_tailnet())
        children.append(subprocess.Popen([sys.executable, '-m', 'operations.ollama_bridge']))
        # Bind bridge before accepting Telegram work. No inference is sent here.
        import socket
        for _ in range(50):
            try:
                with socket.create_connection(('127.0.0.1', 11434), timeout=0.2):
                    break
            except OSError:
                if children[1].poll() is not None:
                    raise ValueError('Private bridge stopped')
                time.sleep(0.1)
        else:
            raise ValueError('Private bridge startup timeout')
        os.environ['A3_GATEWAY_CONFIG'] = str(ROOT / 'secrets/gateway.json')
        os.environ['A3_CLOUD_EVIDENCE'] = '1'
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, stop)
        children.append(subprocess.Popen([sys.executable, '-m', 'telegram_bot']))
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(0.5)
        return 0 if stopping else 1
    except Exception:
        print(json.dumps({'status': 'cloud_start_failed', 'details_redacted': True}), flush=True)
        return 78
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
