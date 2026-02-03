# app/papers/extractor.py
# ---------------------------------------------------------
# Full-text retrieval (offline-first)
#
# Goal:
# - Given a paper candidate (doi/url/title), try to retrieve
#   machine-readable full text.
# - Prefer PMC XML whenever possible.
#
# Strategy:
# 1) Disk cache first (offline-friendly).
# 2) Try to resolve IDs (doi/pmid/pmcid) via PMC ID Converter API.
# 3) If PMCID exists -> try to fetch XML from PMC (best for tables).
# 4) If we can't fetch full text -> return metadata + warnings.
#
# Notes:
# - This module does not write to the DB.
# - It only fetches and returns content + metadata.
# ---------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import hashlib
import json
import os
import re
import time

import requests


# -----------------------------
# Configuration (env vars)
# -----------------------------

DEFAULT_CACHE_DIR = Path(os.getenv("PAPERS_CACHE_DIR", "data/papers_cache"))
DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

NCBI_TOOL = os.getenv("NCBI_TOOL", "amino-acid-tracker")
NCBI_EMAIL = os.getenv("NCBI_EMAIL", "")  # recommended by NCBI, can be blank in dev

HTTP_TIMEOUT_SECS = float(os.getenv("PAPERS_HTTP_TIMEOUT", "20"))
HTTP_MAX_RETRIES = int(os.getenv("PAPERS_HTTP_RETRIES", "3"))
HTTP_BACKOFF_SECS = float(os.getenv("PAPERS_HTTP_BACKOFF", "1.5"))

# Be polite to NCBI endpoints. Keep it small.
RATE_LIMIT_SLEEP_SECS = float(os.getenv("PAPERS_RATE_LIMIT_SLEEP", "0.35"))

USER_AGENT = os.getenv(
    "PAPERS_USER_AGENT",
    "amino-acid-tracker/1.0 (contact: none)",
)


# -----------------------------
# Data model returned by fetch
# -----------------------------

@dataclass
class FullTextDoc:
    """
    Output object for the extraction pipeline.
    Think of this as "best effort": sometimes we will get full XML,
    sometimes only metadata + warnings.
    """
    title: str
    source_url: str
    doi: Optional[str] = None
    pmid: Optional[str] = None
    pmcid: Optional[str] = None

    # Full text content (preferred)
    pmc_xml: Optional[str] = None

    # Metadata for debugging / provenance
    provider: str = "unknown"
    # [] is shared and will cause bugs, field gives every instance a new list
    warnings: list[str] = field(default_factory=list)


# -----------------------------
# Helpers: ID parsing
# -----------------------------

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
_PMCID_RE = re.compile(r"\bPMC\d+\b", re.IGNORE:=(None))  # dummy to appease linters
_PMCID_RE = re.compile(r"\bPMC\d+\b", re.IGNORECASE)
_PMID_RE = re.compile(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|/pubmed/)(\d+)", re.IGNORECASE)


