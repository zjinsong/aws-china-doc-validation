from datetime import datetime, timezone
import boto3
from botocore.session import Session


def verify_service_availability(service: str, feature: str, region: str) -> dict:
    """Perform a safe, credential-free service/region availability check for aws-cn.

    IMPORTANT: this reads the *static* botocore endpoint catalog bundled with the
    installed boto3 version. That catalog lags behind real launches, so a service
    that is missing from it may still exist in the region. To avoid false
    negatives, a missing service is reported as ``sdk_catalog_missing`` (not
    ``unavailable``) and the evidence explicitly says this is inconclusive and
    should be confirmed with a live API probe or a What's New announcement.
    """
    available_regions = Session().get_available_regions(service, partition_name="aws-cn")
    service_available = region in available_regions
    if service_available:
        status = "available"
        note = (
            "The service endpoint is listed in the aws-cn catalog for this region. "
            "Feature-level confirmation may still require an API-specific read-only probe."
        )
    else:
        # Not in the static SDK catalog. This is inconclusive, NOT proof of absence.
        status = "sdk_catalog_missing"
        note = (
            "This service/region is not in the static botocore aws-cn catalog bundled "
            "with the installed boto3 version. The catalog lags behind new launches, so "
            "this does NOT prove the service is unavailable. Confirm with a live API probe "
            "or a What's New announcement before concluding it is unavailable."
        )
    return {
        "service": service,
        "feature": feature,
        "region": region,
        "status": status,
        "evidence": {
            "method": "botocore service catalog (aws-cn partition, static SDK data)",
            "available_regions": available_regions,
            "inconclusive_if_missing": True,
            "note": note,
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def caller_identity(region: str) -> dict:
    """Optional read-only credential validation; never creates or modifies AWS resources."""
    client = boto3.client("sts", region_name=region)
    return client.get_caller_identity()
