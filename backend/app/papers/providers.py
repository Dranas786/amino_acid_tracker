# backend/app/papers/providers.py
# ---------------------------------------------------------
# PURPOSE
#   Paper search "providers" = small wrappers around external APIs
#   that return research paper metadata.
#
#   We implement two providers:
#     1) Crossref Works API (good for DOI + publisher metadata)
#     2) PubMed E-utilities (biomed papers; returns XML on fetch)
#
# OUTPUT
#   Both providers return a list[PaperHit] objects with a consistent schema,
#   so later steps can rank/score/store them uniformly.
# ---------------------------------------------------------

from __future__ import annotations
# ^ IMPORTANT:
#   Delays evaluation of type hints so you can reference classes/types
#   before they're defined and avoid circular import/type issues.

import time
import xml.etree.ElementTree as ET
# ^ Built-in XML parser. PubMed EFetch returns XML; we parse it with this.

from dataclasses import dataclass
from typing import Any, Optional

import requests
# ^ HTTP client library used to call Crossref/PubMed.


# ---------------------------------------------------------
# Identify your app politely to public APIs.
# Some APIs throttle/deny generic user agents.
# Put your email here or a project email.
# ---------------------------------------------------------
USER_AGENT = "amino-acid-tracker/0.1 (mailto:divyanshrana.433@gmail.com)"


# ---------------------------------------------------------
# Standardized paper record we return from both providers.
# This keeps the rest of the pipeline provider-agnostic.
# ---------------------------------------------------------
@dataclass
class PaperHit:
    # Which API produced this hit
    provider: str               # "crossref" | "pubmed"

    # Core metadata
    title: str
    doi: Optional[str]
    url: Optional[str]
    published_year: Optional[int]
    authors: Optional[str]

    # Abstract is often available from PubMed, often missing from Crossref
    abstract: Optional[str]

    # Provider-specific score (e.g., Crossref includes "score")
    raw_score: Optional[float] = None


# ---------------------------------------------------------
# Helper: safely parse a year value into int, with sanity bounds.
# We don't want bad years like 0 or 30000.
# ---------------------------------------------------------
def _safe_year(value: Any) -> Optional[int]:
    try:
        y = int(value)
        # simple sanity bounds for publication years
        return y if 1500 <= y <= 2100 else None
    except Exception:
        return None


# ---------------------------------------------------------
# Provider 1: Crossref search
#
# Endpoint:
#   https://api.crossref.org/works
#
# We'll use "query.bibliographic" which is strong for title-ish searching.
# Crossref typically returns DOI + URL + partial metadata.
# ---------------------------------------------------------
def crossref_search(query: str, rows: int = 20) -> list[PaperHit]:
    # Base URL for Crossref works search
    url = "https://api.crossref.org/works"

    # Query params:
    # - query.bibliographic: good generic search field
    # - rows: number of results we want back
    params = {
        "query.bibliographic": query,
        "rows": rows,
    }

    # Identify ourselves
    headers = {"User-Agent": USER_AGENT}

    # Make HTTP GET request
    # - timeout prevents hanging forever if network/API is slow
    r = requests.get(url, params=params, headers=headers, timeout=60)

    # If status != 2xx, raise an exception immediately
    r.raise_for_status()

    # Crossref responses are JSON
    data = r.json()

    # Crossref nests results like: { "message": { "items": [...] } }
    items = data.get("message", {}).get("items", []) or [] # last [] helps if items exist but is "" or None

    out: list[PaperHit] = []

    # Loop over each returned item and normalize into PaperHit
    for it in items:
        # Title is usually a list; take first element
        title = (it.get("title") or [""])[0] or ""
        title = title.strip()
        if not title:
            # skip empty titles
            continue

        # DOI and URL are typically present
        doi = it.get("DOI")
        url_ = it.get("URL")

        # Crossref includes a numeric score sometimes
        score = it.get("score")

        # Attempt to parse publication year from "issued"
        # issued: { "date-parts": [[YYYY, MM, DD]] }
        year = None
        issued = it.get("issued", {}).get("date-parts")
        if (
            issued # is not None or empty
            and isinstance(issued, list)
            and isinstance(issued[0], list)
            and issued[0] # is not an empty or None double list
        ):
            year = _safe_year(issued[0][0])

        # Build author string "First Last, First Last, ..."
        authors = None
        if "author" in it and isinstance(it["author"], list):
            names = []
            for a in it["author"]:
                given = (a.get("given") or "").strip()
                family = (a.get("family") or "").strip()
                full = (given + " " + family).strip()
                if full:
                    names.append(full)
            if names:
                # keep it short; store first 8 authors
                authors = ", ".join(names[:8])

        out.append(
            PaperHit(
                provider="crossref",
                title=title,
                doi=doi,
                url=url_,
                published_year=year,
                authors=authors,
                abstract=None,  # Crossref often doesn't provide abstracts
                raw_score=float(score) if score is not None else None,
            )
        )

    return out


