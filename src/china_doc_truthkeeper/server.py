from fastmcp import FastMCP

from .auditor import audit_document
from .config import get_settings
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
    """Verify AWS China service endpoint availability using the aws-cn service catalog and cache the result."""
    result = verify_service_availability(service, feature, region or settings.aws_region)
    return knowledge_base.save_check(service, feature, result["region"], result["status"], result["evidence"])


@mcp.tool()
def audit_documentation(documentation_url: str, service: str = "", feature: str = "") -> dict:
    """Fetch a public document and compare it against matching cached validation records."""
    checks = knowledge_base.search(feature or service, 20) if (feature or service) else []
    return audit_document(documentation_url, checks, settings)


@mcp.tool()
def submit_feedback(documentation_url: str, issue_summary: str, evidence: str) -> dict:
    """Create a local feedback draft. This tool never sends email or opens a support case automatically."""
    return knowledge_base.create_feedback_draft(documentation_url, issue_summary, {"evidence": evidence})


if __name__ == "__main__":
    mcp.run(transport="sse")
