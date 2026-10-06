# Video egress (iteration 8b): LiveKit Cloud uploads from its own servers, so it needs
# a key (assume-role is Enterprise-only and still needs one). That key belongs to a
# user that can only add recordings; the key itself is minted by a person into SSM,
# never by Terraform, so it is never in state.

mock_provider "aws" {
  source = "./tests/mocks"
}

variables {
  image_tag = "0123abc"
}

run "the_egress_user_can_only_add_recordings" {
  command = apply

  assert {
    condition     = startswith(aws_iam_user.egress.name, "meetlab-v2-staging-") && aws_iam_user.egress.permissions_boundary == "arn:aws:iam::123456789012:policy/meetlab-v2-boundary"
    error_message = "named for staging and capped by the boundary, like every CI-created principal"
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_user_policy.egress.policy).Statement :
      toset(flatten([s.Action])) == toset(["s3:PutObject", "s3:AbortMultipartUpload"]) && flatten([s.Resource]) == ["${aws_s3_bucket.media.arn}/recordings/*"]
    ])
    error_message = "add files under recordings/ (multipart included), nothing else: no read, list, delete or account-wide actions (v1's key had all of those)"
  }
}
