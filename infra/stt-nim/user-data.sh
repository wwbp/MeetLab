#!/usr/bin/env bash
# Bootstraps the Parakeet-TDT NIM on the g6. The Deep Learning Base GPU AMI already has the
# NVIDIA driver, Docker, and the Container Toolkit — so this only wires up NGC auth and a
# systemd unit that runs the NIM container (auto-restart, model cache persisted).
set -euxo pipefail

REGION="${region}"
NGC_PARAM="${ngc_api_key_ssm_param}"

# Ensure the SSM agent is installed + running (the Ubuntu DL AMI doesn't ship it enabled),
# so the deploy workflow can Run-Command this box for rolls/health checks.
snap install amazon-ssm-agent --classic 2>/dev/null || true
systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service 2>/dev/null \
  || systemctl enable --now amazon-ssm-agent 2>/dev/null || true

# NGC key from SSM SecureString (instance profile grants ssm:GetParameter + kms:Decrypt).
NGC_API_KEY="$(aws ssm get-parameter --name "$NGC_PARAM" --with-decryption --region "$REGION" --query Parameter.Value --output text)"

# Authenticate to NVIDIA's registry for the image pull.
echo "$NGC_API_KEY" | docker login nvcr.io --username '$oauthtoken' --password-stdin

mkdir -p /opt/nim/cache

# Root-only env file consumed by the systemd unit.
umask 077
cat > /etc/nim-stt.env <<EOF
NGC_API_KEY=$NGC_API_KEY
NIM_HTTP_API_PORT=9000
NIM_GRPC_API_PORT=50051
EOF
# Only set NIM_TAGS_SELECTOR for non-default profiles (English v2 uses no selector).
%{ if nim_tags_selector != "" ~}
echo "NIM_TAGS_SELECTOR=${nim_tags_selector}" >> /etc/nim-stt.env
%{ endif ~}

cat > /etc/systemd/system/nim-stt.service <<UNIT
[Unit]
Description=Parakeet-TDT NIM (concurrent STT)
After=docker.service
Requires=docker.service

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=0
EnvironmentFile=/etc/nim-stt.env
ExecStartPre=-/usr/bin/docker rm -f nim-stt
ExecStartPre=/usr/bin/docker pull ${nim_image}
ExecStart=/usr/bin/docker run --rm --name nim-stt \\
  --gpus all --shm-size=8GB --ulimit nofile=2048:2048 \\
  -e NGC_API_KEY -e NIM_TAGS_SELECTOR -e NIM_HTTP_API_PORT -e NIM_GRPC_API_PORT \\
  -p 9000:9000 -p 50051:50051 \\
  -v /opt/nim/cache:/opt/nim/.cache \\
  ${nim_image}
ExecStop=/usr/bin/docker stop nim-stt

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now nim-stt.service
