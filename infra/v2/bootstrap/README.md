# infra/v2/bootstrap — the roles CI uses

Two IAM roles for GitHub Actions, scoped to this repo only (the account is shared with
other lab projects, and the old `github-actions-service-acc` trusts every `wwbp/*` repo).

| Role | Assumable from | Can do |
|---|---|---|
| `meetlab-v2-tf-plan` | PRs in `wwbp/MeetLab`, pushes to `v2` | Read what the stack manages; state under `v2/` |
| `meetlab-v2-tf-apply` | The `staging` GitHub environment only (reviewer-gated, `v2` branch only) | Change what the stack manages; state under `v2/` |

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
