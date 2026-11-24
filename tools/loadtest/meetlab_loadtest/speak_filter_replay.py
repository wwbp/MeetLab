"""Replay the speak filter's scenarios (speak_filter_scenarios.py) through the filter's decision
(meetlab_bot/speak_filter.py) with a real classifier, to choose thresholds from data.

  python -m meetlab_loadtest.speak_filter_replay --i-understand-this-costs-money --max-calls 200 \\
      --env-file .env --out replay.json

THIS CALLS A PAID API (Pipecat's OpenAIDecisionsClassifier, its default model): one request per
scenario per run, except scenarios decided without the classifier. It refuses to run without the
flag and a cap, sends only the scenarios' invented text, and stops if the first call fails.
Run by hand, never in CI. Everything but the call is pure: tests/test_speak_filter_replay.py.

The report keeps the two errors apart:
- false interruption: the bot should have stayed silent and spoke;
- missed turn: the bot should have spoken and was blocked.
"""
import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path

from pipecat.classifiers.base_classifier import BaseClassifier, ClassifierError
from pipecat.metrics.metrics import LLMUsageMetricsData

from meetlab_bot.speak_filter import Rule, decide
from meetlab_loadtest.speak_filter_scenarios import filter_turns, load, validate

SWEEP = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


class ReplayStopped(Exception):
    """The first classifier call failed: an account or model-access problem, not to be repeated."""


async def replay(data: dict, classifier: BaseClassifier, *, runs: int, threshold: float, max_calls: int,
                 timeout_s: float = 10.0) -> list[dict]:
    """Every scenario through decide(), `runs` times, every rule at `threshold`. One record per
    scenario per run. At most one classifier call per record, so `max_calls` is checked before
    the first: a run that could exceed it is refused whole."""
    planned = runs * len(data["scenarios"])
    if planned > max_calls:
        raise ValueError(f"{runs} runs of {len(data['scenarios'])} scenarios can make {planned} calls, "
                         f"more than --max-calls {max_calls}")
    instructions = {r["id"]: r["instructions"] for r in data["rules"]}
    records, asked = [], 0
    for n in range(1, runs + 1):
        for s in data["scenarios"]:
            d = await decide(
                filter_turns(s),
                [s["bot_name"]],
                [Rule(r, instructions[r], threshold) for r in s["active_rules"]],
                classifier, timeout_s=timeout_s, batch_size=s.get("batch_size", 1),
                silence_s=s.get("silence_after_s"), clock=time.perf_counter,
            )
            asked += d.elapsed_ms is not None
            if asked == 1 and d.reason == "classifier_error":
                raise ReplayStopped(d.error)
            records.append({"run": n, "id": s["id"], "speak": d.speak, "reason": d.reason,
                            "blocked_by": list(d.blocked_by), "probabilities": d.probabilities,
                            "elapsed_ms": d.elapsed_ms, "error": d.error})
    return records


def stats(values: list[float]) -> dict:
    """n, min, median, p95 (nearest rank), max."""
    if not values:
        return {"n": 0}
    v = sorted(values)
    return {"n": len(v), "min": round(v[0], 1), "median": round(statistics.median(v), 1),
            "p95": round(v[math.ceil(0.95 * len(v)) - 1], 1), "max": round(v[-1], 1)}


def _rate(n: int, of: int) -> float | None:
    return round(n / of, 4) if of else None


def _errors(rows: list[tuple[dict, bool]]) -> dict:
    """rows: (scenario, the bot spoke). The two error counts and rates, kept apart."""
    silent = [spoke for s, spoke in rows if s["expected"] == "silent"]
    speak = [spoke for s, spoke in rows if s["expected"] == "speak"]
    return {"silent_expected": len(silent), "false_interruptions": sum(silent),
            "false_interruption_rate": _rate(sum(silent), len(silent)),
            "speak_expected": len(speak), "missed_turns": speak.count(False),
            "missed_turn_rate": _rate(speak.count(False), len(speak))}


