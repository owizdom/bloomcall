"""Multi-turn agent test: can a model use tool results to finish a real task?

Each episode copies a small project into a fresh directory, gives the model hearth's six tools,
executes every call for real, returns the result, and loops until the model answers without a
call or hits MAX_TURNS. A task-specific judge then checks the files on disk. The file tools are
confined to the task directory. Shell runs under sandbox-exec with network denied and writes
allowed only under /private/var/folders (the system temp tree that holds the task directory)
and /dev.

usage: python3 -I probe/w0/mt.py OUT_DIR BASE_URL MODEL [TRIALS]
env:   HEARTH_API_KEY_FILE  bearer key file for remote APIs
"""
import fnmatch, hashlib, json, os, re, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

MAX_TURNS = 15
OUT_LIMIT = 8000


def tool(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


S, I = {"type": "string"}, {"type": "integer"}
TOOLS = [
    tool("read", "Read a text file and return its numbered lines.", {"path": S, "offset": I, "limit": I}, ["path"]),
    tool("write", "Create or fully replace a text file.", {"path": S, "content": S}, ["path", "content"]),
    tool("edit", "Replace one exact, unique occurrence of old_string with new_string in a file.",
         {"path": S, "old_string": S, "new_string": S}, ["path", "old_string", "new_string"]),
    tool("shell", "Run a shell command in the working directory and return its output.",
         {"command": S, "timeout_ms": I}, ["command"]),
    tool("grep", "Search file contents with a regular expression; returns path:line:text matches.",
         {"pattern": S, "path": S, "include": S}, ["pattern"]),
    tool("glob", "Find file paths matching a glob pattern such as src/**/*.ts.", {"pattern": S, "path": S}, ["pattern"]),
]
REQUIRED = {t["function"]["name"]: t["function"]["parameters"]["required"] for t in TOOLS}
LEAK = re.compile(r"<tool_call>|<function=|\"name\"\s*:\s*\"(read|write|edit|shell|grep|glob)\"|```(json)?\s*\{"
                  r"|to=functions\.|<\|(constrain|message|call|channel)\|>")   # gpt-oss Harmony tokens leaked into content

COMMON = ("The working directory is the project root. To act, call a tool; the call is executed and its "
          "result is returned to you. Never write a tool call as text, JSON or a code block. Do not guess "
          "file contents or command output: call the tool. When the task is done, reply with a one-line summary.")
SMITH = "You are smith, a general software agent running locally in hearth. " + COMMON
WARDEN = ("You are warden, a security agent running locally in hearth. You audit code for vulnerabilities and "
          "secrets and report evidence with file and line. " + COMMON)


# ---------------------------------------------------------------- tools

class Tools:
    def __init__(self, root):
        self.root = os.path.realpath(root)

    def _path(self, p):
        full = os.path.realpath(os.path.join(self.root, p))
        if full != self.root and not full.startswith(self.root + os.sep):
            raise ValueError(f"path {p!r} is outside the project")
        return full

    def run(self, name, args):
        if name not in REQUIRED:
            return f"ERROR: unknown tool {name!r}"
        if not isinstance(args, dict) or any(k not in args for k in REQUIRED[name]):
            return f"ERROR: {name} requires {REQUIRED[name]}"
        try:
            return getattr(self, "_" + name)(**{k: v for k, v in args.items() if k in
                                                 TOOLS[[t['function']['name'] for t in TOOLS].index(name)]['function']['parameters']['properties']})
        except Exception as e:  # every failure goes back to the model as text
            return f"ERROR: {e}"

    def _read(self, path, offset=1, limit=2000):
        with open(self._path(path), errors="replace") as f:
            lines = f.read().splitlines()
        start = max(int(offset or 1), 1)
        sel = lines[start - 1:start - 1 + int(limit or 2000)]
        return "\n".join(f"{start + i}\t{l}" for i, l in enumerate(sel)) or "(empty file)"

    def _write(self, path, content):
        full = self._path(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return f"wrote {len(content)} bytes to {path}"

    def _edit(self, path, old_string, new_string):
        full = self._path(path)
        with open(full) as f:
            s = f.read()
        n = s.count(old_string)
        if n != 1:
            raise ValueError(f"old_string matches {n} times; it must match exactly once")
        with open(full, "w") as f:
            f.write(s.replace(old_string, new_string))
        return f"edited {path}"

    def _shell(self, command, timeout_ms=30000):
        profile = ("(version 1)(allow default)(deny network*)(deny file-write*)"
                   f'(allow file-write* (subpath "{self.root}") (subpath "/private/var/folders") '
                   '(literal "/dev/null") (literal "/dev/tty") (regex #"^/dev/fd/"))')
        t = min(max(int(timeout_ms or 30000), 1000), 60000) / 1000
        try:
            p = subprocess.run(["sandbox-exec", "-p", profile, "/bin/zsh", "-f", "-c", command], cwd=self.root,
                               capture_output=True, text=True, timeout=t, env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                                                                              "HOME": self.root, "LANG": "C"})
            out = (p.stdout + p.stderr)[:OUT_LIMIT]
            return f"exit {p.returncode}\n{out}"
        except subprocess.TimeoutExpired:
            return f"ERROR: command timed out after {t:.0f}s"

    def _grep(self, pattern, path=".", include=None):
        rx, base, hits = re.compile(pattern), self._path(path), []
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if not d.startswith(".") and d != "__pycache__"]
            for fn in sorted(fns):
                if include and not fnmatch.fnmatch(fn, include):
                    continue
                fp = os.path.join(dp, fn)
                try:
                    with open(fp, errors="strict") as f:
                        for i, line in enumerate(f, 1):
                            if rx.search(line):
                                hits.append(f"{os.path.relpath(fp, self.root)}:{i}:{line.rstrip()}")
                except (UnicodeDecodeError, OSError):
                    continue
        return "\n".join(hits[:200]) or "(no matches)"

    def _glob(self, pattern, path="."):
        base = Path(self._path(path))
        found = sorted(str(p.relative_to(self.root)) for p in base.glob(pattern) if "__pycache__" not in p.parts)
        return "\n".join(found[:200]) or "(no matches)"


