# infra/stt-nim — Parakeet NIM (concurrent STT) on a g6

Terraform for the GPU box that runs NVIDIA's **Parakeet-TDT NIM** (Triton dynamic batching),
replacing the single-threaded Shadowfita sidecar. Applied via GitHub Actions
(`.github/workflows/deploy-stt-nim.yml`) — **not** hand-CLI. Full context: `docs/gpu-stt-deployment.md`.

## What it creates
- `g6.xlarge` (L4, 24 GB, CC 8.9 — NIM needs ≥ 8.0) from the AWS Deep Learning Base GPU AMI.
- Security group opening `:9000` (HTTP) / `:50051` (gRPC) **only to the agent-runner EB SG**.
- Instance profile: SSM Managed Core + read of the NGC key SSM param.
- `user-data.sh` → a `nim-stt` systemd unit that runs the NIM container (auto-restart, model cache).
- A Route53 **private** A record (`stt-nim.vivaprox.internal`) → stable `NEMOTRON_STT_URL`.

## One-time bootstrap (you)
1. **NGC key** → GitHub Actions secret `NGC_API_KEY`. The workflow copies it to SSM SecureString
   (`/meetlab/stt-nim/ngc_api_key`) before apply.
2. **TF state backend** → an S3 bucket (repo **var** `TF_STATE_BUCKET`). State locking is
   S3-native (`use_lockfile`, Terraform ≥ 1.10) — no DynamoDB table needed.
3. **Network ids** → repo **vars** `STT_VPC_ID`, `STT_SUBNET_ID`, `AGENT_RUNNER_SG_ID`,
   `PRIVATE_ZONE_ID` (the vivaprox VPC/subnet, the agent-runner EB security group, the private zone).
4. **IAM/OIDC** → attach [`actions-role-policy.json`](actions-role-policy.json) to the Actions
   role (`AWS_ROLE_NAME`): EC2 + IAM (instance profile) + SSM + S3/DynamoDB (state).
   e.g. `aws iam put-role-policy --role-name <AWS_ROLE_NAME> --policy-name meetlab-stt-nim --policy-document file://actions-role-policy.json`
5. Confirm **NVIDIA AI Enterprise** licensing for prod NIM use.

## Deploy
- GitHub → Actions → **Deploy STT NIM** → `workflow_dispatch` (`plan` to preview, `apply` to run),
  or it applies on push to `main` touching `infra/stt-nim/**`.
- Health is checked on the box via SSM (`curl localhost:9000/v1/health/ready`) — the runner can't
  reach the private IP.

## Local plan (optional)
```
terraform init -backend-config="bucket=<b>" -backend-config="region=us-east-1" -backend-config="dynamodb_table=<t>"
terraform plan -var="vpc_id=<>" -var="subnet_id=<>" -var="agent_runner_sg_id=<>" -var="private_zone_id=<>"
```

## Cutover / rollback
- Cut over: apply → NIM healthy → merge the `agent-runner/.ebextensions/stt.config` flip
  (`NEMOTRON_STT_API=openai`) → confirm via `make bench-stt-concurrency STT_API=openai` + soak.
- Rollback: revert the `.ebextensions` change (agent-runner CD redeploys `shadowfita`) — instant.
- Decommission old T4 (`meetlab-stt-gpu`, not in TF): stop, then terminate once confident.
