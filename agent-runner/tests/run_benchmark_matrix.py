"""Multi-config pipeline latency benchmark.

Tests a matrix of STT/LLM/TTS configurations and saves results with config
metadata so runs can be compared over time.

Usage:
    make benchmark-full                        # all configs, default 5 samples each
    make benchmark-full BENCHMARK_SAMPLES=10
    make benchmark-report                      # print saved results table

Results are appended to tests/fixtures/benchmark_results.json.
"""

import asyncio
import itertools
import json
import os
import sys
import time
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from livekit import api, rtc
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

RUNNER_URL = os.getenv("AGENT_RUNNER_URL", "http://localhost:7860")
LIVEKIT_URL_TEST = os.getenv("LIVEKIT_URL", "ws://transport-server:7880")
API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
SAMPLES = int(os.getenv("BENCHMARK_SAMPLES", "5"))
RESPONSE_TIMEOUT = float(os.getenv("BENCHMARK_TIMEOUT", "25"))
# Max rooms running simultaneously per config. Keeps LiveKit load manageable.
PARALLEL_SAMPLES = int(os.getenv("BENCHMARK_PARALLEL", "3"))

_WAV_ENV = os.getenv("BENCHMARK_WAV")
WAV_PATH = Path(_WAV_ENV) if _WAV_ENV else Path(__file__).parent / "fixtures" / "benchmark_prompt.wav"
RESULTS_PATH = Path(__file__).parent / "fixtures" / "benchmark_results.json"

# ── Config matrix ────────────────────────────────────────────────────────────
# Generated from a Cartesian product of all available options.
# Each entry is run independently; results appended to benchmark_results.json.

ELEVENLABS_VOICE = "WhMcMcvXQ8T2QfmQmlYh"

_STT_MODELS = [
    # nova-3-general only — meeting/phonecall/voicemail variants 100% timeout
    # (not available on current Deepgram plan)
    "nova-3-general",
    "gpt-realtime-whisper",
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
]

_LLM_MODELS = [
    "gpt-5.4-nano",
    "gpt-5.4-mini",
    "gpt-4.1-nano",
    "gpt-4.1-mini",
    "gpt-4o-mini",
]

_TTS_OPTIONS = [
    {"tts_provider": "elevenlabs", "tts_voice": ELEVENLABS_VOICE, "tts_aggregation_mode": "sentence"},
    {"tts_provider": "elevenlabs", "tts_voice": ELEVENLABS_VOICE, "tts_aggregation_mode": "token"},
    {"tts_provider": "openai", "tts_voice": "alloy", "tts_aggregation_mode": "sentence"},
]

# Deepgram endpointing variants. Only applied to nova-3-* STT models; OpenAI STT
# ignores this field. 200=default, 100=lower latency (Experiment 2).
_DEEPGRAM_ENDPOINTING_OPTIONS = [200, 100]

def _endpointing_for_stt(stt_model: str, ep: int) -> dict:
    """Only include stt_endpointing_ms for Deepgram models."""
    if stt_model.startswith("nova-"):
        return {"stt_endpointing_ms": ep}
    return {"stt_endpointing_ms": 200}  # no-op for OpenAI STT

def _ep_label(stt_model: str, ep: int) -> str:
    if stt_model.startswith("nova-") and ep != 200:
        return f"[ep={ep}]"
    return ""

CONFIG_MATRIX = [
    {
        "label": (
            f"{stt} / {llm} / {tts['tts_provider']} [{tts['tts_aggregation_mode']}]"
            + _ep_label(stt, ep)
        ),
        "stt_model": stt,
        "stt_vad_mode": "local",
        "stt_delay": None,
        "llm_model": llm,
        **tts,
        **_endpointing_for_stt(stt, ep),
    }
    for stt, llm, tts, ep in itertools.product(
        _STT_MODELS, _LLM_MODELS, _TTS_OPTIONS,
        # Only vary endpointing for Deepgram; use 200 for all others (deduped below)
        _DEEPGRAM_ENDPOINTING_OPTIONS,
    )
    # Deduplicate: OpenAI STT doesn't use endpointing, so ep=200 and ep=100 produce
    # the same config. Keep only ep=200 for non-Deepgram models.
    if stt.startswith("nova-") or ep == 200
]


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _request(method: str, path: str, body: Optional[dict] = None) -> dict:
    url = f"{RUNNER_URL}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _set_room_config(room_name: str, config: dict) -> None:
    payload = {k: v for k, v in config.items() if k != "label"}
    # Explicitly send stt_delay=null to clear any inherited value
    if "stt_delay" not in payload:
        payload["stt_delay"] = None
    # Default endpointing to 200 if not specified
    if "stt_endpointing_ms" not in payload:
        payload["stt_endpointing_ms"] = 200
    payload["scope"] = room_name
    _request("PUT", "/config", payload)


