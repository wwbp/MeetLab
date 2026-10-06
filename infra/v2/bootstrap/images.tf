# The two image repositories, shared by staging and production and outliving both, so they
# live here rather than in either environment's stack. Adopted from staging's stack once,
# by hand (README.md, "Adopting the image repositories"); the stack forgets them without
# deleting them (its `removed` blocks).
#
# Tags are git SHAs and immutable. Production runs a release's image, which the production
# deploy tags v2.x.y; release images never expire, other builds keep the last 30.

resource "aws_ecr_repository" "this" {
  for_each             = toset(["meet", "agent-runner"])
  name                 = "meetlab-v2/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        # An image matched by a higher-priority rule is never expired by a lower one, so
        # this rule (which never fires) is what keeps release images.
        rulePriority = 1
        description  = "keep every release image (tagged v...)"
        selection    = { tagStatus = "tagged", tagPrefixList = ["v"], countType = "imageCountMoreThan", countNumber = 10000 }
        action       = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "keep the last 30 other images"
        selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
        action       = { type = "expire" }
      },
    ]
  })
}

output "ecr_repository_urls" {
  value = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}
