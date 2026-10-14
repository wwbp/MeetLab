"""One production, no staging (2026-10-14): what the workflows must and must never do.

Merges into main (v2's code) run the tests and build the images (with the images-only role); PRs plan
production read-only; only a release tag, after the reviewer's approval, applies anything,
and its live tests run against production. Run from the repo root (make test-infra):

    python3 infra/stack/tests/pipeline_check.py
"""
import re
import sys

infra = open(".github/workflows/build.yml").read()
release = open(".github/workflows/release.yml").read()
load = open(".github/workflows/loadtest.yml").read()
jobs = set(re.findall(r"^  ([a-z][a-z0-9_-]*):\n", infra.split("\njobs:\n", 1)[1], re.M))

rules = {
    "merges deploy nothing (no staging apply or live tests)": not {"apply", "acceptance"} & jobs,
    "nothing in the merge workflow targets staging":
        not any("staging" in l for l in infra.splitlines() if not l.lstrip().startswith("#")),
    "merges push images with the images-only role": "vars.IMAGES_ROLE_ARN" in infra,
    "main is v2's branch (v1 is the tag v1.0.0)": "branches: [main]" in infra and "refs/heads/main" in infra
        and "origin/main" in release and "v2" not in re.findall(r"branches: \[(\w+)\]", infra),
    "PRs plan production": "-backend-config=key=v2/prod/terraform.tfstate" in infra,
    "PR plans never lock (the plan role can't write production's state)": "-lock=false" in infra,
    "only a release applies, after approval": "environment: production" in release and "terraform apply" in release,
    "a load test runs on production, only after the reviewer's approval (it uses the paid vendors)":
        "environment: production" in load
        and not any("staging" in l for l in load.splitlines() if not l.lstrip().startswith("#")),
    "a release needs the build that passed": "--workflow build.yml" in release and "--status success" in release,
}
for rule, ok in rules.items():
    print(f"{'ok  ' if ok else 'FAIL'} {rule}")
sys.exit(0 if all(rules.values()) else 1)
