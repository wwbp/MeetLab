"""Direct STT-server concurrency benchmark.

Fires transcription requests straight at the Parakeet NIM (bypassing LiveKit and the bot)
and measures how per-request latency and throughput scale with concurrency. This isolates
the SERVER's behavior — the thing that broke the 10-room soak:

  • a serialized server → latency rises ~linearly with concurrency and throughput plateaus;
  • a concurrent/batched server → latency stays flatter as concurrency climbs.

Point STT_URL at a reachable NIM (run from inside the VPC). Results + the Shadowfita
baseline are recorded in docs/latency-experiments.md (Experiments 6-7).

Env:
  STT_URL         base URL of the Parakeet NIM (required, e.g. http://10.0.5.21:9000)
  STT_MODEL       served model id sent as the `model` field (default: the NIM's served id)
  STT_LANGUAGE    language code the NIM requires alongside the model (default multi)
  CONCURRENCIES   comma list of in-flight levels to sweep (default 1,2,4,8,16)
  REQUESTS_PER    requests sent per level (default 24)
  FIXTURE         WAV to send (default tests/fixtures/benchmark_prompt.wav)
  TIMEOUT_S       per-request timeout seconds (default 60)
  WARMUP          warmup requests before timing (default 2)

Run:  make bench-stt-concurrency
"""
import asyncio
import os
import sys
import time
from pathlib import Path

import aiohttp

STT_URL = os.getenv("STT_URL", "").rstrip("/")
# The NIM's served model id (differs from our internal stt_model) + the language code
# it requires alongside it. Defaults match the deployed Parakeet NIM.
STT_MODEL = os.getenv("STT_MODEL", "parakeet-tdt-0.6b-multi-asr-offline")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "multi")
CONCURRENCIES = [int(x) for x in os.getenv("CONCURRENCIES", "1,2,4,8,16").split(",") if x.strip()]
REQUESTS_PER = int(os.getenv("REQUESTS_PER", "24"))
FIXTURE = os.getenv("FIXTURE", str(Path(__file__).parent / "fixtures" / "benchmark_prompt.wav"))
TIMEOUT_S = float(os.getenv("TIMEOUT_S", "60"))
WARMUP = int(os.getenv("WARMUP", "2"))


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round((q / 100.0) * (len(s) - 1))))], 1)


def _build_form(wav: bytes) -> aiohttp.FormData:
    # OpenAI-compatible NIM /v1/audio/transcriptions (model + language required).
    form = aiohttp.FormData()
    form.add_field("file", wav, filename="segment.wav", content_type="audio/wav")
    form.add_field("model", STT_MODEL)
    form.add_field("language", STT_LANGUAGE)
    return form


def _endpoint() -> str:
    return f"{STT_URL}/v1/audio/transcriptions"


async def _one(session: aiohttp.ClientSession, wav: bytes) -> tuple[float, bool]:
    """Return (latency_ms, ok) for a single transcription request."""
    t0 = time.monotonic()
    try:
        async with session.post(_endpoint(), data=_build_form(wav)) as resp:
            await resp.read()
            ok = resp.status == 200
    except Exception:
        ok = False
    return (time.monotonic() - t0) * 1000.0, ok


async def _run_level(wav: bytes, concurrency: int) -> dict:
    """Send REQUESTS_PER requests with at most `concurrency` in flight; measure."""
    sem = asyncio.Semaphore(concurrency)
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
    lat: list[float] = []
    errors = 0

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async def worker():
            nonlocal errors
            async with sem:
                ms, ok = await _one(session, wav)
                if ok:
                    lat.append(ms)
                else:
                    errors += 1

        wall0 = time.monotonic()
        await asyncio.gather(*(worker() for _ in range(REQUESTS_PER)))
        wall = time.monotonic() - wall0

    throughput = (len(lat) / wall) if wall > 0 else 0.0
    return {"concurrency": concurrency, "p50": _pct(lat, 50), "p95": _pct(lat, 95),
            "throughput_rps": round(throughput, 2), "errors": errors, "n": len(lat)}


async def main() -> None:
    if not STT_URL:
        sys.exit("STT_URL is required — point it at a reachable NIM, e.g. "
                 "make bench-stt-concurrency STT_URL=http://10.0.5.21:9000")
    wav = Path(FIXTURE).read_bytes()
    print(f"[bench-stt] url={STT_URL} model={STT_MODEL} "
          f"fixture={Path(FIXTURE).name} ({len(wav)} bytes) requests/level={REQUESTS_PER}")

    # Warmup (model load / first-request cost shouldn't skew the level results).
    if WARMUP > 0:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as s:
            await asyncio.gather(*(_one(s, wav) for _ in range(WARMUP)))

    rows = []
    for c in CONCURRENCIES:
        row = await _run_level(wav, c)
        rows.append(row)
        print(f"  concurrency={c:>3}  p50={str(row['p50']):>8}ms  p95={str(row['p95']):>8}ms  "
              f"throughput={row['throughput_rps']:>6} req/s  errors={row['errors']}")

    bar = "─" * 74
    print(f"\n{bar}")
    print(f"STT CONCURRENCY  (Parakeet NIM @ {STT_URL})")
    print(bar)
    print(f"{'conc':>5}  {'p50 ms':>9}  {'p95 ms':>9}  {'req/s':>8}  {'errors':>6}")
    for r in rows:
        print(f"{r['concurrency']:>5}  {str(r['p50']):>9}  {str(r['p95']):>9}  "
              f"{r['throughput_rps']:>8}  {r['errors']:>6}")
    print(bar)
    # Serialization signal: if p50 at the top level ≫ p50 at concurrency 1, the server
    # is serializing (latency scales with load). Flat p50 = healthy batching/concurrency.
    base = next((r for r in rows if r["concurrency"] == 1), rows[0])
    top = rows[-1]
    if base["p50"] and top["p50"]:
        ratio = top["p50"] / base["p50"]
        verdict = "SERIALIZING (latency scales with load)" if ratio > 2.0 else "scales OK (latency ~flat)"
        print(f"  p50 {base['concurrency']}→{top['concurrency']} conc: "
              f"{base['p50']}ms → {top['p50']}ms  ({ratio:.1f}×)  → {verdict}")
    print(f"{bar}\n")


if __name__ == "__main__":
    asyncio.run(main())
