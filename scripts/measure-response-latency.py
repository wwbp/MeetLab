"""How long production actually makes people wait, measured from its own logs.

The number that matters to a participant is the gap between finishing their
sentence and hearing the bot start. That is the metric the pilot postmortem
reports per day (30 Jul p50 2864ms, 5 Aug p50 1526ms), and this reproduces it
against live production so new runs are comparable with those.

It is derived rather than instrumented: pipecat logs "User stopped speaking" when
a participant's VAD closes, and "Bot started speaking" when the output transport
begins playing. Pairing each bot start with the most recent user stop gives the
felt delay without adding a metric to the hot path.

Also counts interruptions, because a run can look fast while the bot is being cut
off constantly — that was exactly the 2026-08-19 morning failure, where every
individual response was quick and the meeting still felt broken. A latency figure
with no yield count next to it is misleading.

Usage:
    scripts/measure-response-latency.py --since 30
    scripts/measure-response-latency.py --start 2026-08-19T13:10 --end 2026-08-19T14:30
    scripts/measure-response-latency.py --since 20 --label "soak 3 rooms"

Needs AWS credentials with CloudWatch Logs read on the agent-runner log group.
"""
import argparse
import datetime as dt
import json
import statistics as st
import subprocess
import sys

LOG_GROUP = (
    "/aws/elasticbeanstalk/agent-runner/var/log/eb-docker/"
    "containers/eb-current-app/stdouterr.log"
)
REGION = "us-east-1"

# The three lines we pair on, and the yield lines that explain a bad run.
MARKERS = {
    "User stopped speaking": "ustop",
    "Bot started speaking": "botstart",
    "Bot stopped speaking": "botstop",
    "yielding": "yield",
}


def fetch(start_ms: int, end_ms: int) -> list[tuple[int, str]]:
    """Pull the marker lines from CloudWatch, oldest first, following pagination.

    filter-log-events caps a single response at 1MB / 10k events. A busy window —
    eight concurrent rooms, say — blows past that, and without following nextToken
    you silently get a fraction of the window. That reads as a collapse in turn
    count exactly when load is highest, i.e. it fabricates a ceiling precisely
    where you are looking for one. Hence the loop.
    """
    pattern = " ".join(f'?"{m}"' for m in MARKERS)
    rows: list[tuple[int, str]] = []
    token = None
    pages = 0
    while True:
        cmd = [
            "aws", "logs", "filter-log-events",
            "--region", REGION,
            "--log-group-name", LOG_GROUP,
            "--start-time", str(start_ms),
            "--end-time", str(end_ms),
            "--filter-pattern", pattern,
            "--output", "json",
        ]
        if token:
            cmd += ["--next-token", token]
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode != 0:
            sys.exit(f"CloudWatch query failed:\n{out.stderr.strip()}")
        page = json.loads(out.stdout or "{}")
        for ev in page.get("events", []):
            msg = ev.get("message", "")
            for marker, kind in MARKERS.items():
                if marker in msg:
                    rows.append((ev["timestamp"], kind))
                    break
        pages += 1
        token = page.get("nextToken")
        if not token or pages >= 60:
            break
    rows.sort()
    return rows


def analyse(rows):
    """Pair each bot start with the most recent user stop, and with its real end.

    Bot speech needs care. Pipecat emits more "Bot stopped speaking" lines than
    starts — one lands in the same millisecond as the start (CloudWatch buckets
    co-ingested lines), and the genuine end arrives a couple of seconds later.
    Taking the first stop reports every burst as 0ms and claims the bot is being
    cut off constantly, which is exactly the failure this metric exists to detect,
    so the naive pairing manufactures the alarm it is meant to raise.

    Each start therefore takes the LAST stop before the following start.
    """
    waits, bursts, yields = [], [], 0
    last_stop = None
    start_at = None
    end_at = None

    def close():
        if start_at is not None and end_at is not None and end_at > start_at:
            bursts.append(end_at - start_at)

    for ts, kind in rows:
        if kind == "ustop":
            last_stop = ts
        elif kind == "botstart":
            close()
            start_at, end_at = ts, None
            if last_stop is not None:
                waits.append(ts - last_stop)
                last_stop = None
        elif kind == "botstop":
            if start_at is not None:
                end_at = ts
        elif kind == "yield":
            yields += 1
    close()
    return waits, bursts, yields


def pct(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * q))]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", type=int, help="minutes back from now")
    ap.add_argument("--start", help="UTC start, e.g. 2026-08-19T13:10")
    ap.add_argument("--end", help="UTC end, e.g. 2026-08-19T14:30")
    ap.add_argument("--label", default="", help="name for this run in the output")
    a = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    if a.since:
        start, end = now - dt.timedelta(minutes=a.since), now
    elif a.start and a.end:
        p = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)
        start, end = p(a.start), p(a.end)
    else:
        sys.exit("give --since MINUTES, or --start and --end")

    rows = fetch(int(start.timestamp() * 1000), int(end.timestamp() * 1000))
    waits, bursts, yields = analyse(rows)

    head = f"  {a.label}" if a.label else ""
    print(f"\nResponse latency{head}")
    print(f"  window   {start:%Y-%m-%d %H:%M} to {end:%H:%M} UTC")

    if not waits:
        print("  no completed turns in this window")
        print(f"  yields   {yields}")
        return 1

    w = sorted(waits)
    over3 = sum(1 for x in w if x > 3000) / len(w) * 100
    print(f"  turns    {len(w)}")
    print(f"  p50      {st.median(w):7.0f} ms")
    print(f"  p90      {pct(w, 0.90):7.0f} ms")
    print(f"  p99      {pct(w, 0.99):7.0f} ms")
    print(f"  max      {max(w):7.0f} ms")
    print(f"  over 3s  {over3:6.1f} %      <- pilot: 48.6% (30 Jul), 27.4% (5 Aug)")

    if bursts:
        b = sorted(bursts)
        cut = sum(1 for x in b if x < 1000) / len(b) * 100
        print(f"\n  bot speech bursts {len(b)}, median {st.median(b):.0f} ms")
        print(f"  cut off under 1s  {cut:.0f} %")
    print(f"  interruptions     {yields}")
    if yields > len(w):
        print("  WARNING: more yields than turns — the bot is being cut off more "
              "often than it speaks; treat the latency above as optimistic")
    return 0


if __name__ == "__main__":
    sys.exit(main())
