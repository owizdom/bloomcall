"""Replay a saved multi-turn transcript against Darkbloom to reproduce an issue.

usage: HEARTH_API_KEY_FILE=path python3 -I repro/replay.py harmony_leak|http_422 [TIMES]
  harmony_leak  gpt-oss-20b, rename trial 1, every message before the leaked reply.
                Expected issue: the reply's content holds raw Harmony tokens
                ('to=functions.read<|constrain|>json<|message|>{...}') and tool_calls is empty.
  http_422      gpt-oss-20b, count_rows trial 1, the full conversation up to the failed request.
                Expected issue: HTTP 422 {"code":"model_capability","message":"inference generation failed"}.
"""
import json, os, sys, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bench"))
import mt  # noqa: E402

CASES = {"harmony_leak": ("rename-1.json", -1), "http_422": ("count_rows-1.json", None)}
name, times = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3
fname, cut = CASES[name]
d = json.load(open(os.path.join(HERE, "..", "results", "multi-turn", "gpt-oss-20b", fname)))
msgs = d["transcript"][:cut] if cut else d["transcript"]
key = open(os.path.expanduser(os.environ["HEARTH_API_KEY_FILE"])).read().strip()
body = json.dumps({"model": "gpt-oss-20b", "messages": msgs, "tools": mt.TOOLS, "tool_choice": "auto",
                   "max_tokens": 8192, "stream": False}).encode()
for i in range(times):
    req = urllib.request.Request("https://api.darkbloom.dev/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            m = json.load(r)["choices"][0]["message"]
            print(i, "HTTP 200", mt.turn_verdict(m), repr((m.get("content") or "")[:110]),
                  [c["function"]["name"] for c in m.get("tool_calls") or []])
    except urllib.error.HTTPError as e:
        print(i, "HTTP", e.code, e.read()[:200].decode(errors="replace"))
