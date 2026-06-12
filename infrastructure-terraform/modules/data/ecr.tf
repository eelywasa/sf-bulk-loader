# ECR repository for the backend image. Operators mirror the published GHCR
# image here between applying the data layer and the backend layer - the ECS
# service fails its first task launch with image-not-found otherwise (same
# first-deploy ordering as CDK, SFBL-276).

resource "aws_ecr_repository" "backend" {
  name = "bulk-loader-backend-${var.env_name}"

  # Mirror of the CDK autoDeleteImages-on-DESTROY behaviour: a disposable
  # environment can be torn down even though the first-deploy runbook pushed
  # images here; protected environments refuse to delete a non-empty repo.
  force_delete = !var.protect_data

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
