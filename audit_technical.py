#!/usr/bin/env python3
"""
audit_technical.py — Technical SEO + AI crawler access audit
Part of the search-visibility-engineering skill.

Usage:
  python3 audit_technical.py <domain-or-url> [--output json|text] [--psi-key <api-key>]

What it checks:
  - robots.txt: fetch, parse rules for every major classic + AI crawler
  - Live WAF probe: does each AI crawler user-agent actually get a 2xx? (WAF/Cloudflare blocking is the
    #1 silent killer of AI search visibility — site ranks in Google but never cited in ChatGPT)
  - Sitemap: presence, location, format, entry count
  - HTTPS redirect
  - Redirect chain depth
  - IndexNow key presence (Bing/Yandex real-time indexing)
  - Core Web Vitals via PageSpeed Insights API (optional API key, rate-limited without)
  - X-Robots-Tag header
  - Compression (gzip/br)
  - Security headers (basic check)
  - llms.txt presence (noted but not prioritised — Ahrefs June 2026 study: 97% of llms.txt files
    receive zero AI crawler traffic; Google has explicitly stated it's not a ranking/AI factor)
"""

from __future__ import annotations
import sys
import json
import re
import argparse
import time
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
from io import StringIO

try:
    import requests
except ImportError:
    print("Install dependencies: pip3 install requests", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Crawler definitions
# ---------------------------------------------------------------------------
# Sources:
# - Googlebot: developers.google.com/search/docs/crawling-indexing/googlebot
# - Bingbot: bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0
# - GPTBot/OAI-SearchBot: platform.openai.com/docs/gptbot
# - ClaudeBot/anthropic-ai: anthropic.com/claude-web-search
# - PerplexityBot: perplexity.ai/perplexitybot
# - Google-Extended: NOTE — controls Gemini AI TRAINING data, NOT AI Overviews.
#   Blocking Google-Extended does NOT prevent appearing in AI Overviews.
#   AI Overviews are controlled by regular Googlebot access (the standard search index).
# - Applebot-Extended: controls Apple Intelligence training

CRAWLERS = [
    # Classic search — block = not indexed = no traffic
    {
        "name": "Googlebot",
        "category": "classic_search",
        "robots_token": "Googlebot",
        "user_agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "block_effect": "Site disappears from Google Search. Also prevents Google AI Overviews (both use same index).",
        "allow_recommended": True,
    },
    {
        "name": "Bingbot",
        "category": "classic_search",
        "robots_token": "Bingbot",
        "user_agent": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        "block_effect": "Site disappears from Bing and Yahoo Search (Yahoo is Bing-powered since 2009). "
                        "Also prevents Bing Copilot/AI citations.",
        "allow_recommended": True,
    },
    # AI training + search citation crawlers
    {
        "name": "GPTBot",
        "category": "ai_training",
        "robots_token": "GPTBot",
        "user_agent": "GPTBot/1.0",
        "block_effect": "Prevents OpenAI from using your content for model training. "
                        "Does NOT prevent ChatGPT from citing you if OAI-SearchBot is allowed.",
        "allow_recommended": None,  # owner's choice — training vs. citation are separate
    },
    {
        "name": "OAI-SearchBot",
        "category": "ai_search",
        "robots_token": "OAI-SearchBot",
        "user_agent": "OAI-SearchBot/1.0",
        "block_effect": "ChatGPT Search (with web browsing) cannot cite your content. "
                        "This is the bot to ALLOW if you want ChatGPT to reference you.",
        "allow_recommended": True,
    },
    {
        "name": "ClaudeBot",
        "category": "ai_search",
        "robots_token": "ClaudeBot",
        "user_agent": "ClaudeBot/1.0; +https://anthropic.com/",
        "block_effect": "Anthropic's web-search and research crawler cannot access your site.",
        "allow_recommended": True,
    },
    {
        "name": "anthropic-ai",
        "category": "ai_training",
        "robots_token": "anthropic-ai",
        "user_agent": "anthropic-ai",
        "block_effect": "Anthropic's broader training/data crawler cannot access your site.",
        "allow_recommended": None,  # owner's choice
    },
    {
        "name": "PerplexityBot",
        "category": "ai_search",
        "robots_token": "PerplexityBot",
        "user_agent": "PerplexityBot/1.0; +https://perplexity.ai/perplexitybot.html",
        "block_effect": "Perplexity cannot cite your content in AI answers.",
        "allow_recommended": True,
    },
    {
        "name": "Google-Extended",
        "category": "ai_training",
        "robots_token": "Google-Extended",
        "user_agent": "Mozilla/5.0 (compatible; Google-Extended/1.0; +http://www.google.com/bot.html)",
        "block_effect": "IMPORTANT: Google-Extended controls Gemini model TRAINING only. "
                        "It does NOT affect Google Search ranking or AI Overviews visibility. "
                        "AI Overviews use the regular Googlebot index. Common misconception to clarify with clients.",
        "allow_recommended": None,  # owner's choice for training
    },
    {
        "name": "Applebot-Extended",
        "category": "ai_training",
        "robots_token": "Applebot-Extended",
        "user_agent": "Mozilla/5.0 (compatible; Applebot-Extended/0.1; +http://www.apple.com/go/applebot)",
        "block_effect": "Controls Apple Intelligence (Siri, Apple AI summaries) training data access.",
        "allow_recommended": None,  # owner's choice
    },
    {
        "name": "Meta-ExternalAgent",
        "category": "ai_training",
        "robots_token": "Meta-ExternalAgent",
        "user_agent": "Meta-ExternalAgent/1.0 (https://developers.facebook.com/docs/sharing/webmasters/crawler)",
        "block_effect": "Meta AI and Facebook AI training cannot access your content.",
        "allow_recommended": None,
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_base(url: str) -> tuple[str, str]:
    """Returns (scheme://host, host) for a given URL or domain string."""
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}", parsed.netloc


def fetch(url: str, ua: str | None = None, timeout: int = 15,
          allow_redirects: bool = True) -> requests.Response | None:
    headers = {}
    if ua:
        headers["User-Agent"] = ua
    try:
        return requests.get(url, headers=headers, timeout=timeout,
                            allow_redirects=allow_redirects)
    except requests.RequestException:
        return None


# ---------------------------------------------------------------------------
# Robots.txt audit
# ---------------------------------------------------------------------------

def audit_robots(base: str, host: str) -> dict:
    result = {"url": f"{base}/robots.txt", "crawlers": {}, "has_sitemap_directive": False,
              "sitemap_urls": [], "fetch_ok": False, "content_preview": None}

    r = fetch(f"{base}/robots.txt")
    if not r or r.status_code != 200:
        result["error"] = f"Could not fetch robots.txt (status: {r.status_code if r else 'no response'})"
        return result

    result["fetch_ok"] = True
    content = r.text
    result["content_preview"] = content[:500]

    # Extract sitemap directives
    sitemap_lines = [l.strip() for l in content.splitlines() if l.lower().startswith("sitemap:")]
    result["has_sitemap_directive"] = bool(sitemap_lines)
    result["sitemap_urls"] = [l.split(":", 1)[1].strip() for l in sitemap_lines if ":" in l]

    # Parse rules per crawler using Python's RobotFileParser
    rp = RobotFileParser()
    rp.set_url(f"{base}/robots.txt")
    try:
        rp.read()
    except Exception:
        # Fall back to raw parse
        pass

    for crawler in CRAWLERS:
        token = crawler["robots_token"]
        ua = crawler["user_agent"]

        # Check allow/disallow from content directly (more reliable than urllib's parser
        # which doesn't handle partial user-agent name matching well)
        rules = extract_rules_for_token(content, token)

        # Also test the canonical URL
        can_fetch = True
        try:
            can_fetch = rp.can_fetch(token, f"{base}/") or rp.can_fetch(ua, f"{base}/")
        except Exception:
            pass

        # Determine effective access
        if rules["has_disallow_all"]:
            effective = "blocked"
        elif rules["has_disallow_partial"]:
            effective = "partial"
        elif rules["has_allow_all"] or rules["has_allow_partial"]:
            effective = "allowed"
        else:
            effective = "allowed"  # No explicit rule = allowed by default

        result["crawlers"][crawler["name"]] = {
            "effective_access": effective,
            "rules": rules,
            "allow_recommended": crawler["allow_recommended"],
            "category": crawler["category"],
            "block_effect": crawler["block_effect"],
            "issue": _robots_issue(crawler, effective),
        }

    return result


def extract_rules_for_token(content: str, token: str) -> dict:
    """Extract allow/disallow rules for a specific user-agent token from robots.txt."""
    lines = content.splitlines()
    in_block = False
    disallow_paths = []
    allow_paths = []

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("user-agent:"):
            ua_val = line.split(":", 1)[1].strip()
            in_block = ua_val == "*" or token.lower() in ua_val.lower()
        elif in_block:
            if line.lower().startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                disallow_paths.append(path)
            elif line.lower().startswith("allow:"):
                path = line.split(":", 1)[1].strip()
                allow_paths.append(path)

    return {
        "disallow_paths": disallow_paths[:10],
        "allow_paths": allow_paths[:10],
        "has_disallow_all": "/" in disallow_paths and not (allow_paths and allow_paths[0] == "/"),
        "has_disallow_partial": bool(disallow_paths) and "/" not in disallow_paths,
        "has_allow_all": "/" in allow_paths or not disallow_paths,
        "has_allow_partial": bool(allow_paths),
    }


def _robots_issue(crawler: dict, effective: str) -> str | None:
    rec = crawler["allow_recommended"]
    name = crawler["name"]
    if rec is True and effective == "blocked":
        return f"RECOMMENDED: Allow {name}. {crawler['block_effect']}"
    if rec is True and effective == "partial":
        return f"CHECK: {name} has partial rules — verify key pages are accessible."
    return None


# ---------------------------------------------------------------------------
# Live WAF probe
# ---------------------------------------------------------------------------

def probe_waf(base: str) -> dict:
    """
    Probe the site using each AI crawler's User-Agent.
    A 403/406/429/5xx when crawlers hit it but a browser gets 200 = WAF blocking.
    This is the silent killer: site ranks in Google but never cited in ChatGPT/Perplexity.
    """
    results = {}

    # First get browser baseline
    browser_ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    browser_r = fetch(base, ua=browser_ua)
    browser_status = browser_r.status_code if browser_r else None
    results["browser_baseline"] = browser_status

    for crawler in CRAWLERS:
        name = crawler["name"]
        ua = crawler["user_agent"]
        r = fetch(base, ua=ua)
        status = r.status_code if r else None
        blocked = (
            status in (403, 406, 429) or
            (status and status >= 500) or
            status is None
        ) and browser_status in (200, 301, 302)

        results[name] = {
            "status": status,
            "blocked": blocked,
            "note": "WAF/Cloudflare is blocking this crawler" if blocked else None,
        }
        time.sleep(0.3)  # polite delay

    blocked_count = sum(1 for k, v in results.items() if k != "browser_baseline" and v.get("blocked"))
    results["blocked_crawler_count"] = blocked_count
    return results


# ---------------------------------------------------------------------------
# Sitemap audit
# ---------------------------------------------------------------------------

def audit_sitemap(base: str, sitemap_urls: list[str]) -> dict:
    result = {"sitemaps_found": [], "total_urls": 0, "issues": []}

    # Candidate locations
    candidates = list(set(sitemap_urls + [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemap-index.xml",
        f"{base}/sitemap.xml.gz",
    ]))

    for url in candidates:
        r = fetch(url)
        if r and r.status_code == 200:
            content_type = r.headers.get("Content-Type", "")
            is_xml = "xml" in content_type or url.endswith(".xml")
            url_count = r.text.count("<url>") + r.text.count("<sitemap>")
            result["sitemaps_found"].append({
                "url": url, "status": r.status_code,
                "entries": url_count, "content_type": content_type,
            })
            result["total_urls"] += url_count

    if not result["sitemaps_found"]:
        result["issues"].append("No sitemap found at common locations. Create /sitemap.xml and list it in robots.txt.")
    elif result["total_urls"] == 0:
        result["issues"].append("Sitemap found but appears empty.")

    return result


# ---------------------------------------------------------------------------
# HTTPS / redirect audit
# ---------------------------------------------------------------------------

def audit_tls(host: str) -> dict:
    """
    Check TLS certificate validity for both apex and www subdomain.
    Common gap: cert covers www.example.com but NOT example.com (or vice versa).
    Any crawler or AI bot hitting the bare apex over HTTPS gets CERTIFICATE_VERIFY_FAILED.
    This causes a silent retrievability failure — the site ranks fine but bots error out.
    """
    import ssl, socket
    result = {"apex": {}, "www": {}}

    variants = [
        ("apex", host.lstrip("www.").lstrip(".")),
        ("www", f"www.{host.lstrip('www.').lstrip('.')}"),
    ]

    for label, hostname in variants:
        try:
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(socket.socket(), server_hostname=hostname) as s:
                s.settimeout(8)
                s.connect((hostname, 443))
                cert = s.getpeercert()
                sans = [v for t, v in cert.get("subjectAltName", []) if t == "DNS"]
                result[label] = {
                    "hostname": hostname,
                    "valid": True,
                    "sans": sans,
                    "covers_hostname": any(
                        hostname == san or san.startswith("*.") and hostname.endswith(san[1:])
                        for san in sans
                    ),
                    "expires": cert.get("notAfter"),
                }
        except ssl.CertificateError as e:
            result[label] = {"hostname": hostname, "valid": False, "error": str(e), "sans": [], "covers_hostname": False}
        except (OSError, socket.timeout) as e:
            result[label] = {"hostname": hostname, "valid": None, "error": str(e), "sans": [], "covers_hostname": None}

    # Cross-check: both should be covered
    apex_ok = result["apex"].get("covers_hostname")
    www_ok = result["www"].get("covers_hostname")
    result["mismatch"] = bool(apex_ok) != bool(www_ok)
    result["summary"] = (
        "Both apex and www covered ✓" if apex_ok and www_ok else
        f"MISMATCH — apex {'covered' if apex_ok else 'NOT covered'}, www {'covered' if www_ok else 'NOT covered'}. "
        "Crawlers hitting the uncovered variant get CERTIFICATE_VERIFY_FAILED — a silent retrievability failure."
        if result["mismatch"] else
        "Could not verify (connection error)"
    )
    return result


def audit_redirects(base: str, host: str) -> dict:
    result = {}
    http_url = f"http://{host}/"
    https_url = f"https://{host}/"

    # Check HTTP → HTTPS redirect
    r = fetch(http_url, allow_redirects=False)
    if r:
        result["http_to_https_redirect"] = r.status_code in (301, 302, 307, 308)
        result["http_redirect_status"] = r.status_code
        result["http_redirect_target"] = r.headers.get("Location")
    else:
        result["http_to_https_redirect"] = False
        result["http_redirect_status"] = None

    # Check redirect chain depth
    chain = []
    current = https_url
    seen = set()
    for _ in range(10):
        r = fetch(current, allow_redirects=False)
        if not r:
            break
        chain.append({"url": current, "status": r.status_code})
        if r.status_code not in (301, 302, 307, 308):
            break
        location = r.headers.get("Location", "")
        if not location or location in seen:
            break
        seen.add(location)
        current = location if location.startswith("http") else f"https://{host}{location}"

    result["redirect_chain"] = chain
    result["redirect_hops"] = len([c for c in chain if c["status"] in (301,302,307,308)])
    result["redirect_chain_ok"] = result["redirect_hops"] <= 2

    # Response headers from final URL
    final_r = fetch(https_url)
    if final_r:
        headers = dict(final_r.headers)
        result["compression"] = final_r.headers.get("Content-Encoding", "none")
        result["hsts"] = "Strict-Transport-Security" in final_r.headers
        result["x_robots_tag"] = final_r.headers.get("X-Robots-Tag")
        result["is_noindex_header"] = "noindex" in (result["x_robots_tag"] or "").lower()
        result["server"] = final_r.headers.get("Server")
        result["cache_control"] = final_r.headers.get("Cache-Control")

    return result


# ---------------------------------------------------------------------------
# IndexNow
# ---------------------------------------------------------------------------

def audit_indexnow(base: str) -> dict:
    """
    IndexNow: a real-time URL submission protocol supported by Bing, Yandex, and others.
    Google does NOT participate in IndexNow.
    To implement: create a <key>.txt file at site root, then ping api.indexnow.org on page publish/update.
    """
    result = {
        "protocol_info": (
            "IndexNow lets you ping Bing and Yandex instantly when content changes. "
            "Google does not participate — Googlebot discovers changes via crawl or GSC Sitemap API."
        ),
        "key_found": False,
        "key_value": None,
        "submit_endpoint": "https://api.indexnow.org/indexnow",
        "participating_engines": ["Bing", "Yandex", "Naver", "Seznam.cz"],
        "google_participates": False,
    }

    # Look for IndexNow key file (common patterns)
    r_main = fetch(f"{base}/")
    if r_main:
        # Check meta tag
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r_main.text, "html.parser")
        indexnow_meta = soup.find("meta", attrs={"name": re.compile(r"indexnow", re.I)})
        if indexnow_meta:
            result["key_found"] = True
            result["key_value"] = indexnow_meta.get("content")
            result["method"] = "meta_tag"

    # Try common key file pattern (hex string)
    # We can't know the key without checking, so just report the setup status
    return result


# ---------------------------------------------------------------------------
# PageSpeed Insights (Core Web Vitals)
# ---------------------------------------------------------------------------
# Thresholds (Google, confirmed 2024-2026):
# LCP: good < 2.5s, needs improvement < 4.0s, poor >= 4.0s
# INP: good < 200ms, needs improvement < 500ms, poor >= 500ms (replaced FID in March 2024)
# CLS: good < 0.1, needs improvement < 0.25, poor >= 0.25

CWV_THRESHOLDS = {
    "lcp": {"good": 2500, "needs_improvement": 4000, "unit": "ms"},
    "inp": {"good": 200, "needs_improvement": 500, "unit": "ms"},
    "cls": {"good": 0.1, "needs_improvement": 0.25, "unit": "score"},
    "fcp": {"good": 1800, "needs_improvement": 3000, "unit": "ms"},
    "ttfb": {"good": 800, "needs_improvement": 1800, "unit": "ms"},
}


def audit_cwv(url: str, api_key: str | None = None) -> dict:
    """Query PageSpeed Insights API for Core Web Vitals. Works without API key (rate limited)."""
    result = {
        "note": "Core Web Vitals are a confirmed Google ranking factor. Bing uses page quality signals but has not confirmed CWV as an explicit ranking factor.",
        "mobile": None,
        "desktop": None,
        "error": None,
    }

    for strategy in ["mobile", "desktop"]:
        endpoint = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
        params = {"url": url, "strategy": strategy, "category": "performance"}
        if api_key:
            params["key"] = api_key

        r = fetch(f"{endpoint}?{'&'.join(f'{k}={v}' for k,v in params.items())}")
        if not r or r.status_code != 200:
            if r and r.status_code == 429:
                result["error"] = "PSI API rate limited. Pass --psi-key to increase quota."
            else:
                result["error"] = f"PSI API error: {r.status_code if r else 'no response'}"
            continue

        try:
            data = r.json()
            cats = data.get("lighthouseResult", {}).get("categories", {})
            audits = data.get("lighthouseResult", {}).get("audits", {})
            metrics = data.get("loadingExperience", {}).get("metrics", {})

            strategy_result = {
                "performance_score": int((cats.get("performance", {}).get("score", 0) or 0) * 100),
                "metrics": {},
            }

            # Field data from CrUX
            cwv_map = {
                "LARGEST_CONTENTFUL_PAINT_MS": "lcp",
                "INTERACTION_TO_NEXT_PAINT": "inp",
                "CUMULATIVE_LAYOUT_SHIFT_SCORE": "cls",
                "FIRST_CONTENTFUL_PAINT_MS": "fcp",
                "EXPERIMENTAL_TIME_TO_FIRST_BYTE": "ttfb",
            }
            for crux_key, metric in cwv_map.items():
                if crux_key in metrics:
                    m = metrics[crux_key]
                    val = m.get("percentile") or m.get("value")
                    cat = m.get("category", "").lower()
                    strategy_result["metrics"][metric] = {
                        "value": val, "category": cat,
                        "thresholds": CWV_THRESHOLDS.get(metric),
                    }

            result[strategy] = strategy_result
        except Exception as e:
            result["error"] = f"PSI parse error: {e}"

        time.sleep(1)  # respect rate limits

    return result


# ---------------------------------------------------------------------------
# llms.txt (low priority — documented honestly)
# ---------------------------------------------------------------------------

def audit_llmstxt(base: str) -> dict:
    """
    Check for llms.txt presence. Noted but NOT prioritised.

    Evidence: Ahrefs studied 137K domains (June 2026):
    - 28% publish llms.txt
    - 97% of those files received ZERO AI crawler traffic in May 2026
    - 96% of fetches that did land came from bots (checkers, research tools), not AI assistants
    - Google explicitly says llms.txt is NOT a ranking or AI Overviews factor
    - John Mueller (Google): llms.txt is "not done for search; a temporary crutch for coding tools"

    Our recommendation: if the site is developer-facing or has large docs, add llms.txt in ~10 minutes.
    For service/marketing sites, it's optional and very low ROI.
    """
    r = fetch(f"{base}/llms.txt")
    present = bool(r and r.status_code == 200 and len(r.text) > 10)
    return {
        "present": present,
        "url": f"{base}/llms.txt",
        "content_preview": r.text[:300] if present else None,
        "priority": "low",
        "note": (
            "llms.txt is present. Ahrefs (Jun 2026, 137K sites): 97% of llms.txt files get zero AI crawler traffic. "
            "Google says it's not an AI Overviews factor. Keep it if already present; don't prioritise building it."
            if present else
            "llms.txt not found. Low priority — 97% of existing llms.txt files receive zero AI crawler traffic (Ahrefs, Jun 2026). "
            "Add it in ~10 min if site has developer docs; skip for pure marketing sites."
        ),
    }


# ---------------------------------------------------------------------------
# Full audit runner
# ---------------------------------------------------------------------------

def run_audit(url_or_domain: str, psi_key: str | None = None) -> dict:
    base, host = get_base(url_or_domain)
    print(f"  Auditing technical layer for {base} ...", file=sys.stderr)

    result = {"base_url": base, "host": host}

    print("  → robots.txt ...", file=sys.stderr)
    result["robots"] = audit_robots(base, host)

    print("  → WAF probe (testing each AI crawler user-agent) ...", file=sys.stderr)
    result["waf_probe"] = probe_waf(base)

    print("  → Sitemap ...", file=sys.stderr)
    sitemap_urls = result["robots"].get("sitemap_urls", [])
    result["sitemap"] = audit_sitemap(base, sitemap_urls)

    print("  → TLS certificate (apex vs www) ...", file=sys.stderr)
    result["tls"] = audit_tls(host)

    print("  → Redirects + HTTP headers ...", file=sys.stderr)
    result["redirects"] = audit_redirects(base, host)

    print("  → IndexNow ...", file=sys.stderr)
    result["indexnow"] = audit_indexnow(base)

    print("  → llms.txt ...", file=sys.stderr)
    result["llmstxt"] = audit_llmstxt(base)

    if psi_key is not False:  # None = try without key; False = skip entirely
        print("  → PageSpeed Insights (Core Web Vitals) ...", file=sys.stderr)
        result["cwv"] = audit_cwv(base, psi_key)
    else:
        result["cwv"] = {"skipped": True, "note": "Pass --psi-key to enable CWV audit."}

    # Build issues summary
    result["issues"] = build_issues(result)
    return result


def build_issues(result: dict) -> list[dict]:
    issues = []

    def add(sev, check, detail, fix):
        issues.append({"severity": sev, "check": check, "detail": detail, "fix": fix})

    robots = result.get("robots", {})
    waf = result.get("waf_probe", {})
    redirects = result.get("redirects", {})
    sitemap = result.get("sitemap", {})
    cwv = result.get("cwv", {})

    # Critical: WAF blocking
    blocked_names = [k for k, v in waf.items() if isinstance(v, dict) and v.get("blocked")]
    if blocked_names:
        add("critical", f"WAF blocking {len(blocked_names)} AI crawlers: {', '.join(blocked_names)}",
            "Cloudflare / WAF security rules are returning 403/406 to AI crawler user-agents "
            "while letting browsers through. This is the most common reason a site ranks in Google "
            "but never appears in ChatGPT, Perplexity, or AI Overviews.",
            "Whitelist AI crawler IPs/User-Agents in Cloudflare WAF, or create a Page Rule / WAF Rule "
            "to allow: GPTBot, OAI-SearchBot, ClaudeBot, PerplexityBot, Googlebot. "
            "Cloudflare: Security → WAF → Tools → IP Access Rules or Bot Management → verified bots.")

    # Critical: noindex via header
    if redirects.get("is_noindex_header"):
        add("critical", "X-Robots-Tag: noindex header found",
            f"Value: {redirects.get('x_robots_tag')}",
            "Remove the noindex directive from X-Robots-Tag HTTP header or restrict to specific pages.")

    # High: AI search crawlers blocked in robots.txt
    for crawler_name, data in robots.get("crawlers", {}).items():
        issue = data.get("issue")
        if issue and data["category"] == "ai_search":
            add("high", f"robots.txt blocks {crawler_name} (AI search/citation crawler)", issue,
                f"Add to robots.txt:\nUser-agent: {crawler_name}\nAllow: /")

    # High: Classic search crawlers blocked
    for crawler_name, data in robots.get("crawlers", {}).items():
        if data["category"] == "classic_search" and data["effective_access"] == "blocked":
            add("high", f"robots.txt blocks {crawler_name} (classic search crawler)",
                data["block_effect"],
                f"Remove Disallow: / rule for {crawler_name} in robots.txt unless intentional.")

    # High: No sitemap
    if not sitemap.get("sitemaps_found"):
        add("high", "No XML sitemap found",
            "Sitemap helps both classic search engines and some AI crawlers discover all pages quickly.",
            "Create /sitemap.xml. For WordPress: install Yoast SEO or Rank Math. "
            "For static sites: generate with your SSG. Submit to Google Search Console and Bing Webmaster Tools.")

    # Medium: No robots.txt
    if not robots.get("fetch_ok"):
        add("medium", "robots.txt not found or inaccessible",
            "Missing robots.txt means crawlers must guess your crawling preferences. "
            "Also required to specify sitemap location.",
            "Create /robots.txt with at minimum:\nUser-agent: *\nAllow: /\nSitemap: https://yourdomain.com/sitemap.xml")

    # Medium: HTTP → HTTPS redirect missing
    if not redirects.get("http_to_https_redirect"):
        add("medium", "No HTTP → HTTPS redirect",
            f"http:// returns {redirects.get('http_redirect_status')} — not redirecting to HTTPS.",
            "Configure a 301 redirect from HTTP to HTTPS. Most hosting panels have a one-click SSL redirect setting.")

    # Medium: No HSTS
    if not redirects.get("hsts"):
        add("medium", "No HSTS (Strict-Transport-Security) header",
            "HSTS forces browsers to always use HTTPS, preventing SSL stripping attacks.",
            "Add: Strict-Transport-Security: max-age=31536000; includeSubDomains; preload")

    # Medium: Long redirect chain
    if not redirects.get("redirect_chain_ok", True):
        hops = redirects.get("redirect_hops", 0)
        add("medium", f"Redirect chain too long ({hops} hops)",
            "Each redirect hop costs ~100ms and dilutes link equity.",
            "Reduce to maximum 1-2 hops. Update internal links to point directly to the final URL.")

    # Medium: No compression
    if redirects.get("compression") in (None, "none", "identity"):
        add("medium", "No HTTP compression (gzip/Brotli)",
            "Uncompressed HTML transfers are larger and slower — affects page speed and LCP.",
            "Enable Brotli or gzip compression on your web server / CDN. Cloudflare enables this by default.")

    # Medium: No sitemap in robots.txt
    if robots.get("fetch_ok") and not robots.get("has_sitemap_directive") and sitemap.get("sitemaps_found"):
        add("low", "Sitemap found but not referenced in robots.txt",
            "Listing the sitemap in robots.txt helps crawlers discover it faster.",
            "Add to robots.txt: Sitemap: https://yourdomain.com/sitemap.xml")

    # Low: AI training crawlers blocked (informational)
    for crawler_name, data in robots.get("crawlers", {}).items():
        if data["category"] == "ai_training" and data["effective_access"] == "blocked" and data["allow_recommended"] is None:
            add("low", f"robots.txt blocks {crawler_name} (AI training — owner choice)",
                data["block_effect"],
                "This is your choice as site owner. Blocking training crawlers is legitimate. "
                "Note: it does NOT prevent the AI's search crawler from citing you (separate bot).")

    # TLS cert mismatch
    tls = result.get("tls", {})
    if tls.get("mismatch"):
        apex = tls.get("apex", {})
        www = tls.get("www", {})
        uncovered = apex.get("hostname") if not apex.get("covers_hostname") else www.get("hostname")
        add("critical", f"TLS cert mismatch — {uncovered} not covered by certificate",
            tls.get("summary", ""),
            "Ensure the SSL certificate's SAN list covers BOTH the apex domain and www subdomain. "
            "On Cloudflare/Vercel/Netlify this is automatic — check your domain settings. "
            "On custom servers: reissue with certbot using both -d example.com -d www.example.com. "
            "Until fixed, any crawler or AI bot hitting the uncovered variant gets CERTIFICATE_VERIFY_FAILED — "
            "a silent retrievability failure that is very hard to diagnose without this check.")

    # CWV issues
    if cwv and not cwv.get("skipped"):
        for strategy in ["mobile", "desktop"]:
            strat_data = cwv.get(strategy)
            if not strat_data:
                continue
            metrics = strat_data.get("metrics", {})
            for metric, data in metrics.items():
                if data.get("category") in ("slow", "needs improvement", "poor"):
                    thresholds = data.get("thresholds", {})
                    add("medium", f"CWV {metric.upper()} needs improvement on {strategy}",
                        f"Value: {data['value']}{thresholds.get('unit','')} | Good threshold: <{thresholds.get('good','')}",
                        f"CWV is a confirmed Google ranking factor. "
                        f"For {metric}: see Google's developer docs for {metric} optimization.")

    return sorted(issues, key=lambda x: ["critical","high","medium","low"].index(x["severity"]))


# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------

def format_text(result: dict) -> str:
    lines = [
        "",
        f"┌─ TECHNICAL AUDIT ─ {result['base_url']} {'─'*20}",
    ]

    issues = result.get("issues", [])
    lines.append(f"│  Issues: {len(issues)} total")
    lines.append("│")

    # robots.txt
    robots = result.get("robots", {})
    lines.append("│ ROBOTS.TXT")
    lines.append(f"│  Fetch: {'✓' if robots.get('fetch_ok') else '✗ MISSING'}")
    lines.append(f"│  Sitemap directive: {'✓' if robots.get('has_sitemap_directive') else '✗'} | Sitemaps: {robots.get('sitemap_urls', [])}")
    for name, data in (robots.get("crawlers") or {}).items():
        icon = {"allowed": "✓", "partial": "~", "blocked": "✗"}.get(data["effective_access"], "?")
        note = f" ← {data['issue'][:60]}" if data.get("issue") else ""
        lines.append(f"│  {icon} {name}: {data['effective_access'].upper()}{note}")

    lines.append("│")
    lines.append("│ WAF PROBE (live test with actual crawler user-agents)")
    waf = result.get("waf_probe", {})
    browser = waf.get("browser_baseline")
    lines.append(f"│  Browser baseline: {browser}")
    for name, data in waf.items():
        if name in ("browser_baseline", "blocked_crawler_count"):
            continue
        icon = "✗ BLOCKED" if data.get("blocked") else f"✓ {data.get('status')}"
        lines.append(f"│  {name}: {icon}")
    blocked = waf.get("blocked_crawler_count", 0)
    if blocked:
        lines.append(f"│  ⚠  {blocked} crawlers BLOCKED by WAF — this silently kills AI visibility")

    lines.append("│")
    lines.append("│ SITEMAP")
    sitemap = result.get("sitemap", {})
    if sitemap.get("sitemaps_found"):
        for s in sitemap["sitemaps_found"]:
            lines.append(f"│  ✓ {s['url']} ({s['entries']} entries)")
    else:
        lines.append("│  ✗ No sitemap found")

    lines.append("│")
    lines.append("│ HTTPS / REDIRECTS / HEADERS")
    redir = result.get("redirects", {})
    lines.append(f"│  HTTP→HTTPS: {'✓' if redir.get('http_to_https_redirect') else '✗'}  |  HSTS: {'✓' if redir.get('hsts') else '✗'}  |  Compression: {redir.get('compression','?')}")
    lines.append(f"│  Redirect chain: {redir.get('redirect_hops',0)} hops ({'✓ ok' if redir.get('redirect_chain_ok') else '✗ too long'})")
    if redir.get("x_robots_tag"):
        lines.append(f"│  X-Robots-Tag: {redir['x_robots_tag']} {'← ⚠ NOINDEX' if redir.get('is_noindex_header') else ''}")

    lines.append("│")
    lines.append("│ TLS CERTIFICATE")
    tls = result.get("tls", {})
    lines.append(f"│  {tls.get('summary', 'not checked')}")
    for variant in ["apex", "www"]:
        v = tls.get(variant, {})
        if v:
            icon = "✓" if v.get("covers_hostname") else ("✗" if v.get("covers_hostname") is False else "?")
            lines.append(f"│  {icon} {v.get('hostname','')}: {'cert valid' if v.get('valid') else v.get('error','')[:80]}")

    lines.append("│")
    lines.append("│ INDEXNOW")
    idxnow = result.get("indexnow", {})
    lines.append(f"│  Key found: {'✓' if idxnow.get('key_found') else '✗'}  |  {idxnow.get('protocol_info','')[:80]}")

    lines.append("│")
    lines.append("│ LLMS.TXT (low priority)")
    llms = result.get("llmstxt", {})
    lines.append(f"│  Present: {'✓' if llms.get('present') else 'No'}  |  {llms.get('note','')[:100]}")

    lines.append("│")
    cwv = result.get("cwv", {})
    if not cwv.get("skipped"):
        lines.append("│ CORE WEB VITALS")
        for strategy in ["mobile", "desktop"]:
            strat = cwv.get(strategy)
            if strat:
                lines.append(f"│  {strategy.title()}: Performance {strat.get('performance_score')}/100")
                for metric, data in (strat.get("metrics") or {}).items():
                    cat_icon = "✓" if data.get("category") in ("fast", "good") else "✗"
                    lines.append(f"│    {cat_icon} {metric.upper()}: {data.get('value')}{CWV_THRESHOLDS.get(metric,{}).get('unit','')} ({data.get('category')})")
    else:
        lines.append("│ CORE WEB VITALS: skipped (pass --psi-key to enable)")

    lines.append("│")
    lines.append(f"│ ISSUES ({len(issues)} total)")
    lines.append("│")
    for i, issue in enumerate(issues, 1):
        lines.append(f"│  {i}. [{issue['severity'].upper()}] {issue['check']}")
        if issue.get("detail"):
            lines.append(f"│     → {issue['detail'][:130]}")
        lines.append(f"│     Fix: {issue['fix'][:160]}")
        lines.append("│")

    lines.append("└" + "─"*60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Technical SEO + AI crawler access audit"
    )
    parser.add_argument("url", help="Domain or URL to audit (e.g. nagacodex.cloud or https://nagacodex.cloud)")
    parser.add_argument("--output", choices=["json", "text"], default="text")
    parser.add_argument("--save", metavar="FILE")
    parser.add_argument("--psi-key", metavar="KEY", help="Google PageSpeed Insights API key (optional)")
    parser.add_argument("--skip-cwv", action="store_true", help="Skip PageSpeed Insights call")
    args = parser.parse_args()

    psi_key = None
    if args.skip_cwv:
        psi_key = False
    elif args.psi_key:
        psi_key = args.psi_key

    result = run_audit(args.url, psi_key=psi_key)

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
