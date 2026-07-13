import os

from fastmcp import FastMCP

from .auditor import audit_document
from .config import get_settings
from .document_verifier import DocumentFeatureVerifier, DocumentVerificationError
from .evidence_chain import verify_feature_evidence_chain
from .feedback import AwsDocsFeedbackSubmitter, FeedbackSubmissionError
from .knowledge_base import KnowledgeBase
from .verifier import verify_service_availability

settings = get_settings()
knowledge_base = KnowledgeBase(settings.database_path)
mcp = FastMCP("China Doc TruthKeeper")


@mcp.tool()
def query_knowledge_base(query: str, limit: int = 10) -> list[dict]:
    """Search cached AWS China service and feature availability checks."""
    return knowledge_base.search(query, limit)


@mcp.tool()
def verify_feature(service: str, feature: str, region: str | None = None) -> dict:
    """Verify only service endpoint availability using the aws-cn service catalog."""
    result = verify_service_availability(service, feature, region or settings.aws_region)
    return knowledge_base.save_check(service, feature, result["region"], result["status"], result["evidence"])


@mcp.tool()
def verify_document_feature(
    documentation_url: str,
    service: str,
    feature: str,
    region: str | None = None,
    resource_name: str | None = None,
) -> dict:
    """Verify a documented feature with safe List/Describe calls against an AWS China endpoint.

    Set resource_name to opt in to a resource-scoped read-only probe for features whose
    control-plane API needs one identifier (e.g. an S3 bucket name for Intelligent-Tiering,
    or a DynamoDB table name for TTL). Without resource_name, such probes are skipped and no
    account-scoped API is called.
    """
    try:
        result = DocumentFeatureVerifier().verify(
            documentation_url, service, feature, region or settings.aws_region, resource_name
        )
    except (DocumentVerificationError, ValueError) as exc:
        return {"status": "failed", "error_code": type(exc).__name__, "message": str(exc)}
    return knowledge_base.save_check(
        result["service"], result["feature"], result["region"], result["status"], result["evidence"]
    )


@mcp.tool()
def verify_feature_comprehensive(
    service: str,
    feature: str,
    region: str | None = None,
    documentation_url: str | None = None,
    resource_name: str | None = None,
) -> dict:
    """Verify a China-region feature using an ordered multi-source evidence chain.

    Runs, in increasing order of authority: (1) China documentation comparison,
    (2) What's New announcement search at amazonaws.cn/new, (3) a safe live API
    probe. Then synthesizes a graded verdict (available / likely_available /
    unavailable / unknown). This avoids the static-SDK-catalog false negative:
    a missing catalog entry never decides the verdict on its own; a real launch
    announcement or a successful API probe is authoritative positive evidence.

    Provide documentation_url to enable the documentation step and the
    document-driven probe. Provide resource_name to opt in to a resource-scoped
    probe (e.g. an S3 bucket) for features whose API needs one identifier.
    """
    result = verify_feature_evidence_chain(
        service,
        feature,
        region or settings.aws_region,
        documentation_url=documentation_url,
        resource=resource_name,
    )
    return knowledge_base.save_check(
        result["service"], result["feature"], result["region"], result["status"], result
    )


@mcp.tool()
def audit_documentation(documentation_url: str, service: str = "", feature: str = "") -> dict:
    """Fetch a public document and compare it against matching cached validation records."""
    checks = knowledge_base.search(feature or service, 20) if (feature or service) else []
    return audit_document(documentation_url, checks, settings)


@mcp.tool()
def submit_feedback(documentation_url: str, issue_summary: str, evidence: str) -> dict:
    """Submit a documentation issue through the feedback form on an AWS China Docs page."""
    try:
        return AwsDocsFeedbackSubmitter().submit(documentation_url, issue_summary, evidence)
    except FeedbackSubmissionError as exc:
        return {"status": "failed", "error_code": exc.code, "message": str(exc)}


if __name__ == "__main__":
    mcp.run(
        transport="sse",
        host=os.getenv("TRUTHKEEPER_HOST", "127.0.0.1"),
        port=int(os.getenv("TRUTHKEEPER_PORT", "8000")),
    )
