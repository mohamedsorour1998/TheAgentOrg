# Remote state in S3 so the whole team shares one source of truth.
#
# SOROUR: create this bucket once (it must exist before `terraform init`):
#   aws s3 mb s3://theagentorg-shared-terraform-backend --region us-east-1
#   aws s3api put-bucket-versioning --bucket theagentorg-shared-terraform-backend \
#     --versioning-configuration Status=Enabled
#
# Everyone else never touches AWS state — they run the pipeline locally on stubs.

terraform {
  backend "s3" {
    bucket = "theagentorg-shared-terraform-backend"
    key    = "terraform.tfstate"
    region = "us-east-1"
  }
}
