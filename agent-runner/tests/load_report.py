"""A load test's report: what participants heard (the load generator's result) and, step
by step, what each part of staging was doing meanwhile, so a failing step says why.

Quality, per step (quality.py): what the bot heard against what the participant said
(word error rate, fragmented and missed sentences) and how long its replies ran.

Inside staging, per step: the bot's own stage timings (its "bot reply" log lines:
speech-to-text, LLM first token, TTS first audio) and CloudWatch maxima (CPU and memory
of each service, CPU of each machine group, database CPU and connections).

Run by the Load test v2 workflow after the load generator stops, with the acceptance
role; prints Markdown for the run's summary and writes the enriched result back.

    uv run --no-project --with boto3 python agent-runner/tests/load_report.py result.json
"""
import json
import os
import sys
from datetime import datetime, timezone

import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from load_plan import parse_reply, stage_summary, step_of  # noqa: E402
from quality import score_rooms  # noqa: E402

REGION, CLUSTER, DB = "us-east-1", "meetlab-v2-staging", "meetlab-v2-staging"
SERVICES = ["meet", "agent-runner", "livekit"]          # ECS services with their own CPU/memory
GROUPS = ["bots", "ecs", "livekit", "llm", "tts", "stt-nim"]  # machine groups (CPU)


def _queries():
    q = []
    for s in SERVICES:
        for m in ("CPUUtilization", "MemoryUtilization"):
            q.append((f"{s}_{m[:3].lower()}", "AWS/ECS", m, {"ClusterName": CLUSTER, "ServiceName": f"{CLUSTER}-{s}"}))
    for g in GROUPS:
        q.append((f"{g}_hosts_cpu", "AWS/EC2", "CPUUtilization", {"AutoScalingGroupName": f"{CLUSTER}-{g}"}))
    q.append(("db_cpu", "AWS/RDS", "CPUUtilization", {"DBInstanceIdentifier": DB}))
    q.append(("db_connections", "AWS/RDS", "DatabaseConnections", {"DBInstanceIdentifier": DB}))
    return [{"Id": i.replace("-", "_"), "Label": i, "ReturnData": True, "MetricStat": {
        "Metric": {"Namespace": ns, "MetricName": m, "Dimensions": [{"Name": k, "Value": v} for k, v in d.items()]},
        "Period": 60, "Stat": "Maximum"}} for i, ns, m, d in q]


def _metrics(bounds) -> list[dict]:
    """Per step, each metric's maximum."""
    cw = boto3.client("cloudwatch", region_name=REGION)
    out = [{} for _ in bounds]
    start, end = (datetime.fromtimestamp(t, timezone.utc) for t in (bounds[0][0], bounds[-1][1] + 60))
    pages = cw.get_paginator("get_metric_data").paginate(MetricDataQueries=_queries(), StartTime=start, EndTime=end)
    for page in pages:
        for r in page["MetricDataResults"]:
            for ts, v in zip(r["Timestamps"], r["Values"]):
                k = step_of(ts.timestamp(), bounds)
                if k is not None:
                    out[k][r["Label"]] = max(out[k].get(r["Label"], 0), round(v, 1))
    return out


def _stages(bounds) -> list[dict]:
    logs = boto3.client("logs", region_name=REGION)
    per_step = [[] for _ in bounds]
    pages = logs.get_paginator("filter_log_events").paginate(
        logGroupName=f"/meetlab-v2/staging/bot", filterPattern='"bot reply"',
        startTime=int(bounds[0][0] * 1000), endTime=int(bounds[-1][1] * 1000))
    for page in pages:
        for e in page["events"]:
            k, t = step_of(e["timestamp"] / 1000, bounds), parse_reply(e["message"])
            if k is not None and t:
                per_step[k].append(t)
    return [stage_summary(t) for t in per_step]


def _ms(v):
    return "-" if v is None else f"{v:.0f}"


