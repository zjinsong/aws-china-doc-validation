import os

from fastmcp import FastMCP

from .auditor import audit_document
from .config import get_settings
from .document_verifier import DocumentFeatureVerifier, DocumentVerificationError
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
) -> dict:
    """Verify a documented feature with safe List/Describe calls against an AWS China endpoint."""
    try:
        result = DocumentFeatureVerifier().verify(
            documentation_url, service, feature, region or settings.aws_region
        )
    except (DocumentVerificationError, ValueError) as exc:
        return {"status": "failed", "error_code": type(exc).__name__, "message": str(exc)}
    return knowledge_base.save_check(
        result["service"], result["feature"], result["region"], result["status"], result["evidence"]
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
