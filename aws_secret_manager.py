"""AWS Secret Manager integration helper.

This module provides functions to fetch secrets from AWS Secret Manager.
If AWS is not configured or unavailable, it falls back gracefully.
"""

import os
import json
import logging
from typing import Optional, Dict, Any


# Try to import boto3, but don't fail if not available
try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    BOTO3_AVAILABLE = True
except ModuleNotFoundError:
    boto3 = None
    BotoCoreError = None
    ClientError = None
    BOTO3_AVAILABLE = False

logger = logging.getLogger(__name__)

# Recommended secret name (used in docs/examples). AWS is only contacted when
# AWS_SECRET_NAME is explicitly set — this constant is never used implicitly.
DEFAULT_SECRET_NAME = "resume-jd-portal/config"


def configured_secret_name() -> Optional[str]:
    """Return AWS_SECRET_NAME if explicitly set, else None (AWS disabled)."""
    return os.getenv("AWS_SECRET_NAME") or None


def get_secret(secret_name: str, region_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch a secret from AWS Secret Manager.
    
    Args:
        secret_name: Name of the secret in AWS Secret Manager
        region_name: AWS region (defaults to AWS_REGION / AWS_DEFAULT_REGION env var)

    Returns:
        Parsed secret dictionary, or None if AWS is not configured, unreachable,
        or the secret cannot be read. Never raises for AWS errors.
    """
    # Check if boto3 is available
    if not BOTO3_AVAILABLE:
        return None

    # No implicit region: without an explicit region, AWS is not contacted.
    region_name = region_name or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if not secret_name or not region_name:
        return None

    try:
        # Create AWS client
        session = boto3.Session()
        client = session.client(service_name="secretsmanager", region_name=region_name)
        
        # Try to get the secret
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
        
        # Decrypts secret using the associated KMS key.
        secret = get_secret_value_response["SecretString"]
        return json.loads(secret)
        
    except ClientError as e:
        # e.g. ResourceNotFoundException / AccessDeniedException. Log the error
        # code only (never secret contents) and fall back to env/defaults.
        code = e.response.get("Error", {}).get("Code", "Unknown")
        logger.warning("AWS Secrets Manager: could not read secret %r (%s); "
                       "falling back to environment/defaults", secret_name, code)
        return None
    except BotoCoreError as e:
        # No credentials, expired SSO token, network/endpoint errors, etc.
        logger.warning("AWS Secrets Manager unavailable (%s); "
                       "falling back to environment/defaults", type(e).__name__)
        return None
    except Exception:
        # Any other error (including json parsing)
        logger.warning("AWS Secrets Manager: secret %r could not be parsed; "
                       "falling back to environment/defaults", secret_name)
        return None


def get_secret_string(secret_name: str, region_name: Optional[str] = None, key: str = "") -> Optional[str]:
    """Fetch a string secret or a specific key from AWS Secret Manager.
    
    Args:
        secret_name: Name of the secret in AWS Secret Manager
        region_name: AWS region (defaults to AWS_REGION env var or None)
        key: Optional key to extract from JSON secret
    
    Returns:
        Secret value as string, or None if not found
    """
    secret = get_secret(secret_name, region_name)
    
    if secret is None:
        return None
    
    if key:
        return secret.get(key)
    
    # If secret is a string (not JSON), return it directly
    if isinstance(secret, str):
        return secret
    
    # If secret is a dict but no key specified, return as JSON string
    return json.dumps(secret)