def _make_token(room_name: str, identity: str) -> str:
    return (
        api.AccessToken(API_KEY, API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(api.VideoGrants(
            room_join=True, room=room_name,
            can_publish=True, can_subscribe=True, can_publish_data=True,
        ))
        .with_ttl(timedelta(minutes=30))
        .to_jwt()
    )


async def _stream_wav(room: rtc.Room) -> None:
    with wave.open(str(WAV_PATH), "rb") as wf:
        sr, ch, frame_ms = wf.getframerate(), wf.getnchannels(), 60
        frame_samples = sr * frame_ms // 1000
        source = rtc.AudioSource(sample_rate=sr, num_channels=ch)
        track = rtc.LocalAudioTrack.create_audio_track("bench-audio", source)
        await room.local_participant.publish_track(track)
        while True:
            raw = wf.readframes(frame_samples)
            if not raw:
                break
            frame = rtc.AudioFrame(
                data=raw, sample_rate=sr, num_channels=ch,
                samples_per_channel=len(raw) // (2 * ch),
            )
            await source.capture_frame(frame)
            await asyncio.sleep(frame_ms / 1000)


async def _poll_bot_utterance(session_id: str, timeout: float) -> Optional[dict]:
    from db.engine import AsyncSessionLocal, engine
    from db.models import Utterance, Conversation, Speaker
    from sqlalchemy import select

    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Utterance)
                    .join(Conversation, Utterance.conv_id == Conversation.id)
                    .join(Speaker, Utterance.speaker_id == Speaker.id)
                    .where(
                        Conversation.id == session_id,
                        Speaker.meta["role"].astext == "bot",
                    )
                    .order_by(Utterance.ts.asc())
                )
                for utt in result.scalars().all():
                    if utt.meta and "timing" in utt.meta:
                        return utt.meta
            await asyncio.sleep(1.0)
    finally:
        await engine.dispose()
    return None


# ── Single-sample run ─────────────────────────────────────────────────────────

async def _run_sample(room_name: str) -> Optional[dict]:
    resp = _request("POST", "/start", {"room_name": room_name})
    session_id = resp["session_id"]

    room1 = rtc.Room()
    room2 = rtc.Room()
    uid1 = f"bench_u1_{uuid4().hex[:6]}"
    uid2 = f"bench_u2_{uuid4().hex[:6]}"
    await room1.connect(LIVEKIT_URL_TEST, _make_token(room_name, uid1))
    await room2.connect(LIVEKIT_URL_TEST, _make_token(room_name, uid2))

    await asyncio.sleep(1.5)
    await _stream_wav(room1)

    meta = await _poll_bot_utterance(session_id, timeout=RESPONSE_TIMEOUT)

    await room1.disconnect()
    await room2.disconnect()

    if not meta:
        return None

    timing = meta.get("timing", {})
    e2e = meta.get("latency_ms")
    llm = timing.get("llm_ttft_ms")
    # New format: sentence_agg_ms + tts_ttfb_ms (from Pipecat MetricsFrame)
    # Old format: tts_first_ms (manual llm_first → tts_audio, kept for backward compat)
    tts_ttfb = timing.get("tts_ttfb_ms")
    sentence_agg = timing.get("sentence_agg_ms") or 0
    tts = (tts_ttfb + sentence_agg) if tts_ttfb is not None else timing.get("tts_first_ms")
    stt = round(e2e - llm - tts, 1) if (e2e and llm and tts) else None
    return {
        "stt_ms": stt,
        "llm_ttft_ms": llm,
        "tts_first_ms": tts,
        "sentence_agg_ms": timing.get("sentence_agg_ms"),
        "tts_ttfb_ms": timing.get("tts_ttfb_ms"),
        "e2e_ms": e2e,
    }


# ── Config run (N samples, parallel) ─────────────────────────────────────────

