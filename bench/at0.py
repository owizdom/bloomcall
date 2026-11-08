"""AT0 gate (spec 0001): can the served model emit parsed tool calls?

Sends 10 single-tool requests to llama-server's /v1/chat/completions with hearth's six
tool definitions and a short agent system prompt, one request at a time. Saves every raw
response and judges each one mechanically.

PASS for a trial: message.tool_calls is non-empty, and every call names an offered tool
and carries JSON-object arguments containing that tool's required keys.
Failure kinds (counted as hallucinated in the probe later):
  text_leak    a tool call written into content instead of emitted as a call
  unoffered    a call to a tool name that was not offered
  bad_args     arguments that are not a JSON object or miss required keys
  no_call      a plain answer with no call
The gate passes at >= 8 of 10.

usage: python3 -I probe/w0/at0.py OUT_DIR [BASE_URL] [PAUSE_SECONDS]
PAUSE_SECONDS (default 0) idles between trials so the machine stays usable during a run.
"""
import json, os, re, sys, time, urllib.request

OUT = sys.argv[1]
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8080/v1"
PAUSE = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
FORCE_MODEL = sys.argv[4] if len(sys.argv) > 4 else None   # required for multi-model APIs
KEY_FILE = os.environ.get("HEARTH_API_KEY_FILE")             # bearer key for remote APIs
HEADERS = {"Content-Type": "application/json"}
if KEY_FILE:
    HEADERS["Authorization"] = "Bearer " + open(os.path.expanduser(KEY_FILE)).read().strip()
MAX_TOKENS = int(os.environ.get("HEARTH_MAX_TOKENS", "8192"))
MODEL = "qwen3.8-27b-turbo"
WORKDIR = "/tmp/hearth-w0-project"