# ---------------------------------------------------------
# Provider 2: PubMed search
#
# PubMed has a two-step flow:
#   1) ESearch -> returns a list of PMIDs (paper IDs)
#   2) EFetch  -> fetches full metadata for those PMIDs (XML)
#
# Why 2 steps?
#   - PubMed search endpoint returns IDs only (fast)
#   - Details come from a separate fetch endpoint
# ---------------------------------------------------------
def pubmed_search(query: str, retmax: int = 20) -> list[PaperHit]:
    headers = {"User-Agent": USER_AGENT}

    # ---- Step 1: ESearch (JSON) -> list of PMIDs ----
    esearch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

    r = requests.get(
        esearch,
        params={
            "db": "pubmed",        # search PubMed database
            "term": query,         # the search query
            "retmode": "json",     # ask for JSON output
            "retmax": retmax,      # number of ids
        },
        headers=headers,
        timeout=60,
    )
    r.raise_for_status()

    # JSON shape: {"esearchresult": {"idlist": ["123", "456", ...]}}
    pmids = r.json().get("esearchresult", {}).get("idlist", []) or []

    # If no results, return empty list
    if not pmids:
        return []

    # Be polite: NCBI asks not to hammer endpoints too quickly.
    time.sleep(0.34)

    # ---- Step 2: EFetch (XML) -> paper metadata ----
    efetch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    r2 = requests.get(
        efetch,
        params={
            "db": "pubmed",
            "id": ",".join(pmids),  # fetch multiple PMIDs at once
            "retmode": "xml",       # metadata in XML
        },
        headers=headers,
        timeout=60,
    )
    r2.raise_for_status()

    # Parse the XML response into an element tree
    root = ET.fromstring(r2.text)

    out: list[PaperHit] = []

    # PubMed XML contains multiple <PubmedArticle> entries
    for article in root.findall(".//PubmedArticle"):
        # Title is in ArticleTitle
        title = (article.findtext(".//ArticleTitle") or "").strip()
        if not title:
            continue

        # Publication year typically appears here; not always present
        year = None
        y = article.findtext(".//PubDate/Year")
        if y:
            year = _safe_year(y)

        # DOI exists in ArticleId nodes with IdType="doi"
        doi = None
        for idnode in article.findall(".//ArticleId"):
            if idnode.attrib.get("IdType") == "doi":
                doi = (idnode.text or "").strip() or None
                break

        # PMID is the PubMed identifier; build a stable URL
        pmid = (article.findtext(".//PMID") or "").strip()
        url_ = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None

        # Authors are inside AuthorList/Author
        names = []
        for a in article.findall(".//AuthorList/Author"):
            last = (a.findtext("LastName") or "").strip()
            fore = (a.findtext("ForeName") or "").strip()
            nm = (fore + " " + last).strip()
            if nm:
                names.append(nm)
        authors = ", ".join(names[:8]) if names else None

        # Abstract can have multiple AbstractText blocks; join them
        abstract_parts = []
        for t in article.findall(".//Abstract/AbstractText"):
            txt = (t.text or "").strip()
            if txt:
                abstract_parts.append(txt)
        abstract = " ".join(abstract_parts) if abstract_parts else None

        out.append(
            PaperHit(
                provider="pubmed",
                title=title,
                doi=doi,
                url=url_,
                published_year=year,
                authors=authors,
                abstract=abstract,
                raw_score=None,  # PubMed doesn't return a "score" here
            )
        )

    return out
