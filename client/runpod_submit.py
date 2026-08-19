from __future__ import annotations
import argparse, base64, json, os, time
from pathlib import Path
from urllib.request import Request, urlopen

def request_json(url, api_key, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(url, data=data, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }, method="POST" if payload is not None else "GET")
    with urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--image", action="append", default=[], help="NAME=PATH")
    ap.add_argument("--poll", type=int, default=5)
    args = ap.parse_args()

    api_key = os.environ["RUNPOD_API_KEY"]
    endpoint_id = os.environ["RUNPOD_ENDPOINT_ID"]
    workflow = json.loads(Path(args.workflow).read_text(encoding="utf-8"))
    images = []
    for item in args.image:
        name, path = item.split("=", 1)
        images.append({"name": name, "image": base64.b64encode(Path(path).read_bytes()).decode()})

    base = f"https://api.runpod.ai/v2/{endpoint_id}"
    job = request_json(f"{base}/run", api_key, {"input": {"workflow": workflow, "images": images}})
    job_id = job["id"]
    print(f"submitted: {job_id}")

    while True:
        status = request_json(f"{base}/status/{job_id}", api_key)
        state = status.get("status")
        print(state)
        if state in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            print(json.dumps(status, indent=2)[:20000])
            break
        time.sleep(args.poll)

if __name__ == "__main__":
    main()
