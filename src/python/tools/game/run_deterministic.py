"""Run a bounded local prefix; stop at the first AI decision or observation gap."""
import argparse
import json
from pathlib import Path
from uuid import uuid4
import httpx
from autoslay.bridge.client import load_config

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true',help='execute proven actions; otherwise inspect only')
    parser.add_argument('--max-actions',type=int,default=12)
    args=parser.parse_args()
    if not 1<=args.max_actions<=100: parser.error('max-actions must be between 1 and 100')
    token=Path(load_config().token_file).read_text().strip()
    with httpx.Client(trust_env=False,timeout=40) as client:
        for _ in range(args.max_actions if args.execute else 1):
            # One identity per operation. This client NEVER retries failed HTTP requests.
            body=dict(execute=args.execute,request_id='local-'+uuid4().hex)
            response=client.post('http://127.0.0.1:18766/autonomy',json=body,headers={'Authorization':'Bearer '+token})
            response.raise_for_status(); result=response.json();proof=result.get('autonomy',result)
            print(json.dumps(dict(status=result.get('status'),mode=proof.get('mode'),rule=proof.get('rule_id'),
                reason=proof.get('reason'),blockers=proof.get('reasons'),request_id=body['request_id']),ensure_ascii=True),flush=True)
            if not args.execute or result.get('status')!='completed': break

if __name__=='__main__': main()
