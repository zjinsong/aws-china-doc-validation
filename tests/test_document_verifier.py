import unittest

from botocore.exceptions import ClientError, NoCredentialsError

from china_doc_truthkeeper.document_verifier import (
    DocumentFeatureVerifier,
    DocumentVerificationError,
    parse_feature_document,
    validate_china_documentation_url,
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
            return self.responses.get(
                name,
                {"ResponseMetadata": {"HTTPStatusCode": 200}},
            )

        return call


def html_for(operation, body="Feature is available in China Regions."):
    return (
        "<html><head><title>Feature guide</title></head><body>"
        f"<p>{body}</p>"
        f'<a href="../APIReference/API_{operation}.html">{operation}</a>'
        '<a href="../APIReference/API_CreateTable.html">CreateTable</a>'
        "</body></html>"
    )


class DocumentVerifierTests(unittest.TestCase):
    def make_verifier(self, service, client, html):
        response = FakeResponse(html)
        return DocumentFeatureVerifier(
            http_get=lambda *_args, **_kwargs: response,
            client_factory=lambda requested, region_name: client,
        )

    def test_rejects_non_china_documentation_url(self):
        with self.assertRaises(DocumentVerificationError):
            validate_china_documentation_url("https://docs.aws.amazon.com/test.html")

    def test_extracts_only_list_and_describe_operations(self):
        parsed = parse_feature_document(
            html_for("ListGlobalTables", "Global Tables is available in China Regions."),
            "Global Tables",
        )
        self.assertIn("ListGlobalTables", parsed["candidate_operations"])
        self.assertNotIn("CreateTable", parsed["candidate_operations"])
        self.assertEqual(parsed["document_conclusion"], "available")

    def test_required_resource_parameter_is_not_called(self):
        client = FakeClient()
        verifier = self.make_verifier("dynamodb", client, html_for("DescribeTable", "Table feature"))
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html", "dynamodb", "Table feature", "cn-north-1"
        )
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["evidence"]["probes"][0]["status"], "not_safely_probeable")
        self.assertEqual(client.calls, [])

    def test_document_operation_missing_from_sdk_is_not_called(self):
        client = FakeClient()
        verifier = self.make_verifier("dynamodb", client, html_for("ListImaginaryFeatures", "Imaginary feature"))
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html",
            "dynamodb",
            "Imaginary feature",
            "cn-north-1",
        )
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["evidence"]["probes"][0]["status"], "api_not_in_sdk")
        self.assertEqual(client.calls, [])

    def test_generic_success_is_api_available_and_discards_response(self):
        client = FakeClient(responses={"describe_endpoints": {"Endpoints": [{"Address": "private"}], "ResponseMetadata": {"HTTPStatusCode": 200}}})
        verifier = self.make_verifier("dynamodb", client, html_for("DescribeEndpoints", "Endpoint discovery"))
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html", "dynamodb", "Endpoint discovery", "cn-north-1"
        )
        self.assertEqual(result["status"], "api_available")
        self.assertNotIn("_response", result["evidence"]["probes"][0])
        self.assertNotIn("private", str(result))

    def test_access_denied_and_missing_credentials_are_unknown(self):
        denied = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "DescribeEndpoints",
        )
        for error in (denied, NoCredentialsError()):
            client = FakeClient(error=error)
            verifier = self.make_verifier("dynamodb", client, html_for("DescribeEndpoints", "Endpoint discovery"))
            result = verifier.verify(
                "https://docs.amazonaws.cn/test/feature.html", "dynamodb", "Endpoint discovery", "cn-north-1"
            )
            self.assertEqual(result["status"], "unknown")

    def test_unknown_operation_is_unavailable(self):
        client = FakeClient(
            error=ClientError(
                {"Error": {"Code": "UnknownOperationException", "Message": "unknown"}},
                "DescribeEndpoints",
            )
        )
        verifier = self.make_verifier("dynamodb", client, html_for("DescribeEndpoints", "Endpoint discovery"))
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html", "dynamodb", "Endpoint discovery", "cn-north-1"
        )
        self.assertEqual(result["status"], "unavailable")

    def test_ec2_instance_family_adapter_checks_offerings(self):
        client = FakeClient(
            responses={
                "describe_instance_type_offerings": {
                    "InstanceTypeOfferings": [
                        {"InstanceType": "c8i.large", "LocationType": "region", "Location": "cn-north-1"}
                    ],
                    "ResponseMetadata": {"HTTPStatusCode": 200},
                }
            }
        )
        verifier = self.make_verifier("ec2", client, "<html><p>c8i instance types</p></html>")
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html", "ec2", "c8i", "cn-north-1"
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["evidence"]["probes"][0]["matched_instance_types"], ["c8i.large"])
        self.assertEqual(client.calls[0][1]["Filters"][0]["Values"], ["c8i.*"])

    def test_ec2_empty_offerings_is_unavailable(self):
        client = FakeClient(
            responses={
                "describe_instance_type_offerings": {
                    "InstanceTypeOfferings": [],
                    "ResponseMetadata": {"HTTPStatusCode": 200},
                }
            }
        )
        verifier = self.make_verifier("ec2", client, "<html><p>c8i instance types</p></html>")
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html", "ec2", "c8i", "cn-north-1"
        )
        self.assertEqual(result["status"], "unavailable")

    def test_ec2_non_instance_feature_does_not_use_instance_adapter(self):
        client = FakeClient()
        verifier = self.make_verifier("ec2", client, "<html><p>IPv6 networking</p></html>")
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html", "ec2", "IPv6", "cn-north-1"
        )
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(client.calls, [])

    def test_dynamodb_empty_list_is_only_api_available(self):
        client = FakeClient(
            responses={
                "list_global_tables": {
                    "GlobalTables": [],
                    "ResponseMetadata": {"HTTPStatusCode": 200},
                }
            }
        )
        verifier = self.make_verifier("dynamodb", client, "<html><p>Global Tables guide</p></html>")
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html",
            "dynamodb",
            "Global Tables",
            "cn-north-1",
        )
        self.assertEqual(result["status"], "api_available")
        self.assertIn("does not by itself prove", result["evidence"]["probes"][0]["interpretation"])

    def test_documented_mrsc_unavailability_wins_over_generic_api_success(self):
        client = FakeClient()
        verifier = self.make_verifier(
            "dynamodb",
            client,
            html_for("ListGlobalTables", "Global Tables MRSC is not available in China Regions."),
        )
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html",
            "dynamodb",
            "Global Tables MRSC",
            "cn-north-1",
        )
        self.assertEqual(result["status"], "unavailable")

    def test_document_and_feature_specific_probe_conflict(self):
        client = FakeClient(
            responses={
                "describe_instance_type_offerings": {
                    "InstanceTypeOfferings": [{"InstanceType": "c8i.large"}],
                    "ResponseMetadata": {"HTTPStatusCode": 200},
                }
            }
        )
        verifier = self.make_verifier("ec2", client, "<html><p>c8i is not available in China Regions.</p></html>")
        result = verifier.verify(
            "https://docs.amazonaws.cn/test/feature.html", "ec2", "c8i", "cn-north-1"
        )
        self.assertEqual(result["status"], "conflict")


if __name__ == "__main__":
    unittest.main()
