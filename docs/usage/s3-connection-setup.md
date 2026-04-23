---
title: S3 connection setup
slug: s3-connection-setup
nav_order: 85
tags: [s3, connections, aws, setup]
required_permission: connections.manage
summary: >-
  AWS-side IAM user, policy, and bucket setup plus the in-app steps for
  creating an S3-backed input or output connection.
---

# S3 Connection Setup Guide

## What this covers / who should read this

Everything needed to configure an S3 bucket for use with the Bulk Loader —
the AWS-side IAM user / policy / bucket setup, and the steps for creating the
connection in the UI. Read this before creating an S3-backed input or output
connection.

Connections can be configured for three purposes, controlled by the **Direction** field:

| Direction | Used for | Permissions required |
|---|---|---|
| **Input** | Reading source CSV files | `s3:ListBucket`, `s3:GetObject` |
| **Output** | Writing result CSVs (successes, errors, unprocessed) | `s3:ListBucket`, `s3:PutObject`, `s3:DeleteObject` |
| **Both** | Reading source CSVs and writing results | All of the above |

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

Choose the policy template that matches the connection's intended direction. Replace `my-bucket` and `data/` with your actual bucket name and root prefix.

The object-level resource must cover the root prefix **and all subdirectories beneath it** — use a single wildcard at the end of the prefix (`data/*`). If you scope it too narrowly (e.g. `data/csvs/*`), files in other subdirectories will be denied.

#### Input only (`direction: in`)

Read access to list and download objects:

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

#### Output only (`direction: out`)

Write access to upload and clean up result CSVs. The `s3:ListBucket` permission is used by the connection test to verify basic bucket access before attempting a write.

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
      "Sid": "AllowObjectWrite",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::my-bucket/data/*"
    }
  ]
}
```

> **Why `s3:DeleteObject`?** The connection test writes a small probe file (`.sfbl-write-test`) to verify write access, then immediately deletes it. Without `s3:DeleteObject` the test will fail even if `s3:PutObject` works, and the probe file will be left in the bucket.

#### Input and Output (`direction: both`)

Full read and write access, combining both statement sets above:

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
    },
    {
      "Sid": "AllowObjectWrite",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::my-bucket/data/*"
    }
  ]
}
```

If you do not use a root prefix (the connection reads/writes from the bucket root), remove the `Condition` block from `AllowBucketListing` and change the object-level resource to `arn:aws:s3:::my-bucket/*`.

### 3. Bucket Configuration

No special bucket configuration is required beyond normal access controls. Confirm the following:

- **Block Public Access** — leave enabled; the application uses IAM credentials, not public URLs
- **Encryption** — any encryption setting (SSE-S3, SSE-KMS) is supported; the IAM user will need `kms:Decrypt` permission for reads and `kms:GenerateDataKey` for writes if using a customer-managed KMS key
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

Choose the policy document that matches your intended direction and save it to a local file.

**Input only:**

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

**Output only:**

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
      "Sid": "AllowObjectWrite",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::my-bucket/data/*"
    }
  ]
}
EOF
```

**Both (input and output):**

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
    },
    {
      "Sid": "AllowObjectWrite",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:DeleteObject"
      ],
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

### 4. Create an Access Key

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

### 5. Verify the Policy (Optional)

Confirm the policy is attached and test access manually:

```bash
# Check attached policies
aws iam list-attached-user-policies --user-name sf-bulk-loader-s3

# Test listing (input / both)
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE \
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY \
aws s3 ls s3://my-bucket/data/ --region us-east-1

# Test writing (output / both)
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE \
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY \
aws s3 cp /dev/null s3://my-bucket/data/.write-test --region us-east-1 && \
aws s3 rm s3://my-bucket/data/.write-test --region us-east-1
```

### Bucket-Root Variant (No Prefix)

If you are not using a root prefix, omit the `Condition` block from `AllowBucketListing` and change all object-level resources to `arn:aws:s3:::my-bucket/*`.

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
2. Under the **Storage Connections** section, click **New Storage Connection**
3. Fill in the form:

| Field | Required | Description |
|---|---|---|
| **Name** | Yes | A display name for this connection, e.g. `Production S3` |
| **Direction** | Yes | `Input` — source CSVs only; `Output` — result CSVs only; `Both` — source and result CSVs |
| **Bucket** | Yes | The S3 bucket name, e.g. `my-data-bucket` |
| **Region** | No | The AWS region, e.g. `us-east-1`. Recommended — without it boto3 will attempt to detect the region automatically |
| **Root Prefix** | No | A path prefix within the bucket to treat as the root, e.g. `data/csvs/`. Use this to limit the app's visible scope to a subfolder |
| **Access Key ID** | Yes | The IAM access key ID (`AKIA...`) |
| **Secret Access Key** | Yes | The IAM secret access key |
| **Session Token** | No | Only required when using temporary STS credentials |

4. Click **Save**, then click **Test** to verify the connection:
   - For **Input** connections the test performs a `ListObjectsV2` call to confirm read access
   - For **Output** and **Both** connections the test additionally performs a `PutObject` and `DeleteObject` on a temporary probe file to confirm write access — the probe file is immediately removed on success

---

## Editing Credentials

When editing an existing connection, the credential fields (Access Key ID, Secret Access Key, Session Token) are blank by default. Leave them blank to keep the existing stored values. Only fill them in if you want to replace the credentials.

---

## Security Notes

- All credentials are encrypted at rest using Fernet symmetric encryption. The encryption key is set via the `ENCRYPTION_KEY` environment variable on the server — ensure this is kept secret and backed up
- Credentials are never returned in API responses; the connection list and detail endpoints omit all secret fields
- Use a dedicated IAM user per environment (dev/staging/prod) so credentials can be rotated or revoked independently
- Scope the IAM policy to the specific bucket and prefix your load plans actually need — avoid `s3:*` or wildcard resources
- For output connections, `s3:DeleteObject` is required in addition to `s3:PutObject`. The application uses it to clean up the write-test probe during connection testing; it does not delete result files after writing them

---

## Related

- [Output sinks](output-sinks.md) — picking between local and S3 output
- [Authoring load plans](load-plans.md) — attaching a connection to a plan
