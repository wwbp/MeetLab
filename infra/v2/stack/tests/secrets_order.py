"""No ECS service starts before its tasks may read their secrets.

The secrets policy names the database's password secret, so on a stack built from
nothing it comes ~10 min after the database; a service created before it has its first
tasks refused their secrets and ECS gives up on it (v2.0.0, production's first release,
2026-10-07: meet). Every service must depend on the policy, read from Terraform's own
dependency graph (`terraform graph`, on a copy with a local backend: no state, no AWS).
Run from the repo root (make test-infra):

    python3 infra/v2/stack/tests/secrets_order.py
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

POLICY = "aws_iam_role_policy.execution_secrets"


def edges(dot: str) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for src, dst in re.findall(r'"([^"]+)" -> "([^"]+)"', dot):
        graph.setdefault(src, set()).add(dst)
    return graph


def depends_on(graph: dict[str, set[str]], node: str, target: str) -> bool:
    seen, todo = set(), [node]
    while todo:
        n = todo.pop()
        if n == target:
            return True
        if n not in seen:
            seen.add(n)
            todo.extend(graph.get(n, ()))
    return False


def early_services(dot: str) -> list[str]:
    graph = edges(dot)
    services = sorted(n for n in graph if n.startswith("aws_ecs_service."))
    return [s for s in services if not depends_on(graph, s, POLICY)]


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        stack = Path(tmp) / "stack"
        shutil.copytree("infra/v2/stack", stack, ignore=shutil.ignore_patterns(".terraform*", "*.tfstate*"))
        (stack / "backend_override.tf").write_text('terraform {\n  backend "local" {}\n}\n')
        tf = lambda *a: subprocess.run(["terraform", f"-chdir={stack}", *a], check=True, capture_output=True, text=True).stdout  # noqa: E731
        tf("init", "-input=false")
        dot = tf("graph")
    assert "aws_ecs_service.meet" in dot, "no services in the graph: is the stack initialised?"
    early = early_services(dot)
    for s in early:
        print(f"{s} can start before {POLICY}: its tasks may be refused their secrets")
    sys.exit(1 if early else 0)
