"""Re-derive per-turn verdicts from saved multi-turn transcripts with the current mt.turn_verdict.

Writes RUN_DIR/<model>/summary.rescored.json next to the original summary.json, which is left untouched.
usage: python3 -I probe/w0/rescore.py RUN_DIR
"""
import glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mt  # noqa: E402

run = sys.argv[1]
for mdir in sorted(glob.glob(os.path.join(run, "*/"))):
    rows = []
    for f in sorted(glob.glob(os.path.join(mdir, "*-?.json"))):
        d = json.load(open(f))
        r = d["row"]
        verdicts = {k: 0 for k in ("call", "final", "text_leak", "unoffered", "bad_args")}
        final_text = ""
        for m in d["transcript"]:
            if m["role"] != "assistant":
                continue
            v = mt.turn_verdict(m)
            verdicts[v] += 1
            if not m.get("tool_calls"):
                final_text = m.get("content") or ""
        claimed_done = (not r["pass"] and r["error"] is None and verdicts["text_leak"] == 0
                        and final_text.strip() != "")
        rows.append({**r, "verdicts": verdicts, "false_completion_claim": claimed_done,
                     "silent_stop": not r["pass"] and r["error"] is None and final_text.strip() == ""
                     and verdicts["text_leak"] == 0})
    valid = [r for r in rows if r["error"] is None]
    s = {"model": json.load(open(os.path.join(mdir, "summary.json")))["summary"]["model"],
         "episodes": len(rows), "valid_episodes": len(valid), "passed": sum(r["pass"] for r in valid),
         "tool_turns": sum(r["verdicts"]["call"] for r in rows),
         "text_leaks": sum(r["verdicts"]["text_leak"] for r in rows),
         "unoffered_or_bad_args": sum(r["verdicts"]["unoffered"] + r["verdicts"]["bad_args"] for r in rows),
         "false_completion_claims": sum(r["false_completion_claim"] for r in valid),
         "silent_stops": sum(r["silent_stop"] for r in valid),
         "errors": sum(r["error"] is not None for r in rows)}
    json.dump({"summary": s, "rows": rows}, open(os.path.join(mdir, "summary.rescored.json"), "w"), indent=1)
    print(json.dumps(s))
