# infra/v2/bootstrap — the roles CI uses

Two IAM roles for GitHub Actions, scoped to this repo only (the account is shared with
other lab projects, and the old `github-actions-service-acc` trusts every `wwbp/*` repo).

| Role | Assumable from | Can do |
|---|---|---|
| `meetlab-v2-tf-plan` | PRs in `wwbp/MeetLab`, pushes to `v2` | Read staging; state under `v2/staging/` |
| `meetlab-v2-tf-apply` | The `staging` GitHub environment only (reviewer-gated, `v2` branch only) | Change staging's resources only; state under `v2/staging/` |
| `meetlab-v2-tf-apply-prod` | The `production` GitHub environment only (your approval) | Change production's resources only; state under `v2/prod/` |
| `meetlab-v2-acceptance`, `meetlab-v2-acceptance-prod` | Their environment only | The live tests after a deploy, in that environment |

Each environment's roles name only that environment's resources (`meetlab-v2-staging*` or
`meetlab-v2-prod*`, plus `Environment` tags for EC2), so a staging deploy can never reach
production's database, recordings or tasks. Each environment has its own permission
boundary for the roles its stack creates. The image repositories are shared and live here
(`images.tf`): release images (tagged `v...`) never expire.

**Applied by a person, not CI.** If CI could apply this stack, the apply role could grant
itself anything. Its state sits at `bootstrap/v2.tfstate`, outside the `v2/` prefix
both roles can reach.

Permissions grow one PR at a time: a PR that adds a new resource type to a v2 stack
also adds the actions it needs here, so the roles never hold more than the stack uses.

```bash
make test-infra                                # offline tests, no AWS
cd infra/v2/bootstrap && terraform init && terraform plan -out=bootstrap.tfplan
terraform apply bootstrap.tfplan               # admin credentials, after review
```

### Adopting the image repositories (once, 2026-10-06)

They were created by staging's stack, which now forgets them (`removed`, nothing deleted).
Before the first apply that contains `images.tf`, adopt them into this state:

```bash
for r in meet agent-runner; do
  terraform import "aws_ecr_repository.this[\"$r\"]" "meetlab-v2/$r"
  terraform import "aws_ecr_lifecycle_policy.this[\"$r\"]" "meetlab-v2/$r"
done
terraform plan   # the repositories: no change; their lifecycle policies: updated in place
```
