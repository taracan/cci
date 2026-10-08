# AWS Secret Manager Setup Guide

This document describes how to configure the application to use AWS Secret Manager for configuration management.

## Overview

The application supports loading configuration from multiple sources in this priority order:

1. **AWS Secret Manager** (highest priority - if configured)
2. **Environment Variables**
3. **Hardcoded Defaults** (lowest priority)

This allows you to securely store sensitive configuration (like database passwords, secret keys) in AWS Secret Manager while keeping non-sensitive configuration in environment variables or defaults.

## Required Configuration

The following configuration values are **required** and must be provided in either AWS Secret Manager or environment variables:

| Key | Description | Example |
|-----|-------------|---------|
| `SECRET_KEY` | Application secret key for Flask sessions | `a1b2c3d4e5f6...` |
| `ADMIN_KEY` | Admin authentication key | `admin-key-123` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+psycopg2://user:pass@host:5432/db` |

The application will **fail to start** if any of these values are missing from both AWS Secret Manager and environment variables.

## Prerequisites

1. **AWS Account** - You should have an AWS account with access to AWS Secret Manager
2. **IAM Permissions** - Your AWS credentials need the following permissions:
   - `secretsmanager:GetSecretValue`
3. **AWS CLI or SDK configured** - Run `aws configure` to set up your credentials

## Step-by-Step Setup

### Step 1: Create Your Secret in AWS Secret Manager

1. Go to [AWS Secret Manager Console](https://console.aws.amazon.com/secretsmanager/)
2. Click "Store a new secret"
3. Select "Other type of secrets" (for JSON)
4. In the "Key/value pairs" section, add your configuration as JSON:

```json
{
  "SECRET_KEY": "your-secure-secret-key-here",
  "ADMIN_KEY": "your-secure-admin-key-here",
  "DATABASE_URL": "postgresql+psycopg2://username:password@host:5432/database_name",
  "LLM_PROVIDER": "ollama",
  "OLLAMA_BASE_URL": "http://localhost:11434",
  "DEFAULT_LLM_MODEL": "llama3.2:latest",
  "LLM_TIMEOUT_SECS": 120,
  "LLM_TEMPERATURE": 0.2,
  "MATCH_TOP_K": 10,
  "QUEUE_MAX_CONCURRENT": 2,
  "QUEUE_TTL_SECS": 180,
  "LOG_DIR": "logs",
  "LOG_LEVEL": "INFO",
  "LOG_TO_CLOUDWATCH": false
}
```

5. Name your secret (e.g., `resume-jd-portal/config`)
6. Click "Next" and follow the prompts to complete creation

### Step 2: Configure Environment Variables

Set these environment variables to point to your secret:

```bash
# AWS Configuration (for AWS Secret Manager)
AWS_SECRET_NAME=resume-jd-portal/config
AWS_REGION=us-east-1

# Optional: Override specific values via environment variables
# DATABASE_URL=postgresql+psycopg2://user:password@host:5432/database_name
```

### Step 3: Install boto3

Ensure `boto3` is installed:

```bash
pip install boto3
```

### Step 4: Test Your Setup

```bash
# Set your AWS credentials (if not already configured)
# Using AWS CLI: aws configure
# Or set environment variables:
set AWS_ACCESS_KEY_ID=your-access-key
set AWS_SECRET_ACCESS_KEY=your-secret-key

# Set your secret configuration
set AWS_SECRET_NAME=resume-jd-portal/config
set AWS_REGION=us-east-1

# Run the application
python main.py
```

## Fallback Behavior

AWS Secret Manager is **opt-in**: it is only contacted when **both** `AWS_SECRET_NAME` and a region (`AWS_REGION` or `AWS_DEFAULT_REGION`) are set. There is no implicit secret name or region.

### When AWS Secret Manager is NOT configured:
1. AWS is never contacted (no network calls, no credential lookup)
2. The application reads configuration from environment variables
3. If required values (SECRET_KEY, ADMIN_KEY, DATABASE_URL) are missing → **Application fails to start with clear error**
4. Optional values use their defaults

### When AWS Secret Manager is configured but unavailable:
1. The application attempts to fetch from AWS
2. Any AWS error (secret not found, access denied, no/expired credentials, network error, invalid JSON) is logged as a warning — it does **not** crash the application
3. It falls back to environment variables, then defaults

### When neither AWS nor environment variables are available:
1. Required values (SECRET_KEY, ADMIN_KEY, DATABASE_URL) → **Application fails with error**
2. Optional values use their defaults

## Security Considerations

1. **Use IAM Roles** - Instead of access keys, use IAM roles when running on AWS services (EC2, Lambda, ECS, etc.)
2. **Enable Secret Rotation** - Configure automatic rotation for sensitive secrets
3. **Use KMS Encryption** - Ensure your secrets are encrypted with AWS KMS
4. **Least Privilege** - Grant only necessary permissions to your IAM roles/users
5. **Audit Access** - Enable CloudTrail to audit secret access

## Troubleshooting

### Application fails to start with "Required configuration 'X' is missing"

**Error**: `ValueError: Required configuration 'SECRET_KEY' is missing.`

**Solution**:
- Make sure the secret exists in AWS Secret Manager with the correct name
- Or set the environment variable: `set SECRET_KEY=your-secret-key`
- Or add the value to your environment in AWS Secret Manager

> AWS errors below are **logged as warnings** (logger `aws_secret_manager`) and the app falls back to environment variables/defaults. If values are not coming from AWS, check the logs for these warnings.

### "AWS credentials not found"

**Warning**: `AWS Secrets Manager unavailable (NoCredentialsError); falling back to environment/defaults`

**Solution**:
- Run `aws configure` to set up your credentials
- Or set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` environment variables

### "Secret not found"

**Warning**: `AWS Secrets Manager: could not read secret 'resume-jd-portal/config' (ResourceNotFoundException); falling back to environment/defaults`

**Solution**:
- Verify the secret name matches exactly (case-sensitive)
- Check that your AWS region is correct
- Ensure your IAM role has permission to access the secret

### "Json parsing failed"

**Warning**: `AWS Secrets Manager: secret '...' could not be parsed; falling back to environment/defaults`

**Solution**:
- Ensure your secret contains valid JSON
- Check for trailing commas or invalid characters

## Migration from Environment Variables

If you're currently using environment variables and want to migrate to AWS Secret Manager:

1. Copy your current environment variable values to the AWS Secret Manager JSON
2. Set `AWS_SECRET_NAME` and `AWS_REGION` environment variables
3. Remove the corresponding environment variables (optional - env vars still work as fallback)
4. Restart the application

## Benefits of AWS Secret Manager

1. **Secure Storage** - Secrets are encrypted at rest and in transit
2. **Access Control** - Fine-grained IAM policies for who can access what
3. **Audit Logging** - CloudTrail integration for tracking secret access
4. **Rotation** - Automatic secret rotation support
5. **Versioning** - Track changes to your secrets over time

## Testing the Configuration

You can test the configuration loader directly:

```python
from config_loader import Config, config_get

# Test with AWS (requires AWS_SECRET_NAME and AWS_REGION to be set)
config = Config()
print(config.get("SECRET_KEY"))  # Returns secret from AWS or env

# Test required values
try:
    secret = config.get_required("DATABASE_URL")
    print(f"Database URL: {secret}")
except ValueError as e:
    print(f"Error: {e}")

# Test non-strict mode
config = Config(strict_mode=False)
print(config.get("MISSING_KEY", default="fallback"))  # Returns "fallback"
```