def _extract_doi(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _DOI_RE.search(text)
    if not m:
        return None
    return m.group(0).strip().lower()


def _extract_pmcid(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = _PMCID_RE.search(text)
    if not m:
        return None
    # Normalize to uppercase "PMC12345"
    return m.group(0).upper()


def _extract_pmid_from_url(url: str | None) -> Optional[str]:
    if not url:
        return None
    m = _PMID_RE.search(url)
    return m.group(1) if m else None


def _stable_key(*parts: str) -> str:
    joined = "||".join([p for p in parts if p])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


# -----------------------------
# HTTP helper with retries
# -----------------------------

def _http_get(url: str, *, params: dict | None = None, headers: dict | None = None) -> requests.Response:
    """
    Requests GET with a small retry + backoff loop.
    """
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)

    last_exc: Optional[Exception] = None
    for attempt in range(1, HTTP_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers=h, timeout=HTTP_TIMEOUT_SECS)
            # Retry on transient server errors
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(HTTP_BACKOFF_SECS * attempt)
                last_exc = RuntimeError(f"HTTP {resp.status_code} for {url}")
                continue # continue skips the return and goes into the next for loop
            return resp # could imagine as else returning resp here
        except Exception as e:
            last_exc = e
            time.sleep(HTTP_BACKOFF_SECS * attempt)

    # If we got here, retries were exhausted
    raise RuntimeError(f"GET failed after {HTTP_MAX_RETRIES} tries: {url}") from last_exc


# -----------------------------
# Cache helpers
# -----------------------------

def _cache_paths(cache_dir: Path, key: str) -> tuple[Path, Path]:
    meta_path = cache_dir / f"{key}.json"
    xml_path = cache_dir / f"{key}.xml"
    return meta_path, xml_path


def _read_cache(cache_dir: Path, key: str) -> Optional[FullTextDoc]:
    meta_path, xml_path = _cache_paths(cache_dir, key)
    if not meta_path.exists():
        return None

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        doc = FullTextDoc(
            title=meta.get("title", ""),
            source_url=meta.get("source_url", ""),
            doi=meta.get("doi"),
            pmid=meta.get("pmid"),
            pmcid=meta.get("pmcid"),
            provider=meta.get("provider", "unknown"),
            warnings=list(meta.get("warnings", [])),
        )
        if xml_path.exists():
            doc.pmc_xml = xml_path.read_text(encoding="utf-8", errors="ignore")
        return doc
    except Exception:
        # Cache corruption shouldn't crash pipeline; just ignore cache.
        return None


def _write_cache(cache_dir: Path, key: str, doc: FullTextDoc) -> None:
    meta_path, xml_path = _cache_paths(cache_dir, key)

    meta = {
        "title": doc.title,
        "source_url": doc.source_url,
        "doi": doc.doi,
        "pmid": doc.pmid,
        "pmcid": doc.pmcid,
        "provider": doc.provider,
        "warnings": doc.warnings,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    if doc.pmc_xml:
        xml_path.write_text(doc.pmc_xml, encoding="utf-8")


# -----------------------------
# ID resolution (PMC ID Converter API)
# -----------------------------

def resolve_ids_via_idconv(*, doi: Optional[str], pmid: Optional[str], pmcid: Optional[str]) -> dict:
    """
    Uses PMC ID Converter API to convert between DOI/PMID/PMCID when possible.

    Base URL documented by PMC:
      https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/
    """
    # If we already have a PMCID, we don't need conversion.
    if pmcid:
        return {"doi": doi, "pmid": pmid, "pmcid": pmcid}

    # The API accepts a comma-separated ids list.
    ids = []
    if doi:
        ids.append(doi)
    if pmid:
        ids.append(pmid)

    if not ids: # dont have doi, pmid, or pmcid
        return {"doi": doi, "pmid": pmid, "pmcid": pmcid}

    url = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
    params = {
        "ids": ",".join(ids),
        "format": "json",
        "tool": NCBI_TOOL,
    }
    if NCBI_EMAIL:
        params["email"] = NCBI_EMAIL

    # Polite rate limiting
    time.sleep(RATE_LIMIT_SLEEP_SECS)

    resp = _http_get(url, params=params)
    if resp.status_code != 200:
        return {"doi": doi, "pmid": pmid, "pmcid": pmcid, "warning": f"idconv http {resp.status_code}"}

    data = resp.json()

    # The API response format contains records; we pick the first match.
    # Be defensive: structure can vary slightly.
    records = []
    if isinstance(data, dict):
        records = data.get("records") or data.get("Records") or []

    if not records:
        return {"doi": doi, "pmid": pmid, "pmcid": pmcid}

    rec = records[0]
    # Normalize outputs
    out_doi = (rec.get("doi") or doi)
    out_pmid = (str(rec.get("pmid")) if rec.get("pmid") else pmid)
    out_pmcid = (rec.get("pmcid") or pmcid)
    if out_pmcid:
        out_pmcid = str(out_pmcid).upper()

    return {"doi": out_doi.lower() if out_doi else None, "pmid": out_pmid, "pmcid": out_pmcid}


# -----------------------------
# PMC full text fetch
# -----------------------------

def fetch_pmc_xml(pmcid: str) -> Optional[str]:
    """
    Best-effort attempt to fetch full-text XML for a PMCID.

    We try an NCBI E-utilities efetch for PMC first (often works),
    because it's a stable API surface.
    """
    pmcid = pmcid.upper().strip()

    # EFetch for PMC database. Note: PMCID string usually works as id.
    # If it fails, we just return None and let pipeline mark needs_review.
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {
        "db": "pmc",
        "id": pmcid,
        "retmode": "xml",
        "tool": NCBI_TOOL,
    }
    if NCBI_EMAIL:
        params["email"] = NCBI_EMAIL

    time.sleep(RATE_LIMIT_SLEEP_SECS)
    resp = _http_get(url, params=params)

    if resp.status_code != 200:
        return None

    text = resp.text
    # Some failures still return XML-ish error pages; basic sanity check:
    if "<error" in text.lower():
        return None
    if len(text.strip()) < 200:
        return None

    return text


# -----------------------------
# Public entrypoint
# -----------------------------

def fetch_fulltext_offline_first(
    *,
    title: str,
    url: str,
    doi: Optional[str] = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> FullTextDoc:
    """
    Main function used by the extraction pipeline.

    Inputs:
      - title/url/doi from PaperCandidate (or derived)
    Output:
      - FullTextDoc with pmc_xml if we got it, otherwise warnings.
    """
    # Derive IDs from url/title if missing
    doi = (doi or _extract_doi(url) or _extract_doi(title))
    pmcid = _extract_pmcid(url) or _extract_pmcid(title)
    pmid = _extract_pmid_from_url(url)

    key = _stable_key(title[:120], url[:200], doi or "", pmcid or "", pmid or "")
    cached = _read_cache(cache_dir, key)
    if cached is not None:
        return cached

    doc = FullTextDoc(
        title=title,
        source_url=url,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        provider="idconv+pmc",
        warnings=[],
    )

    # 1) Resolve IDs if we don't have PMCID
    try:
        resolved = resolve_ids_via_idconv(doi=doc.doi, pmid=doc.pmid, pmcid=doc.pmcid)
        doc.doi = resolved.get("doi") or doc.doi
        doc.pmid = resolved.get("pmid") or doc.pmid
        doc.pmcid = resolved.get("pmcid") or doc.pmcid
        if resolved.get("warning"):
            doc.warnings.append(str(resolved["warning"]))
    except Exception as e:
        doc.warnings.append(f"idconv failed: {e}")

    # 2) If we have PMCID, try to fetch PMC XML
    if doc.pmcid:
        try:
            xml = fetch_pmc_xml(doc.pmcid)
            if xml:
                doc.pmc_xml = xml
            else:
                doc.warnings.append("pmc_xml not available (efetch returned empty or error)")
        except Exception as e:
            doc.warnings.append(f"pmc_xml fetch failed: {e}")
    else:
        doc.warnings.append("no PMCID (cannot fetch PMC full text)")

    _write_cache(cache_dir, key, doc)
    return doc
