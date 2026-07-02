# AWS Terraform Deployment

This deploys Reading Sound Game to one EC2 Docker host.

Terraform creates:

- EC2 instance
- Security group for HTTP/HTTPS
- IAM role for reading Azure Speech settings from SSM Parameter Store
- Optional Elastic IP

The EC2 boot script installs Docker, clones the repository, writes a local `.env`
from SSM parameters, and runs `docker compose up -d --build`.

It also installs a systemd timer that checks GitHub every few minutes. If the
configured branch has a new commit, the host pulls it and reruns
`docker compose up -d --build`.

## 1. Store Azure Speech settings in SSM

Use your AWS profile/account and region:

```bash
aws ssm put-parameter \
  --region us-west-2 \
  --name /reading-sound-game/AZURE_SPEECH_KEY \
  --type SecureString \
  --value "YOUR_AZURE_SPEECH_KEY" \
  --overwrite

aws ssm put-parameter \
  --region us-west-2 \
  --name /reading-sound-game/AZURE_SPEECH_REGION \
  --type SecureString \
  --value "eastus" \
  --overwrite

aws ssm put-parameter \
  --region us-west-2 \
  --name /reading-sound-game/AZURE_SPEECH_VOICE \
  --type SecureString \
  --value "en-US-BrandonMultilingualNeural" \
  --overwrite
```

Optional tuning:

```bash
aws ssm put-parameter \
  --region us-west-2 \
  --name /reading-sound-game/AZURE_STT_CORRECTNESS_THRESHOLD \
  --type SecureString \
  --value "65" \
  --overwrite
```

## 2. Configure Terraform variables

```bash
cd infra/aws
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`.

Important:

- `repository_url` must point to a repo the EC2 host can clone.
- `site_address` should be a real domain for HTTPS and microphone support.
- For the root domain, use `site_address = "readingsoundgames.com"`.
- For first HTTP-only smoke tests, `site_address = ":80"` is okay, but STT mic
  access will not work in normal deployed browsers without HTTPS.
- `auto_update_interval_minutes = 5` means the server checks GitHub every five
  minutes. Set it to `0` to disable automatic updates.

## 3. Optional S3 backend

Create a Terraform state bucket if you want remote state:

```bash
aws s3api create-bucket \
  --bucket YOUR_UNIQUE_STATE_BUCKET \
  --region us-west-2 \
  --create-bucket-configuration LocationConstraint=us-west-2

aws s3api put-bucket-versioning \
  --bucket YOUR_UNIQUE_STATE_BUCKET \
  --versioning-configuration Status=Enabled
```

Then copy and edit:

```bash
cp backend.tf.example backend.tf
```

## 4. Deploy

```bash
terraform init
terraform fmt -recursive
terraform validate
terraform plan
terraform apply
```

## 5. Check the server

Terraform prints the instance IP/DNS.

If SSH is enabled:

```bash
ssh ec2-user@YOUR_PUBLIC_DNS
sudo docker ps
sudo docker compose -f /opt/reading-sound-game/compose.yaml logs -f
systemctl list-timers reading-sound-game-update.timer
```

If SSM Session Manager is available in your account, you can connect from the AWS
Console without opening SSH.

## 6. Point GoDaddy DNS to AWS

In GoDaddy DNS for `readingsoundgames.com`, update or create:

- Type: `A`
- Name: `@`
- Value: Terraform `public_ip`
- TTL: default or 600 seconds

Optional:

- Type: `CNAME`
- Name: `www`
- Value: `readingsoundgames.com`

If you use `www`, set `site_address` to:

```hcl
site_address = "readingsoundgames.com, www.readingsoundgames.com"
```

## 7. Destroy

```bash
terraform destroy
```

The SSM parameters and optional S3 backend bucket are intentionally not destroyed
by this Terraform project.
