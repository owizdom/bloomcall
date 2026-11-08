"""Render the Experiment 1 chart from the measured result files. Standard library only.

usage: python3 -I chart/make_chart.py RESULTS_DIR OUT.svg
Reads RESULTS_DIR/multi-turn/*/summary.rescored.json (multi-turn scores) and
RESULTS_DIR/single-turn/*/summary.json (single-turn passes). Every number drawn comes from those files.

Design follows the dataviz method: one series, so one hue and no legend; magnitude against a known
whole, so a meter (light track of the same ramp, solid fill); bars <= 24px with a 4px rounded data
end and a square start; value at the tip in text ink, never in the series color; no gridlines;
the headline states the finding.
"""
import glob, json, os, sys
from xml.sax.saxutils import escape

res, out = sys.argv[1], sys.argv[2]

SURFACE, INK, INK_2, FILL, TRACK = "#ffffff", "#0b0b0b", "#52514e", "#2a78d6", "#cde2fb"
FONT = "'Helvetica Neue', Helvetica, Arial, sans-serif"
NAMES = {"EigenLabs/Qwen3.8-27B-4bit-mtp": "Qwen3.8 27B", "gemma-4-26b": "Gemma 4 26B",
         "glm-5.3-flash": "GLM 5.3 Flash", "qwen3.5-35b-a3b": "Qwen3.5 35B-A3B",
         "qwen3.6-35b-a3b-vl-mtp-mxfp8": "Qwen3.6 35B-A3B", "gpt-oss-20b": "gpt-oss 20B"}


def load(kind, fname):
    return {json.load(open(f))["summary"]["model"]: json.load(open(f))["summary"]
            for f in glob.glob(os.path.join(res, kind, "*", fname))}


multi = load("multi-turn", "summary.rescored.json")
single = load("single-turn", "summary.json")
rows = sorted(multi.values(), key=lambda s: (-s["passed"] / s["valid_episodes"], NAMES.get(s["model"], s["model"])))
solved, valid = sum(s["passed"] for s in rows), sum(s["valid_episodes"] for s in rows)
st_pass, st_total = sum(s["passes"] for s in single.values()), sum(s["trials"] for s in single.values())

W, H = 1200, 586
left, label_x, plot_x, plot_w = 64, 300, 320, 700
value_x = plot_x + plot_w + 20
top, row_h, bar_h = 196, 62, 22
r = 4  # rounded data end


def bar_path(x, y, w, h):
    """Square at the baseline (left), 4px rounded data end (right)."""
    if w <= 0:
        return ""
    rr = min(r, w)
    return (f"M{x},{y} H{x + w - rr} Q{x + w},{y} {x + w},{y + rr} V{y + h - rr} "
            f"Q{x + w},{y + h} {x + w - rr},{y + h} H{x} Z")


svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">',
       f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>',
       f'<text x="{left}" y="92" font-size="40" font-weight="700" fill="{INK}" letter-spacing="-0.5">'
       f'Darkbloom models solved {solved} of {valid} agent tasks</text>',
       f'<text x="{left}" y="136" font-size="22" fill="{INK_2}">'
       f'Six models, six multi-step coding tasks, two runs each, judged on disk. '
       f'{st_pass} of {st_total} single tool calls parsed.</text>']
for n, s in enumerate(rows):
    y = top + n * row_h
    frac = s["passed"] / s["valid_episodes"]
    name = NAMES.get(s["model"], s["model"])
    svg.append(f'<text x="{label_x}" y="{y + bar_h - 4}" font-size="22" fill="{INK}" text-anchor="end">{escape(name)}</text>')
    svg.append(f'<path d="{bar_path(plot_x, y, plot_w, bar_h)}" fill="{TRACK}"/>')
    svg.append(f'<path d="{bar_path(plot_x, y, plot_w * frac, bar_h)}" fill="{FILL}"/>')
    svg.append(f'<text x="{value_x}" y="{y + bar_h - 4}" font-size="22" font-weight="600" fill="{INK}">'
               f'{s["passed"]}<tspan fill="{INK_2}" font-weight="400">/{s["valid_episodes"]}</tspan></text>')
svg.append("</svg>")
with open(out, "w") as f:
    f.write("\n".join(svg))
print(f"wrote {out}: {len(rows)} models, {solved}/{valid} solved, {st_pass}/{st_total} single-turn")