# ---------------------------------------------------------------- tasks

def py_ok(d, code):
    p = subprocess.run([sys.executable, "-I", "-c", code], cwd=d, capture_output=True, text=True, timeout=30)
    return p.returncode == 0, (p.stdout + p.stderr)[-300:]


def read(d, rel):
    try:
        with open(os.path.join(d, rel)) as f:
            return f.read()
    except OSError:
        return None


CALC = "def add_all(xs):\n    total = 0\n    for i in range(len(xs) - 1):\n        total += xs[i]\n    return total\n"
TEST_CALC = ("import unittest\nfrom calc import add_all\n\n\nclass T(unittest.TestCase):\n"
             "    def test_sum(self):\n        self.assertEqual(add_all([1, 2, 3]), 6)\n\n"
             "    def test_empty(self):\n        self.assertEqual(add_all([]), 0)\n")
TEST_CALC_SHA = hashlib.sha256(TEST_CALC.encode()).hexdigest()


def judge_fix_bug(d):
    t = read(d, "test_calc.py")
    if t is None or hashlib.sha256(t.encode()).hexdigest() != TEST_CALC_SHA:
        return False, "test_calc.py was changed"
    return py_ok(d, "import sys,unittest; sys.path.insert(0,'.'); import test_calc; "
                    "r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromModule(test_calc)); "
                    "sys.exit(0 if r.wasSuccessful() and r.testsRun==2 else 1)")


CONFIG = ("[database]\nport = 5432\nhost = \"db.internal\"\n\n[server]\nhost = \"0.0.0.0\"\nport = 8443\n"
          "workers = 4\n\n[metrics]\nport = 9100\n")
CSV = "id,name,score\n" + "".join(f"{i},user{i},{(i * 37) % 101}\n" for i in range(1, 1285))
USERS = "def get_user(uid):\n    return {\"id\": uid}\n"
API = "from users import get_user\n\n\ndef handler(uid):\n    return get_user(uid)\n"
CLI = "from users import get_user\n\n\ndef main():\n    print(get_user(1))\n"
SETTINGS = "".join(f"# setting {i}\n" for i in range(1, 17)) + 'AWS_ACCESS_KEY_ID = "AKIAQ7HX3MZP4YRN2KDW"\nREGION = "eu-west-1"\n'
AWS_DOC = "# AWS\n\nKeys look like AKIAXXXXXXXXXXXXXXXX (example format, not a real key).\n"
AUTH = ("import sqlite3\n\n\ndef login(conn, name, pw):\n    cur = conn.execute(\n"
        "        f\"SELECT name FROM users WHERE name = '{name}' AND pw = '{pw}'\"\n    )\n"
        "    row = cur.fetchone()\n    return row[0] if row else None\n")
