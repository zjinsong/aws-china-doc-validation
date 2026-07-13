import json
import tempfile
import unittest
from pathlib import Path

from china_doc_truthkeeper.document_verifier import (
    DocumentFeatureVerifier,
    load_probe_registry,
)


class FakeResponse:
    def __init__(self, html, url="https://docs.amazonaws.cn/test/feature.html"):
        self.text = html
        self.url = url

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []

    def __getattr__(self, name):
        def call(**kwargs):
            self.calls.append((name, kwargs))
            if self.error:
                raise self.error
            return self.responses.get(name, {"ResponseMetadata": {"HTTPStatusCode": 200}})

        return call


def make_verifier(service, client, html, registry):
    response = FakeResponse(html)
    return DocumentFeatureVerifier(
        http_get=lambda *_a, **_k: response,
        client_factory=lambda requested, region_name: client,
        registry=registry,
    )


class ProbeRegistryTests(unittest.TestCase):
    def test_default_registry_loads_and_contains_entries(self):
        registry = load_probe_registry()
        self.assertTrue(registry)
        services = {entry["service"] for entry in registry}
        # A representative sample of high-frequency services should be covered.
        self.assertIn("ec2", services)
        self.assertIn("dynamodb", services)
        self.assertIn("kinesis", services)
        self.assertIn("lambda", services)

    def test_missing_registry_file_yields_empty_registry(self):
        missing = str(Path(tempfile.gettempdir()) / "does-not-exist-probes.json")
        self.assertEqual(load_probe_registry(missing), ())

    def test_malformed_registry_file_yields_empty_registry(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{ this is not valid json ]")
            bad_path = handle.name
        self.assertEqual(load_probe_registry(bad_path), ())

    def test_keyword_probe_from_registry_is_api_reachable(self):
        registry = [
            {
                "service": "kinesis",
                "match": {"type": "keywords", "any_of": ["data stream"]},
                "probe": {
                    "type": "api_reachable",
                    "operation": "ListStreams",
                    "interpretation": "Kinesis ListStreams is reachable.",
                },
            }
        ]
        client = FakeClient(
            responses={"list_streams": {"StreamNames": [], "ResponseMetadata": {"HTTPStatusCode": 200}}}
        )
        verifier = make_verifier("kinesis", client, "<html><p>Kinesis data stream guide</p></html>", registry)
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html", "kinesis", "Data Stream", "cn-north-1"
        )
        self.assertEqual(result["status"], "api_available")
        probe = result["evidence"]["probes"][0]
        self.assertEqual(probe["operation"], "ListStreams")
        self.assertEqual(probe["interpretation"], "Kinesis ListStreams is reachable.")
        self.assertEqual(client.calls[0][0], "list_streams")

    def test_keyword_mismatch_falls_back_to_generic_probing(self):
        registry = [
            {
                "service": "kinesis",
                "match": {"type": "keywords", "any_of": ["data stream"]},
                "probe": {"type": "api_reachable", "operation": "ListStreams"},
            }
        ]
        client = FakeClient()
        # Feature does not contain the keyword, and the doc exposes no List/Describe API,
        # so no adapter probe runs and no API is called.
        verifier = make_verifier("kinesis", client, "<html><p>Encryption at rest</p></html>", registry)
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html", "kinesis", "Encryption", "cn-north-1"
        )
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(client.calls, [])

    def test_registry_instance_family_probe_still_works(self):
        registry = [
            {
                "service": "ec2",
                "match": {"type": "instance_family"},
                "probe": {"type": "instance_family", "operation": "DescribeInstanceTypeOfferings"},
            }
        ]
        client = FakeClient(
            responses={
                "describe_instance_type_offerings": {
                    "InstanceTypeOfferings": [{"InstanceType": "m7g.large"}],
                    "ResponseMetadata": {"HTTPStatusCode": 200},
                }
            }
        )
        verifier = make_verifier("ec2", client, "<html><p>m7g instances</p></html>", registry)
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html", "ec2", "m7g", "cn-north-1"
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["evidence"]["probes"][0]["matched_instance_types"], ["m7g.large"])

    def test_empty_registry_uses_generic_probing_only(self):
        client = FakeClient(
            responses={
                "list_streams": {"StreamNames": [], "ResponseMetadata": {"HTTPStatusCode": 200}}
            }
        )
        html = (
            "<html><head><title>Kinesis</title></head><body>"
            "<p>Streams available in China Regions.</p>"
            '<a href="../APIReference/API_ListStreams.html">ListStreams</a>'
            "</body></html>"
        )
        verifier = make_verifier("kinesis", client, html, registry=[])
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html", "kinesis", "Streams", "cn-north-1"
        )
        # ListStreams has no required parameters, so generic probing calls it.
        self.assertEqual(client.calls[0][0], "list_streams")
        self.assertIn(result["status"], {"available", "api_available"})


