# Offline: mock provider, no AWS. The v1 -> v2 data copy task (migrate.tf).

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "0123abc"
}

run "the_copy_runs_first_and_the_check_only_after_it_succeeds" {
  command = apply

  assert {
    condition     = [for c in jsondecode(aws_ecs_task_definition.migration.container_definitions) : c.name] == ["copy", "migrate"]
    error_message = "two steps: copy (pg_dump | pg_restore), then migrate (upgrade, prune, check)"
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.migration.container_definitions)[1].dependsOn == [{ containerName = "copy", condition = "SUCCESS" }]
    error_message = "the check runs only on a copy that completed"
  }
  assert {
    condition     = strcontains(jsondecode(aws_ecs_task_definition.migration.container_definitions)[1].command[2], "--real --prune")
    error_message = "the check removes our programmatic runs and compares every other row (migration_check.py)"
  }
}

run "it_replaces_its_own_database_only_when_told_to" {
  command = apply

  assert {
    condition     = strcontains(jsondecode(aws_ecs_task_definition.migration.container_definitions)[0].command[2], "CONFIRM")
    error_message = "TARGET_DB = this environment's database needs CONFIRM=replace-<name>"
  }
}

run "it_needs_no_aws_permissions_and_holds_no_secret_in_the_definition" {
  command = apply

  assert {
    condition     = aws_ecs_task_definition.migration.task_role_arn == null
    error_message = "the copy makes no AWS calls: no task role"
  }
  assert {
    condition = alltrue([for c in jsondecode(aws_ecs_task_definition.migration.container_definitions) :
    contains([for s in c.secrets : s.name], "SOURCE_URL")])
    error_message = "the source (a restored copy of v1) comes from SSM, never the definition"
  }
}

run "a_new_revision_never_deregisters_the_old" {
  command = plan

  assert {
    condition     = aws_ecs_task_definition.migration.skip_destroy == true
    error_message = "CI may not deregister task definitions (#230's staging apply failed on it): keep old revisions, like every task here"
  }
}