SQLI_CHECK = ("import sqlite3, sys; sys.path.insert(0,'.'); import auth\n"
              "c=sqlite3.connect(':memory:'); c.execute('create table users(name text, pw text)')\n"
              "c.execute(\"insert into users values('admin','s3cret')\")\n"
              "assert auth.login(c,'admin','s3cret')=='admin', 'valid login broke'\n"
              "assert auth.login(c,\"admin' --\",'x') is None, 'injection still works'\n"
              "assert auth.login(c,'admin',\"' OR '1'='1\") is None, 'injection still works'\n")

TASKS = [
    {"id": "fix_bug", "agent": SMITH, "files": {"calc.py": CALC, "test_calc.py": TEST_CALC},
     "prompt": "The tests in test_calc.py fail. Fix the bug in calc.py so they pass. Do not change the tests.",
     "judge": judge_fix_bug},
    {"id": "read_port", "agent": SMITH, "files": {"config.toml": CONFIG},
     "prompt": "What port does the server in config.toml listen on? Write only that number to answer.txt.",
     "judge": lambda d: ((read(d, "answer.txt") or "").strip() == "8443", repr(read(d, "answer.txt")))},
    {"id": "count_rows", "agent": SMITH, "files": {"data.csv": CSV},
     "prompt": "How many data rows, not counting the header, are in data.csv? Write only that number to answer.txt.",
     "judge": lambda d: ((read(d, "answer.txt") or "").strip() == "1284", repr(read(d, "answer.txt")))},
    {"id": "rename", "agent": SMITH, "files": {"src/users.py": USERS, "src/api.py": API, "src/cli.py": CLI},
     "prompt": "Rename the function get_user to fetch_user everywhere under src/, including every call and import.",
     "judge": lambda d: (lambda left: (False, f"get_user still in {left}") if left else
                         py_ok(d, "import sys; sys.path.insert(0,'src'); import api, cli, users; "
                                  "assert api.handler(3)=={'id':3}; assert users.fetch_user(1)=={'id':1}"))(
         [n for n in ("users.py", "api.py", "cli.py") if "get_user" in (read(d, "src/" + n) or "get_user")])},
    {"id": "find_secret", "agent": WARDEN,
     "files": {"deploy/settings.py": SETTINGS, "docs/aws.md": AWS_DOC, "app.py": "print('hello')\n"},
     "prompt": "Find any hardcoded cloud credential in this repository. Write findings.md listing the file path "
               "and line number of each real one.",
     "judge": lambda d: (lambda t: ("deploy/settings.py" in t and re.search(r"\b17\b", t) is not None, t[:200]))(
         read(d, "findings.md") or "")},
    {"id": "fix_sqli", "agent": WARDEN, "files": {"auth.py": AUTH},
     "prompt": "auth.py has a SQL injection vulnerability in login(). Fix it with a parameterized query, "
               "keeping valid logins working.",
     "judge": lambda d: py_ok(d, SQLI_CHECK)},
]
TASK_BY_ID = {t["id"]: t for t in TASKS}


def setup_task(task, d):
    os.makedirs(d, exist_ok=True)
    for rel, content in task["files"].items():
        os.makedirs(os.path.dirname(os.path.join(d, rel)) or d, exist_ok=True)
        with open(os.path.join(d, rel), "w") as f:
            f.write(content)
    return os.path.realpath(d)


# ---------------------------------------------------------------- episode loop

def turn_verdict(msg):
    calls = msg.get("tool_calls") or []
    if not calls:
        return "text_leak" if LEAK.search(msg.get("content") or "") else "final"
    for c in calls:
        fn = c.get("function", {})
        if fn.get("name") not in REQUIRED:
            return "unoffered"
        try:
            args = json.loads(fn.get("arguments") or "")
        except (json.JSONDecodeError, TypeError):
            return "bad_args"
        if not isinstance(args, dict) or any(k not in args for k in REQUIRED[fn["name"]]):
            return "bad_args"
    return "call"


RETRYABLE = {429, 500, 502, 503, 504}


