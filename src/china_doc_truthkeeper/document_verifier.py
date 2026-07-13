"""Document-driven, read-only AWS China API feature verification."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import re
from urllib.parse import urlparse

import boto3
from botocore import xform_name
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    ParamValidationError,
    PartialCredentialsError,
)
from botocore.session import Session
from bs4 import BeautifulSoup
import requests


_API_ACTION = re.compile(r"\b((?:List|Describe)[A-Z][A-Za-z0-9]+)\b")
_API_LINK = re.compile(r"API_((?:List|Describe)[A-Za-z0-9]+)\.html", re.I)
_UNAVAILABLE = re.compile(
    r"(not available|isn't available|is not supported|unsupported|不可用|不支持|尚未提供|尚不支持)", re.I
)
_AVAILABLE = re.compile(r"(available in|is available|supported in|可用|支持)", re.I)
_INSTANCE_FAMILY = re.compile(r"\b([a-z]{1,2}\d+[a-z]*)\b", re.I)
_UNSUPPORTED_CODES = {
    "UnknownOperationException",
    "UnsupportedOperation",
    "UnsupportedOperationException",
    "InvalidAction",
}
_PERMISSION_CODES = {
    "AccessDenied",
    "AccessDeniedException",
    "UnauthorizedOperation",
    "UnrecognizedClientException",
    "InvalidClientTokenId",
    "ExpiredToken",
    "ExpiredTokenException",
}

_DEFAULT_REGISTRY_PATH = Path(__file__).with_name("probes.json")


@lru_cache(maxsize=None)
def load_probe_registry(path: str | None = None) -> tuple[dict, ...]:
    """Load the data-driven probe registry from JSON.

    Returns a tuple of probe entries. Results are cached per path. A missing or
    malformed registry yields an empty registry rather than raising, so the
    generic List/Describe probing still works.
    """
    registry_path = Path(path) if path else _DEFAULT_REGISTRY_PATH
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    entries = data.get("probes", []) if isinstance(data, dict) else []
    return tuple(entry for entry in entries if isinstance(entry, dict) and entry.get("service"))


class DocumentVerificationError(ValueError):
    """The requested document verification is invalid or unsafe."""


def validate_china_documentation_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname != "docs.amazonaws.cn":
        raise DocumentVerificationError("documentation_url must use https://docs.amazonaws.cn/")
    return url.strip()


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _feature_excerpt(soup: BeautifulSoup, feature: str) -> list[str]:
    feature_lower = feature.strip().lower()
    blocks: list[str] = []
    for node in soup.find_all(["h1", "h2", "h3", "p", "li", "td"]):
        text = _normalize(node.get_text(" ", strip=True))
        if text and feature_lower in text.lower() and text not in blocks:
            blocks.append(text[:1000])
        if len(blocks) == 5:
            break
    if blocks:
        return blocks
    text = _normalize(soup.get_text(" ", strip=True))
    return [text[:1500]] if text else []


def parse_feature_document(html: str, feature: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()

    operations: set[str] = set()
    for link in soup.find_all("a", href=True):
        match = _API_LINK.search(link["href"])
        if match:
            operations.add(match.group(1))
        operations.update(_API_ACTION.findall(link.get_text(" ", strip=True)))
    operations.update(_API_ACTION.findall(soup.get_text(" ", strip=True)))

    excerpts = _feature_excerpt(soup, feature)
    relevant_text = " ".join(excerpts)
    if _UNAVAILABLE.search(relevant_text):
        conclusion = "unavailable"
    elif _AVAILABLE.search(relevant_text):
        conclusion = "available"
    else:
        conclusion = "unknown"

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return {
        "title": title,
        "feature_excerpts": excerpts,
        "document_conclusion": conclusion,
        "candidate_operations": sorted(operations),
    }


class DocumentFeatureVerifier:
    """Discover and execute safe List/Describe probes for one document feature."""

    max_generic_probes = 10

    def __init__(self, http_get=requests.get, client_factory=boto3.client, botocore_session=None, registry=None):
        self.http_get = http_get
        self.client_factory = client_factory
        self.botocore_session = botocore_session or Session()
        self.registry = tuple(registry) if registry is not None else load_probe_registry()

    def verify(self, documentation_url: str, service: str, feature: str, region: str) -> dict:
        url = validate_china_documentation_url(documentation_url)
        service = service.strip()
        feature = feature.strip()
        if not service or not feature:
            raise DocumentVerificationError("service and feature must not be empty")

        try:
            response = self.http_get(url, timeout=20, headers={"User-Agent": "China-Doc-TruthKeeper/1.0"})
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DocumentVerificationError(f"unable to fetch documentation: {exc}") from exc
        final_url = validate_china_documentation_url(response.url)
        document = parse_feature_document(response.text, feature)

        candidates = set(document["candidate_operations"])
        adapter = self._adapter(service, feature)
        if adapter:
            candidates.add(adapter["operation"])

        try:
            model = self.botocore_session.get_service_model(service)
        except BotoCoreError as exc:
            raise DocumentVerificationError(f"unknown or unavailable boto3 service: {service}") from exc
        known_operations = set(model.operation_names)
        client = None
        probes: list[dict] = []

        selected_candidates = sorted(candidates)[: self.max_generic_probes]
        for operation in selected_candidates:
            if not (operation.startswith("List") or operation.startswith("Describe")):
                continue
            if operation not in known_operations:
                probes.append({"operation": operation, "status": "api_not_in_sdk"})
                continue

            operation_model = model.operation_model(operation)
            required = list(operation_model.input_shape.required_members) if operation_model.input_shape else []
            is_adapter_operation = adapter and operation == adapter["operation"]
            if required and not is_adapter_operation:
                probes.append(
                    {
                        "operation": operation,
                        "status": "not_safely_probeable",
                        "required_parameters": required,
                    }
                )
                continue

            if client is None:
                client = self.client_factory(service, region_name=region)
            if is_adapter_operation:
                probe = self._run_adapter(client, service, feature, region, adapter)
            else:
                probe = self._call(client, service, operation, region, {})
            probes.append(probe)

        status = self._combine_status(document["document_conclusion"], probes)
        safe_probes = self.sanitized_probes(probes)
        return {
            "service": service,
            "feature": feature,
            "region": region,
            "status": status,
            "evidence": {
                "method": "AWS China documentation + safe List/Describe API probes",
                "documentation_url": final_url,
                "document": document,
                "probes": safe_probes,
                "probe_limit": self.max_generic_probes,
                "candidate_operations_truncated": len(candidates) > len(selected_candidates),
                "note": "API responses are discarded; only status and non-account feature evidence are retained.",
            },
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def _adapter(self, service: str, feature: str) -> dict | None:
        """Resolve a feature-specific probe from the data-driven registry.

        Returns a normalized adapter dict with at least an "operation" key, plus
        "probe_type" and any extra fields (e.g. "family", "interpretation").
        Returns None when no registry entry matches, so the generic
        List/Describe probing is used instead.
        """
        lower = feature.lower().strip()
        for entry in self.registry:
            if entry.get("service") != service:
                continue
            match = entry.get("match", {})
            probe = entry.get("probe", {})
            match_type = match.get("type")

            if match_type == "instance_family":
                family_match = _INSTANCE_FAMILY.fullmatch(lower)
                if family_match:
                    return {
                        "operation": probe.get("operation", "DescribeInstanceTypeOfferings"),
                        "probe_type": "instance_family",
                        "family": family_match.group(1),
                    }
            elif match_type == "keywords":
                keywords = match.get("any_of", [])
                if any(keyword.lower() in lower for keyword in keywords):
                    adapter = {
                        "operation": probe["operation"],
                        "probe_type": probe.get("type", "api_reachable"),
                    }
                    if "interpretation" in probe:
                        adapter["interpretation"] = probe["interpretation"]
                    return adapter
        return None

    def _run_adapter(self, client, service: str, feature: str, region: str, adapter: dict) -> dict:
        probe_type = adapter.get("probe_type")
        operation = adapter["operation"]

        if probe_type == "instance_family":
            family = adapter["family"]
            parameters = {
                "LocationType": "region",
                "Filters": [{"Name": "instance-type", "Values": [f"{family}.*"]}],
                "MaxResults": 1000,
            }
            probe = self._call(client, service, operation, region, parameters)
            probe["cli_command"] = (
                f"aws {service} {xform_name(operation).replace('_', '-')} --location-type region "
                f"--filters Name=instance-type,Values={family}.* --region {region}"
            )
            if probe["status"] == "api_available":
                offerings = probe.pop("_response", {}).get("InstanceTypeOfferings", [])
                matches = sorted(
                    {
                        item.get("InstanceType", "")
                        for item in offerings
                        if item.get("InstanceType", "").lower().startswith(family.lower() + ".")
                    }
                )
                probe["status"] = "available" if matches else "unavailable"
                probe["matched_instance_types"] = matches
                probe["interpretation"] = "Matching regional EC2 instance type offerings were returned."
            return probe

        # Default: an "api_reachable" probe. Calling a zero-parameter List/Describe
        # operation confirms the API endpoint exists in the region. An empty result
        # says nothing about feature support, so the response body is ignored.
        probe = self._call(client, service, operation, region, {})
        probe["interpretation"] = adapter.get(
            "interpretation",
            f"The {operation} API is reachable in this region; its response does not by "
            "itself prove feature support.",
        )
        return probe

    @staticmethod
    def _call(client, service: str, operation: str, region: str, parameters: dict) -> dict:
        command = f"aws {service} {xform_name(operation).replace('_', '-')} --region {region}"
        result = {"operation": operation, "cli_command": command}
        try:
            response = getattr(client, xform_name(operation))(**parameters)
            result.update(
                {
                    "status": "api_available",
                    "http_status": response.get("ResponseMetadata", {}).get("HTTPStatusCode"),
                    "_response": response,
                }
            )
        except (NoCredentialsError, PartialCredentialsError) as exc:
            result.update({"status": "unknown", "error_code": type(exc).__name__})
        except ParamValidationError:
            result.update({"status": "not_safely_probeable", "error_code": "ParamValidationError"})
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = error.get("Code", "ClientError")
            if code in _UNSUPPORTED_CODES:
                probe_status = "unavailable"
            elif code in _PERMISSION_CODES:
                probe_status = "unknown"
            else:
                probe_status = "unknown"
            result.update({"status": probe_status, "error_code": code})
        except BotoCoreError as exc:
            result.update({"status": "unknown", "error_code": type(exc).__name__})
        return result

    @staticmethod
    def _combine_status(document_conclusion: str, probes: list[dict]) -> str:
        statuses = {probe["status"] for probe in probes}
        if document_conclusion == "unavailable":
            if "available" in statuses:
                return "conflict"
            return "unavailable"
        if "available" in statuses:
            return "available"
        if "api_available" in statuses:
            return "api_available"
        if "unavailable" in statuses and statuses <= {"unavailable", "api_not_in_sdk"}:
            return "unavailable"
        return "unknown"

    @staticmethod
    def sanitized_probes(probes: list[dict]) -> list[dict]:
        """Strip raw AWS responses before evidence is persisted or returned."""
        return [{key: value for key, value in probe.items() if key != "_response"} for probe in probes]
