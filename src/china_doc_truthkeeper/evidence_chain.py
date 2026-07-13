"""Multi-source evidence chain for AWS China feature availability.

Combines three independent sources, in increasing order of authority, into a
single graded conclusion:

  1. China documentation comparison  (keyword parse of the aws-cn doc page)
  2. What's New announcement search   (amazonaws.cn/new launch announcements)
  3. Live API probe                   (safe read-only List/Describe call)

Rationale for the ordering and the final synthesis: the static SDK catalog
lags behind launches (it produced a false negative for S3 Vectors), so a
missing catalog entry is treated as inconclusive. A real China-region launch
announcement or a successful live API probe is authoritative positive evidence
and overrides a "not in SDK catalog" signal.

This module deliberately does not depend on any LLM. The optional DeepSeek
audit remains a separate, opt-in tool.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from .announcements import search_announcements
from .document_verifier import (
    DocumentFeatureVerifier,
    DocumentVerificationError,
    parse_feature_document,
    validate_china_documentation_url,
)
from .verifier import verify_service_availability


def _document_evidence(documentation_url: str | None, feature: str, http_get) -> dict:
    if not documentation_url:
        return {"source": "china documentation", "conclusion": "not_checked"}
    try:
        url = validate_china_documentation_url(documentation_url)
        response = http_get(url, timeout=20, headers={"User-Agent": "China-Doc-TruthKeeper/1.0"})
        response.raise_for_status()
    except (DocumentVerificationError, requests.RequestException) as exc:
        return {"source": "china documentation", "conclusion": "fetch_failed", "error": str(exc)}
    parsed = parse_feature_document(response.text, feature)
    return {
        "source": "china documentation",
        "conclusion": parsed["document_conclusion"],
        "title": parsed["title"],
        "feature_excerpts": parsed["feature_excerpts"][:3],
    }


def _synthesize(doc: dict, announce: dict, api: dict) -> dict:
    """Combine the three sources into a single graded status.

    Priority (most authoritative positive evidence first):
      - a real live API probe returning available/api_available -> available
      - a China-region availability announcement                -> available (announced)
      - documentation says unavailable and nothing contradicts it -> unavailable
      - a weak positive signal (related announcement, documentation
        'available', or the static catalog merely listing the service) -> likely_available
      - otherwise                                                -> unknown

    The static botocore service catalog is only a weak signal: it says the
    service exists in the partition, not that this specific feature works, and
    it lags behind launches. It can never outrank an announcement, and on its
    own only yields 'likely_available'.
    """
    probe_type = api.get("probe_type")
    api_status = api.get("status")
    doc_conclusion = doc.get("conclusion")
    announce_conclusion = announce.get("conclusion")

    # A real, live read-only API call actually exercised the feature endpoint.
    live_probe_positive = probe_type == "live_probe" and api_status in {"available", "api_available"}
    # The static catalog merely lists the service in the aws-cn partition.
    catalog_lists_service = probe_type == "service_catalog" and api_status == "available"

    reasons: list[str] = []

    if live_probe_positive:
        reasons.append(f"a live API probe returned '{api_status}'")
        status = "available"
    elif announce_conclusion == "announced_available":
        reasons.append("a China-region What's New announcement indicates availability")
        status = "available"
    elif doc_conclusion == "unavailable":
        reasons.append("China documentation states the feature is not available")
        status = "unavailable"
    elif announce_conclusion == "related_mention":
        reasons.append("related China-region announcements mention this feature")
        status = "likely_available"
    elif doc_conclusion == "available":
        reasons.append("China documentation describes the feature as available")
        status = "likely_available"
    elif catalog_lists_service:
        reasons.append(
            "the static service catalog lists the service in aws-cn (weak signal: "
            "confirms the service exists, not that this specific feature works)"
        )
        status = "likely_available"
    else:
        reasons.append("no source produced positive or negative confirmation")
        status = "unknown"

    # Surface the SDK-catalog-missing signal as context, never as the deciding factor.
    if api_status == "sdk_catalog_missing":
        reasons.append(
            "note: service missing from the static SDK catalog (inconclusive, ignored for the verdict)"
        )
    return {"status": status, "reasons": reasons}


def verify_feature_evidence_chain(
    service: str,
    feature: str,
    region: str,
    documentation_url: str | None = None,
    resource: str | None = None,
    http_get=requests.get,
    verifier: DocumentFeatureVerifier | None = None,
    announcement_search=search_announcements,
) -> dict:
    """Run the ordered multi-source evidence chain and synthesize a conclusion."""
    service = service.strip()
    feature = feature.strip()

    # 1) China documentation comparison
    doc_evidence = _document_evidence(documentation_url, feature, http_get)

    # 2) What's New announcement search
    announce_evidence = announcement_search(service, feature, http_get=http_get)

    # 3) Live API probe. Prefer the document-driven probe when a doc URL is given
    #    (it can run registry/resource probes and make real read-only calls).
    #    Without a doc URL we can only consult the static botocore service catalog,
    #    which is a weak signal (it just says the service exists in the partition,
    #    not that this specific feature works) and must not be mislabeled as a
    #    live probe.
    if documentation_url:
        try:
            probe = (verifier or DocumentFeatureVerifier()).verify(
                documentation_url, service, feature, region, resource
            )
            api_evidence = {
                "source": "live API probe",
                "probe_type": "live_probe",
                "status": probe["status"],
                "probes": probe["evidence"].get("probes", []),
            }
        except (DocumentVerificationError, ValueError) as exc:
            api_evidence = {
                "source": "live API probe",
                "probe_type": "live_probe",
                "status": "unknown",
                "error": str(exc),
            }
    else:
        catalog = verify_service_availability(service, feature, region)
        api_evidence = {
            "source": "static botocore service catalog (not a live probe)",
            "probe_type": "service_catalog",
            "status": catalog["status"],
            "evidence": catalog["evidence"],
        }

    verdict = _synthesize(doc_evidence, announce_evidence, api_evidence)

    return {
        "service": service,
        "feature": feature,
        "region": region,
        "status": verdict["status"],
        "reasons": verdict["reasons"],
        "evidence_chain": {
            "step_1_documentation": doc_evidence,
            "step_2_announcements": announce_evidence,
            "step_3_api_probe": api_evidence,
        },
        "method": "multi-source evidence chain: documentation -> announcements -> live API probe",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