async def run_config(cfg: dict, n: int, idx: int = 0, total: int = 0) -> dict:
    label = cfg["label"]
    progress = f"[{idx}/{total}]" if total else ""
    print(f"\n{'═'*70}")
    print(f"Config {progress}: {label}")
    ep = cfg.get('stt_endpointing_ms', 200)
    print(f"  stt={cfg.get('stt_model')}[ep={ep}ms]  llm={cfg.get('llm_model')}  tts={cfg.get('tts_provider')}")
    print(f"  Running {n} samples (parallel={PARALLEL_SAMPLES})...")
    print(f"{'═'*70}")

    sem = asyncio.Semaphore(PARALLEL_SAMPLES)
    print_lock = asyncio.Lock()

    async def _run_one(i: int) -> Optional[dict]:
        room_name = f"bench-{uuid4().hex[:8]}"
        _set_room_config(room_name, cfg)
        async with sem:
            try:
                row = await _run_sample(room_name)
            except Exception as e:
                async with print_lock:
                    print(f"  [{i+1}/{n}] ERROR: {e}")
                return None
        async with print_lock:
            if row is None:
                print(f"  [{i+1}/{n}] timeout (>{RESPONSE_TIMEOUT}s)")
            else:
                agg = f"  agg={row['sentence_agg_ms']:.0f}" if row.get("sentence_agg_ms") else ""
                ttfb = f"  tts={row['tts_ttfb_ms']:.0f}" if row.get("tts_ttfb_ms") else f"  tts={row['tts_first_ms']}"
                print(f"  [{i+1}/{n}] llm={row['llm_ttft_ms']}ms{agg}ms{ttfb}ms  e2e={row['e2e_ms']}ms")
        return row

    results = await asyncio.gather(*[_run_one(i) for i in range(n)])
    samples = [r for r in results if r is not None]

    metrics = _aggregate(samples)
    record = {
        "run_id": str(uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "config": {k: v for k, v in cfg.items() if k != "label"},
        "n_requested": n,
        "n_collected": len(samples),
        "metrics": metrics,
    }
    _print_config_summary(record)
    return record


# ── Stats helpers ─────────────────────────────────────────────────────────────

def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    return sorted(vals)[max(0, int(len(vals) * p / 100) - 1)]


def _aggregate(samples: list[dict]) -> dict:
    keys = ["stt_ms", "llm_ttft_ms", "sentence_agg_ms", "tts_ttfb_ms", "tts_first_ms", "e2e_ms"]
    out: dict = {}
    for k in keys:
        # Discard negative/zero values — artifact of timing races between turns
        vals = sorted(v for s in samples if (v := s.get(k)) is not None and v > 0)
        if not vals:
            out[k] = None
            continue
        out[k] = {
            "mean": round(sum(vals) / len(vals), 1),
            "p50": round(_pct(vals, 50), 1),
            "p95": round(_pct(vals, 95), 1),
            "min": round(vals[0], 1),
            "values": vals,
        }
    return out


def _print_config_summary(record: dict) -> None:
    m = record["metrics"]
    n_ok = record["n_collected"]
    n_req = record["n_requested"]
    print(f"\n  Summary ({n_ok}/{n_req} samples):")
    stages = [
        ("stt_ms",        "STT (derived)"),
        ("llm_ttft_ms",   "LLM TTFB"),
        ("sentence_agg_ms","Sentence agg"),
        ("tts_ttfb_ms",   "TTS TTFB"),
        ("tts_first_ms",  "TTS total"),
        ("e2e_ms",        "E2E"),
    ]
    for key, label in stages:
        v = m.get(key)
        if v:
            print(f"    {label:<16}  mean={v['mean']:>6.0f}ms  p50={v['p50']:>6.0f}ms  p95={v['p95']:>6.0f}ms")
        else:
            print(f"    {label:<16}  — no data")


# ── Results persistence ───────────────────────────────────────────────────────

def _load_results() -> list[dict]:
    if RESULTS_PATH.exists():
        return json.loads(RESULTS_PATH.read_text())
    return []


def _save_results(results: list[dict]) -> None:
    RESULTS_PATH.parent.mkdir(exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=2))


def _append_record(record: dict) -> None:
    results = _load_results()
    results.append(record)
    _save_results(results)
    print(f"\n  Saved → {RESULTS_PATH}  (total runs: {len(results)})")


# ── Comparison report ─────────────────────────────────────────────────────────

