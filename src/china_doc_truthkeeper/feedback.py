"""Submit feedback through the feedback widget on AWS China documentation pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from urllib.parse import urlparse


class FeedbackSubmissionError(RuntimeError):
    """A feedback form could not be submitted safely."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_documentation_url(documentation_url: str) -> str:
    """Return a validated AWS China documentation URL."""
    parsed = urlparse(documentation_url.strip())
    if parsed.scheme != "https" or parsed.hostname != "docs.amazonaws.cn":
        raise FeedbackSubmissionError(
            "invalid_documentation_url",
            "documentation_url must be an https://docs.amazonaws.cn/ documentation URL.",
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise FeedbackSubmissionError(
            "invalid_documentation_url",
            "documentation_url contains an invalid port.",
        ) from exc
    if not parsed.path or parsed.username or parsed.password or port:
        raise FeedbackSubmissionError(
            "invalid_documentation_url",
            "documentation_url must be a normal AWS China documentation page URL.",
        )
    return documentation_url.strip()


def build_feedback_message(issue_summary: str, evidence: str) -> str:
    """Create the concise, reviewable text sent to the documentation team."""
    summary = issue_summary.strip()
    proof = evidence.strip()
    if not summary:
        raise FeedbackSubmissionError("missing_issue_summary", "issue_summary must not be empty.")
    if not proof:
        raise FeedbackSubmissionError("missing_evidence", "evidence must not be empty.")
    return f"问题摘要：\n{summary}\n\n验证证据：\n{proof}"


@dataclass(frozen=True)
class FeedbackSubmission:
    status: str
    documentation_url: str
    submitted_at: str
    confirmation: str

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "documentation_url": self.documentation_url,
            "submitted_at": self.submitted_at,
            "confirmation": self.confirmation,
        }


class AwsDocsFeedbackSubmitter:
    """One-shot, headless Playwright interaction with the AWS Docs feedback UI."""

    timeout_ms = 20_000
    _negative_feedback = re.compile(r"(thumb.*down|not helpful|negative|不好|没帮助|不满意|反馈)", re.I)
    _provide_feedback = re.compile(r"(provide feedback|send feedback|反馈|comment|评论)", re.I)
    _submit = re.compile(r"^(submit|send|提交|发送)$", re.I)
    _success = re.compile(r"(thank you|thanks|submitted|感谢|已提交|谢谢)", re.I)

    def submit(self, documentation_url: str, issue_summary: str, evidence: str) -> dict:
        url = validate_documentation_url(documentation_url)
        message = build_feedback_message(issue_summary, evidence)
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on deployment image
            raise FeedbackSubmissionError(
                "browser_unavailable",
                "Playwright is not installed. Install dependencies and run `playwright install chromium`.",
            ) from exc

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.set_default_timeout(self.timeout_ms)
                    page.goto(url, wait_until="domcontentloaded")
                    validate_documentation_url(page.url)
                    confirmation = self._submit_on_page(page, message)
                    result = FeedbackSubmission(
                        status="submitted",
                        documentation_url=page.url,
                        submitted_at=datetime.now(timezone.utc).isoformat(),
                        confirmation=confirmation,
                    )
                    return result.as_dict()
                finally:
                    browser.close()
        except FeedbackSubmissionError:
            raise
        except PlaywrightTimeoutError as exc:
            raise FeedbackSubmissionError(
                "feedback_component_timeout",
                "AWS Docs feedback controls did not load before the timeout.",
            ) from exc
        except Exception as exc:
            raise FeedbackSubmissionError("feedback_submission_failed", str(exc)) from exc

    def _submit_on_page(self, page, message: str) -> str:
        # AWS Docs periodically changes presentation. These are semantic names, not CSS selectors.
        try:
            page.get_by_role("button", name=self._negative_feedback).first.click()
        except Exception:
            try:
                page.get_by_role("button", name=self._provide_feedback).first.click()
            except Exception as exc:
                raise FeedbackSubmissionError(
                    "feedback_not_supported",
                    "This documentation page does not expose an accessible feedback control.",
                ) from exc

        text_area = page.get_by_role("textbox").last
        try:
            text_area.fill(message)
        except Exception as exc:
            raise FeedbackSubmissionError(
                "feedback_field_not_found",
                "The AWS Docs feedback text field could not be located.",
            ) from exc

        try:
            page.get_by_role("button", name=self._submit).last.click()
        except Exception as exc:
            raise FeedbackSubmissionError(
                "feedback_submit_control_not_found",
                "The AWS Docs feedback submit button could not be located.",
            ) from exc

        try:
            page.get_by_text(self._success).first.wait_for(state="visible", timeout=self.timeout_ms)
            return page.get_by_text(self._success).first.inner_text().strip()
        except Exception as exc:
            raise FeedbackSubmissionError(
                "feedback_submission_unconfirmed",
                "The feedback form was submitted but AWS Docs did not show a success confirmation; it was not retried.",
            ) from exc