def report(data: dict, records: list[dict], *, threshold: float) -> dict:
    """What the replay showed, from its records alone (the sweep makes no calls)."""
    scenarios = {s["id"]: s for s in data["scenarios"]}
    rules = [r["id"] for r in data["rules"]]
    rows = [(scenarios[r["id"]], r) for r in records]

    def spoke_at(r: dict, t: float, only: str | None = None) -> bool:
        """Would the bot speak with every rule (or only one) at threshold t? A record with no
        probabilities (called by name, filter off, classifier failed) spoke whatever t is."""
        return not any(p >= t for rule, p in r["probabilities"].items() if only in (None, rule))

    by_rule = {}
    for rule in rules:
        own = [r for s, r in rows if s["expected"] == "silent" and s["expected_rule"] == rule]
        by_rule[rule] = {
            "should_block": len(own),
            "false_interruptions": sum(r["speak"] for r in own),
            "missed_turns": sum(rule in r["blocked_by"] for s, r in rows if s["expected"] == "speak"),
            "blocked_for_another_rule": sum(rule in r["blocked_by"] for s, r in rows
                                            if s["expected"] == "silent" and s["expected_rule"] != rule),
        }
    per_scenario = []
    for s in data["scenarios"]:
        mine = [r for r in records if r["id"] == s["id"]]
        probabilities = {rule: [r["probabilities"][rule] for r in mine if rule in r["probabilities"]] for rule in rules}
        probabilities = {rule: p for rule, p in probabilities.items() if p}
        decisions = ["speak" if r["speak"] else "silent" for r in mine]
        per_scenario.append({
            "id": s["id"], "category": s["category"], "expected": s["expected"], "expected_rule": s["expected_rule"],
            "decisions": decisions, "reasons": [r["reason"] for r in mine], "blocked_by": [r["blocked_by"] for r in mine],
            "flipped": len(set(decisions)) > 1, "correct": [d == s["expected"] for d in decisions],
            "probabilities": probabilities,
            "spread": {rule: round(max(p) - min(p), 4) for rule, p in probabilities.items()},
        })
    asked_speak = [r for s, r in rows if s["expected"] == "speak" and r["probabilities"]]
    return {
        "runs": max((r["run"] for r in records), default=0),
        "threshold": threshold,
        "scenarios_count": len(scenarios),
        "calls": sum(r["elapsed_ms"] is not None for r in records),
        "classifier_errors": sum(r["reason"] == "classifier_error" for r in records),
        "errors": {
            "overall": _errors([(s, r["speak"]) for s, r in rows]),
            "by_category": {c: _errors([(s, r["speak"]) for s, r in rows if s["category"] == c])
                            for c in dict.fromkeys(s["category"] for s in data["scenarios"])},
            "by_rule": by_rule,
        },
        "flips": [s["id"] for s in per_scenario if s["flipped"]],
        "latency_ms": stats([r["elapsed_ms"] for r in records if r["elapsed_ms"] is not None]),
        "sweep": {
            "all_rules": [{"threshold": t, **_errors([(s, spoke_at(r, t)) for s, r in rows])} for t in SWEEP],
            "by_rule": {rule: [{
                "threshold": t,
                "should_block": by_rule[rule]["should_block"],
                "blocks_own": sum(not spoke_at(r, t, rule) for s, r in rows
                                  if s["expected"] == "silent" and s["expected_rule"] == rule),
                "blocks_speak": sum(not spoke_at(r, t, rule) for r in asked_speak),
                "speak_asked": len(asked_speak),
            } for t in SWEEP] for rule in rules},
        },
        "scenarios": per_scenario,
    }


def table(rep: dict) -> str:
    """The report in a few Markdown tables."""
    def pct(x):
        return "-" if x is None else f"{100 * x:.0f}%"

    def errors(name, e):
        return (f"| {name} | {e['false_interruptions']}/{e['silent_expected']} | {pct(e['false_interruption_rate'])} "
                f"| {e['missed_turns']}/{e['speak_expected']} | {pct(e['missed_turn_rate'])} |")
    lat, tokens = rep["latency_ms"], rep.get("tokens") or {}
    lines = [
        f"Speak filter replay: model {rep.get('model')}, {rep['scenarios_count']} scenarios x {rep['runs']} runs, "
        f"{rep['calls']} classifier calls, {rep['classifier_errors']} classifier errors, every rule at {rep['threshold']}.",
        f"Tokens: {tokens.get('input', '?')} in, {tokens.get('output', '?')} out. Cost: {rep.get('cost_usd', 'not known (no price given)')}.",
        "Classifier call, ms: " + (f"min {lat['min']}, median {lat['median']}, p95 {lat['p95']}, max {lat['max']} (n={lat['n']})."
                                   if lat["n"] else "none made."),
        "",
        "| | false interruptions (should be silent, spoke) | | missed turns (should speak, blocked) | |",
        "|---|---|---|---|---|",
        errors("**overall**", rep["errors"]["overall"]),
        *(errors(c, e) for c, e in rep["errors"]["by_category"].items()),
        "",
        "| rule | cases it should block | of those, the bot spoke | speak cases it blocked | silent cases of another rule it blocked |",
        "|---|---|---|---|---|",
        *(f"| {rule} | {e['should_block']} | {e['false_interruptions']} | {e['missed_turns']} | {e['blocked_for_another_rule']} |"
          for rule, e in rep["errors"]["by_rule"].items()),
        "",
        "Threshold sweep, every rule at the same threshold (from the recorded probabilities):",
        "",
        "| threshold | false interruptions | | missed turns | |",
        "|---|---|---|---|---|",
        *(errors(row["threshold"], row) for row in rep["sweep"]["all_rules"]),
        "",
        "Each rule alone: own cases blocked / speak cases blocked, at each threshold:",
        "",
        "| rule | " + " | ".join(str(t) for t in SWEEP) + " |",
        "|---|" + "---|" * len(SWEEP),
        *(f"| {rule} (own {rows[0]['should_block']}, speak {rows[0]['speak_asked']}) | "
          + " | ".join(f"{r['blocks_own']} / {r['blocks_speak']}" for r in rows) + " |"
          for rule, rows in rep["sweep"]["by_rule"].items()),
        "",
        f"Decision changed between runs ({len(rep['flips'])}): {', '.join(rep['flips']) or 'none'}.",
        "",
        "Wrong in at least one run:",
        "",
        "| scenario | expected | decisions | blocked by | probabilities per run |",
        "|---|---|---|---|---|",
        *(f"| {s['id']} | {s['expected']} ({s['expected_rule']}) | {' '.join(s['decisions'])} "
          f"| {' '.join(sorted({b for run in s['blocked_by'] for b in run})) or '-'} "
          f"| {'; '.join(f'{rule} ' + ' '.join(f'{p:.2f}' for p in ps) for rule, ps in s['probabilities'].items()) or '-'} |"
          for s in rep["scenarios"] if not all(s["correct"])),
    ]
    return "\n".join(lines)


