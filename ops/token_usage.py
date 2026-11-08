"""Token use by model and by session from the Claude Code transcripts.

Usage: python3 -I ops/token_usage.py [SINCE]   (SINCE is an ISO date, default 2026-11-01)

It reads ~/.claude/projects/**/*.jsonl, counts each model request once (message id and request id),
and prints totals by model and one row for each session that used Fable. Session names come from
~/.claude/jobs/<id>/state.json (undocumented). Written for the token review of 2026-11-08.
"""
import json, glob, os, sys, collections
since = sys.argv[1] if len(sys.argv) > 1 else "2026-11-01"
root = os.path.expanduser("~/.claude/projects")
names = {}
for st in glob.glob(os.path.expanduser("~/.claude/jobs/*/state.json")):
    try:
        d = json.load(open(st)); sid = d.get("sessionId") or ""
        if sid: names[sid[:8]] = d.get("name") or ""
    except Exception: pass
agg = collections.defaultdict(lambda: collections.Counter())
first = {}; last = {}
seen = set()
for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
    proj = os.path.relpath(f, root).split(os.sep)[0]
    rel = os.path.relpath(f, root)
    sub = "subagents" in rel or "/agent-" in rel
    for line in open(f, errors="replace"):
        try: e = json.loads(line)
        except Exception: continue
        if e.get("type") != "assistant": continue
        ts = e.get("timestamp", "")
        if ts < since: continue
        m = e.get("message") or {}
        u = m.get("usage") or {}
        if not u: continue
        key = (m.get("id"), e.get("requestId"))
        if key in seen: continue
        seen.add(key)
        sid = (e.get("sessionId") or os.path.basename(f))[:8]
        model = m.get("model") or "?"
        k = (proj.replace("-home-june6423-", "~"), sid, names.get(sid, ""), "sub" if (sub or e.get("isSidechain")) else "main", model)
        c = agg[k]
        c["in"] += u.get("input_tokens", 0); c["cw"] += u.get("cache_creation_input_tokens", 0)
        c["cr"] += u.get("cache_read_input_tokens", 0); c["out"] += u.get("output_tokens", 0); c["calls"] += 1
        first[k] = min(first.get(k, ts), ts); last[k] = max(last.get(k, ts), ts)
bymodel = collections.Counter(); bymodel_out = collections.Counter(); bymodel_cw = collections.Counter(); bymodel_in = collections.Counter()
for k, c in agg.items():
    bymodel[k[4]] += c["cr"]; bymodel_out[k[4]] += c["out"]; bymodel_cw[k[4]] += c["cw"]; bymodel_in[k[4]] += c["in"]
print(f"since {since}")
print("== by model (M tokens): input | cache write | cache read | output | calls")
calls = collections.Counter()
for k, c in agg.items(): calls[k[4]] += c["calls"]
for mdl in sorted(bymodel, key=lambda x: -(bymodel[x] + bymodel_cw[x])):
    print(f"{mdl:22s} {bymodel_in[mdl]/1e6:8.2f} {bymodel_cw[mdl]/1e6:8.2f} {bymodel[mdl]/1e6:9.2f} {bymodel_out[mdl]/1e6:7.2f} {calls[mdl]:6d}")
print("== fable sessions (M tokens): project | session | name | main/sub | in | cw | cr | out | calls | first..last")
rows = [(k, c) for k, c in agg.items() if "fable" in k[4]]
rows.sort(key=lambda kc: -(kc[1]["cr"] + kc[1]["cw"]))
for k, c in rows:
    print(f"{k[0]:28s} {k[1]} {k[2]:36.36s} {k[3]:4s} {c['in']/1e6:6.2f} {c['cw']/1e6:6.2f} {c['cr']/1e6:8.2f} {c['out']/1e6:5.2f} {c['calls']:5d} {first[k][5:16]}..{last[k][5:16]}")
