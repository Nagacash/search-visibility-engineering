#!/usr/bin/env python3
"""
audit_page.py — On-page SEO + AI extractability audit
Part of the search-visibility-engineering skill.

Usage:
  python3 audit_page.py <url> [--output json|text] [--save <filename>]

What it checks (no JavaScript execution — mirrors AI crawler perspective):
  - Raw HTML fetchability, status, redirect chain
  - JS-only shell detection (invisible to AI crawlers)
  - Title, meta description, canonical, Open Graph
  - Heading hierarchy (H1/H2/H3)
  - JSON-LD structured data presence and parse validity
  - Content extractability score (self-contained paragraphs, statistics, FAQ, tables)
  - E-E-A-T signals (author, date, Impressum, citations, credentials)
  - Image alt-text coverage
"""

from __future__ import annotations
import sys
import json
import re
import argparse
from urllib.parse import urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Install dependencies: pip3 install requests beautifulsoup4 lxml", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UA_DEFAULT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
UA_GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

SEVERITY_WEIGHT = {"critical": 40, "high": 15, "medium": 7, "low": 3}


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_page(url: str, ua: str = UA_DEFAULT) -> requests.Response | None:
    """Fetch raw HTML without JavaScript. Mirrors what most AI crawlers see."""
    try:
        r = requests.get(
            url, headers={"User-Agent": ua},
            timeout=20, allow_redirects=True
        )
        return r
    except requests.RequestException as e:
        print(f"Fetch error: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def detect_js_shell(soup: BeautifulSoup) -> bool:
    """True if page is an empty JS shell — invisible to crawlers that don't render JS."""
    body = soup.find("body")
    if not body:
        return True
    text = body.get_text(separator=" ", strip=True)
    scripts = body.find_all("script")
    return len(text) < 250 and len(scripts) > 3


def audit_metadata(soup: BeautifulSoup) -> dict:
    result = {}

    # Title
    tag = soup.find("title")
    title = tag.get_text(strip=True) if tag else None
    result["title"] = title
    result["title_length"] = len(title) if title else 0
    result["title_ok"] = 30 <= result["title_length"] <= 65

    # Meta description
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    desc = meta.get("content", "").strip() if meta else ""
    result["meta_description"] = desc
    result["meta_description_length"] = len(desc)
    result["meta_description_ok"] = 100 <= len(desc) <= 165

    # Canonical
    can = soup.find("link", attrs={"rel": re.compile(r"^canonical$", re.I)})
    result["canonical"] = can.get("href") if can else None

    # Robots meta
    rob = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    result["robots_meta"] = rob.get("content", "").lower() if rob else None
    result["is_noindex"] = "noindex" in (result["robots_meta"] or "")

    # Open Graph
    for prop in ["title", "description", "image", "type"]:
        tag = soup.find("meta", property=f"og:{prop}") or soup.find("meta", attrs={"name": f"og:{prop}"})
        result[f"og_{prop}"] = tag.get("content") if tag else None

    # Twitter card
    tc = soup.find("meta", attrs={"name": re.compile(r"^twitter:card$", re.I)})
    result["twitter_card"] = tc.get("content") if tc else None

    # Viewport
    vp = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    result["has_viewport"] = vp is not None

    # hreflang
    hreflangs = soup.find_all("link", attrs={"rel": re.compile(r"^alternate$", re.I), "hreflang": True})
    result["hreflang_count"] = len(hreflangs)
    result["hreflang_locales"] = [h.get("hreflang") for h in hreflangs[:6]]

    return result


def audit_headings(soup: BeautifulSoup) -> dict:
    h1 = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2 = [h.get_text(strip=True) for h in soup.find_all("h2")]
    h3 = [h.get_text(strip=True) for h in soup.find_all("h3")]
    return {
        "h1_count": len(h1), "h1_texts": h1[:3],
        "h2_count": len(h2), "h2_texts": h2[:6],
        "h3_count": len(h3),
        "h1_ok": len(h1) == 1,
        "hierarchy_ok": len(h1) == 1 and len(h2) >= 2,
    }


def audit_jsonld(soup: BeautifulSoup) -> dict:
    result = {
        "schemas": [], "types": [], "parse_errors": [],
        "has_organization": False, "has_website": False,
        "has_faqpage": False, "has_service": False,
        "has_breadcrumb": False, "has_webpage": False,
    }
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            items = data.get("@graph", [data]) if isinstance(data, dict) else [data]
            for item in items:
                t = item.get("@type", "")
                types = t if isinstance(t, list) else [t]
                result["types"].extend(types)
                result["schemas"].append(item)
        except Exception as e:
            result["parse_errors"].append(str(e))

    result["schema_count"] = len(result["schemas"])
    types = result["types"]
    result["has_organization"] = any(t in types for t in ["Organization", "LocalBusiness", "Corporation", "ProfessionalService", "Consulting"])
    result["has_website"] = "WebSite" in types
    result["has_faqpage"] = "FAQPage" in types
    result["has_service"] = "Service" in types
    result["has_breadcrumb"] = "BreadcrumbList" in types
    result["has_webpage"] = any(t in types for t in ["WebPage", "AboutPage", "ContactPage", "ServicePage"])
    return result


def audit_extractability(soup: BeautifulSoup) -> dict:
    body = soup.find("body") or soup
    full_text = body.get_text(separator=" ", strip=True)
    words = full_text.split()

    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
    paragraphs = [p for p in paragraphs if len(p) > 60]
    long_paragraphs = [p for p in paragraphs if len(p) > 180]

    # Self-contained: count paragraphs that start with a noun-like word (capital or 'Die/Der/Das/The/A')
    self_contained = [
        p for p in paragraphs
        if re.match(r'^[A-ZÄÖÜ]|\b(Die|Der|Das|The|A|An|Our|We|You)\b', p)
        and not p.startswith("Und ") and not p.startswith("Aber ")
    ]

    # Statistics / numbers density
    numbers = re.findall(
        r'\b\d+(?:[.,]\d+)?(?:\s*(?:%|Prozent|percent|€|EUR|\$|USD|x\b|km\b|kg\b))?\b',
        full_text
    )

    # Lists and tables
    ul_ol = soup.find_all(["ul", "ol"])
    li_items = soup.find_all("li")
    tables = soup.find_all("table")

    # Definition-style content (dd elements, strong+colon patterns)
    definitions = soup.find_all("dd")
    definition_count = len(definitions)

    # FAQ detection
    faq_ids_classes = soup.find_all(attrs={"id": re.compile(r"faq|question", re.I)})
    faq_headings = [h for h in soup.find_all(["h2","h3"]) if re.search(r"faq|question|fragen|antwort", h.get_text(), re.I)]
    has_faq = bool(faq_ids_classes or faq_headings or soup.find_all(["details", "summary"]))

    # Images with alt
    images = soup.find_all("img")
    images_with_alt = [i for i in images if i.get("alt", "").strip()]
    images_with_dims = [i for i in images if i.get("width") and i.get("height")]

    stat_density = round(len(numbers) / max(len(words), 1) * 1000, 1)

    # Extractability score: weighted 0-100
    score = 0
    if len(words) >= 800: score += 20
    elif len(words) >= 400: score += 12
    elif len(words) >= 200: score += 5
    if stat_density >= 8: score += 18
    elif stat_density >= 4: score += 10
    elif stat_density >= 1: score += 4
    if has_faq: score += 12
    if len(long_paragraphs) >= 4: score += 18
    elif len(long_paragraphs) >= 2: score += 10
    elif len(long_paragraphs) >= 1: score += 4
    if len(self_contained) >= 5: score += 12
    elif len(self_contained) >= 2: score += 6
    if len(ul_ol) >= 3: score += 10
    elif len(ul_ol) >= 1: score += 5
    if len(tables) >= 1: score += 10
    if definition_count >= 2: score += 8
    elif definition_count >= 1: score += 4
    if images and (len(images_with_alt) / len(images)) >= 0.8: score += 5

    return {
        "word_count": len(words),
        "paragraph_count": len(paragraphs),
        "long_paragraph_count": len(long_paragraphs),
        "self_contained_paragraph_count": len(self_contained),
        "statistic_count": len(numbers),
        "statistic_density_per_1000": stat_density,
        "has_faq": has_faq,
        "list_count": len(ul_ol),
        "list_item_count": len(li_items),
        "table_count": len(tables),
        "definition_count": definition_count,
        "image_count": len(images),
        "images_with_alt": len(images_with_alt),
        "images_with_dimensions": len(images_with_dims),
        "images_alt_coverage": round(len(images_with_alt) / max(len(images), 1), 2),
        "extractability_score": min(score, 100),
    }


def audit_eeat(soup: BeautifulSoup, url: str) -> dict:
    result = {}
    html_lower = str(soup).lower()
    parsed = urlparse(url)

    # Author signals
    author_patterns = [
        r'author', r'autor', r'verfasst von', r'written by',
        r'geschrieben von', r'"author"', r'rel="author"',
        r'class="author"', r'itemprop="author"',
    ]
    result["has_author"] = any(re.search(p, html_lower) for p in author_patterns)

    # Date signals
    date_patterns = [
        r'datetime=', r'datepublished', r'datemodified',
        r'published', r'veröffentlicht', r'updated', r'aktualisiert',
        r'<time', r'itemprop="date',
    ]
    result["has_date"] = any(re.search(p, html_lower) for p in date_patterns)

    # Credential / expertise signals
    cred_patterns = [
        r'certif', r'zertifiz', r'years.*experience', r'jahre.*erfahrung',
        r'expertise', r'specialist', r'spezialist', r'ausgezeichnet',
        r'award', r'featured in', r'bachelor|master|diplom|phd',
    ]
    result["has_credentials"] = any(re.search(p, html_lower) for p in cred_patterns)

    # About / Contact / Legal links (critical for E-E-A-T and German law)
    links = [a.get("href", "") for a in soup.find_all("a", href=True)]
    hrefs_lower = [l.lower() for l in links]
    result["has_about_link"] = any("about" in h or "/ueber" in h or "über-" in h or "/about" in h for h in hrefs_lower)
    result["has_contact_link"] = any("contact" in h or "kontakt" in h for h in hrefs_lower)
    result["has_impressum"] = any("impressum" in h or "imprint" in h or "legal" in h for h in hrefs_lower)
    result["has_privacy"] = any("privacy" in h or "datenschutz" in h or "dsgvo" in h for h in hrefs_lower)
    result["has_cookie_consent"] = bool(re.search(r'cookie|consent|gdpr|cookiebot|cookieyes', html_lower))

    # External citations
    own_host = parsed.hostname or ""
    external = [l for l in links if l.startswith("http") and own_host not in l]
    trusted_sources = [
        l for l in external
        if any(d in l for d in [
            "wikipedia.org", "google.com", "statista.com", "destatis.de",
            "bundesregierung.de", "springer.com", "pubmed", "nature.com",
            "forbes.com", "handelsblatt.com", "faz.net", "spiegel.de",
        ])
    ]
    result["external_link_count"] = len(external)
    result["trusted_citation_count"] = len(trusted_sources)
    result["has_citations"] = result["external_link_count"] >= 2

    # Social proof signals
    social_patterns = [r'trustpilot', r'google.*review', r'bewertung', r'rezension', r'review', r'testimonial', r'erfahrungsbericht']
    result["has_social_proof"] = any(re.search(p, html_lower) for p in social_patterns)

    return result


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def build_issues(metadata, headings, jsonld, extractability, eeat, is_js_shell) -> list[dict]:
    issues = []

    def add(severity, check, detail, fix):
        issues.append({"severity": severity, "check": check, "detail": detail, "fix": fix})

    # Critical
    if is_js_shell:
        add("critical", "JS-only shell detected",
            "Page body contains <250 chars of text without JS rendering. "
            "AI crawlers (GPTBot, OAI-SearchBot, ClaudeBot, PerplexityBot) and some search crawlers do NOT execute JavaScript.",
            "Switch to SSR (server-side rendering) or SSG (static site generation). "
            "Ensure all critical content is in the raw HTML response, not injected by React/Vue/Next after load.")

    if metadata.get("is_noindex"):
        add("critical", "Page is set to noindex",
            "robots meta tag contains 'noindex' — this page cannot be indexed by any search engine.",
            "Remove noindex from the robots meta tag unless this page should be excluded from search.")

    # High
    if not metadata["title"]:
        add("high", "Missing <title> tag",
            "No title tag found.", "Add a descriptive <title> between 30-65 characters.")
    elif not metadata["title_ok"]:
        add("high", f"Title length {metadata['title_length']} chars (ideal 30-65)",
            f"Title: \"{metadata['title']}\"",
            "Rewrite to 30-65 characters. Put the primary keyword near the start.")

    if headings["h1_count"] == 0:
        add("high", "No H1 tag",
            "No H1 found. AI systems and search engines use H1 as the primary topic signal.",
            "Add exactly one H1 tag as the main page heading containing the primary keyword.")
    elif headings["h1_count"] > 1:
        add("high", f"Multiple H1 tags ({headings['h1_count']})",
            f"H1s: {headings['h1_texts']}",
            "Reduce to one H1. Convert extra H1s to H2.")

    if jsonld["schema_count"] == 0:
        add("high", "No JSON-LD structured data",
            "No schema.org markup found. Structured data is a confirmed rich-result signal for Google and Bing, "
            "and entity clarity helps AI systems know what you are.",
            "Run generate_entity_schema.py to create Organization, WebSite, and WebPage schemas. "
            "Add FAQPage for Q&A sections.")
    else:
        if not jsonld["has_organization"]:
            add("high", "No Organization/LocalBusiness schema",
                "AI systems rely on entity disambiguation. Without an Organization or LocalBusiness schema, "
                "they cannot confidently identify your brand, category, or location.",
                "Add an Organization or LocalBusiness JSON-LD block. Use generate_entity_schema.py.")
        if jsonld["parse_errors"]:
            add("high", f"JSON-LD parse errors ({len(jsonld['parse_errors'])})",
                f"Errors: {'; '.join(jsonld['parse_errors'][:2])}",
                "Fix malformed JSON-LD. Validate at https://validator.schema.org/ and https://search.google.com/test/rich-results")

    if extractability["word_count"] < 300:
        add("high", f"Low word count ({extractability['word_count']} words)",
            "Pages under 300 words are rarely cited in AI-generated answers — "
            "there's not enough extractable content.",
            "Expand to at least 400-600 words with substantive, specific content. "
            "Thin pages rank poorly in both classic search and AI citation.")

    if extractability["extractability_score"] < 35:
        add("high", f"Low extractability score ({extractability['extractability_score']}/100)",
            "AI systems prefer pages with self-contained answer paragraphs, concrete statistics, "
            "FAQ sections, and comparison tables. This page scores low on all dimensions.",
            "Add: (1) a FAQ block with at least 3 Q&A pairs, (2) concrete numbers/statistics, "
            "(3) a comparison table or feature list, (4) at least 3 paragraphs of 150+ words each.")

    # Medium
    if not metadata["meta_description"]:
        add("medium", "Missing meta description",
            "Meta description is the primary snippet text in search results. "
            "Google and Bing both use it when no better snippet is found.",
            "Add <meta name='description' content='...'> with 100-165 characters, "
            "including the primary keyword and a clear value proposition.")
    elif not metadata["meta_description_ok"]:
        add("medium", f"Meta description length {metadata['meta_description_length']} chars (ideal 100-165)",
            f"Description: \"{metadata['meta_description'][:80]}...\"",
            "Adjust to 100-165 characters.")

    if not metadata["canonical"]:
        add("medium", "Missing canonical tag",
            "Without canonical, duplicate or near-duplicate URLs can split link equity.",
            "Add <link rel='canonical' href='https://yourdomain.com/this-page/'> in <head>.")

    if headings["h2_count"] < 2:
        add("medium", f"Fewer than 2 H2 headings ({headings['h2_count']} found)",
            "H2 headings signal content structure to both search engines and AI retrievers. "
            "AI systems use heading text as topical anchors when selecting passages to cite.",
            "Add at least 3 H2 headings that describe the page's main topics or answer key questions.")

    if not jsonld["has_website"]:
        add("medium", "No WebSite schema",
            "WebSite schema with SearchAction enables Google Sitelinks Search Box.",
            "Add WebSite JSON-LD. generate_entity_schema.py produces this automatically.")

    if not jsonld["has_faqpage"] and extractability["has_faq"]:
        add("medium", "FAQ content detected but no FAQPage schema",
            "FAQ content found in HTML but no FAQPage JSON-LD. "
            "FAQPage schema unlocks Google rich results and makes Q&A directly extractable by AI.",
            "Wrap FAQ items in FAQPage JSON-LD. generate_entity_schema.py includes a FAQ mode.")

    if not eeat["has_author"]:
        add("medium", "No author signal",
            "E-E-A-T (Experience, Expertise, Authority, Trust) requires human author signals. "
            "Google's quality rater guidelines and Bing's quality signals both look for this.",
            "Add an author byline with the person's name and a link to their bio/about page.")

    if not eeat["has_impressum"]:
        add("medium", "No Impressum link",
            "Impressum is a legal requirement for commercial German websites under TMG §5. "
            "Its absence is also a trust signal that reduces E-E-A-T scores.",
            "Add an Impressum page and link it in the footer.")

    if not eeat["has_privacy"]:
        add("medium", "No Datenschutz/Privacy link",
            "Required under DSGVO/GDPR for any site processing EU user data.",
            "Add a Datenschutzerklärung page and link it in the footer.")

    if extractability["extractability_score"] >= 35 and extractability["extractability_score"] < 60:
        add("medium", f"Medium extractability score ({extractability['extractability_score']}/100)",
            "Content is present but could be structured more extractably for AI citation.",
            "Add concrete statistics, a FAQ block, or a comparison table to improve AI citation rate.")

    if not metadata["has_viewport"]:
        add("medium", "Missing viewport meta tag",
            "Bing and Google both use mobile-friendliness as a ranking signal.",
            "Add <meta name='viewport' content='width=device-width, initial-scale=1'>")

    # Low
    if not metadata["og_image"]:
        add("low", "Missing og:image",
            "Open Graph image is used by social platforms and some AI summarizers for previews.",
            "Add <meta property='og:image' content='https://yourdomain.com/og-image.jpg'>. "
            "Recommended: 1200x630px.")

    if not eeat["has_date"]:
        add("low", "No publication/update date signal",
            "Recency is a quality signal. Content without dates appears stale to both AI and human readers.",
            "Add a visible publication date and include datePublished/dateModified in JSON-LD.")

    if not eeat["has_contact_link"]:
        add("low", "No contact page link",
            "Contact page is a trust signal for both E-E-A-T and user experience.",
            "Add a Contact link in the navigation or footer.")

    if not eeat["has_citations"] and extractability["word_count"] > 200:
        add("low", "No external citation links",
            "Linking to credible external sources (studies, official data) increases perceived authority.",
            "Add 2-3 links to credible external sources when making factual claims.")

    if extractability["image_count"] > 0 and extractability["images_alt_coverage"] < 0.8:
        missing = extractability["image_count"] - extractability["images_with_alt"]
        add("low", f"{missing} images missing alt text",
            "Alt text is used by search engines as image content signals and is required for accessibility.",
            "Add descriptive alt attributes to all <img> tags.")

    if extractability["images_with_dimensions"] < extractability["image_count"] * 0.5 and extractability["image_count"] > 0:
        add("low", "Images missing width/height attributes",
            "Explicit dimensions prevent layout shift (CLS) and help browsers allocate space before loading.",
            "Add width and height attributes to all <img> tags.")

    return issues


def compute_score(issues: list[dict]) -> int:
    score = 100
    for issue in issues:
        score -= SEVERITY_WEIGHT.get(issue["severity"], 0)
    return max(0, score)


# ---------------------------------------------------------------------------
# Main audit runner
# ---------------------------------------------------------------------------

def run_audit(url: str) -> dict:
    print(f"  Fetching {url} ...", file=sys.stderr)
    r = fetch_page(url)
    if not r:
        return {"error": f"Failed to fetch {url}"}
    if r.status_code >= 400:
        return {"error": f"HTTP {r.status_code} for {url}"}

    soup = BeautifulSoup(r.text, "html.parser")
    is_js_shell = detect_js_shell(soup)
    metadata = audit_metadata(soup)
    headings = audit_headings(soup)
    jsonld = audit_jsonld(soup)
    extractability = audit_extractability(soup)
    eeat = audit_eeat(soup, url)
    issues = build_issues(metadata, headings, jsonld, extractability, eeat, is_js_shell)
    score = compute_score(issues)

    return {
        "url": url,
        "status_code": r.status_code,
        "final_url": r.url,
        "is_js_shell": is_js_shell,
        "score": score,
        "metadata": metadata,
        "headings": headings,
        "json_ld": jsonld,
        "extractability": extractability,
        "eeat": eeat,
        "issues": sorted(issues, key=lambda x: ["critical","high","medium","low"].index(x["severity"])),
    }


# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------

GRADE = {range(85,101):"A", range(70,85):"B", range(50,70):"C", range(30,50):"D", range(0,30):"F"}

def letter_grade(score: int) -> str:
    for r, g in GRADE.items():
        if score in r: return g
    return "F"


def format_text(result: dict) -> str:
    if "error" in result:
        return f"ERROR: {result['error']}"

    r = result
    ex = r["extractability"]
    meta = r["metadata"]
    score = r["score"]
    grade = letter_grade(score)

    lines = [
        "",
        f"┌─ PAGE AUDIT ─ {r['url']} ─{'─'*20}",
        f"│  Score: {score}/100 ({grade})  │  Status: {r['status_code']}  │  Final URL: {r['final_url']}",
        f"│  JS-only shell: {'⚠  YES — crawlers see empty page' if r['is_js_shell'] else '✓  No'}",
        "│",
        "│ METADATA",
        f"│  Title ({meta['title_length']} chars {'✓' if meta['title_ok'] else '✗'}): {meta['title'] or '— MISSING'}",
        f"│  Meta desc ({meta['meta_description_length']} chars {'✓' if meta['meta_description_ok'] else '✗'}): "
        f"{(meta['meta_description'] or '— MISSING')[:80]}",
        f"│  Canonical: {meta['canonical'] or '— MISSING'}",
        f"│  OG image: {'✓' if meta['og_image'] else '✗ missing'}  │  Viewport: {'✓' if meta['has_viewport'] else '✗'}  │  noindex: {'⚠ YES' if meta['is_noindex'] else 'No'}",
        "│",
        "│ HEADINGS",
        f"│  H1 ({r['headings']['h1_count']}): {r['headings']['h1_texts'] or '— none'}",
        f"│  H2 ({r['headings']['h2_count']}): {r['headings']['h2_texts'][:3] or '— none'}",
        "│",
        "│ STRUCTURED DATA",
        f"│  Schemas: {r['json_ld']['schema_count']}  │  Types: {r['json_ld']['types'] or ['none']}",
        f"│  Organization: {'✓' if r['json_ld']['has_organization'] else '✗'}  │  WebSite: {'✓' if r['json_ld']['has_website'] else '✗'}  │  FAQPage: {'✓' if r['json_ld']['has_faqpage'] else '✗'}  │  Parse errors: {len(r['json_ld']['parse_errors'])}",
        "│",
        "│ EXTRACTABILITY (AI Citation Readiness)",
        f"│  Score: {ex['extractability_score']}/100  │  Words: {ex['word_count']}  │  Long paragraphs: {ex['long_paragraph_count']}",
        f"│  Statistic density: {ex['statistic_density_per_1000']}/1000 words  │  FAQ: {'✓' if ex['has_faq'] else '✗'}  │  Tables: {ex['table_count']}  │  Lists: {ex['list_count']}",
        f"│  Images: {ex['image_count']} total  │  With alt: {ex['images_with_alt']} ({int(ex['images_alt_coverage']*100)}%)",
        "│",
        "│ E-E-A-T SIGNALS",
        f"│  Author: {'✓' if r['eeat']['has_author'] else '✗'}  │  Date: {'✓' if r['eeat']['has_date'] else '✗'}  │  Credentials: {'✓' if r['eeat']['has_credentials'] else '✗'}  │  Social proof: {'✓' if r['eeat']['has_social_proof'] else '✗'}",
        f"│  Impressum: {'✓' if r['eeat']['has_impressum'] else '✗'}  │  Datenschutz: {'✓' if r['eeat']['has_privacy'] else '✗'}  │  Contact: {'✓' if r['eeat']['has_contact_link'] else '✗'}  │  About: {'✓' if r['eeat']['has_about_link'] else '✗'}",
        f"│  External links: {r['eeat']['external_link_count']}  │  Trusted citations: {r['eeat']['trusted_citation_count']}",
        "│",
        f"│ ISSUES ({len(r['issues'])} total)",
        "│",
    ]
    for i, issue in enumerate(r["issues"], 1):
        sev = issue["severity"].upper()
        lines.append(f"│  {i}. [{sev}] {issue['check']}")
        if issue.get("detail"):
            lines.append(f"│     → {issue['detail'][:120]}")
        lines.append(f"│     Fix: {issue['fix'][:150]}")
        lines.append("│")
    lines.append("└" + "─"*60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="On-page SEO + AI extractability audit (no JS rendering)"
    )
    parser.add_argument("url", help="URL to audit")
    parser.add_argument("--output", choices=["json", "text"], default="text",
                        help="Output format (default: text)")
    parser.add_argument("--save", metavar="FILE", help="Save output to file")
    args = parser.parse_args()

    url = args.url
    if not url.startswith("http"):
        url = "https://" + url

    result = run_audit(url)

    output = (
        json.dumps(result, indent=2, ensure_ascii=False)
        if args.output == "json"
        else format_text(result)
    )

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Saved to {args.save}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
