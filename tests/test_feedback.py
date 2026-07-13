import unittest

from china_doc_truthkeeper.feedback import (
    AwsDocsFeedbackSubmitter,
    FeedbackSubmissionError,
    build_feedback_message,
    validate_documentation_url,
)


class FakeLocator:
    def __init__(self, *, click_error=None, wait_error=None, text="感谢您的反馈"):
        self.click_error = click_error
        self.wait_error = wait_error
        self.text = text
        self.filled = None
        self.clicked = 0

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    def click(self):
        self.clicked += 1
        if self.click_error:
            raise self.click_error

    def fill(self, value):
        if self.click_error:
            raise self.click_error
        self.filled = value

    def wait_for(self, **_kwargs):
        if self.wait_error:
            raise self.wait_error

    def inner_text(self):
        return self.text


class FakePage:
    def __init__(self, *, negative=None, provide=None, textbox=None, submit=None, success=None):
        self.negative = negative or FakeLocator()
        self.provide = provide or FakeLocator()
        self.textbox = textbox or FakeLocator()
        self.submit = submit or FakeLocator()
        self.success = success or FakeLocator()

    def get_by_role(self, role, name=None):
        if role == "textbox":
            return self.textbox
        pattern = getattr(name, "pattern", "")
        if "submit" in pattern.lower() or "提交" in pattern:
            return self.submit
        if "provide" in pattern.lower() or "comment" in pattern:
            return self.provide
        return self.negative

    def get_by_text(self, _name):
        return self.success


class FeedbackTests(unittest.TestCase):
    def test_accepts_only_aws_china_documentation_urls(self):
        self.assertEqual(
            validate_documentation_url("https://docs.amazonaws.cn/AWSEC2/latest/UserGuide/concepts.html"),
            "https://docs.amazonaws.cn/AWSEC2/latest/UserGuide/concepts.html",
        )
        with self.assertRaisesRegex(FeedbackSubmissionError, "docs.amazonaws.cn"):
            validate_documentation_url("https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/concepts.html")

    def test_builds_a_readable_feedback_message(self):
        self.assertEqual(
            build_feedback_message("功能不可用", "cn-north-1 返回 AccessDenied"),
            "问题摘要：\n功能不可用\n\n验证证据：\ncn-north-1 返回 AccessDenied",
        )

    def test_rejects_missing_feedback_content(self):
        with self.assertRaisesRegex(FeedbackSubmissionError, "issue_summary"):
            build_feedback_message(" ", "evidence")
        with self.assertRaisesRegex(FeedbackSubmissionError, "evidence"):
            build_feedback_message("summary", " ")

    def test_submits_simulated_feedback_form(self):
        page = FakePage()
        confirmation = AwsDocsFeedbackSubmitter()._submit_on_page(page, "message")
        self.assertEqual(page.textbox.filled, "message")
        self.assertEqual(page.submit.clicked, 1)
        self.assertEqual(confirmation, "感谢您的反馈")

    def test_reports_missing_feedback_component(self):
        error = RuntimeError("not found")
        page = FakePage(negative=FakeLocator(click_error=error), provide=FakeLocator(click_error=error))
        with self.assertRaisesRegex(FeedbackSubmissionError, "does not expose"):
            AwsDocsFeedbackSubmitter()._submit_on_page(page, "message")

    def test_reports_unconfirmed_submission_without_retrying(self):
        page = FakePage(success=FakeLocator(wait_error=RuntimeError("not visible")))
        with self.assertRaisesRegex(FeedbackSubmissionError, "not show a success"):
            AwsDocsFeedbackSubmitter()._submit_on_page(page, "message")
        self.assertEqual(page.submit.clicked, 1)


if __name__ == "__main__":
    unittest.main()
