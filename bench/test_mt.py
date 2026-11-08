"""Tests for probe/w0/mt.py, written before the implementation.

Run: python3 -I -m unittest -v probe/w0/test_mt.py   (from the repo root, with probe/w0 on sys.path)
"""
import os, shutil, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mt  # noqa: E402


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="mt-test-"))
        self.t = mt.Tools(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_path_escape_is_rejected(self):
        self.assertTrue(self.t.run("read", {"path": "../../etc/hosts"}).startswith("ERROR"))
        self.assertTrue(self.t.run("write", {"path": "/tmp/mt-escape.txt", "content": "x"}).startswith("ERROR"))
        self.assertFalse(os.path.exists("/tmp/mt-escape.txt"))

    def test_write_creates_parents_and_read_numbers_lines(self):
        self.t.run("write", {"path": "a/b/c.txt", "content": "one\ntwo\n"})
        out = self.t.run("read", {"path": "a/b/c.txt"})
        self.assertIn("1\tone", out)
        self.assertIn("2\ttwo", out)

    def test_edit_requires_a_unique_match(self):
        self.t.run("write", {"path": "f.txt", "content": "x = 1\nx = 1\n"})
        self.assertTrue(self.t.run("edit", {"path": "f.txt", "old_string": "x = 1", "new_string": "x = 2"}).startswith("ERROR"))
        self.t.run("write", {"path": "g.txt", "content": "x = 1\ny = 1\n"})
        self.assertFalse(self.t.run("edit", {"path": "g.txt", "old_string": "x = 1", "new_string": "x = 2"}).startswith("ERROR"))
        with open(os.path.join(self.dir, "g.txt")) as f:
            self.assertEqual(f.read(), "x = 2\ny = 1\n")

    def test_grep_and_glob(self):
        self.t.run("write", {"path": "src/a.py", "content": "# TODO one\nok\n"})
        self.t.run("write", {"path": "src/b.ts", "content": "// TODO two\n"})
        g = self.t.run("grep", {"pattern": "TODO", "path": "src", "include": "*.py"})
        self.assertIn("src/a.py:1:", g)
        self.assertNotIn("b.ts", g)
        self.assertIn("src/b.ts", self.t.run("glob", {"pattern": "src/**/*.ts"}))

    def test_unknown_tool_and_bad_args_return_errors(self):
        self.assertTrue(self.t.run("browser", {}).startswith("ERROR"))
        self.assertTrue(self.t.run("read", {}).startswith("ERROR"))

    def test_shell_runs_in_the_task_dir(self):
        self.assertIn(self.dir, self.t.run("shell", {"command": "pwd"}))

    def test_shell_cannot_write_outside_the_task_dir(self):
        target = os.path.expanduser("~/mt-sandbox-probe.txt")
        try:
            self.t.run("shell", {"command": "echo pwned > " + target})
            self.assertFalse(os.path.exists(target), "sandbox let a shell command write to $HOME")
        finally:
            if os.path.exists(target):
                os.remove(target)

    def test_shell_can_write_inside_the_task_dir(self):
        self.t.run("shell", {"command": "echo ok > inside.txt"})
        self.assertTrue(os.path.exists(os.path.join(self.dir, "inside.txt")))

    def test_shell_has_no_network(self):
        out = self.t.run("shell", {"command": "curl -sS -m 5 -o /dev/null -w '%{http_code}' https://example.com; echo; echo rc=$?"})
        self.assertNotIn("200", out.split("rc=")[0], "sandbox allowed a network request")


class JudgeTest(unittest.TestCase):
    """Each task's judge must reject the untouched setup and accept a known-good solution."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="mt-judge-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def check(self, task_id, solve):
        task = mt.TASK_BY_ID[task_id]
        bad = mt.setup_task(task, os.path.join(self.root, "bad"))
        ok, detail = task["judge"](bad)
        self.assertFalse(ok, f"{task_id}: untouched setup passed the judge ({detail})")
        good = mt.setup_task(task, os.path.join(self.root, "good"))
        solve(good)
        ok, detail = task["judge"](good)
        self.assertTrue(ok, f"{task_id}: known-good solution failed the judge ({detail})")

    @staticmethod
    def sub(path, old, new):
        with open(path) as f:
            s = f.read()
        with open(path, "w") as f:
            f.write(s.replace(old, new))

    def test_fix_bug(self):
        self.check("fix_bug", lambda d: self.sub(os.path.join(d, "calc.py"), "range(len(xs) - 1)", "range(len(xs))"))

    def test_fix_bug_rejects_edited_tests(self):
        task = mt.TASK_BY_ID["fix_bug"]
        d = mt.setup_task(task, os.path.join(self.root, "cheat"))
        with open(os.path.join(d, "test_calc.py"), "w") as f:
            f.write("import unittest\n")
        self.assertFalse(task["judge"](d)[0])

    def test_read_port(self):
        self.check("read_port", lambda d: open(os.path.join(d, "answer.txt"), "w").write("8443\n"))

    def test_count_rows(self):
        self.check("count_rows", lambda d: open(os.path.join(d, "answer.txt"), "w").write("1284\n"))

    def test_rename(self):
        def solve(d):
            for name in ("users.py", "api.py", "cli.py"):
                self.sub(os.path.join(d, "src", name), "get_user", "fetch_user")
        self.check("rename", solve)

    def test_find_secret(self):
        self.check("find_secret", lambda d: open(os.path.join(d, "findings.md"), "w").write(
            "# Findings\n\n- AWS key at deploy/settings.py line 17\n"))

    def test_fix_sqli(self):
        self.check("fix_sqli", lambda d: self.sub(
            os.path.join(d, "auth.py"),
            "f\"SELECT name FROM users WHERE name = '{name}' AND pw = '{pw}'\"",
            "\"SELECT name FROM users WHERE name = ? AND pw = ?\", (name, pw)"))


class TurnVerdictTest(unittest.TestCase):
    def test_classifies_turns(self):
        call = lambda name, args: {"tool_calls": [{"id": "c1", "function": {"name": name, "arguments": args}}]}
        self.assertEqual(mt.turn_verdict(call("read", '{"path": "a"}')), "call")
        self.assertEqual(mt.turn_verdict(call("browser", "{}")), "unoffered")
        self.assertEqual(mt.turn_verdict(call("read", "{not json")), "bad_args")
        self.assertEqual(mt.turn_verdict(call("read", "{}")), "bad_args")
        self.assertEqual(mt.turn_verdict({"content": '<tool_call>{"name": "read"}</tool_call>'}), "text_leak")
        self.assertEqual(mt.turn_verdict({"content": "Done. The port is 8443."}), "final")
        # gpt-oss Harmony-format call leaked into content (seen on Darkbloom, 2026-11-08)
        harmony = 'to=functions.read?commentary<|constrain|>json<|message|>{"limit":200,"offset":0,"path":"src\\/cli.py"}'
        self.assertEqual(mt.turn_verdict({"content": harmony}), "text_leak")


class RetryTest(unittest.TestCase):
    """post() must retry rate limits and server errors, and give up on other client errors."""

    def serve(self, statuses):
        import http.server, threading
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                code = statuses[min(len(seen), len(statuses) - 1)]
                seen.append(code)
                body = b'{"choices": [{"message": {"content": "ok"}}]}' if code == 200 else b"{}"
                self.send_response(code)
                if code == 429:
                    self.send_header("Retry-After", "0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}", seen

    def test_retries_429_and_503_then_succeeds(self):
        base, seen = self.serve([429, 503, 200])
        resp = mt.post(base, {"Content-Type": "application/json"}, {}, backoff=0)
        self.assertEqual(resp["choices"][0]["message"]["content"], "ok")
        self.assertEqual(seen, [429, 503, 200])

    def test_does_not_retry_400(self):
        base, seen = self.serve([400, 200])
        with self.assertRaises(Exception):
            mt.post(base, {"Content-Type": "application/json"}, {}, backoff=0)
        self.assertEqual(seen, [400])


if __name__ == "__main__":
    unittest.main()