def markdown(r: dict) -> str:
    lines = [f"### Load test {r['run']}: {r['shape']} on `{r['profile']}` {json.dumps(r['config'])}", "",
             "**What participants heard**", "",
             "| step | rooms | turns | reply | p50 ms | p95 ms | p99 ms | join p95 s | verdict |", "|---|---|---|---|---|---|---|---|---|"]
    for k, m in enumerate(r["steps"]):
        reply = "-" if m["reply_rate"] is None else f"{m['reply_rate']:.0%}"
        verdict = "PASS" if m["pass"] else "FAIL: " + "; ".join(m["why"])
        lines.append(f"| {k} | {m['rooms']} | {m['turns']} | {reply} | {_ms(m['p50_ms'])} | {_ms(m['p95_ms'])} | "
                     f"{_ms(m['p99_ms'])} | {_ms(m['join_p95_s'])} | {verdict} |")
    if any("server" in m for m in r["steps"]):
        lines += ["", "**Inside staging** (p95 of the bot's own stage timings; maximum CPU/memory %)", "",
                  "| step | rooms | STT ms | LLM first token ms | TTS first audio ms | meet CPU/mem | runner CPU/mem | "
                  "LiveKit CPU | bot machines CPU | LLM / TTS / STT machine CPU | DB CPU / connections |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for k, m in enumerate(r["steps"]):
            s, x = m.get("server", {}).get("stages", {}), m.get("server", {}).get("max", {})
            p95 = lambda st: _ms(s.get(st, {}).get("p95"))  # noqa: E731
            g = lambda key: "-" if key not in x else f"{x[key]:.0f}"  # noqa: E731
            lines.append(f"| {k} | {m['rooms']} | {p95('stt_ms')} | {p95('llm_ttft_ms')} | {p95('tts_ttfb_ms')} | "
                         f"{g('meet_cpu')}/{g('meet_mem')} | {g('agent-runner_cpu')}/{g('agent-runner_mem')} | "
                         f"{g('livekit_hosts_cpu')} | {g('bots_hosts_cpu')} | "
                         f"{g('llm_hosts_cpu')} / {g('tts_hosts_cpu')} / {g('stt-nim_hosts_cpu')} | {g('db_cpu')} / {g('db_connections')} |")
    if r.get("rooms"):
        lines += ["", "**Quality** (what the bot heard against what was said; how long it talked)", "",
                  "| step | rooms | sentences | word error rate | fragmented | missed | replies | words p50 / p95 / max | over 40 words |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for k, (a, b) in enumerate(r["bounds"][:len(r["steps"])]):
            q = score_rooms(r["rooms"], a, b)
            h, rep = q["hearing"], q["replies"]
            if not h["sentences"]:
                continue
            r["steps"][k]["quality"] = q
            lines.append(f"| {k} | {r['steps'][k]['rooms']} | {h['sentences']} | {h['wer']:.1%} | {h['fragmented']} | {h['missed']} | "
                         f"{rep['replies']} | {rep['p50_words']} / {rep['p95_words']} / {rep['max_words']} | {rep['over_40_words']} |")
    lines += ["", f"**Capacity** (largest passing step): {r.get('capacity_rooms')} rooms. "
              f"Sessions left running: {len(r.get('sessions_left_running', []))}. "
              f"Harness valid: {r.get('harness_valid')}. **Verdict: {'PASS' if r.get('pass') else 'FAIL'}**"]
    return "\n".join(lines)


def main(path: str):
    r = json.loads(open(path).read())
    bounds = [tuple(b) for b in r.get("bounds", [])][:len(r["steps"])]
    if bounds:
        for m, stages, mx in zip(r["steps"], _stages(bounds), _metrics(bounds)):
            m["server"] = {"stages": stages, "max": mx}
    text = markdown(r)  # adds each step's quality scores to r
    open(path, "w").write(json.dumps(r, indent=2))
    print(text)


if __name__ == "__main__":
    main(sys.argv[1])
