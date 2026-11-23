"""Explicit future initialization of objects in an EXISTING private bucket; no provisioning."""
import argparse
import hashlib
import json
from pathlib import Path
from persistence.bundle import unpack
from persistence.coordinator import Coordinator
from persistence.objects import GCSObjects,PersistenceFault


def initialize(objects,seed):
    unpack(seed)
    coordinator=Coordinator(objects)
    coordinator.acquire(bootstrap=True)
    coordinator.export=lambda:seed
    try:
        coordinator.checkpoint()
        restored,_=objects.get(coordinator.head['snapshot'])
        if restored!=seed: raise PersistenceFault('Initialization readback mismatch')
        return {'seed_sha256':hashlib.sha256(seed).hexdigest(),'roundtrip_checksum_verified':True,
                'container_restart_verified':False}
    finally:
        if not coordinator.poisoned: coordinator.release()


def main():
    p=argparse.ArgumentParser(description='EXTERNAL APPROVAL REQUIRED: initialize a new namespace in an existing dedicated assignment bucket')
    p.add_argument('--bucket',required=True);p.add_argument('--namespace',default='csci599-a3');p.add_argument('--seed',required=True)
    args=p.parse_args()
    try:
        seed=Path(args.seed).read_bytes();unpack(seed)
        result=initialize(GCSObjects(args.bucket,prefix=args.namespace),seed)
        print(json.dumps(result));return 0
    except Exception:
        print('Cloud initialization failed; no bucket was created or existing namespace reset.');return 2


if __name__=='__main__': raise SystemExit(main())
