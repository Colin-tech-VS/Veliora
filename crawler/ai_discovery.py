"""Extraction IA des liens d'annonces quand les heuristiques ne suffisent pas."""

from __future__ import annotations

import json
import logging
import os
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from crawler.extractors import is_excluded_listing_url
from crawler.listing_guard import validate_listing_url

logger = logging.getLogger(__name__)

_HTML_MAX_CHARS = 36_000
_TIMEOUT_SEC = 28


def ai_discovery_enabled() -> bool:
    raw = (os.getenv("CRAWL_AI_DISCOVERY") or "").strip().lower()
    if raw in ("0", "false", "no"):
        return False
    if raw in ("1", "true", "yes"):
        return True
    from crm.ai.config import AI_API_KEY

    return bool(AI_API_KEY.strip())


def _compact_html(html: str, page_url: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    links: list[str] = []
    for a in soup.find_all("a", href=True)[:120]:
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full = urljoin(page_url, href).split("#")[0]
        if full.startswith("http"):
            label = (a.get_text(" ", strip=True) or "")[:80]
            links.append(f"{full} | {label}")
    blob = "LIENS:\n" + "\n".join(links[:80]) + "\n\nTEXTE:\n" + text
    return blob[:_HTML_MAX_CHARS]


def _parse_url_list(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if x]
    except json.JSONDecodeError:
        pass
    urls = re.findall(r"https?://[^\s\"'<>]+", raw)
    return [u.rstrip(".,);]") for u in urls]


def ai_extract_listing_urls(
    html: str,
    page_url: str,
    base_url: str,
    *,
    limit: int = 40,
) -> list[str]:
    """Demande au LLM configuré (Groq, etc.) les URLs de fiches annonces visibles."""
    if not ai_discovery_enabled() or not html:
        return []

    from crm.ai.config import AI_API_KEY, AI_BASE_URL, AI_MODEL, AI_PROVIDER
    from crm.ai.providers.openai_compat import _PROVIDERS

    if not AI_API_KEY.strip():
        return []

    provider = (AI_PROVIDER or "groq").strip().lower()
    meta = _PROVIDERS.get(provider) or _PROVIDERS["groq"]
    base = (AI_BASE_URL or meta["base_url"]).rstrip("/")
    model = (AI_MODEL or meta["default_model"]).strip()
    compact = _compact_html(html, page_url)
    if len(compact) < 200:
        return []

    prompt = (
        "Tu analyses une page web immobilière française. "
        "Renvoie UNIQUEMENT un tableau JSON d'URLs absolues de FICHES annonce "
        "(pas pages de recherche, login, blog, agence). "
        f"Maximum {limit} URLs, même domaine que {base_url or page_url}. "
        "Exemple: [\"https://exemple.fr/annonce/123\"]"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Page: {page_url}\n\n{compact}"},
        ],
        "temperature": 0.1,
        "max_tokens": 1200,
    }
    try:
        resp = requests.post(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {AI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=_TIMEOUT_SEC,
        )
        if not resp.ok:
            logger.debug("AI discovery HTTP %s: %s", resp.status_code, resp.text[:200])
            return []
        content = (
            (resp.json().get("choices") or [{}])[0]
            .get("message", {})
            .get("content", "")
        )
    except Exception as exc:
        logger.debug("AI discovery failed: %s", exc)
        return []

    out: list[str] = []
    seen: set[str] = set()
    base_host = urlparse(base_url or page_url).netloc.lower().replace("www.", "")

    for raw_url in _parse_url_list(content):
        u = raw_url.split("#")[0].rstrip("/")
        if not u.startswith("http") or u in seen:
            continue
        host = urlparse(u).netloc.lower().replace("www.", "")
        if base_host and host and base_host not in host and host not in base_host:
            continue
        if is_excluded_listing_url(u):
            continue
        ok, _ = validate_listing_url(u)
        if not ok:
            ok, _ = validate_listing_url_import_relaxed(u)
            if not ok:
                continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out


def validate_listing_url_import_relaxed(url: str) -> tuple[bool, str]:
    """Assouplit la validation pour les URLs proposées par l'IA."""
    if not url or not url.startswith("http"):
        return False, "URL invalide"
    if is_excluded_listing_url(url):
        return False, "exclue"
    path = urlparse(url).path.lower()
    if re.search(r"\d{5,}", path) or re.search(
        r"/(?:annonce|detail|listing|property|ad|bien|fiche)/", path, re.I
    ):
        return True, ""
    return False, "pas une fiche"
