"""Call the resident executor without starting an MCP server for each action."""
import argparse
import json
from pathlib import Path
from time import perf_counter
import httpx
from autoslay.bridge.client import load_config
from autoslay.paths import ROOT

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["health", "state", "analyze", "decision", "compare", "plan", "autonomy", "calibration-choice", "calibration-observe"])
    parser.add_argument("file", nargs="?", type=Path)
    args = parser.parse_args()
    if args.operation in {"plan", "compare", "calibration-choice", "calibration-observe"} and args.file is None:
        parser.error(args.operation + " requires a JSON file")
    body = json.loads(args.file.read_text(encoding="utf-8-sig")) if args.file else {}
    token = Path(load_config().token_file).read_text().strip()
    started = perf_counter()
    try:
        with httpx.Client(trust_env=False, timeout=330) as client:
            route=args.operation.replace('calibration-','calibration/')
            response = client.post("http://127.0.0.1:18766/" + route, json=body,
                                   headers={"Authorization": "Bearer " + token})
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        print(json.dumps(dict(error='RESIDENT_UNAVAILABLE',type=type(exc).__name__)))
        raise SystemExit(1)
    output = ROOT / ".runtime/logs" / ("resident-" + args.operation + ".json")
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = dict(status=data.get("status"), reason=data.get("reason"), error=data.get('error'), connected=data.get('connected'),
                   roundtrip_ms=(perf_counter()-started)*1000, output=str(output))
    if args.operation == 'autonomy': summary['autonomy']=data.get('autonomy',data)
    if args.operation == "analyze":
        summary.update(source=data.get("source"), coverage=data.get("coverage"), timings_ms=data.get("timings_ms"),
                       candidates=data.get("candidates", [])[:3], observation_boundaries=data.get("observation_boundaries", [])[:3])
    elif args.operation in {'decision','compare'}:
        summary.update(phase=data.get('phase'),cache_hit=data.get('cache_hit'),request_elapsed_ms=data.get('request_elapsed_ms'),
                       source=data.get('source'),shortlist=data.get('shortlist'),choices=data.get('choices'),
                       shop=data.get('shop'),event=data.get('event'),route=data.get('route'),
                       lookahead=(data.get('combat') or {}).get('lookahead'))
    elif args.operation == "plan":
        s = (data.get("snapshot") or {}).get("state", {})
        p = s.get("player") or {}
        summary.update(total_ms=data.get("total_ms"), steps_executed=len(data.get("steps", [])),
                       phase=s.get("phase"), turn=p.get("turn"), hp=(p.get("creature") or {}).get("hp"),
                       block=(p.get("creature") or {}).get("block"), energy=p.get("energy"))
    print(json.dumps(summary, ensure_ascii=True))

if __name__ == "__main__":
    main()