class ResourceProbeTests(unittest.TestCase):
    S3_REGISTRY = [
        {
            "service": "s3",
            "match": {"type": "keywords", "any_of": ["intelligent-tiering", "intelligent tiering"]},
            "probe": {
                "type": "resource_probe",
                "operation": "ListBucketIntelligentTieringConfigurations",
                "param_name": "Bucket",
                "interpretation": "S3 Intelligent-Tiering control-plane API works for the provided bucket.",
            },
        }
    ]

    def test_without_resource_no_api_call_is_made(self):
        client = FakeClient()
        verifier = make_verifier(
            "s3", client, "<html><p>S3 Intelligent-Tiering guide</p></html>", self.S3_REGISTRY
        )
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html", "s3", "Intelligent-Tiering", "cn-northwest-1"
        )
        self.assertEqual(client.calls, [])
        self.assertFalse(result["evidence"]["resource_provided"])
        probe = result["evidence"]["probes"][0]
        self.assertEqual(probe["status"], "not_probeable_without_resource")
        self.assertEqual(probe["required_resource"], "Bucket")
        self.assertEqual(result["status"], "unknown")

    def test_with_resource_calls_api_and_reports_available(self):
        client = FakeClient(
            responses={
                "list_bucket_intelligent_tiering_configurations": {
                    "IsTruncated": False,
                    "ResponseMetadata": {"HTTPStatusCode": 200},
                }
            }
        )
        verifier = make_verifier(
            "s3", client, "<html><p>S3 Intelligent-Tiering guide</p></html>", self.S3_REGISTRY
        )
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html",
            "s3",
            "Intelligent-Tiering",
            "cn-northwest-1",
            resource="my-bucket",
        )
        self.assertTrue(result["evidence"]["resource_provided"])
        self.assertEqual(len(client.calls), 1)
        name, kwargs = client.calls[0]
        self.assertEqual(name, "list_bucket_intelligent_tiering_configurations")
        self.assertEqual(kwargs, {"Bucket": "my-bucket"})
        probe = result["evidence"]["probes"][0]
        self.assertEqual(probe["status"], "available")
        self.assertEqual(probe["provided_resource"], "Bucket")
        self.assertEqual(result["status"], "available")
        # The raw response must not leak into the returned evidence.
        self.assertNotIn("_response", probe)

    def test_unsupported_operation_is_unavailable(self):
        from botocore.exceptions import ClientError

        client = FakeClient(
            error=ClientError(
                {"Error": {"Code": "UnsupportedOperation", "Message": "not supported"}},
                "ListBucketIntelligentTieringConfigurations",
            )
        )
        verifier = make_verifier(
            "s3", client, "<html><p>S3 Intelligent-Tiering guide</p></html>", self.S3_REGISTRY
        )
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html",
            "s3",
            "Intelligent-Tiering",
            "cn-northwest-1",
            resource="my-bucket",
        )
        self.assertEqual(result["evidence"]["probes"][0]["status"], "unavailable")
        self.assertEqual(result["status"], "unavailable")

    def test_access_denied_with_resource_is_unknown(self):
        from botocore.exceptions import ClientError

        client = FakeClient(
            error=ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "denied"}},
                "ListBucketIntelligentTieringConfigurations",
            )
        )
        verifier = make_verifier(
            "s3", client, "<html><p>S3 Intelligent-Tiering guide</p></html>", self.S3_REGISTRY
        )
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html",
            "s3",
            "Intelligent-Tiering",
            "cn-northwest-1",
            resource="my-bucket",
        )
        # Permission errors must never be misread as feature unavailability.
        self.assertEqual(result["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