def print_report(results: Optional[list[dict]] = None, top_n: int = 0) -> None:
    if results is None:
        results = _load_results()
    if not results:
        print("No benchmark results found. Run: make benchmark-full")
        return

    keys = ["stt_ms", "llm_ttft_ms", "tts_first_ms", "e2e_ms"]
    labels = {"stt_ms": "STT", "llm_ttft_ms": "LLM TTFT", "tts_first_ms": "TTS first", "e2e_ms": "E2E"}

    # Sort by E2E P50 ascending; configs with no data (timeout) go last
    def _e2e_p50(r: dict) -> float:
        m = r["metrics"].get("e2e_ms")
        return m["p50"] if m else float("inf")

    sorted_results = sorted(results, key=_e2e_p50)
    if top_n:
        display = sorted_results[:top_n]
    else:
        display = sorted_results

    col_w = 46
    metric_w = 8
    total_w = col_w + len(keys) * (metric_w * 2 + 3) + 6

    print(f"\n{'═'*total_w}")
    print(f"Benchmark Results — sorted by E2E P50 ({len(sorted_results)} configs)")
    if top_n:
        print(f"Showing top {len(display)}")
    print(f"{'═'*total_w}")

    header = f"{'Config':<{col_w}}"
    for k in keys:
        header += f"  {labels[k]:>{metric_w*2+1}}"
    print(header)
    print(f"{'─'*total_w}")
    sub = " " * col_w
    for _ in keys:
        sub += f"  {'mean':>{metric_w}} {'p50':>{metric_w}}"
    print(sub)
    print(f"{'─'*total_w}")

    # Collect best-in-class for highlighting
    best: dict[str, float] = {}
    for k in keys:
        vals = [r["metrics"][k]["p50"] for r in display if r["metrics"].get(k)]
        if vals:
            best[k] = min(vals)

    for rec in display:
        label = rec["label"]
        short = label[:col_w - 1] if len(label) >= col_w else label
        n_ok = rec["n_collected"]
        n_req = rec["n_requested"]
        row = f"{short:<{col_w}}"
        for k in keys:
            m = rec["metrics"].get(k)
            if m:
                mean_v = m["mean"]
                p50_v = m["p50"]
                star = "*" if best.get(k) == p50_v else " "
                row += f"  {mean_v:>{metric_w-1}.0f}{star} {p50_v:>{metric_w}.0f}"
            else:
                row += f"  {'—':>{metric_w}}  {'—':>{metric_w}}"
        suffix = f"  ({n_ok}/{n_req})"
        print(f"{row}{suffix}")

    print(f"{'─'*total_w}")
    print("* = best P50 for that stage  |  (ok/total) = samples collected\n")

    # Top 5 by E2E P50
    top5 = [(r["metrics"]["e2e_ms"]["p50"], r["label"]) for r in sorted_results if r["metrics"].get("e2e_ms")]
    if top5:
        print("── Top 5 by E2E P50 ──────────────────────────────────────────────────────")
        for rank, (p50, lbl) in enumerate(top5[:5], 1):
            print(f"  #{rank}  {p50:>6.0f}ms  {lbl}")
        print()


# ── Entry point ───────────────────────────────────────────────────────────────

async def _main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"

    if mode == "report":
        print_report()
        return

    if not WAV_PATH.exists():
        print(f"ERROR: Benchmark audio not found at {WAV_PATH}")
        print("Run: make benchmark-audio")
        sys.exit(1)

    # Optionally filter to specific config labels via env
    only = os.getenv("BENCHMARK_CONFIGS", "").strip()
    matrix = CONFIG_MATRIX
    if only:
        wanted = {s.strip() for s in only.split(",")}
        matrix = [c for c in CONFIG_MATRIX if c["label"] in wanted]
        if not matrix:
            print(f"No configs matched BENCHMARK_CONFIGS={only}")
            sys.exit(1)

    total = len(matrix)
    print(f"\nRunning {total} config(s) × {SAMPLES} samples each  (parallel={PARALLEL_SAMPLES})")
    all_records: list[dict] = []

    for i, cfg in enumerate(matrix, 1):
        record = await run_config(cfg, SAMPLES, idx=i, total=total)
        _append_record(record)
        all_records.append(record)
        # Brief cooldown between configs to let LiveKit drain ghost rooms
        await asyncio.sleep(3)

    print(f"\n{'═'*70}")
    print("FULL COMPARISON — TOP 20 BY E2E P50")
    print_report(_load_results(), top_n=20)


if __name__ == "__main__":
    asyncio.run(_main())
