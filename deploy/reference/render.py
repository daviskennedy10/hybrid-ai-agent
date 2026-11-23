"""Print an Instances command for review. Never executes gcloud or provisions resources."""
import json
from pathlib import Path
import shlex


def render(c):
    if (c['resource_type']!='cloud_run_instance' or c['region'] in {'us-central1','us-east1','europe-west1'}
            or c['cpu']!=1 or c['memory'] not in {'1Gi','2Gi'} or c['port']!=8080
            or c['invoker_iam_check'] is not True or c['preparation_only'] is not True
            or c['bucket_read_only'] is not True or c['restart_policy']!='always'):
        raise ValueError('Unsafe or unsupported preparation plan')
    args=['gcloud','beta','run','instances','create',c['instance'],'--project='+c['project'],'--region='+c['region'],
          '--image='+c['image'],'--cpu=1','--memory='+c['memory'],'--port=8080','--restart-policy=always',
          '--ingress=all','--invoker-iam-check','--service-account='+c['service_account'],
          '--add-volume=name=archive,type=cloud-storage,bucket='+c['bucket']+',readonly=true',
          '--add-volume-mount=volume=archive,mount-path='+c['bucket_mount'],
          '--set-env-vars=HERMES_HOME=/run/hermes-profile',
          '--set-secrets=/app/secrets/telegram.env=a3-telegram:1,/app/secrets/skills.json=a3-skills:1,/app/secrets/schedule.json=a3-schedule:1,/app/secrets/persistence.json=a3-persistence:1,/run/hermes-profile/config.yaml=a3-hermes-config:1,/run/hermes-profile/auth.json=a3-hermes-auth:1']
    return '# PREPARATION ONLY; missing runtime components block startup. Do not execute yet.\n'+shlex.join(args)


if __name__=='__main__': print(render(json.loads(Path(__file__).with_name('instance.example.json').read_text())))