def key_from(path) -> str | None:
    """OPENAI_API_KEY's value from an env file: that one line, nothing else kept or set."""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        name, _, value = line.strip().removeprefix("export ").partition("=")
        if name.strip() == "OPENAI_API_KEY":
            return value.split(" #")[0].strip().strip("'\"") or None
    return None


async def openai_classifier(args) -> BaseClassifier:
    """Pipecat's OpenAI Decisions classifier, its default model, no retries (so one call is one
    request). connect() looks the model up, which checks the key and its access for free; it
    raises ClassifierError with OpenAI's reason."""
    from pipecat.classifiers.openai.decisions.classifier import OpenAIDecisionsClassifier
    from pipecat.classifiers.openai.decisions.client import OpenAIDecisionsClient

    key = os.environ.get("OPENAI_API_KEY") or (key_from(args.env_file) if args.env_file else None)
    if not key:
        raise ClassifierError("no OPENAI_API_KEY in the environment or in --env-file")
    client = OpenAIDecisionsClient(api_key=key, timeout=args.timeout, max_retries=0)
    try:
        await client.connect()
    except ClassifierError:
        await client.close()
        raise
    return OpenAIDecisionsClassifier(client=client)


async def _run(args, data: dict, make_classifier) -> int:
    try:
        classifier = await make_classifier(args)
    except ClassifierError as e:
        print(f"stopped before any classifier call: {e}", file=sys.stderr)
        return 1
    tokens = {"input": 0, "output": 0}

    async def on_metrics(_classifier, metrics):
        for m in metrics:
            if isinstance(m, LLMUsageMetricsData):
                tokens["input"] += m.value.prompt_tokens
                tokens["output"] += m.value.completion_tokens
    classifier.add_event_handler("on_metrics", on_metrics)
    try:
        records = await replay(data, classifier, runs=args.runs, threshold=args.threshold,
                               max_calls=args.max_calls, timeout_s=args.timeout)
    except ReplayStopped as e:
        print(f"stopped: the first classifier call failed: {e}", file=sys.stderr)
        return 1
    finally:
        await classifier.cleanup()  # waits for on_metrics, closes a client the classifier owns
        client = getattr(classifier, "client", None)
        if client:
            await client.close()
    rep = {"date": date.today().isoformat(), "model": classifier.model, **report(data, records, threshold=args.threshold),
           "tokens": tokens}
    if args.usd_per_1m_input is not None and args.usd_per_1m_output is not None:
        rep["cost_usd"] = round((tokens["input"] * args.usd_per_1m_input + tokens["output"] * args.usd_per_1m_output) / 1e6, 4)
    if args.out:
        Path(args.out).write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(table(rep))
    return 0


def main(argv=None, make_classifier=openai_classifier) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--i-understand-this-costs-money", action="store_true", help="required: this calls a paid API")
    p.add_argument("--max-calls", type=int, help="required: refuse a run that could make more classifier calls")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--threshold", type=float, default=0.5, help="every rule's threshold for the run")
    p.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for one answer")
    p.add_argument("--env-file", help="read OPENAI_API_KEY (only) from this file if the environment has none")
    p.add_argument("--usd-per-1m-input", type=float, help="the model's price, to estimate the cost")
    p.add_argument("--usd-per-1m-output", type=float)
    p.add_argument("--out", help="write the JSON report here")
    args = p.parse_args(argv)
    if not args.i_understand_this_costs_money or args.max_calls is None:
        print("refused: this calls a paid API. Pass --i-understand-this-costs-money and --max-calls N.", file=sys.stderr)
        return 2
    data = load()
    problems = validate(data)
    planned = args.runs * len(data["scenarios"])
    if problems or planned > args.max_calls:
        print("refused: " + ("; ".join(problems) or f"{planned} calls possible, --max-calls is {args.max_calls}"), file=sys.stderr)
        return 2
    return asyncio.run(_run(args, data, make_classifier))


if __name__ == "__main__":
    sys.exit(main())
