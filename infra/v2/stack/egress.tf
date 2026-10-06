# Video egress (iteration 8b): LiveKit Cloud uploads from its own servers, so it needs
# a key (assume-role is Enterprise-only and still needs one). That key belongs to a
# user that can only add recordings; the key itself is minted by a person into SSM,
# never by Terraform, so it is never in state (docs/v2-deployment.md).

resource "aws_iam_user" "egress" {
  name                 = "${local.name}-egress-writer"
  permissions_boundary = local.boundary
}

resource "aws_iam_user_policy" "egress" {
  name = "add-recordings"
  user = aws_iam_user.egress.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:AbortMultipartUpload"]
      Resource = "${aws_s3_bucket.media.arn}/recordings/*"
    }]
  })
}
