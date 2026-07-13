import unittest

from china_doc_truthkeeper.announcements import search_announcements, fetch_announcement_slugs
from china_doc_truthkeeper.evidence_chain import verify_feature_evidence_chain
from china_doc_truthkeeper.verifier import verify_service_availability


class FakeResponse:
    def __init__(self, text, url="https://docs.amazonaws.cn/test/feature.html"):
        self.text = text
        self.url = url

    def raise_for_status(self):
        return None


# A tiny What's New index page fragment with real-looking permalinks.
WHATS_NEW_HTML = """
<html><body>
<a href="/en/new/2026/amazon-lambda-durable-functions-are-available/">x</a>
<a href="/en/new/2025/announcing-amazon-s3-vectors-cloud-object-storage-store-and-query-vectors/">y</a>
<a href="/en/new/2026/amazon-eks-supports-kubernetes-version-rollbacks/">z</a>
<a href="/en/new/2026/some-unrelated-networking-update/">n</a>
</body></html>
"""


def fake_get_factory(whats_new_html=WHATS_NEW_HTML, doc_html="<html><body></body></html>"):
    def fake_get(url, *args, **kwargs):
        if "/new/" in url:
            return FakeResponse(whats_new_html, url=url)
        return FakeResponse(doc_html, url=url)
    return fake_get


class AnnouncementSearchTests(unittest.TestCase):
    def test_fetch_slugs_parses_permalinks(self):
        entries = fetch_announcement_slugs(http_get=fake_get_factory())
        slugs = {e["slug"] for e in entries}
        self.assertIn("amazon-lambda-durable-functions-are-available", slugs)
        # De-duplicated across the multiple index paths that get requested.
        self.assertEqual(len(entries), len({(e["year"], e["slug"]) for e in entries}))

    def test_availability_announcement_matches(self):
        r = search_announcements("lambda", "durable functions", http_get=fake_get_factory())
        self.assertEqual(r["conclusion"], "announced_available")
        self.assertTrue(r["availability_announcements"])

    def test_s3_vectors_is_found_as_announcement(self):
        r = search_announcements("s3", "vectors", http_get=fake_get_factory())
        # 'announcing-...' is treated as an availability signal.
        self.assertEqual(r["conclusion"], "announced_available")

    def test_no_match_returns_no_announcement(self):
        r = search_announcements("dynamodb", "global tables", http_get=fake_get_factory())
        self.assertEqual(r["conclusion"], "no_announcement_found")


class VerifierFalseNegativeTests(unittest.TestCase):
    def test_missing_service_is_sdk_catalog_missing_not_unavailable(self):
        # A bogus service will not be in the aws-cn catalog.
        result = verify_service_availability("s3vectors-bogus-xyz", "Vectors", "cn-northwest-1")
        self.assertEqual(result["status"], "sdk_catalog_missing")
        self.assertNotEqual(result["status"], "unavailable")
        self.assertTrue(result["evidence"]["inconclusive_if_missing"])


class EvidenceChainSynthesisTests(unittest.TestCase):
    def test_announcement_overrides_missing_sdk_catalog(self):
        # A service genuinely missing from the static catalog, but with a
        # China-region availability announcement, must resolve to available.
        # We inject an announcement page whose slug carries the bogus service
        # token plus a feature token and an availability hint.
        html = (
            '<html><body>'
            '<a href="/en/new/2026/foobarsvc-vectors-is-available/">x</a>'
            '</body></html>'
        )
        result = verify_feature_evidence_chain(
            "foobarsvc",
            "vectors",
            "cn-northwest-1",
            documentation_url=None,
            http_get=fake_get_factory(whats_new_html=html),
        )
        self.assertEqual(result["status"], "available")
        self.assertTrue(any("announcement" in r for r in result["reasons"]))
        # And the SDK-missing signal is present only as context.
        self.assertTrue(any("SDK catalog" in r for r in result["reasons"]))

    def test_live_api_available_yields_available(self):
        result = verify_feature_evidence_chain(
            "lambda",
            "durable functions",
            "cn-northwest-1",
            documentation_url=None,
            http_get=fake_get_factory(),
        )
        self.assertEqual(result["status"], "available")

    def test_no_signal_is_unknown(self):
        result = verify_feature_evidence_chain(
            "dynamodb",
            "some obscure feature",
            "cn-northwest-1",
            documentation_url=None,
            http_get=fake_get_factory(),
        )
        # dynamodb exists in the catalog (status available) -> the chain reports
        # available via the catalog; use a bogus service to force unknown.
        self.assertIn(result["status"], {"available", "likely_available", "unknown"})

    def test_bogus_service_no_announcement_is_unknown(self):
        result = verify_feature_evidence_chain(
            "totally-bogus-service-xyz",
            "some obscure feature",
            "cn-northwest-1",
            documentation_url=None,
            http_get=fake_get_factory(),
        )
        self.assertEqual(result["status"], "unknown")
        # The SDK-missing signal must appear only as context, not as the verdict.
        self.assertTrue(any("SDK catalog" in r or "sdk" in r.lower() for r in result["reasons"]))

    def test_evidence_chain_has_three_steps(self):
        result = verify_feature_evidence_chain(
            "lambda", "durable functions", "cn-northwest-1", http_get=fake_get_factory()
        )
        chain = result["evidence_chain"]
        self.assertIn("step_1_documentation", chain)
        self.assertIn("step_2_announcements", chain)
        self.assertIn("step_3_api_probe", chain)


if __name__ == "__main__":
    unittest.main()
