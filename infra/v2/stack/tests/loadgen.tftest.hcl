# Offline: mock provider, no AWS. The load generator (load-test readiness L5): our
# synthetic participants run inside AWS, as a one-off Fargate task the "Load test v2"
# workflow starts, so a run measures staging and not a laptop's network.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "0123abc"
}

run "a_fargate_task_running_the_load_driver_from_the_runner_image" {
  command = apply

  assert {
    condition = (
      aws_ecs_task_definition.loadgen.requires_compatibilities == toset(["FARGATE"]) &&
      aws_ecs_task_definition.loadgen.network_mode == "awsvpc" &&
      jsondecode(aws_ecs_task_definition.loadgen.container_definitions)[0].image == "${data.aws_ecr_repository.this["agent-runner"].repository_url}:0123abc" &&
      jsondecode(aws_ecs_task_definition.loadgen.container_definitions)[0].command == ["python", "tests/load_run.py"]
    )
    error_message = "the deployed commit's own harness, on Fargate (no machines to keep; billed only while a run lasts)"
  }
  assert {
    condition = contains([for e in jsondecode(aws_ecs_task_definition.loadgen.container_definitions)[0].environment : "${e.name}=${e.value}"],
    "MEET_URL=https://meet-staging.wwbp.org")
    error_message = "participants come in the front door, as people do: meet's console and the public LiveKit address"
  }
  assert {
    condition = (
      contains([for s in jsondecode(aws_ecs_task_definition.loadgen.container_definitions)[0].secrets : s.name], "CONSOLE_PASSWORD") &&
      contains([for s in jsondecode(aws_ecs_task_definition.loadgen.container_definitions)[0].secrets : s.name], "LIVEKIT_API_SECRET")
    )
    error_message = "the console password and the LiveKit key in use come from the parameters, never from Terraform"
  }
  assert {
    condition     = contains([for s in jsondecode(aws_ecs_task_definition.loadgen.container_definitions)[0].secrets : s.name], "OPENAI_API_KEY")
    error_message = "the answer judge (agent-runner/judge.py) runs at the end of each load test, with the stored OpenAI key"
  }
  assert {
    condition     = length(aws_security_group.loadgen.ingress) == 0 && aws_security_group.loadgen.name == "meetlab-v2-staging-loadgen"
    error_message = "nothing reaches the load generator, and it is not in the app group (no database); the workflow finds it by name"
  }
  assert {
    condition = (
      jsondecode(aws_iam_role_policy.loadgen_results.policy).Statement[0].Action == "s3:PutObject" &&
      jsondecode(aws_iam_role_policy.loadgen_results.policy).Statement[0].Resource == "${aws_s3_bucket.media.arn}/loadtests/*"
    )
    error_message = "it may only add results under loadtests/"
  }
  assert {
    condition = anytrue([for st in jsondecode(aws_iam_role_policy.loadgen_results.policy).Statement :
    st.Action == "s3:GetObject" && st.Resource == "${aws_s3_bucket.media.arn}/loadtests/library/*"])
    error_message = "it reads the conversation library (agent-runner/conversation_library.py), and nothing else in the bucket"
  }
  assert {
    condition     = anytrue([for st in jsondecode(aws_iam_role_policy.loadgen_results.policy).Statement : st.Action == "s3:ListBucket" && st.Condition.StringLike["s3:prefix"] == "loadtests/library/*"])
    error_message = "it lists only the library's files"
  }
}
