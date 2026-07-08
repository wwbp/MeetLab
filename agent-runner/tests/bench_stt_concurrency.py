"""Direct STT-server concurrency benchmark.

Fires transcription requests straight at the STT server (bypassing LiveKit and the bot)
and measures how per-request latency and throughput scale with concurrency. This isolates
the SERVER's behavior — the thing that broke the 10-room soak:

  • a serialized server (the Shadowfita FastAPI wrapper) → latency rises ~linearly with
    concurrency and throughput plateaus (one request at a time);
  • a batched/concurrent server (NVIDIA NIM / Riva on Triton) → latency stays roughly flat
    as concurrency climbs and throughput scales.

Run it against the current sidecar to capture the baseline, then against the NIM/Riva
server to quantify the fix — same tool, just point STT_URL / STT_API at each.

Env:
  STT_URL         base URL (default http://stt-nemotron:8000)
  STT_API         shadowfita → POST /transcribe (default)
                  openai     → POST /v1/audio/transcriptions (NIM / OpenAI-compatible)
  STT_MODEL       model field for the openai API (default parakeet-tdt-0.6b-v2)
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

STT_URL = os.getenv("STT_URL", "http://stt-nemotron:8000").rstrip("/")
STT_API = os.getenv("STT_API", "shadowfita").strip().lower()
STT_MODEL = os.getenv("STT_MODEL", "parakeet-tdt-0.6b-v2")
# NIM (openai API) requires a language code alongside the model.
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
    form = aiohttp.FormData()
    if STT_API == "openai":
        # OpenAI-compatible (NVIDIA NIM /v1/audio/transcriptions)
        form.add_field("file", wav, filename="segment.wav", content_type="audio/wav")
        form.add_field("model", STT_MODEL)
        form.add_field("language", STT_LANGUAGE)
    else:
        # Shadowfita /transcribe (segments are pre-cut → should_chunk=false)
        form.add_field("file", wav, filename="segment.wav", content_type="audio/wav")
        form.add_field("should_chunk", "false")
    return form


def _endpoint() -> str:
    return f"{STT_URL}/v1/audio/transcriptions" if STT_API == "openai" else f"{STT_URL}/transcribe"


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
    wav = Path(FIXTURE).read_bytes()
    print(f"[bench-stt] url={STT_URL} api={STT_API} model={STT_MODEL} "
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
    print(f"STT CONCURRENCY  ({STT_API} @ {STT_URL})")
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
