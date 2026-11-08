# bloomcall

Experiments on one question: can the work coding agents do run on Darkbloom, the network of idle Macs?

| # | Experiment | Status |
|---|---|---|
| 1 | Can Darkbloom's models run an agent loop? | Done, below |
| 2 | How much of a real Claude Code workload could Darkbloom serve? | Next |

## Experiment 1: Darkbloom's models can run an agent loop

![Darkbloom models solved 67 of 71 agent tasks](chart/bloomcall.png)

Across six Darkbloom models, 60 of 60 tool calls parsed cleanly and 67 of 71 multi-step agent episodes were solved, for about seven cents in total.

This started as an attempt to run a 27B coding model on a 16 GB Mac. At 2-bit it ran at 0.4 tokens per second, about 200 seconds per tool call. The same Qwen3.8-27B base model on Darkbloom answered the same two requests in 2.1 and 2.3 seconds, so the question became whether Darkbloom's models can be the brain of an agent.

| Model | Single-turn tool calls | Multi-turn episodes solved |
|---|---|---|
| Qwen3.8-27B (4-bit, MTP) | 10/10 | 12/12 |
| gemma-4-26b | 10/10 | 12/12 |
| glm-5.3-flash | 10/10 | 12/12 |
| qwen3.5-35b-a3b | 10/10 | 11/12 |
| qwen3.6-35b-a3b | 10/10 | 11/12 |
| gpt-oss-20b | 10/10 | 9/11 |

What was tested:

- **Single-turn.** Ten requests that each need one of six agent tools (read, write, edit, shell, grep, glob). A pass is a structured tool call naming an offered tool with its required arguments.
- **Multi-turn.** Six small coding tasks, each run twice: fix a bug without touching the tests, read a value from a config, count rows in a CSV, rename a function across three files, find a planted AWS key next to a decoy, and fix a SQL injection. Every tool call runs for real and its result goes back to the model. A judge checks the files on disk afterwards. File tools are confined to the task folder. Shell runs under macOS sandbox-exec with network access denied and writes limited to the system temporary directory that holds the task folder.

Across 308 tool-using turns, no model called a tool that did not exist or left out a required argument. Single-turn requests took 1.4 to 5.8 seconds end to end.

### Two issues for Darkbloom

1. **gpt-oss-20b returns tool calls as raw Harmony text.** On the rename task, gpt-oss-20b's next tool call came back in `content` as `to=functions.read?commentary<|constrain|>json<|message|>{...}`, with `tool_calls` empty, so an agent has nothing to run. Replaying the same conversation reproduced it 7 times out of 7. Reproduce with `python3 -I repro/replay.py harmony_leak`.
2. **An intermittent HTTP 422 on gpt-oss-20b.** One conversation returned `422 {"code":"model_capability","message":"inference generation failed"}` on the original run and on 3 of 3 replays shortly after, then succeeded on 2 of 2 replays later. Replay with `python3 -I repro/replay.py http_422`.

The other two failed episodes were model behaviour, not platform issues: qwen3.5-35b-a3b renamed the function in two of three files and reported it done, and qwen3.6-35b-a3b found the real key, then stopped with an empty reply without writing its findings.

### Limits

The tasks are small and each ran twice, so this shows the agent loop works on Darkbloom, not which model is best. Timings include the network path from one location.

### Next: Experiment 2

Small tasks are not real work. Experiment 2 shadows a real Claude Code workload: every request still goes to Anthropic, a copy goes to a Darkbloom model, and the report shows, per tool, how often Darkbloom would have made the same move and what it would have cost.

## Reproduce

Python 3 and a Darkbloom API key in a file named by `HEARTH_API_KEY_FILE`.

```
python3 -I bench/test_mt.py
HEARTH_API_KEY_FILE=~/.config/hearth/darkbloom.key python3 -I bench/at0.py results/single-turn/MODEL https://api.darkbloom.dev/v1 0 MODEL
HEARTH_API_KEY_FILE=~/.config/hearth/darkbloom.key python3 -I bench/mt.py results/multi-turn/MODEL https://api.darkbloom.dev/v1 MODEL 2
python3 -I bench/rescore.py results/multi-turn
HEARTH_API_KEY_FILE=~/.config/hearth/darkbloom.key python3 -I repro/replay.py harmony_leak
```

Every request, response and transcript is in `results/`. Multi-turn scores come from `summary.rescored.json`, which reapplies the final tool-call leak detector to the saved transcripts.

## Links

- Darkbloom: https://darkbloom.dev
- Darkbloom on GitHub: https://github.com/Layr-Labs/d-inference
