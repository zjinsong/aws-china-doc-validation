from datetime import datetime, timezone
import boto3
from botocore.session import Session


def verify_service_availability(service: str, feature: str, region: str) -> dict:
    """Perform a safe, credential-free service/region availability check for aws-cn."""
    available_regions = Session().get_available_regions(service, partition_name="aws-cn")
    service_available = region in available_regions
    status = "available" if service_available else "not_listed"
    return {
        "service": service,
        "feature": feature,
        "region": region,
        "status": status,
        "evidence": {
            "method": "botocore service catalog (aws-cn partition)",
            "available_regions": available_regions,
            "note": "This validates service endpoint availability. Feature-level confirmation may require an API-specific read-only probe or manual verification.",
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def caller_identity(region: str) -> dict:
    """Optional read-only credential validation; never creates or modifies AWS resources."""
    client = boto3.client("sts", region_name=region)
    return client.get_caller_identity()