def post(base, headers, body, retries=8, backoff=2.0):
    """POST with retries on rate limits and server errors; honours Retry-After. Other errors raise at once."""
    import urllib.error
    data = json.dumps(body).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(base + "/chat/completions", data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in RETRYABLE or attempt == retries:
                raise
            wait = e.headers.get("Retry-After") if e.headers else None
            try:
                delay = float(wait) if wait is not None else backoff * (2 ** attempt)
            except ValueError:
                delay = backoff * (2 ** attempt)
            time.sleep(min(delay, 60))


def run_episode(base, headers, model, task, workdir):
    d = setup_task(task, workdir)
    tools = Tools(d)
    msgs = [{"role": "system", "content": task["agent"]}, {"role": "user", "content": task["prompt"]}]
    turns, usage, t0, err = [], {"prompt": 0, "completion": 0}, time.time(), None
    for _ in range(MAX_TURNS):
        try:
            resp = post(base, headers, {"model": model, "messages": msgs, "tools": TOOLS, "tool_choice": "auto",
                                        "max_tokens": 8192, "stream": False})
        except Exception as e:
            err = repr(e)[:300]
            break
        u = resp.get("usage") or {}
        usage["prompt"] += u.get("prompt_tokens") or 0
        usage["completion"] += u.get("completion_tokens") or 0
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        v = turn_verdict(msg)
        turns.append({"verdict": v, "calls": [c.get("function", {}).get("name") for c in msg.get("tool_calls") or []]})
        if v in ("final", "text_leak"):
            msgs.append({"role": "assistant", "content": msg.get("content") or ""})
            break
        am = {"role": "assistant", "content": msg.get("content") or "", "tool_calls": msg["tool_calls"]}
        msgs.append(am)
        for n, c in enumerate(msg["tool_calls"]):
            fn = c.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "")
            except (json.JSONDecodeError, TypeError):
                args = None
            msgs.append({"role": "tool", "tool_call_id": c.get("id") or f"call_{len(turns)}_{n}",
                         "content": tools.run(fn.get("name"), args)})
    ok, detail = task["judge"](d)
    counts = {k: sum(t["verdict"] == k for t in turns) for k in ("call", "final", "text_leak", "unoffered", "bad_args")}
    return {"task": task["id"], "pass": bool(ok), "judge": str(detail)[:300], "turns": len(turns), "verdicts": counts,
            "seconds": round(time.time() - t0, 1), "usage": usage, "error": err,
            "hit_turn_limit": len(turns) == MAX_TURNS and turns[-1]["verdict"] == "call"}, msgs


def main():
    out, base, model = sys.argv[1], sys.argv[2], sys.argv[3]
    trials = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    headers = {"Content-Type": "application/json"}
    if os.environ.get("HEARTH_API_KEY_FILE"):
        headers["Authorization"] = "Bearer " + open(os.path.expanduser(os.environ["HEARTH_API_KEY_FILE"])).read().strip()
    os.makedirs(out, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(prefix="mt-run-") as tmp:
        for trial in range(1, trials + 1):
            for task in TASKS:
                row, transcript = run_episode(base, headers, model, task, os.path.join(tmp, f"{task['id']}-{trial}"))
                row["trial"] = trial
                rows.append(row)
                with open(os.path.join(out, f"{task['id']}-{trial}.json"), "w") as f:
                    json.dump({"model": model, "row": row, "transcript": transcript}, f, indent=1)
                print(json.dumps(row), flush=True)
    valid = [r for r in rows if r["error"] is None]   # API errors are infrastructure, never a model verdict
    summary = {"model": model, "episodes": len(rows), "valid_episodes": len(valid),
               "passed": sum(r["pass"] for r in valid),
               "per_task": {t["id"]: f"{sum(r['pass'] for r in valid if r['task'] == t['id'])}/"
                                     f"{sum(1 for r in valid if r['task'] == t['id'])}" for t in TASKS},
               "hallucinated_turns": sum(r["verdicts"]["text_leak"] + r["verdicts"]["unoffered"] +
                                         r["verdicts"]["bad_args"] for r in rows),
               "tool_turns": sum(r["verdicts"]["call"] for r in rows),
               "errors": sum(r["error"] is not None for r in rows),
               "tokens": {"prompt": sum(r["usage"]["prompt"] for r in rows),
                          "completion": sum(r["usage"]["completion"] for r in rows)}}
    with open(os.path.join(out, "summary.json"), "w") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=1)
    print("SUMMARY " + json.dumps(summary))


if __name__ == "__main__":
    main()
