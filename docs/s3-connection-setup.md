# S3 Input Connection Setup Guide

This guide covers everything needed to configure an S3 bucket for use with the Salesforce Bulk Loader, including the required AWS-side setup and how to create the connection in the UI.

---

## AWS Setup

You can complete the AWS setup either through the **AWS Console** (steps below) or the **AWS CLI** (see [AWS CLI Setup](#aws-cli-setup)).

### 1. Create an IAM User

The application authenticates using static IAM credentials (access key + secret). Create a dedicated IAM user with the minimum permissions required.

1. Go to **IAM → Users → Create user**
2. Give it a descriptive name, e.g. `sf-bulk-loader-s3`
3. Select **Attach policies directly** and create a custom inline policy (see below)
4. After creation, go to **Security credentials → Create access key**
5. Choose **Application running outside AWS**, then save the Access Key ID and Secret Access Key — you will need these when creating the connection in the app

### 2. Create an IAM Policy

The application only needs to list and read objects — it never writes to S3. Attach the following least-privilege policy to the IAM user, replacing `my-bucket` and `data/` with your actual bucket name and root prefix.

The `GetObject` resource must cover the root prefix **and all subdirectories beneath it** — use a single wildcard at the end of the prefix (`data/*`), not a deeper path. If you scope it too narrowly (e.g. `data/csvs/*`), files in other subdirectories (e.g. `data/10K/Account.csv`) will be denied.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowBucketListing",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::my-bucket",
      "Condition": {
        "StringLike": {
          "s3:prefix": ["data/*"]
        }
      }
    },
    {
      "Sid": "AllowObjectRead",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::my-bucket/data/*"
    }
  ]
}
```

If you do not use a root prefix (the connection will read from the bucket root), remove the `Condition` block from the `AllowBucketListing` statement and set the `AllowObjectRead` resource to `arn:aws:s3:::my-bucket/*`.

### 3. Bucket Configuration

No special bucket configuration is required beyond normal access controls. Confirm the following:

- **Block Public Access** — leave enabled; the application uses IAM credentials, not public URLs
- **Encryption** — any encryption setting (SSE-S3, SSE-KMS) is supported; the IAM user will need `kms:Decrypt` permission if using a customer-managed KMS key
- **Versioning** — the application always reads the latest version; versioning can be on or off
- **Bucket region** — note the region (e.g. `us-east-1`); you will enter this when creating the connection

### 4. Using Temporary Credentials (Optional)

If your organisation uses AWS STS (e.g. via IAM Identity Center or an assumed role), you can provide a session token instead of long-lived static credentials. The session token field is optional and only needed for temporary credentials.

Note that temporary credentials expire. The connection will fail once they expire and you will need to update it with new credentials.

---

## AWS CLI Setup

The following commands replicate the console steps above. Replace the placeholder values (`my-bucket`, `data/`, `us-east-1`) with your own before running.

**Prerequisites:** the [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) installed and configured with credentials that have IAM write permissions.

### 1. Create the S3 Bucket

Skip this step if the bucket already exists.

```bash
# us-east-1 does not use --create-bucket-configuration
aws s3api create-bucket \
  --bucket my-bucket \
  --region us-east-1

# All other regions require LocationConstraint
aws s3api create-bucket \
  --bucket my-bucket \
  --region ap-southeast-2 \
  --create-bucket-configuration LocationConstraint=ap-southeast-2
```

Block public access (recommended):

```bash
aws s3api put-public-access-block \
  --bucket my-bucket \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

### 2. Create the IAM User

```bash
aws iam create-user --user-name sf-bulk-loader-s3
```

### 3. Create and Attach the IAM Policy

Save the policy document to a local file:

```bash
cat > sf-bulk-loader-s3-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowBucketListing",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::my-bucket",
      "Condition": {
        "StringLike": {
          "s3:prefix": ["data/*"]
        }
      }
    },
    {
      "Sid": "AllowObjectRead",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::my-bucket/data/*"
    }
  ]
}
EOF
```

Create the policy in AWS and capture its ARN:

```bash
POLICY_ARN=$(aws iam create-policy \
  --policy-name sf-bulk-loader-s3-policy \
  --policy-document file://sf-bulk-loader-s3-policy.json \
  --query 'Policy.Arn' \
  --output text)

echo "Policy ARN: $POLICY_ARN"
```

Attach the policy to the user:

```bash
aws iam attach-user-policy \
  --user-name sf-bulk-loader-s3 \
  --policy-arn "$POLICY_ARN"
```

### 3. Create an Access Key

```bash
aws iam create-access-key --user-name sf-bulk-loader-s3
```

This returns JSON containing `AccessKeyId` and `SecretAccessKey`. Copy both values — the secret is only shown once.

```json
{
    "AccessKey": {
        "UserName": "sf-bulk-loader-s3",
        "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",
        "Status": "Active",
        "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "CreateDate": "2026-01-01T00:00:00Z"
    }
}
```

### 4. Verify the Policy (Optional)

Confirm the policy is attached and test that listing objects works:

```bash
# Check attached policies
aws iam list-attached-user-policies --user-name sf-bulk-loader-s3

# Test listing using the new credentials (replace key values)
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE \
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY \
aws s3 ls s3://my-bucket/data/csvs/ --region us-east-1
```

### Bucket-Root Variant (No Prefix)

If you are not using a root prefix, use this policy document instead (omits the `Condition` block and grants access to all objects):

```bash
cat > sf-bulk-loader-s3-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowBucketListing",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::my-bucket"
    },
    {
      "Sid": "AllowObjectRead",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::my-bucket/*"
    }
  ]
}
EOF
```

### Cleanup (If Needed)

To remove everything created above:

```bash
aws iam detach-user-policy \
  --user-name sf-bulk-loader-s3 \
  --policy-arn "$POLICY_ARN"

aws iam delete-access-key \
  --user-name sf-bulk-loader-s3 \
  --access-key-id AKIAIOSFODNN7EXAMPLE

aws iam delete-user --user-name sf-bulk-loader-s3
aws iam delete-policy --policy-arn "$POLICY_ARN"
```

---

## Creating the Connection in the App

1. Navigate to **Connections** in the sidebar
2. Under the **Input Connections** section, click **New Input Connection**
3. Fill in the form:

| Field | Required | Description |
|---|---|---|
| **Name** | Yes | A display name for this connection, e.g. `Production S3` |
| **Bucket** | Yes | The S3 bucket name, e.g. `my-data-bucket` |
| **Region** | No | The AWS region, e.g. `us-east-1`. Recommended — without it boto3 will attempt to detect the region automatically |
| **Root Prefix** | No | A path prefix within the bucket to treat as the root, e.g. `data/csvs/`. Use this to limit the app's visible scope to a subfolder |
| **Access Key ID** | Yes | The IAM access key ID (`AKIA...`) |
| **Secret Access Key** | Yes | The IAM secret access key |
| **Session Token** | No | Only required when using temporary STS credentials |

4. Click **Save**, then click **Test** to verify the connection. The test performs a lightweight `ListObjectsV2` call against the bucket (scoped to the root prefix if set) and reports success or a specific error (e.g. `AccessDenied`, `NoSuchBucket`, `InvalidClientTokenId`)

---

## Editing Credentials

When editing an existing connection, the credential fields (Access Key ID, Secret Access Key, Session Token) are blank by default. Leave them blank to keep the existing stored values. Only fill them in if you want to replace the credentials.

---

## Security Notes

- All credentials are encrypted at rest using Fernet symmetric encryption. The encryption key is set via the `ENCRYPTION_KEY` environment variable on the server — ensure this is kept secret and backed up
- Credentials are never returned in API responses; the connection list and detail endpoints omit all secret fields
- Use a dedicated IAM user per environment (dev/staging/prod) so credentials can be rotated or revoked independently
- Scope the IAM policy to the specific bucket and prefix your load plans actually need — avoid `s3:*` or wildcard resources