def tool(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


S = {"type": "string"}
I = {"type": "integer"}
TOOLS = [
    tool("read", "Read a text file and return its numbered lines.", {"path": S, "offset": I, "limit": I}, ["path"]),
    tool("write", "Create or fully replace a text file.", {"path": S, "content": S}, ["path", "content"]),
    tool("edit", "Replace one exact, unique occurrence of old_string with new_string in a file.",
         {"path": S, "old_string": S, "new_string": S}, ["path", "old_string", "new_string"]),
    tool("shell", "Run a shell command in the working directory and return its output.",
         {"command": S, "timeout_ms": I}, ["command"]),
    tool("grep", "Search file contents with a regular expression; returns path:line matches.",
         {"pattern": S, "path": S, "include": S}, ["pattern"]),
    tool("glob", "Find file paths matching a glob pattern such as src/**/*.ts.", {"pattern": S, "path": S}, ["pattern"]),
]
REQUIRED = {t["function"]["name"]: t["function"]["parameters"]["required"] for t in TOOLS}

COMMON = (f"The working directory is {WORKDIR}. To act, call a tool; the call is executed and its "
          "result is returned to you. Never write a tool call as text, JSON or a code block. "
          "Do not guess file contents or command output: call the tool.")
SMITH = "You are smith, a general software agent running locally in hearth. " + COMMON
WARDEN = ("You are warden, a security agent running locally in hearth. You audit code and systems "
          "for vulnerabilities, secrets and misconfiguration, and you report evidence with file and line. " + COMMON)

TRIALS = [  # (agent prompt, user request, tool we expect)
    (SMITH, "Show me what's in notes.txt.", "read"),
    (SMITH, "Create hello.py that prints hello world.", "write"),
    (SMITH, "In config.toml the line `timeout = 60` should be `timeout = 30`. Change it.", "edit"),
    (SMITH, "Run the test suite with npm test.", "shell"),
    (SMITH, "Find every TODO comment under src.", "grep"),
    (SMITH, "List all the TypeScript files under src.", "glob"),
    (SMITH, "Which git branch am I on?", "shell"),
    (WARDEN, "Search this repo for hardcoded AWS access keys (they start with AKIA).", "grep"),
    (WARDEN, "Check which processes are listening on TCP ports on this machine.", "shell"),
    (WARDEN, "Write our findings so far to findings.md with the heading '# Findings' and the line 'No secrets found yet.'", "write"),
]

LEAK = re.compile(r"<tool_call>|<function=|\"name\"\s*:\s*\"(read|write|edit|shell|grep|glob)\"|```(json)?\s*\{")


def judge(msg):
    calls = msg.get("tool_calls") or []
    content = msg.get("content") or ""
    if not calls:
        return ("text_leak" if LEAK.search(content) else "no_call"), []
    names = []
    for c in calls:
        fn = c.get("function", {})
        name = fn.get("name")
        names.append(name)
        if name not in REQUIRED:
            return "unoffered", names
        try:
            args = json.loads(fn.get("arguments") or "")
        except (json.JSONDecodeError, TypeError):
            return "bad_args", names
        if not isinstance(args, dict) or any(k not in args for k in REQUIRED[name]):
            return "bad_args", names
    return "pass", names


def post(body):
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(body).encode(), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.load(r)


def served_model():
    """Alias and file path of the model llama-server is serving, so results name what they measured."""
    global MODEL
    if FORCE_MODEL:
        MODEL = FORCE_MODEL
        return "remote:" + BASE
    with urllib.request.urlopen(urllib.request.Request(BASE + "/models", headers=HEADERS), timeout=30) as r:
        MODEL = json.load(r)["data"][0]["id"]
    try:
        with urllib.request.urlopen(BASE.rsplit("/v1", 1)[0] + "/props", timeout=30) as r:
            return json.load(r).get("model_path")
    except Exception:
        return "unknown"


def main():
    os.makedirs(OUT, exist_ok=True)
    model_path = served_model()
    print(json.dumps({"model": MODEL, "model_path": model_path}), flush=True)
    rows = []
    for i, (system, user, expect) in enumerate(TRIALS, 1):
        if i > 1 and PAUSE:
            time.sleep(PAUSE)
        body ={"model": MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "tools": TOOLS, "tool_choice": "auto", "max_tokens": MAX_TOKENS, "stream": False}
        t0 = time.time()
        try:
            resp = post(body)
            err = None
        except Exception as e:  # recorded, counted as a failure, run continues
            resp, err = {}, repr(e)
        dt = time.time() - t0
        msg = (resp.get("choices") or [{}])[0].get("message", {}) if resp else {}
        verdict, names = judge(msg) if not err else ("error", [])
        tm = dict(resp.get("timings") or {})
        us = resp.get("usage") or {}
        tm.setdefault("prompt_n", us.get("prompt_tokens"))
        tm.setdefault("predicted_n", us.get("completion_tokens"))
        if "predicted_per_second" not in tm and us.get("completion_tokens") and dt:
            tm["predicted_per_second"] = us["completion_tokens"] / dt   # wall-clock, includes network
        row = {"trial": i, "agent": "warden" if system is WARDEN else "smith", "expect": expect, "verdict": verdict,
               "called": names, "expected_tool_called": expect in names, "seconds": round(dt, 1),
               "finish": (resp.get("choices") or [{}])[0].get("finish_reason") if resp else None,
               "prompt_tokens": tm.get("prompt_n"), "prompt_tps": round(tm.get("prompt_per_second", 0), 1),
               "gen_tokens": tm.get("predicted_n"), "gen_tps": round(tm.get("predicted_per_second", 0), 2),
               "reasoning_chars": len(msg.get("reasoning_content") or ""), "error": err}
        rows.append(row)
        with open(os.path.join(OUT, f"trial-{i:02d}.json"), "w") as f:
            json.dump({"request": body, "response": resp, "row": row}, f, indent=1)
        print(json.dumps(row), flush=True)
    passes = sum(r["verdict"] == "pass" for r in rows)
    summary = {"model": MODEL, "model_path": model_path,
               "passes": passes, "trials": len(rows), "gate": "PASS" if passes >= 8 else "FAIL",
               "expected_tool_called": sum(r["expected_tool_called"] for r in rows),
               "verdicts": {v: sum(r["verdict"] == v for r in rows) for v in sorted({r["verdict"] for r in rows})}}
    with open(os.path.join(OUT, "summary.json"), "w") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=1)
    print("SUMMARY " + json.dumps(summary))


if __name__ == "__main__":
    main()
