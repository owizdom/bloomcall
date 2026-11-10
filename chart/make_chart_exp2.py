"""Render the Experiment 2 chart from the billed report. Standard library only.

usage: python3 -I chart/make_chart_exp2.py results/experiment-2/report.billed.json OUT.svg
Every number drawn comes from that file: mean cost per bug (C counted once, Darkbloom attempts
counted with any Claude escalation) and bugs solved out of attempts.

Same method as the Experiment 1 chart: one hue for the Darkbloom-first policies, a neutral bar for
the Claude-only baseline, bars <= 24px with a 4px rounded data end, values in text ink, no gridlines,
a headline that states the finding.
"""
import json, sys
from xml.sax.saxutils import escape

src, out = sys.argv[1], sys.argv[2]
rep = json.load(open(src))

SURFACE, INK, INK_2, FILL, BASE = "#ffffff", "#0b0b0b", "#52514e", "#2a78d6", "#a3a29e"
FONT = "'Helvetica Neue', Helvetica, Arial, sans-serif"
ROWS = [("C", "Claude only", BASE),
        ("ESTAR", "Darkbloom, perfect handoff", FILL),
        ("E2", "Darkbloom, hand off on failure or broken tests", FILL),
        ("E1", "Darkbloom, hand off when it gives up", FILL),
        ("D", "Darkbloom only", FILL)]

base = rep["C"]["mean_cost"]
lo = round((1 - rep["E2"]["cost_ratio"]) * 100)
hi = round((1 - rep["E1"]["cost_ratio"]) * 100)

W, H = 1200, 560
left, label_x, plot_x, plot_w = 64, 530, 550, 420
value_x = plot_x + plot_w + 20
solved_x = W - 64
top, row_h, bar_h, r = 220, 62, 22, 4


def bar_path(x, y, w, h):
    if w <= 0:
        return ""
    rr = min(r, w)
    return (f"M{x},{y} H{x + w - rr} Q{x + w},{y} {x + w},{y + rr} V{y + h - rr} "
            f"Q{x + w},{y + h} {x + w - rr},{y + h} H{x} Z")


svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">',
       f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>',
       f'<text x="{left}" y="92" font-size="40" font-weight="700" fill="{INK}" letter-spacing="-0.5">'
       f'Starting on Darkbloom cut the cost of a bug fix {lo} to {hi}%</text>',
       f'<text x="{left}" y="136" font-size="22" fill="{INK_2}">'
       f'20 real Django bugs, judged by hidden tests. Cost per bug in cents, failed attempts included.</text>',
       f'<text x="{solved_x}" y="{top - 24}" font-size="18" fill="{INK_2}" text-anchor="end">solved</text>']
for n, (key, name, color) in enumerate(ROWS):
    s = rep[key]
    y = top + n * row_h
    w = plot_w * s["mean_cost"] / base
    svg.append(f'<text x="{label_x}" y="{y + bar_h - 4}" font-size="22" fill="{INK}" text-anchor="end">{escape(name)}</text>')
    svg.append(f'<path d="{bar_path(plot_x, y, w, bar_h)}" fill="{color}"/>')
    svg.append(f'<text x="{plot_x + w + 14}" y="{y + bar_h - 4}" font-size="22" font-weight="600" fill="{INK}">'
               f'{s["mean_cost"] * 100:.1f}¢</text>')
    svg.append(f'<text x="{solved_x}" y="{y + bar_h - 4}" font-size="22" fill="{INK}" text-anchor="end">'
               f'{s["resolved"]}<tspan fill="{INK_2}">/{s["n"]}</tspan></text>')
svg.append("</svg>")
with open(out, "w") as f:
    f.write("\n".join(svg))
print(f"wrote {out}: saving {lo}-{hi}% (E2-E1), base {base * 100:.2f} cents")
