#!/usr/bin/env python3
"""Seagrass Research Radar.

Daily scholarly-literature harvester for a static GitHub Pages dashboard.

Sources
-------
* Crossref (publisher-deposited metadata; backbone)
* OpenAlex (broad scholarly index)
* Europe PMC (PubMed + life-science and related sources)
* Semantic Scholar (independent scholarly graph; optional API key)
* DOAJ OAI-PMH (extra coverage for smaller open-access journals)

The script keeps a local JSON cache in docs/data/papers.json, deduplicates by DOI
and title, preserves first-seen dates, tags themes/species/publishers, estimates
study countries from title/abstract text, and writes coverage/digest metadata.

It intentionally does not scrape publisher websites. Publisher HTML changes often,
and broad registries/indexes are more reliable and more inclusive of small journals.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "docs" / "data"
PAPERS_FILE = DATA_DIR / "papers.json"
STATUS_FILE = DATA_DIR / "status.json"
DIGEST_FILE = DATA_DIR / "digest.json"

USER_AGENT = os.getenv(
    "USER_AGENT",
    "SeagrassResearchRadar/2.0 (+https://github.com/; contact via CROSSREF_MAILTO)",
)
TODAY = datetime.now(timezone.utc).date()
NOW_ISO = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

# The daily job deliberately overlaps recent days. Indexes can ingest records late,
# and overlap makes the job resilient to a failed day.
RETRIEVAL_LOOKBACK_DAYS = int(os.getenv("RETRIEVAL_LOOKBACK_DAYS", "21"))
OPENALEX_PUBLICATION_LOOKBACK_DAYS = int(os.getenv("OPENALEX_PUBLICATION_LOOKBACK_DAYS", "180"))
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "1095"))
MAX_RESULTS_PER_QUERY = int(os.getenv("MAX_RESULTS_PER_QUERY", "200"))
MIN_RELEVANCE_SCORE = int(os.getenv("MIN_RELEVANCE_SCORE", "7"))

OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "").strip()
CROSSREF_MAILTO = os.getenv("CROSSREF_MAILTO", "").strip()
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna").strip()
AI_SUMMARY_LIMIT = int(os.getenv("AI_SUMMARY_LIMIT", "12"))
ENABLE_DOAJ = os.getenv("ENABLE_DOAJ", "1") not in {"0", "false", "False"}
ENABLE_EUROPE_PMC = os.getenv("ENABLE_EUROPE_PMC", "1") not in {"0", "false", "False"}
ENABLE_SEMANTIC_SCHOLAR = os.getenv("ENABLE_SEMANTIC_SCHOLAR", "1") not in {"0", "false", "False"}

# Retrieval vocabulary: broad enough to catch papers that never use the word
# "seagrass" in the title. Genera are searched independently where necessary.
SEARCH_TERMS = [
    "seagrass",
    "seagrasses",
    "eelgrass",
    '"marine angiosperm"',
    '"marine angiosperms"',
    '"marine phanerogam"',
    '"marine phanerogams"',
    '"phanerogames marines"',
    "Zostera",
    "Nanozostera",
    "Heterozostera",
    "Posidonia",
    "Cymodocea",
    "Halodule",
    "Halophila",
    "Thalassia",
    "Syringodium",
    "Enhalus",
    "Thalassodendron",
    "Amphibolis",
    "Phyllospadix",
    "Ruppia",
]

GENERA = [
    "Zostera", "Nanozostera", "Heterozostera", "Posidonia", "Cymodocea",
    "Halodule", "Halophila", "Thalassia", "Syringodium", "Enhalus",
    "Thalassodendron", "Amphibolis", "Phyllospadix", "Ruppia",
]

# High-confidence legacy spellings / taxonomic combinations. Genus-level searching
# already captures many historic records, but these help scoring and display.
LEGACY_NAMES = [
    "Zostera noltii", "Nanozostera noltii", "Zostera capricorni",
    "Zostera muelleri", "Zostera tasmanica", "Heterozostera tasmanica",
    "Zostera japonica", "Zostera americana", "Phucagrostis major",
    "Cymodocea nodosa", "Thalassia hemprichii", "Thalassia testudinum",
]

COMMON_NAMES = [
    "turtle grass", "manatee grass", "shoal grass", "paddle weed",
    "wire weed", "surfgrass", "tape grass",
]

THEMES: dict[str, list[str]] = {
    "Restoration": ["restor", "transplant", "reveget", "seed", "planting", "recovery", "rehabilitat", "nursery"],
    "Climate & heat": ["climate", "warming", "heatwave", "heat wave", "temperature", "thermal", "marine heat", "ocean acidification"],
    "Blue carbon": ["blue carbon", "carbon stock", "carbon storage", "carbon seques", "sediment carbon", "greenhouse gas", "methane", "co2"],
    "Fisheries & fauna": ["fish", "fisheries", "nursery habitat", "fauna", "invertebrate", "megafauna", "dugong", "turtle", "seahorse", "grazing"],
    "Biodiversity & ecology": ["biodiversity", "community", "ecology", "food web", "trophic", "ecosystem function", "species richness", "assemblage"],
    "Mapping & remote sensing": ["remote sensing", "satellite", "drone", "uav", "mapping", "acoustic", "sonar", "lidar", "machine learning", "earth observation"],
    "Water quality & nutrients": ["nutrient", "nitrogen", "phosph", "eutroph", "water quality", "sewage", "wastewater", "runoff", "turbidity", "sediment load", "light limitation"],
    "Genetics & genomics": ["genetic", "genomic", "population structure", "connectivity", "gene flow", "transcriptom", "epigen", "snp", "microsatellite"],
    "Physiology & productivity": ["photosynth", "productivity", "growth", "physiolog", "respiration", "biomass", "leaf growth", "primary production"],
    "Conservation & management": ["conservation", "management", "protected area", "marine protected area", "policy", "governance", "threat", "anchor", "dredg", "coastal development"],
    "Pollution & contaminants": ["pollut", "contamin", "microplastic", "heavy metal", "pesticide", "herbicide", "oil spill", "pfas"],
    "Microbiome & disease": ["microbi", "pathogen", "disease", "wasting disease", "labyrinthula", "fung", "bacter", "virome"],
    "Biogeochemistry": ["biogeochem", "oxygen", "sulfide", "sulphide", "denitrification", "nitrogen fixation", "porewater", "redox"],
    "Social & economic": ["livelihood", "food security", "economic", "valuation", "social", "human well", "governance", "community-based", "small-scale fisher"],
}

# Canonical groups used in the dashboard coverage audit. Matching occurs against
# publisher + journal metadata, which helps when a platform brand (e.g. Cell Press)
# is deposited under its parent publisher.
PUBLISHER_GROUPS: dict[str, list[str]] = {
    "Springer Nature": ["springer nature", "springer", "nature portfolio", "nature research", "biomed central", "bmc"],
    "Elsevier": ["elsevier", "cell press"],
    "Wiley": ["wiley", "john wiley", "hindawi"],
    "Frontiers": ["frontiers media", "frontiers"],
    "MDPI": ["mdpi"],
    "PLOS": ["public library of science", "plos"],
    "IOP Publishing": ["iop publishing", "institute of physics"],
    "Taylor & Francis": ["taylor & francis", "taylor and francis", "informa uk", "informa ltd", "routledge"],
    "SAGE": ["sage publications", "sage publishing", "sage journals"],
    "Oxford University Press": ["oxford university press", "oup"],
    "Cambridge University Press": ["cambridge university press"],
    "Royal Society": ["royal society"],
    "AAAS / Science": ["american association for the advancement of science", "aaas"],
    "PNAS / NAS": ["national academy of sciences", "proceedings of the national academy"],
    "Copernicus": ["copernicus publications", "copernicus gmbh"],
    "CSIRO Publishing": ["csiro publishing"],
    "Inter-Research": ["inter-research", "inter research"],
    "BioOne": ["bioone"],
    "PeerJ": ["peerj"],
    "American Geophysical Union": ["american geophysical union", "agu"],
}

# A pragmatic world-location dictionary for map inference from title/abstract.
# These are country/territory centroids, not study-site coordinates. Author
# affiliations are intentionally not used as study-location evidence.
COUNTRIES: dict[str, tuple[float, float, list[str]]] = {
    "Australia": (-25.2744, 133.7751, ["australia", "queensland", "western australia", "south australia", "tasmania", "new south wales", "great barrier reef", "shark bay", "moreton bay"]),
    "United Kingdom": (55.3781, -3.4360, ["united kingdom", " uk ", "britain", "england", "wales", "scotland", "northern ireland", "irish sea", "solent", "pembrokeshire"]),
    "Ireland": (53.1424, -7.6921, ["ireland", "irish coast"]),
    "United States": (37.0902, -95.7129, ["united states", " usa ", "u.s.a.", "florida", "chesapeake bay", "california", "texas", "long island", "puget sound", "hawaii"]),
    "Canada": (56.1304, -106.3468, ["canada", "british columbia", "nova scotia", "new brunswick"]),
    "Mexico": (23.6345, -102.5528, ["mexico", "yucatan", "gulf of california", "baja california"]),
    "Belize": (17.1899, -88.4976, ["belize"]),
    "Cuba": (21.5218, -77.7812, ["cuba"]),
    "Bahamas": (25.0343, -77.3963, ["bahamas"]),
    "Brazil": (-14.2350, -51.9253, ["brazil", "brasil"]),
    "Chile": (-35.6751, -71.5430, ["chile"]),
    "Argentina": (-38.4161, -63.6167, ["argentina"]),
    "Colombia": (4.5709, -74.2973, ["colombia"]),
    "Ecuador": (-1.8312, -78.1834, ["ecuador", "galapagos"]),
    "Venezuela": (6.4238, -66.5897, ["venezuela"]),
    "France": (46.2276, 2.2137, ["france", "french mediterranean", "corsica"]),
    "Spain": (40.4637, -3.7492, ["spain", "balearic", "ibiza", "mallorca"]),
    "Portugal": (39.3999, -8.2245, ["portugal", "ria formosa"]),
    "Italy": (41.8719, 12.5674, ["italy", "sardinia", "sicily", "adriatic"]),
    "Greece": (39.0742, 21.8243, ["greece", "aegean"]),
    "Croatia": (45.1, 15.2, ["croatia"]),
    "Turkey": (38.9637, 35.2433, ["turkey", "türkiye"]),
    "Cyprus": (35.1264, 33.4299, ["cyprus"]),
    "Malta": (35.9375, 14.3754, ["malta"]),
    "Netherlands": (52.1326, 5.2913, ["netherlands", "dutch wadden", "wadden sea"]),
    "Germany": (51.1657, 10.4515, ["germany", "german wadden"]),
    "Denmark": (56.2639, 9.5018, ["denmark"]),
    "Sweden": (60.1282, 18.6435, ["sweden"]),
    "Norway": (60.4720, 8.4689, ["norway"]),
    "Finland": (61.9241, 25.7482, ["finland"]),
    "Poland": (51.9194, 19.1451, ["poland", "baltic sea"]),
    "Egypt": (26.8206, 30.8025, ["egypt", "red sea egypt"]),
    "Israel": (31.0461, 34.8516, ["israel"]),
    "Saudi Arabia": (23.8859, 45.0792, ["saudi arabia", "red sea"]),
    "United Arab Emirates": (23.4241, 53.8478, ["united arab emirates", " uae ", "abu dhabi"]),
    "Qatar": (25.3548, 51.1839, ["qatar"]),
    "Bahrain": (26.0667, 50.5577, ["bahrain"]),
    "Oman": (21.4735, 55.9754, ["oman"]),
    "Kenya": (-0.0236, 37.9062, ["kenya"]),
    "Tanzania": (-6.3690, 34.8888, ["tanzania", "zanzibar"]),
    "Mozambique": (-18.6657, 35.5296, ["mozambique"]),
    "Madagascar": (-18.7669, 46.8691, ["madagascar"]),
    "South Africa": (-30.5595, 22.9375, ["south africa"]),
    "Mauritius": (-20.3484, 57.5522, ["mauritius"]),
    "Seychelles": (-4.6796, 55.4920, ["seychelles"]),
    "India": (20.5937, 78.9629, ["india", "andaman", "lakshadweep"]),
    "Sri Lanka": (7.8731, 80.7718, ["sri lanka"]),
    "Bangladesh": (23.6850, 90.3563, ["bangladesh"]),
    "Pakistan": (30.3753, 69.3451, ["pakistan"]),
    "Maldives": (3.2028, 73.2207, ["maldives"]),
    "China": (35.8617, 104.1954, ["china", "hainan", "yellow sea"]),
    "Japan": (36.2048, 138.2529, ["japan", "okinawa"]),
    "South Korea": (35.9078, 127.7669, ["south korea", "republic of korea", "korea"]),
    "Taiwan": (23.6978, 120.9605, ["taiwan"]),
    "Philippines": (12.8797, 121.7740, ["philippines"]),
    "Indonesia": (-0.7893, 113.9213, ["indonesia", "sulawesi", "bali", "java", "sumatra"]),
    "Malaysia": (4.2105, 101.9758, ["malaysia", "sabah", "sarawak"]),
    "Singapore": (1.3521, 103.8198, ["singapore"]),
    "Thailand": (15.8700, 100.9925, ["thailand"]),
    "Vietnam": (14.0583, 108.2772, ["vietnam", "viet nam"]),
    "Cambodia": (12.5657, 104.9910, ["cambodia"]),
    "Myanmar": (21.9162, 95.9560, ["myanmar", "burma"]),
    "New Zealand": (-40.9006, 174.8860, ["new zealand", "aotearoa"]),
    "Papua New Guinea": (-6.3150, 143.9555, ["papua new guinea"]),
    "Fiji": (-17.7134, 178.0650, ["fiji"]),
    "Solomon Islands": (-9.6457, 160.1562, ["solomon islands"]),
    "Vanuatu": (-15.3767, 166.9592, ["vanuatu"]),
}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_bytes(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, retries: int = 4) -> bytes:
    if params:
        qs = urllib.parse.urlencode(params, doseq=True)
        url = f"{url}{'&' if '?' in url else '?'}{qs}"
    req_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            code = getattr(exc, "code", None)
            if code and code not in {408, 429, 500, 502, 503, 504}:
                break
            time.sleep(min(10, 2 ** attempt))
    raise RuntimeError(f"Request failed: {url}: {last_error}")


def fetch_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    return json.loads(fetch_bytes(url, params=params, headers={"Accept": "application/json", **(headers or {})}).decode("utf-8"))


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    s = html.unescape(str(value))
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def normalize_title(title: str) -> str:
    s = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_doi(doi: str | None) -> str:
    if not doi:
        return ""
    d = clean_text(doi).lower()
    d = re.sub(r"^doi:\s*", "", d)
    d = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", d)
    return d.rstrip(" .);,")


def date_from_parts(parts: Any) -> str:
    try:
        p = parts[0]
        y = int(p[0]); m = int(p[1]) if len(p) > 1 else 1; d = int(p[2]) if len(p) > 2 else 1
        return date(y, m, d).isoformat()
    except Exception:
        return ""


def safe_date(value: str) -> str:
    if not value:
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return ""
    m = re.match(r"^(\d{4})-(\d{1,2})$", value)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-01"
    m = re.match(r"^(\d{4})$", value)
    return f"{m.group(1)}-01-01" if m else ""


def reconstruct_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    pairs: list[tuple[int, str]] = []
    for word, positions in index.items():
        pairs.extend((int(pos), word) for pos in positions)
    pairs.sort()
    return " ".join(word for _, word in pairs)


def compact_authors(authors: Iterable[str], limit: int = 30) -> list[str]:
    out: list[str] = []
    seen = set()
    for raw in authors:
        a = clean_text(raw)
        key = a.lower()
        if a and key not in seen:
            seen.add(key); out.append(a)
        if len(out) >= limit:
            break
    return out


def infer_publisher_group(publisher: str, journal: str = "") -> str:
    hay = f" {publisher} {journal} ".lower()
    for group, aliases in PUBLISHER_GROUPS.items():
        if any(alias in hay for alias in aliases):
            return group
    return clean_text(publisher) or "Other / independent"


def infer_countries(title: str, abstract: str) -> list[dict[str, Any]]:
    # Add spaces to reduce short-token false positives such as "uk" inside words.
    hay = f" {title} {abstract} ".lower()
    found = []
    for country, (lat, lon, aliases) in COUNTRIES.items():
        if any(alias in hay for alias in aliases):
            found.append({"country": country, "lat": lat, "lon": lon, "method": "title/abstract text"})
    return found[:5]


def classify(title: str, abstract: str) -> dict[str, Any]:
    title_l = f" {title.lower()} "
    text = f" {title} {abstract} ".lower()
    score = 0
    matched: set[str] = set()
    species: set[str] = set()

    strong = {
        "seagrass": 12, "seagrasses": 12, "eelgrass": 11,
        "marine angiosperm": 11, "marine angiosperms": 11,
        "marine phanerogam": 11, "marine phanerogams": 11,
        "phanerogames marines": 10, "phanérogames marines": 10,
    }
    for term, weight in strong.items():
        if term in text:
            score += weight + (5 if term in title_l else 0)
            matched.add(term)

    for genus in GENERA:
        pattern = rf"\b{re.escape(genus.lower())}\b"
        if re.search(pattern, text):
            score += 7 + (4 if re.search(pattern, title_l) else 0)
            matched.add(genus)
            species.add(genus)

    for nm in LEGACY_NAMES:
        if nm.lower() in text:
            score += 6
            matched.add(nm)
            species.add(nm)

    for nm in COMMON_NAMES:
        if nm in text and any(ctx in text for ctx in ["marine", "coast", "estuar", "meadow", "seagrass"]):
            score += 5
            matched.add(nm)

    # Ruppia and Posidonia have important non-seagrass meanings. Require ecological
    # context unless another strong seagrass term is present.
    if "ruppia" in text and not any(x in text for x in ["seagrass", "marine", "coast", "estuar", "lagoon", "subtidal", "intertidal"]):
        score -= 8
    if re.search(r"\bposidonia\b", text) and not any(x in text for x in ["seagrass", "oceanica", "australis", "meadow", "marine", "mediterranean"]):
        score -= 8

    themes = [name for name, needles in THEMES.items() if any(n in text for n in needles)]
    return {
        "relevance_score": max(score, 0),
        "matched_terms": sorted(matched, key=str.lower),
        "species": sorted(species, key=str.lower),
        "themes": themes or ["General seagrass science"],
    }


def canonical_record(**kwargs: Any) -> dict[str, Any]:
    title = clean_text(kwargs.get("title"))
    abstract = clean_text(kwargs.get("abstract"))
    doi = normalize_doi(kwargs.get("doi"))
    c = classify(title, abstract)
    publisher = clean_text(kwargs.get("publisher"))
    journal = clean_text(kwargs.get("journal"))
    url = clean_text(kwargs.get("url")) or (f"https://doi.org/{doi}" if doi else "")
    pub_date = safe_date(clean_text(kwargs.get("published_date")))
    authors = compact_authors(kwargs.get("authors") or [])
    source = clean_text(kwargs.get("source"))
    source_id = clean_text(kwargs.get("source_id"))
    return {
        "uid": doi or hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:20],
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "journal": journal,
        "publisher": publisher,
        "publisher_group": infer_publisher_group(publisher, journal),
        "published_date": pub_date,
        "year": int(pub_date[:4]) if pub_date else kwargs.get("year"),
        "type": clean_text(kwargs.get("type")) or "journal-article",
        "url": url,
        "open_access": bool(kwargs.get("open_access")),
        "oa_status": clean_text(kwargs.get("oa_status")),
        "cited_by_count": int(kwargs.get("cited_by_count") or 0),
        "topics": [clean_text(x) for x in (kwargs.get("topics") or []) if clean_text(x)][:8],
        "sources": [source] if source else [],
        "source_ids": {source: source_id} if source and source_id else {},
        "source_indexed_dates": {source: clean_text(kwargs.get("source_indexed_date"))} if source and kwargs.get("source_indexed_date") else {},
        "first_seen": TODAY.isoformat(),
        "last_seen": TODAY.isoformat(),
        "ai_summary": "",
        "why_it_matters": "",
        "study_location_ai": "",
        "location_inference": infer_countries(title, abstract),
        **c,
    }


def crossref_published_date(item: dict[str, Any]) -> str:
    for field in ["published-online", "published-print", "published", "issued"]:
        parts = (item.get(field) or {}).get("date-parts")
        if parts:
            d = date_from_parts(parts)
            if d:
                return d
    created = (item.get("created") or {}).get("date-time", "")
    return safe_date(created)


def parse_crossref(item: dict[str, Any]) -> dict[str, Any]:
    authors = []
    for a in item.get("author") or []:
        name = " ".join(x for x in [a.get("given", ""), a.get("family", "")] if x).strip() or a.get("name", "")
        if name: authors.append(name)
    title = clean_text((item.get("title") or [""])[0])
    abstract = clean_text(item.get("abstract"))
    journal = clean_text((item.get("container-title") or [""])[0])
    doi = normalize_doi(item.get("DOI"))
    indexed = (item.get("indexed") or {}).get("date-time", "")[:10]
    licenses = item.get("license") or []
    oa = any("creativecommons" in str(x.get("URL", "")).lower() or "open" in str(x.get("URL", "")).lower() for x in licenses)
    return canonical_record(
        title=title, abstract=abstract, authors=authors, journal=journal,
        publisher=item.get("publisher", ""), published_date=crossref_published_date(item),
        doi=doi, type=item.get("type", "journal-article"),
        url=item.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
        open_access=oa, cited_by_count=item.get("is-referenced-by-count", 0),
        source="Crossref", source_id=doi, source_indexed_date=indexed,
    )


def harvest_crossref(start: date, end: date) -> list[dict[str, Any]]:
    log("Crossref: searching publisher-deposited metadata")
    results: list[dict[str, Any]] = []
    # Search each primary term independently. This is more complete than one long
    # relevance query, while the short index-date window keeps request sizes modest.
    unique_terms = ["seagrass", "eelgrass", "marine angiosperm", "marine phanerogam", *GENERA]
    for term in unique_terms:
        params: dict[str, Any] = {
            "query.bibliographic": term,
            "filter": f"from-index-date:{start.isoformat()},until-index-date:{end.isoformat()},type:journal-article",
            "rows": min(MAX_RESULTS_PER_QUERY, 1000),
            "sort": "published",
            "order": "desc",
        }
        if CROSSREF_MAILTO:
            params["mailto"] = CROSSREF_MAILTO
        try:
            data = fetch_json("https://api.crossref.org/works", params=params)
            for item in data.get("message", {}).get("items", []):
                rec = parse_crossref(item)
                if rec["relevance_score"] >= MIN_RELEVANCE_SCORE:
                    results.append(rec)
        except Exception as exc:
            log(f"Crossref warning ({term}): {exc}")
    return results


def parse_openalex(item: dict[str, Any]) -> dict[str, Any]:
    primary = item.get("primary_location") or {}
    source = primary.get("source") or {}
    host_name = source.get("host_organization_name") or ""
    authors = [((a.get("author") or {}).get("display_name") or "") for a in item.get("authorships") or []]
    topics = [t.get("display_name", "") for t in item.get("topics") or []]
    oa = item.get("open_access") or {}
    doi = normalize_doi(item.get("doi"))
    return canonical_record(
        title=item.get("title") or item.get("display_name"),
        abstract=reconstruct_abstract(item.get("abstract_inverted_index")),
        authors=authors,
        journal=source.get("display_name", ""), publisher=host_name,
        published_date=item.get("publication_date", ""), year=item.get("publication_year"),
        doi=doi, type=item.get("type", "journal-article"),
        url=(f"https://doi.org/{doi}" if doi else primary.get("landing_page_url") or item.get("id", "")),
        open_access=oa.get("is_oa", False), oa_status=oa.get("oa_status", ""),
        cited_by_count=item.get("cited_by_count", 0), topics=topics,
        source="OpenAlex", source_id=item.get("id", ""),
        source_indexed_date=(item.get("updated_date") or item.get("created_date") or "")[:10],
    )


def harvest_openalex(start: date, end: date) -> list[dict[str, Any]]:
    if not OPENALEX_API_KEY:
        log("OpenAlex: OPENALEX_API_KEY not set; skipping (recommended source)")
        return []
    log("OpenAlex: searching broad scholarly index")
    results: list[dict[str, Any]] = []
    pub_start = end - timedelta(days=OPENALEX_PUBLICATION_LOOKBACK_DAYS)
    # One Boolean search is efficient; sort by publication date to prioritise newest.
    query = " OR ".join([f'"{x.strip(chr(34))}"' if " " in x.strip('"') else x for x in ["seagrass", "eelgrass", "marine angiosperm", "marine phanerogam", *GENERA]])
    cursor = "*"
    pages = 0
    while cursor and pages < 5:
        params: dict[str, Any] = {
            "search": f"({query})",
            "filter": f"from_publication_date:{pub_start.isoformat()},to_publication_date:{end.isoformat()}",
            "sort": "publication_date:desc",
            "per-page": 200,
            "cursor": cursor,
            "api_key": OPENALEX_API_KEY,
        }
        try:
            data = fetch_json("https://api.openalex.org/works", params=params)
        except Exception as exc:
            log(f"OpenAlex warning: {exc}")
            break
        for item in data.get("results", []):
            rec = parse_openalex(item)
            if rec["relevance_score"] >= MIN_RELEVANCE_SCORE:
                results.append(rec)
        nxt = (data.get("meta") or {}).get("next_cursor")
        cursor = nxt if nxt and nxt != cursor else ""
        pages += 1
        if len(data.get("results", [])) < 200:
            break
    return results


def parse_europe_pmc(item: dict[str, Any]) -> dict[str, Any]:
    doi = normalize_doi(item.get("doi"))
    authors: list[str] = []
    for a in (item.get("authorList") or {}).get("author", []) if isinstance(item.get("authorList"), dict) else []:
        authors.append(a.get("fullName") or " ".join([a.get("firstName", ""), a.get("lastName", "")]).strip())
    if not authors and item.get("authorString"):
        authors = [x.strip() for x in str(item.get("authorString")).rstrip(".").split(",")]
    journal_info = item.get("journalInfo") or {}
    journal = ((journal_info.get("journal") or {}).get("title") or item.get("journalTitle") or "")
    pub_date = item.get("firstPublicationDate") or item.get("firstIndexDate") or item.get("journalInfo", {}).get("printPublicationDate") or ""
    return canonical_record(
        title=item.get("title"), abstract=item.get("abstractText"), authors=authors,
        journal=journal, publisher="", published_date=pub_date, doi=doi,
        type=(item.get("pubTypeList") or {}).get("pubType", ["journal-article"])[0] if isinstance(item.get("pubTypeList"), dict) else "journal-article",
        url=(f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/{item.get('source','MED')}/{item.get('id','')}") ,
        open_access=str(item.get("isOpenAccess", "N")).upper() == "Y",
        cited_by_count=item.get("citedByCount", 0),
        source="Europe PMC", source_id=f"{item.get('source','')}:{item.get('id','')}",
        source_indexed_date=(item.get("firstIndexDate") or "")[:10],
    )


def harvest_europe_pmc(start: date, end: date) -> list[dict[str, Any]]:
    if not ENABLE_EUROPE_PMC:
        return []
    log("Europe PMC: searching PubMed/life-science literature")
    terms = ["seagrass", "eelgrass", *GENERA]
    bool_query = " OR ".join(f'\"{x}\"' if " " in x else x for x in terms)
    query = f"({bool_query}) AND FIRST_PDATE:[{(end - timedelta(days=OPENALEX_PUBLICATION_LOOKBACK_DAYS)).isoformat()} TO {end.isoformat()}]"
    results: list[dict[str, Any]] = []
    cursor = "*"
    pages = 0
    while pages < 5:
        try:
            data = fetch_json(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                params={"query": query, "format": "json", "resultType": "core", "pageSize": 1000, "cursorMark": cursor},
            )
        except Exception as exc:
            log(f"Europe PMC warning: {exc}")
            break
        batch = (data.get("resultList") or {}).get("result", [])
        for item in batch:
            rec = parse_europe_pmc(item)
            if rec["relevance_score"] >= MIN_RELEVANCE_SCORE:
                results.append(rec)
        nxt = data.get("nextCursorMark")
        pages += 1
        if not nxt or nxt == cursor or len(batch) < 1000:
            break
        cursor = nxt
    return results


def parse_semantic_scholar(item: dict[str, Any]) -> dict[str, Any]:
    ext = item.get("externalIds") or {}
    doi = normalize_doi(ext.get("DOI"))
    journal = (item.get("journal") or {}).get("name") or item.get("venue") or ""
    authors = [a.get("name", "") for a in item.get("authors") or []]
    oa_pdf = item.get("openAccessPdf") or {}
    return canonical_record(
        title=item.get("title"), abstract=item.get("abstract"), authors=authors,
        journal=journal, publisher="", published_date=item.get("publicationDate") or str(item.get("year") or ""),
        doi=doi, type=((item.get("publicationTypes") or ["journal-article"])[0]),
        url=(f"https://doi.org/{doi}" if doi else item.get("url") or oa_pdf.get("url", "")),
        open_access=bool(oa_pdf.get("url")), cited_by_count=item.get("citationCount", 0),
        topics=[x.get("category", "") for x in item.get("s2FieldsOfStudy") or []],
        source="Semantic Scholar", source_id=item.get("paperId", ""),
    )


def harvest_semantic_scholar(start: date, end: date) -> list[dict[str, Any]]:
    if not ENABLE_SEMANTIC_SCHOLAR:
        return []
    log("Semantic Scholar: searching independent scholarly graph")
    headers = {"x-api-key": SEMANTIC_SCHOLAR_API_KEY} if SEMANTIC_SCHOLAR_API_KEY else {}
    query = " OR ".join(["seagrass", "eelgrass", *GENERA])
    params = {
        "query": f"({query})",
        "publicationDateOrYear": f"{(end - timedelta(days=OPENALEX_PUBLICATION_LOOKBACK_DAYS)).isoformat()}:{end.isoformat()}",
        "fields": "paperId,externalIds,url,title,abstract,authors,venue,journal,year,publicationDate,publicationTypes,openAccessPdf,citationCount,s2FieldsOfStudy",
        "sort": "publicationDate:desc",
    }
    results: list[dict[str, Any]] = []
    token = ""
    pages = 0
    while pages < 4:
        p = dict(params)
        if token:
            p["token"] = token
        try:
            data = fetch_json("https://api.semanticscholar.org/graph/v1/paper/search/bulk", params=p, headers=headers)
        except Exception as exc:
            log(f"Semantic Scholar warning: {exc}")
            break
        for item in data.get("data", []):
            rec = parse_semantic_scholar(item)
            if rec["relevance_score"] >= MIN_RELEVANCE_SCORE:
                results.append(rec)
        token = data.get("token") or ""
        pages += 1
        if not token:
            break
    return results


def _xml_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_doaj_record(record: ET.Element) -> dict[str, Any] | None:
    # oai_dc is intentionally used: it is stable and available in the free feed.
    vals: dict[str, list[str]] = defaultdict(list)
    header_datestamp = ""
    for el in record.iter():
        local = _xml_local(el.tag)
        text = clean_text(el.text)
        if not text:
            continue
        if local == "datestamp" and not header_datestamp:
            header_datestamp = text
        elif local in {"title", "creator", "identifier", "date", "publisher", "description", "type", "source", "subject", "rights"}:
            vals[local].append(text)
    title = vals["title"][0] if vals["title"] else ""
    if not title:
        return None
    doi = ""
    url = ""
    for ident in vals["identifier"]:
        if "doi.org/" in ident.lower() or ident.lower().startswith("10."):
            doi = normalize_doi(ident)
        elif ident.startswith("http") and not url:
            url = ident
    abstract = " ".join(vals["description"][:2])
    journal = vals["source"][0] if vals["source"] else ""
    publisher = vals["publisher"][0] if vals["publisher"] else ""
    pub_date = vals["date"][0] if vals["date"] else ""
    rights = " ".join(vals["rights"]).lower()
    rec = canonical_record(
        title=title, abstract=abstract, authors=vals["creator"], journal=journal,
        publisher=publisher, published_date=pub_date, doi=doi, type="journal-article",
        url=(f"https://doi.org/{doi}" if doi else url), open_access=True,
        oa_status="open access / DOAJ", topics=vals["subject"],
        source="DOAJ", source_id=doi or url, source_indexed_date=header_datestamp,
    )
    return rec


def harvest_doaj(start: date, end: date) -> list[dict[str, Any]]:
    if not ENABLE_DOAJ:
        return []
    log("DOAJ: harvesting newly indexed OA articles (small-publisher safety net)")
    base = "https://doaj.org/oai.article"
    params: dict[str, Any] = {
        "verb": "ListRecords",
        "metadataPrefix": "oai_dc",
        "from": start.isoformat(),
        "until": end.isoformat(),
    }
    results: list[dict[str, Any]] = []
    pages = 0
    while pages < 20:
        try:
            xml_bytes = fetch_bytes(base, params=params, headers={"Accept": "application/xml,text/xml"})
            root = ET.fromstring(xml_bytes)
        except Exception as exc:
            log(f"DOAJ warning: {exc}")
            break
        records = [el for el in root.iter() if _xml_local(el.tag) == "record"]
        for node in records:
            rec = parse_doaj_record(node)
            if rec and rec["relevance_score"] >= MIN_RELEVANCE_SCORE:
                results.append(rec)
        token_el = next((el for el in root.iter() if _xml_local(el.tag) == "resumptionToken"), None)
        token = clean_text(token_el.text) if token_el is not None else ""
        pages += 1
        if not token:
            break
        params = {"verb": "ListRecords", "resumptionToken": token}
    return results


def richness(rec: dict[str, Any]) -> int:
    return (
        len(rec.get("abstract", "")) // 100
        + len(rec.get("authors", []))
        + len(rec.get("topics", []))
        + (10 if rec.get("doi") else 0)
        + (3 if rec.get("publisher") else 0)
        + (2 if rec.get("journal") else 0)
    )


def merge_record(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    # Prefer the richer metadata record but explicitly union provenance and tags.
    base, other = (a, b) if richness(a) >= richness(b) else (b, a)
    out = dict(base)
    for field in ["doi", "title", "abstract", "journal", "publisher", "published_date", "url", "oa_status", "type", "ai_summary", "why_it_matters", "study_location_ai"]:
        if not out.get(field) and other.get(field):
            out[field] = other[field]
    out["authors"] = compact_authors([*(base.get("authors") or []), *(other.get("authors") or [])])
    out["sources"] = sorted(set((a.get("sources") or []) + (b.get("sources") or [])))
    out["source_ids"] = {**(a.get("source_ids") or {}), **(b.get("source_ids") or {})}
    out["source_indexed_dates"] = {**(a.get("source_indexed_dates") or {}), **(b.get("source_indexed_dates") or {})}
    out["topics"] = sorted(set((a.get("topics") or []) + (b.get("topics") or [])))[:12]
    out["themes"] = sorted(set((a.get("themes") or []) + (b.get("themes") or [])))
    out["species"] = sorted(set((a.get("species") or []) + (b.get("species") or [])), key=str.lower)
    out["matched_terms"] = sorted(set((a.get("matched_terms") or []) + (b.get("matched_terms") or [])), key=str.lower)
    out["location_inference"] = a.get("location_inference") or b.get("location_inference") or []
    out["open_access"] = bool(a.get("open_access") or b.get("open_access"))
    out["cited_by_count"] = max(int(a.get("cited_by_count") or 0), int(b.get("cited_by_count") or 0))
    out["relevance_score"] = max(int(a.get("relevance_score") or 0), int(b.get("relevance_score") or 0))
    firsts = [x for x in [a.get("first_seen"), b.get("first_seen")] if x]
    lasts = [x for x in [a.get("last_seen"), b.get("last_seen")] if x]
    out["first_seen"] = min(firsts) if firsts else TODAY.isoformat()
    out["last_seen"] = max(lasts + [TODAY.isoformat()]) if lasts else TODAY.isoformat()
    out["publisher_group"] = infer_publisher_group(out.get("publisher", ""), out.get("journal", ""))
    out["uid"] = out.get("doi") or hashlib.sha1(normalize_title(out.get("title", "")).encode("utf-8")).hexdigest()[:20]
    return out


def deduplicate(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_doi: dict[str, dict[str, Any]] = {}
    by_title: dict[str, str] = {}
    no_doi: dict[str, dict[str, Any]] = {}
    for rec in records:
        if not rec.get("title"):
            continue
        doi = normalize_doi(rec.get("doi"))
        title_key = normalize_title(rec.get("title", ""))
        if doi:
            if doi in by_doi:
                by_doi[doi] = merge_record(by_doi[doi], rec)
            elif title_key in by_title and by_title[title_key] in by_doi:
                old_doi = by_title[title_key]
                by_doi[doi] = merge_record(by_doi.pop(old_doi), rec)
                by_title[title_key] = doi
            else:
                by_doi[doi] = rec
                by_title[title_key] = doi
        else:
            if title_key in by_title:
                d = by_title[title_key]
                if d in by_doi:
                    by_doi[d] = merge_record(by_doi[d], rec)
                else:
                    no_doi[title_key] = merge_record(no_doi[title_key], rec)
            elif title_key in no_doi:
                no_doi[title_key] = merge_record(no_doi[title_key], rec)
            else:
                no_doi[title_key] = rec
                by_title[title_key] = title_key
    return [*by_doi.values(), *no_doi.values()]


def load_existing() -> list[dict[str, Any]]:
    if not PAPERS_FILE.exists():
        return []
    try:
        data = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
        return data.get("papers", data if isinstance(data, list) else [])
    except Exception as exc:
        log(f"Existing database warning: {exc}")
        return []


def is_demo_record(rec: dict[str, Any]) -> bool:
    """Return True for records shipped only to demonstrate the interface.

    Real harvesting must never preserve starter/demo content.  Several markers are
    checked so this also removes demo records created by older Radar versions.
    """
    title = str(rec.get("title") or "").strip().lower()
    doi = normalize_doi(rec.get("doi"))
    source = str(rec.get("source") or "").strip().lower()
    sources = {str(x).strip().lower() for x in (rec.get("sources") or [])}
    return (
        title.startswith("[demo]")
        or doi.startswith("10.0000/demo.")
        or source == "demo"
        or "demo" in sources
    )


def likely_new(rec: dict[str, Any]) -> bool:
    return rec.get("first_seen") == TODAY.isoformat()


def ai_enrich(records: list[dict[str, Any]]) -> None:
    if not OPENAI_API_KEY or AI_SUMMARY_LIMIT <= 0:
        return
    candidates = [r for r in records if likely_new(r) and r.get("abstract") and not r.get("ai_summary")]
    candidates.sort(key=lambda r: (r.get("relevance_score", 0), r.get("published_date", "")), reverse=True)
    log(f"AI: summarising up to {min(AI_SUMMARY_LIMIT, len(candidates))} newly discovered papers")
    for rec in candidates[:AI_SUMMARY_LIMIT]:
        prompt = (
            "You are creating a scientific literature radar for seagrass researchers. "
            "Using only the title and abstract below, return a single JSON object with keys "
            '"summary", "why_it_matters", and "study_location". '
            "summary: max 55 words, factual. why_it_matters: max 35 words, explain relevance to seagrass science/conservation/restoration. "
            "study_location: a place only if explicitly stated or clearly supported; otherwise empty string. Do not add markdown.\n\n"
            f"TITLE: {rec['title']}\nABSTRACT: {rec['abstract'][:9000]}"
        )
        try:
            payload = json.dumps({"model": OPENAI_MODEL, "input": prompt}).encode("utf-8")
            req = urllib.request.Request(
                "https://api.openai.com/v1/responses", data=payload, method="POST",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json", "User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=90) as r:
                response = json.loads(r.read().decode("utf-8"))
            text = response.get("output_text", "")
            if not text:
                parts = []
                for out in response.get("output", []):
                    for content in out.get("content", []):
                        if content.get("type") == "output_text": parts.append(content.get("text", ""))
                text = "".join(parts)
            obj = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
            rec["ai_summary"] = clean_text(obj.get("summary"))
            rec["why_it_matters"] = clean_text(obj.get("why_it_matters"))
            rec["study_location_ai"] = clean_text(obj.get("study_location"))
        except Exception as exc:
            log(f"AI warning for {rec.get('doi') or rec.get('title','')[:35]}: {exc}")


def filter_retention(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = TODAY - timedelta(days=RETENTION_DAYS)
    out = []
    for r in records:
        d = safe_date(r.get("published_date", ""))
        # Keep papers with unknown/older publication dates if they were newly indexed
        # recently; this catches online-first/date-correction and metadata backfills.
        first = safe_date(r.get("first_seen", ""))
        if (d and d >= cutoff.isoformat()) or (first and first >= cutoff.isoformat()) or not d:
            out.append(r)
    return out


def coverage_audit(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audit = []
    for group in PUBLISHER_GROUPS:
        hits = [r for r in records if r.get("publisher_group") == group]
        hits.sort(key=lambda x: x.get("published_date", ""), reverse=True)
        journals = Counter(r.get("journal") for r in hits if r.get("journal"))
        audit.append({
            "publisher": group,
            "papers_in_database": len(hits),
            "latest_published": hits[0].get("published_date", "") if hits else "",
            "latest_first_seen": max((r.get("first_seen", "") for r in hits), default=""),
            "journals_seen": [x for x, _ in journals.most_common(8)],
            "status": "seen" if hits else "no recent hit",
        })
    return audit


def make_digest(records: list[dict[str, Any]]) -> dict[str, Any]:
    cutoff = TODAY - timedelta(days=7)
    recent = [r for r in records if safe_date(r.get("first_seen", "")) >= cutoff.isoformat()]
    theme_counts = Counter(t for r in recent for t in r.get("themes", []))
    publisher_counts = Counter(r.get("publisher_group", "") for r in recent if r.get("publisher_group"))
    top = sorted(recent, key=lambda r: (r.get("relevance_score", 0), r.get("published_date", ""), r.get("cited_by_count", 0)), reverse=True)[:10]
    return {
        "generated_at": NOW_ISO,
        "window_days": 7,
        "new_papers": len(recent),
        "theme_counts": dict(theme_counts.most_common()),
        "publisher_counts": dict(publisher_counts.most_common(12)),
        "top_papers": [{k: r.get(k) for k in ["uid", "title", "authors", "journal", "publisher_group", "published_date", "url", "themes", "ai_summary", "why_it_matters"]} for r in top],
    }


def demo_records() -> list[dict[str, Any]]:
    examples = [
        dict(title="[DEMO] Heatwave exposure alters recovery trajectories in Zostera marina meadows", abstract="A multi-site study examines marine heatwave exposure, eelgrass recovery and thermal thresholds across coastal Europe.", authors=["A. Example", "B. Example"], journal="Marine Ecology Progress Series", publisher="Inter-Research", published_date=(TODAY - timedelta(days=1)).isoformat(), doi="10.0000/demo.001", source="Demo", source_id="demo1", open_access=True),
        dict(title="[DEMO] Scaling seagrass restoration using seed-based approaches", abstract="We compare seed collection, storage and planting approaches for seagrass restoration in temperate Zostera marina systems.", authors=["C. Example"], journal="Restoration Ecology", publisher="John Wiley & Sons, Ltd", published_date=(TODAY - timedelta(days=3)).isoformat(), doi="10.0000/demo.002", source="Demo", source_id="demo2"),
        dict(title="[DEMO] Blue carbon stocks of tropical Thalassia and Halodule meadows", abstract="Sediment carbon stocks were quantified in tropical seagrass meadows in Indonesia with implications for conservation and blue carbon accounting.", authors=["D. Example"], journal="Frontiers in Marine Science", publisher="Frontiers Media SA", published_date=(TODAY - timedelta(days=5)).isoformat(), doi="10.0000/demo.003", source="Demo", source_id="demo3", open_access=True),
        dict(title="[DEMO] Satellite mapping of Posidonia oceanica meadow extent in the Mediterranean", abstract="Earth observation and machine learning were used to map Posidonia oceanica distribution around Spain.", authors=["E. Example"], journal="Remote Sensing", publisher="MDPI AG", published_date=(TODAY - timedelta(days=8)).isoformat(), doi="10.0000/demo.004", source="Demo", source_id="demo4", open_access=True),
    ]
    return [canonical_record(**x) for x in examples]


def write_outputs(records: list[dict[str, Any]], source_status: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    records.sort(key=lambda r: (r.get("first_seen", ""), r.get("published_date", ""), r.get("relevance_score", 0)), reverse=True)
    payload = {
        "generated_at": NOW_ISO,
        "paper_count": len(records),
        "papers": records,
        "publisher_coverage": coverage_audit(records),
        "source_status": source_status,
        "search_vocabulary": SEARCH_TERMS,
        "notes": {
            "publisher_coverage": "Crossref is the publisher-metadata backbone; OpenAlex, Europe PMC, Semantic Scholar and DOAJ add independent redundancy. A 'no recent hit' publisher status means no matching seagrass paper is currently in the retained database, not that the publisher was excluded.",
            "study_locations": "Map locations are inferred only from explicit place names in title/abstract text unless an optional AI study_location is present. Author affiliation countries are not treated as study sites.",
            "first_seen": "The date this Radar first discovered the record. This can differ from formal publication date because indexes ingest metadata at different times.",
        },
    }
    PAPERS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    STATUS_FILE.write_text(json.dumps({"generated_at": NOW_ISO, "sources": source_status, "paper_count": len(records)}, indent=2), encoding="utf-8")
    DIGEST_FILE.write_text(json.dumps(make_digest(records), ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="Write demo data without calling external APIs")
    parser.add_argument("--no-ai", action="store_true", help="Disable optional OpenAI summaries for this run")
    args = parser.parse_args()

    existing = load_existing()
    if args.demo:
        # Demo mode is intentionally isolated from the real literature database.
        records = deduplicate(demo_records())
        write_outputs(records, {"Demo": {"ok": True, "records": len(records), "message": "Demo mode"}})
        log(f"Demo complete: {len(records)} records")
        return 0

    # A normal run must contain real literature only.  Older trial packages shipped
    # interface examples in papers.json; discard them before harvesting/merging.
    demo_count = sum(1 for r in existing if is_demo_record(r))
    if demo_count:
        log(f"Removing {demo_count} starter/demo records before the real harvest")
    existing = [r for r in existing if not is_demo_record(r)]

    start = TODAY - timedelta(days=RETRIEVAL_LOOKBACK_DAYS)
    source_status: dict[str, Any] = {}
    harvested: list[dict[str, Any]] = []
    source_functions = [
        ("Crossref", harvest_crossref),
        ("OpenAlex", harvest_openalex),
        ("Europe PMC", harvest_europe_pmc),
        ("Semantic Scholar", harvest_semantic_scholar),
        ("DOAJ", harvest_doaj),
    ]
    for name, fn in source_functions:
        t0 = time.time()
        if name == "OpenAlex" and not OPENALEX_API_KEY:
            source_status[name] = {"ok": False, "skipped": True, "records": 0, "seconds": 0, "error": "OPENALEX_API_KEY not set"}
            log("OpenAlex: skipped because OPENALEX_API_KEY is not set")
            continue
        try:
            rows = fn(start, TODAY)
            harvested.extend(rows)
            source_status[name] = {"ok": True, "records": len(rows), "seconds": round(time.time() - t0, 1)}
            log(f"{name}: kept {len(rows)} relevant records")
        except Exception as exc:
            source_status[name] = {"ok": False, "records": 0, "seconds": round(time.time() - t0, 1), "error": str(exc)}
            log(f"{name}: FAILED but continuing: {exc}")

    # Existing records are loaded first so their first_seen date survives merges.
    combined = deduplicate([*existing, *harvested])
    combined = [r for r in combined if int(r.get("relevance_score") or 0) >= MIN_RELEVANCE_SCORE]
    combined = filter_retention(combined)

    if not args.no_ai:
        ai_enrich(combined)

    write_outputs(combined, source_status)
    new_count = sum(1 for r in combined if r.get("first_seen") == TODAY.isoformat())
    log(f"Done: {len(combined)} retained papers; {new_count} first seen today")
    return 0


if __name__ == "__main__":
    sys.exit(main())
