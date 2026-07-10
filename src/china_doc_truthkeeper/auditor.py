from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

from .config import Settings


def fetch_document(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http(s) documentation URLs are supported")
    response = requests.get(url, timeout=20, headers={"User-Agent": "China-Doc-TruthKeeper/1.0"})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup(["script", "style", "noscript"]): node.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    return {"url": response.url, "title": soup.title.get_text(strip=True) if soup.title else "", "text": text[:20000]}


def audit_document(url: str, known_checks: list[dict], settings: Settings) -> dict:
    document = fetch_document(url)
    result = {
        "document": {"url": document["url"], "title": document["title"]},
        "known_checks": known_checks,
        "finding": "No LLM analysis performed: set QWEN_API_KEY to enable Qwen3-235B-VL document analysis.",
        "document_excerpt": document["text"][:1500],
    }
    # The service remains usable without a key. The public OpenAI-compatible endpoint is deliberately opt-in.
    if settings.qwen_api_key:
        result["qwen"] = {"model": settings.qwen_model, "base_url": settings.qwen_base_url, "status": "configured"}
    return result
