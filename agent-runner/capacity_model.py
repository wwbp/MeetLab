"""How much to run for N rooms at once: for each part, its measured use per room and the
utilization at which replies still meet 2 s p95; the cheapest machine that keeps it under that.
A best-known path, not an optimum (user, 2026-10-06): only measured parts, nothing guessed.
Sources: infra/v2/LEDGER.md (L6, B3, the 100-room spike, the right-sizing sweep).
"""
import math
from dataclasses import dataclass

MAX_ROOMS = 100        # tested to 100 rooms at once (spike, 2026-10-05)
CEILING = 0.8          # utilization kept free of queueing (CPU and connections alike)
HOURS_PER_MONTH = 730

# On-demand us-east-1, $/hour (ec2.shop, instances.vantage.sh; 2026-10-06).
PRICE = {"c6i.large": 0.085, "c6i.xlarge": 0.17, "c6i.2xlarge": 0.34,
         "db.t4g.small": 0.064, "db.t4g.medium": 0.13}  # databases: two zones

# Speech-to-text on CPU: up to 46% of a c6i.large at 15 rooms (sweep run C; 45% at 20 in run A)
# = 3% a room, the cautious figure. The GPU (g6.xlarge) was ~15 ms faster at p95 and no more
# accurate (run B), so it is never chosen: CPU machines hold the tested 100 rooms for less.
STT_SHARE_PER_ROOM = {"c6i.large": 0.03, "c6i.xlarge": 0.015, "c6i.2xlarge": 0.0075}
GPU_PRICE = 0.805  # g6.xlarge, before an NVIDIA production licence
# Database: 165 connections at 102 rooms (B3) = ~1.6 a room; Postgres' limit by size.
DB_CONNECTIONS_PER_ROOM = 1.6
DB_MAX_CONNECTIONS = {"db.t4g.small": 180, "db.t4g.medium": 400}
BOT_MACHINE = "c6i.large"


@dataclass(frozen=True)
class Plan:
    rooms: int
    bot_machines: int
    stt: str
    db: str
    binding: str
    usd_per_hour: float


def _smallest(options: dict, fits) -> str:
    return next(k for k in options if fits(k))


def plan(rooms: int, bots_per_machine: int = 5) -> Plan:  # 5 per c6i.large: sweep run C
    if not 1 <= rooms <= MAX_ROOMS:
        raise ValueError(f"rooms must be 1 to {MAX_ROOMS}: beyond that nothing was measured")
    bots = math.ceil(rooms / bots_per_machine)
    stt = _smallest(STT_SHARE_PER_ROOM, lambda m: rooms * STT_SHARE_PER_ROOM[m] <= CEILING)
    db = _smallest(DB_MAX_CONNECTIONS, lambda d: rooms * DB_CONNECTIONS_PER_ROOM <= CEILING * DB_MAX_CONNECTIONS[d])
    grew = [name for name, bigger in (("database connections", db != "db.t4g.small"),
                                      ("speech-to-text CPU", stt != "c6i.large")) if bigger]
    binding = grew[0] if grew else "bot machines"
    cost = bots * PRICE[BOT_MACHINE] + PRICE[stt] + PRICE[db]
    return Plan(rooms, bots, stt, db, binding, round(cost, 4))


def matrix(sizes=(1, 5, 10, 20, 26, 50, 100), bots_per_machine: int = 5) -> list[dict]:
    """The decision matrix: one row per size of study."""
    return [{**vars(p), "usd_per_month": round(p.usd_per_hour * HOURS_PER_MONTH, 2)}
            for p in (plan(n, bots_per_machine) for n in sizes)]


if __name__ == "__main__":
    for r in matrix():
        print(r)
