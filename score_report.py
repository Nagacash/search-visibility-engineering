#!/usr/bin/env python3
"""
score_report.py — Aggregated scoring + bilingual client report
Part of the search-visibility-engineering skill.

Reads JSON output from audit_page.py and audit_technical.py, computes a
weighted pillar score, ranks fixes by impact/effort, and generates a
bilingual (DE/EN) Markdown and HTML report ready to send to clients.

Usage:
  # Run both audits first:
  python3 audit_page.py https://example.com --output json --save page_audit.json
  python3 audit_technical.py example.com --output json --save tech_audit.json

  # Then generate the report:
  python3 score_report.py \\
    --page page_audit.json \\
    --tech tech_audit.json \\
    --client "Mustermann GmbH" \\
    --prepared-by "Naga Codex" \\
    --save report.html

  # Or run everything in one command:
  python3 score_report.py --url https://example.com --client "Mustermann GmbH"
"""

from __future__ import annotations
import json
import sys
import argparse
import subprocess
import tempfile
import os
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Pillar definitions and weights
# ---------------------------------------------------------------------------

PILLARS = {
    "retrievability": {
        "name_de": "Auffindbarkeit",
        "name_en": "Retrievability",
        "desc_de": "Können Suchmaschinen und KI-Crawler die Inhalte lesen?",
        "desc_en": "Can search engines and AI crawlers access and read the content?",
        "weight": 0.25,
    },
    "entity_clarity": {
        "name_de": "Entitätsklarheit",
        "name_en": "Entity Clarity",
        "desc_de": "Weiß die KI, wer das Unternehmen ist, was es macht und wo?",
        "desc_en": "Does the AI know who the business is, what it does, and where?",
        "weight": 0.20,
    },
    "extractability": {
        "name_de": "Extrahierbarkeit",
        "name_en": "Content Extractability",
        "desc_de": "Können KI-Systeme einzelne Passagen als Antworten zitieren?",
        "desc_en": "Can AI systems extract standalone passages as quotable answers?",
        "weight": 0.25,
    },
    "classic_seo": {
        "name_de": "Klassisches SEO",
        "name_en": "Classic SEO",
        "desc_de": "Google, Bing, Yahoo: technische Grundlagen, Struktur, Metadaten",
        "desc_en": "Google, Bing, Yahoo: technical fundamentals, structure, metadata",
        "weight": 0.20,
    },
    "trust_eeat": {
        "name_de": "Vertrauen & E-E-A-T",
        "name_en": "Trust & E-E-A-T",
        "desc_de": "Glaubwürdigkeit, Expertise, Autoren, rechtliche Pflichtangaben",
        "desc_en": "Credibility, expertise, authorship, legal compliance",
        "weight": 0.10,
    },
}

GRADE_LABELS = {
    "A": {"de": "Ausgezeichnet", "en": "Excellent"},
    "B": {"de": "Gut", "en": "Good"},
    "C": {"de": "Ausbaufähig", "en": "Needs work"},
    "D": {"de": "Kritisch", "en": "Critical"},
    "F": {"de": "Dringend", "en": "Urgent"},
}


def letter_grade(score: int) -> str:
    if score >= 85: return "A"
    if score >= 70: return "B"
    if score >= 50: return "C"
    if score >= 30: return "D"
    return "F"


# ---------------------------------------------------------------------------
# Score computation from audit data
# ---------------------------------------------------------------------------

def compute_pillar_scores(page: dict, tech: dict) -> dict:
    """Map audit findings to pillar scores (0-100 each)."""
    scores = {}

    # --- RETRIEVABILITY (from tech audit) ---
    ret_score = 100
    waf = tech.get("waf_probe", {})
    robots = tech.get("robots", {})
    redir = tech.get("redirects", {})

    # JS shell in page audit is also a retrievability issue
    if page.get("is_js_shell"):
        ret_score -= 50

    blocked = waf.get("blocked_crawler_count", 0)
    ret_score -= min(blocked * 15, 40)

    # Classic search crawlers blocked in robots.txt
    for name, data in (robots.get("crawlers") or {}).items():
        if data.get("category") == "classic_search" and data.get("effective_access") == "blocked":
            ret_score -= 30

    # No sitemap
    if not tech.get("sitemap", {}).get("sitemaps_found"):
        ret_score -= 10

    # No robots.txt
    if not robots.get("fetch_ok"):
        ret_score -= 10

    # No HTTPS redirect
    if not redir.get("http_to_https_redirect"):
        ret_score -= 10

    scores["retrievability"] = max(0, min(100, ret_score))

    # --- ENTITY CLARITY (from page audit JSON-LD) ---
    ent_score = 100
    jsonld = page.get("json_ld", {})
    if jsonld.get("schema_count", 0) == 0:
        ent_score -= 60
    else:
        if not jsonld.get("has_organization"):
            ent_score -= 35
        if not jsonld.get("has_website"):
            ent_score -= 15
        if jsonld.get("parse_errors"):
            ent_score -= 20

    # sameAs presence can't be checked without deeper parse, but count types
    has_breadcrumb = jsonld.get("has_breadcrumb", False)
    has_service = jsonld.get("has_service", False)
    if has_breadcrumb: ent_score = min(100, ent_score + 5)
    if has_service: ent_score = min(100, ent_score + 5)
    if jsonld.get("has_faqpage"): ent_score = min(100, ent_score + 10)

    scores["entity_clarity"] = max(0, min(100, ent_score))

    # --- EXTRACTABILITY (from page audit) ---
    ex = page.get("extractability", {})
    scores["extractability"] = ex.get("extractability_score", 0)

    # --- CLASSIC SEO ---
    meta = page.get("metadata", {})
    head = page.get("headings", {})
    classic_score = 100

    if not meta.get("title"):
        classic_score -= 20
    elif not meta.get("title_ok"):
        classic_score -= 8

    if not meta.get("meta_description"):
        classic_score -= 12
    elif not meta.get("meta_description_ok"):
        classic_score -= 5

    if not meta.get("canonical"):
        classic_score -= 8

    if head.get("h1_count", 0) == 0:
        classic_score -= 15
    elif head.get("h1_count", 0) > 1:
        classic_score -= 8

    if head.get("h2_count", 0) < 2:
        classic_score -= 8

    if not meta.get("has_viewport"):
        classic_score -= 5

    if not meta.get("og_image"):
        classic_score -= 5

    # CWV from tech
    cwv = tech.get("cwv", {})
    if cwv and not cwv.get("skipped"):
        mobile = cwv.get("mobile", {})
        if mobile:
            perf = mobile.get("performance_score", 50)
            if perf < 50:
                classic_score -= 15
            elif perf < 70:
                classic_score -= 8

    # Sitemap and redirects
    if not tech.get("sitemap", {}).get("sitemaps_found"):
        classic_score -= 8

    if tech.get("redirects", {}).get("redirect_hops", 0) > 2:
        classic_score -= 5

    scores["classic_seo"] = max(0, min(100, classic_score))

    # --- TRUST & E-E-A-T ---
    eeat = page.get("eeat", {})
    trust_score = 100

    if not eeat.get("has_author"):
        trust_score -= 20
    if not eeat.get("has_impressum"):
        trust_score -= 25  # German legal requirement
    if not eeat.get("has_privacy"):
        trust_score -= 15
    if not eeat.get("has_date"):
        trust_score -= 10
    if not eeat.get("has_credentials"):
        trust_score -= 10
    if not eeat.get("has_citations"):
        trust_score -= 10
    if not eeat.get("has_social_proof"):
        trust_score -= 10

    scores["trust_eeat"] = max(0, min(100, trust_score))

    return scores


def overall_score(pillar_scores: dict) -> int:
    total = sum(
        pillar_scores.get(k, 0) * PILLARS[k]["weight"]
        for k in PILLARS
    )
    return round(total)


# ---------------------------------------------------------------------------
# Issue prioritisation (impact × effort)
# ---------------------------------------------------------------------------

EFFORT = {
    # check_keyword: effort label and hours estimate
    "JS-only": ("high", "1-3 days"),
    "noindex": ("low", "5 min"),
    "WAF blocking": ("medium", "30 min"),
    "robots.txt blocks": ("low", "5 min"),
    "Missing <title>": ("low", "10 min"),
    "Title length": ("low", "10 min"),
    "Missing meta description": ("low", "10 min"),
    "Meta description length": ("low", "10 min"),
    "No H1 tag": ("low", "10 min"),
    "Multiple H1": ("low", "10 min"),
    "No JSON-LD": ("medium", "1-2 hours"),
    "No Organization": ("medium", "30 min"),
    "JSON-LD parse": ("low", "15 min"),
    "No WebSite schema": ("low", "15 min"),
    "FAQPage": ("medium", "45 min"),
    "Low word count": ("high", "1-2 days"),
    "Low extractability": ("high", "1-2 days"),
    "Medium extractability": ("medium", "3-5 hours"),
    "No author": ("low", "15 min"),
    "No publication": ("low", "10 min"),
    "No Impressum": ("low", "20 min"),
    "No Datenschutz": ("low", "30 min"),
    "No canonical": ("low", "5 min"),
    "Fewer than 2 H2": ("low", "15 min"),
    "No HTTP → HTTPS": ("medium", "30 min"),
    "No HSTS": ("low", "5 min"),
    "No XML sitemap": ("medium", "1 hour"),
    "No compression": ("low", "15 min"),
    "No external citation": ("low", "30 min"),
    "missing alt text": ("low", "30 min"),
}

IMPACT_FROM_SEVERITY = {"critical": 4, "high": 3, "medium": 2, "low": 1}
EFFORT_LEVEL = {"low": 1, "medium": 2, "high": 3}


def rank_fixes(all_issues: list[dict]) -> list[dict]:
    """Score issues by impact/effort ratio and sort quick wins first."""
    ranked = []
    for issue in all_issues:
        impact = IMPACT_FROM_SEVERITY.get(issue["severity"], 1)

        # Find effort match
        effort_label = "medium"
        effort_time = "varies"
        for keyword, (eff_label, eff_time) in EFFORT.items():
            if keyword.lower() in issue["check"].lower():
                effort_label = eff_label
                effort_time = eff_time
                break

        effort_level = EFFORT_LEVEL.get(effort_label, 2)
        ratio = impact / effort_level

        ranked.append({
            **issue,
            "effort": effort_label,
            "time_estimate": effort_time,
            "priority_score": ratio,
        })

    return sorted(ranked, key=lambda x: -x["priority_score"])


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

HTML_STYLE = """
<style>
  :root {
    --bg: #ffffff; --text: #1a1a1a; --muted: #6b7280;
    --border: #e5e7eb; --accent: #0f172a; --accent2: #2563eb;
    --good: #16a34a; --warn: #d97706; --bad: #dc2626;
    --tag-bg: #f1f5f9;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); line-height: 1.6; font-size: 15px; }
  .page { max-width: 860px; margin: 0 auto; padding: 48px 24px; }
  header { border-bottom: 2px solid var(--accent); padding-bottom: 24px; margin-bottom: 40px; }
  .logo { font-size: 12px; letter-spacing: .15em; text-transform: uppercase; color: var(--muted); }
  h1 { font-size: 28px; font-weight: 700; margin: 8px 0 4px; }
  .subtitle { color: var(--muted); font-size: 14px; }
  .meta { display: flex; gap: 24px; margin-top: 12px; font-size: 13px; color: var(--muted); }
  .scorecard { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 32px 0; }
  .score-main { grid-column: 1/3; background: var(--accent); color: white;
                border-radius: 8px; padding: 28px 32px; display: flex; align-items: center; gap: 24px; }
  .score-num { font-size: 72px; font-weight: 800; line-height: 1; }
  .score-label { font-size: 14px; opacity: .7; margin-top: 4px; }
  .score-grade { font-size: 48px; font-weight: 700; margin-left: auto; opacity: .9; }
  .pillar { background: var(--tag-bg); border-radius: 8px; padding: 16px 20px; }
  .pillar-name { font-weight: 600; font-size: 13px; margin-bottom: 4px; }
  .pillar-desc { font-size: 12px; color: var(--muted); margin-bottom: 10px; }
  .bar-wrap { background: #e2e8f0; border-radius: 4px; height: 8px; }
  .bar { height: 8px; border-radius: 4px; }
  .bar-val { font-size: 12px; font-weight: 700; margin-top: 4px; }
  .green { background: var(--good); }
  .yellow { background: var(--warn); }
  .red { background: var(--bad); }
  section { margin: 40px 0; }
  h2 { font-size: 18px; font-weight: 700; margin-bottom: 16px;
       padding-bottom: 8px; border-bottom: 1px solid var(--border); }
  h3 { font-size: 15px; font-weight: 600; margin: 20px 0 8px; }
  .issues-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .issues-table th { text-align: left; padding: 8px 12px; background: var(--tag-bg);
                      font-weight: 600; border-bottom: 2px solid var(--border); }
  .issues-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
  .issues-table tr:hover td { background: var(--tag-bg); }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px;
           font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }
  .critical { background: #fef2f2; color: var(--bad); }
  .high { background: #fff7ed; color: #c2410c; }
  .medium { background: #fefce8; color: #a16207; }
  .low { background: #f0fdf4; color: var(--good); }
  .effort-low { color: var(--good); }
  .effort-medium { color: var(--warn); }
  .effort-high { color: var(--bad); }
  .fix-text { color: var(--muted); font-size: 12px; margin-top: 4px; }
  .quickwins { background: #f0fdf4; border: 1px solid #86efac; border-radius: 8px; padding: 20px 24px; }
  .checklist { list-style: none; }
  .checklist li { display: flex; align-items: flex-start; gap: 10px; padding: 6px 0;
                  border-bottom: 1px solid var(--border); font-size: 13px; }
  .checklist li:last-child { border-bottom: none; }
  .check-box { width: 16px; height: 16px; border: 2px solid #9ca3af; border-radius: 3px;
               flex-shrink: 0; margin-top: 2px; }
  .next-steps { background: var(--accent); color: white; border-radius: 8px; padding: 28px 32px; }
  .next-steps h2 { color: white; border-color: rgba(255,255,255,.2); }
  .next-steps li { margin: 8px 0; font-size: 14px; opacity: .9; }
  footer { margin-top: 56px; padding-top: 24px; border-top: 1px solid var(--border);
           font-size: 12px; color: var(--muted); }
  @media (max-width: 600px) {
    .scorecard { grid-template-columns: 1fr; }
    .score-main { grid-column: 1; flex-direction: column; gap: 8px; }
    .score-grade { margin-left: 0; }
  }
</style>
"""

def bar_color(score: int) -> str:
    if score >= 70: return "green"
    if score >= 45: return "yellow"
    return "red"


def build_html_report(
    page: dict,
    tech: dict,
    pillar_scores: dict,
    ranked_issues: list[dict],
    client_name: str,
    prepared_by: str = "Naga Codex",
    url: str = "",
    lang: str = "de",
) -> str:
    total = overall_score(pillar_scores)
    grade = letter_grade(total)
    grade_label = GRADE_LABELS[grade][lang]
    date_str = datetime.now().strftime("%d. %B %Y" if lang == "de" else "%B %d, %Y")

    # Localised labels
    L = {
        "de": {
            "title": "SEO & KI-Sichtbarkeits-Audit",
            "prepared_for": "Erstellt für",
            "prepared_by": "Erstellt von",
            "date": "Datum",
            "overall": "Gesamtwertung",
            "pillar_section": "Pillar-Scores",
            "issues_section": "Alle Befunde — nach Priorität sortiert",
            "quickwins": "⚡ Quick Wins — Sofort umsetzbar (< 30 Min. je Punkt)",
            "check": "Befund",
            "severity": "Schwere",
            "effort": "Aufwand",
            "fix": "Maßnahme",
            "nextsteps": "Empfohlene nächste Schritte",
            "steps": [
                "1. Quick Wins sofort umsetzen (kein Budget nötig)",
                "2. JSON-LD / Structured Data ergänzen → generate_entity_schema.py",
                "3. Inhalte für KI-Zitierbarkeit optimieren (FAQ, Statistiken, selbsttragende Absätze)",
                "4. WAF-Einstellungen prüfen: KI-Crawler freischalten",
                "5. Sichtbarkeit messen: build_prompt_set.py → Prompts in ChatGPT / Perplexity testen",
                "6. In 6-8 Wochen: Audit wiederholen und Verbesserung messen",
            ],
            "footer": f"Erstellt von {prepared_by} · Alle Angaben nach bestem Wissen zum Zeitpunkt der Erstellung · Kein Anspruch auf Vollständigkeit",
        },
        "en": {
            "title": "SEO & AI Visibility Audit",
            "prepared_for": "Prepared for",
            "prepared_by": "Prepared by",
            "date": "Date",
            "overall": "Overall Score",
            "pillar_section": "Pillar Scores",
            "issues_section": "All Findings — Sorted by Priority",
            "quickwins": "⚡ Quick Wins — Implementable in under 30 min each",
            "check": "Finding",
            "severity": "Severity",
            "effort": "Effort",
            "fix": "Action",
            "nextsteps": "Recommended Next Steps",
            "steps": [
                "1. Implement quick wins immediately (no budget required)",
                "2. Add JSON-LD / Structured Data → generate_entity_schema.py",
                "3. Optimise content for AI extractability (FAQ, statistics, self-contained paragraphs)",
                "4. Review WAF settings: allow AI crawler user-agents",
                "5. Measure visibility: build_prompt_set.py → test prompts in ChatGPT / Perplexity",
                "6. In 6-8 weeks: re-run audit and measure improvement",
            ],
            "footer": f"Prepared by {prepared_by} · All findings accurate at time of preparation · Not exhaustive",
        }
    }
    t = L[lang]

    # Pillar score rows
    pillar_html = ""
    for key, pillar in PILLARS.items():
        s = pillar_scores.get(key, 0)
        bc = bar_color(s)
        name = pillar[f"name_{lang}"]
        desc = pillar[f"desc_{lang}"]
        weight_pct = int(pillar["weight"] * 100)
        pillar_html += f"""
        <div class="pillar">
          <div class="pillar-name">{name} <span style="font-weight:400;color:#9ca3af">({weight_pct}%)</span></div>
          <div class="pillar-desc">{desc}</div>
          <div class="bar-wrap"><div class="bar {bc}" style="width:{s}%"></div></div>
          <div class="bar-val" style="color:{'var(--good)' if bc=='green' else 'var(--warn)' if bc=='yellow' else 'var(--bad)'}">{s}/100</div>
        </div>"""

    # Quick wins (effort=low, severity critical/high/medium)
    quick_wins = [i for i in ranked_issues if i.get("effort") == "low"][:8]
    qw_html = "<ul class='checklist'>" + "".join(
        f"<li><div class='check-box'></div><div><strong>{i['check']}</strong><div class='fix-text'>{i['fix'][:200]}</div></div></li>"
        for i in quick_wins
    ) + "</ul>"

    # Issues table
    issues_html = "<table class='issues-table'><thead><tr>"
    issues_html += f"<th>#</th><th>{t['check']}</th><th>{t['severity']}</th><th>{t['effort']}</th><th>{t['fix']}</th>"
    issues_html += "</tr></thead><tbody>"
    for i, issue in enumerate(ranked_issues, 1):
        sev = issue["severity"]
        eff = issue.get("effort", "medium")
        issues_html += f"""<tr>
          <td style="color:var(--muted)">{i}</td>
          <td><span class='badge {sev}'>{sev}</span> {issue['check']}</td>
          <td><span class='badge {sev}'>{sev.upper()}</span></td>
          <td class="effort-{eff}">{eff} ({issue.get('time_estimate','?')})</td>
          <td>{issue['fix'][:180]}</td>
        </tr>"""
    issues_html += "</tbody></table>"

    steps_html = "<ul style='margin-top:12px'>" + "".join(
        f"<li>{s}</li>" for s in t["steps"]
    ) + "</ul>"

    html = f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t['title']} – {client_name}</title>
{HTML_STYLE}
</head>
<body>
<div class="page">
  <header>
    <div class="logo">{prepared_by}</div>
    <h1>{t['title']}</h1>
    <div class="subtitle">{url}</div>
    <div class="meta">
      <span>{t['prepared_for']}: <strong>{client_name}</strong></span>
      <span>{t['prepared_by']}: <strong>{prepared_by}</strong></span>
      <span>{t['date']}: {date_str}</span>
    </div>
  </header>

  <div class="scorecard">
    <div class="score-main">
      <div>
        <div style="font-size:13px;opacity:.7;margin-bottom:4px">{t['overall']}</div>
        <div class="score-num">{total}</div>
        <div class="score-label">/ 100 — {grade_label}</div>
      </div>
      <div class="score-grade">{grade}</div>
    </div>
    {pillar_html}
  </div>

  <section>
    <h2>{t['quickwins']}</h2>
    <div class="quickwins">{qw_html}</div>
  </section>

  <section>
    <h2>{t['issues_section']}</h2>
    {issues_html}
  </section>

  <div class="next-steps">
    <h2>{t['nextsteps']}</h2>
    {steps_html}
  </div>

  <footer>{t['footer']}</footer>
</div>
</body>
</html>"""

    return html


def build_markdown_report(
    page: dict,
    tech: dict,
    pillar_scores: dict,
    ranked_issues: list[dict],
    client_name: str,
    prepared_by: str = "Naga Codex",
    url: str = "",
    lang: str = "de",
) -> str:
    total = overall_score(pillar_scores)
    grade = letter_grade(total)
    date_str = datetime.now().strftime("%d.%m.%Y")

    lines = [
        f"# SEO & KI-Sichtbarkeits-Audit — {client_name}" if lang == "de" else f"# SEO & AI Visibility Audit — {client_name}",
        f"",
        f"**URL:** {url}  ",
        f"**Gesamtscore / Overall:** {total}/100 ({grade})  " if lang == "de" else f"**Overall Score:** {total}/100 ({grade})  ",
        f"**Erstellt von / Prepared by:** {prepared_by}  ",
        f"**Datum / Date:** {date_str}",
        "",
        "---",
        "",
        "## Pillar Scores",
        "",
    ]

    for key, pillar in PILLARS.items():
        s = pillar_scores.get(key, 0)
        bar = "█" * (s // 10) + "░" * (10 - s // 10)
        name = pillar[f"name_{lang}"]
        lines.append(f"| {name} | {bar} | **{s}/100** |")

    lines += [
        "",
        "---",
        "",
        "## Befunde nach Priorität / Findings by Priority",
        "",
        "| # | Schwere | Befund | Aufwand | Maßnahme |",
        "|---|---------|--------|---------|----------|",
    ]

    for i, issue in enumerate(ranked_issues, 1):
        sev = issue["severity"].upper()
        check = issue["check"].replace("|", "/")[:60]
        fix = issue["fix"].replace("|", "/")[:100]
        eff = issue.get("effort", "?")
        est = issue.get("time_estimate", "")
        lines.append(f"| {i} | **{sev}** | {check} | {eff} ({est}) | {fix} |")

    lines += [
        "",
        "---",
        "",
        "## Quick Wins — Sofort umsetzbar / Immediately actionable",
        "",
    ]
    for issue in ranked_issues:
        if issue.get("effort") == "low":
            lines.append(f"- [ ] [{issue['severity'].upper()}] **{issue['check']}** — {issue['fix'][:150]}")

    lines += ["", "---", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Run-all helper
# ---------------------------------------------------------------------------

def run_audits(url: str, psi_key: Optional[str] = None) -> tuple[dict, dict]:
    """Run both audit scripts and return parsed JSON results."""
    page_file = tempfile.NamedTemporaryFile(suffix="_page.json", delete=False)
    tech_file = tempfile.NamedTemporaryFile(suffix="_tech.json", delete=False)
    page_file.close(); tech_file.close()

    script_dir = os.path.dirname(os.path.abspath(__file__))

    print(f"  Running audit_page.py ...", file=sys.stderr)
    subprocess.run([
        sys.executable, os.path.join(script_dir, "audit_page.py"),
        url, "--output", "json", "--save", page_file.name
    ], check=True)

    print(f"  Running audit_technical.py ...", file=sys.stderr)
    tech_args = [
        sys.executable, os.path.join(script_dir, "audit_technical.py"),
        url, "--output", "json", "--save", tech_file.name
    ]
    if psi_key:
        tech_args += ["--psi-key", psi_key]
    else:
        tech_args += ["--skip-cwv"]
    subprocess.run(tech_args, check=True)

    with open(page_file.name, encoding="utf-8") as f:
        page = json.load(f)
    with open(tech_file.name, encoding="utf-8") as f:
        tech = json.load(f)

    os.unlink(page_file.name)
    os.unlink(tech_file.name)

    return page, tech


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate aggregated SEO + AI visibility report"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--url", help="Run both audits fresh for this URL")
    group.add_argument("--page", metavar="FILE", help="Pre-run page audit JSON")

    parser.add_argument("--tech", metavar="FILE", help="Pre-run technical audit JSON")
    parser.add_argument("--client", default="Client", help="Client company name")
    parser.add_argument("--prepared-by", default="Naga Codex")
    parser.add_argument("--lang", choices=["de", "en", "both"], default="both",
                        help="Report language (de/en/both — 'both' generates two files)")
    parser.add_argument("--format", choices=["html", "md", "both"], default="html")
    parser.add_argument("--save", metavar="FILE", help="Output filename (without extension)")
    parser.add_argument("--psi-key", metavar="KEY", help="PageSpeed Insights API key")

    args = parser.parse_args()

    # Load or run audits
    if args.url:
        page, tech = run_audits(args.url, args.psi_key)
        url = args.url
    elif args.page:
        with open(args.page, encoding="utf-8") as f:
            page = json.load(f)
        if args.tech:
            with open(args.tech, encoding="utf-8") as f:
                tech = json.load(f)
        else:
            tech = {}
        url = page.get("url", args.page)
    else:
        parser.error("Provide either --url or --page (and optionally --tech)")

    # Compute
    pillar_scores = compute_pillar_scores(page, tech)
    all_issues = page.get("issues", []) + tech.get("issues", [])
    ranked = rank_fixes(all_issues)

    # Output
    base_name = args.save or "seo_report"
    langs = ["de", "en"] if args.lang == "both" else [args.lang]
    formats = ["html", "md"] if args.format == "both" else [args.format]

    for lang in langs:
        for fmt in formats:
            suffix = f"_{lang}" if args.lang == "both" else ""
            if fmt == "html":
                content = build_html_report(page, tech, pillar_scores, ranked, args.client, args.prepared_by, url, lang)
                fname = f"{base_name}{suffix}.html"
            else:
                content = build_markdown_report(page, tech, pillar_scores, ranked, args.client, args.prepared_by, url, lang)
                fname = f"{base_name}{suffix}.md"

            with open(fname, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"Saved: {fname}", file=sys.stderr)

    total = overall_score(pillar_scores)
    grade = letter_grade(total)

    # Print summary to stderr (progress/scores)
    print(f"\nOverall score: {total}/100 ({grade})", file=sys.stderr)
    for key, pillar in PILLARS.items():
        s = pillar_scores.get(key, 0)
        bar = "█" * (s // 10) + "░" * (10 - s // 10)
        print(f"  {pillar['name_en']:<28} {bar} {s}/100", file=sys.stderr)
    n_crit = sum(1 for i in ranked if i['severity'] == 'critical')
    n_high = sum(1 for i in ranked if i['severity'] == 'high')
    print(f"\nTotal issues: {len(ranked)}  ({n_crit} critical, {n_high} high)\n", file=sys.stderr)

    # Print ranked fix list to STDOUT so it's always visible in terminal
    lines = [
        f"{'─'*70}",
        f"RANKED FIX LIST — {url}",
        f"Score: {total}/100 ({grade})  |  Issues: {len(ranked)} total",
        f"{'─'*70}",
        f"{'#':<3} {'SEV':<9} {'EFFORT':<20} WHAT TO FIX",
        f"{'─'*70}",
    ]
    for i, issue in enumerate(ranked, 1):
        sev = issue['severity'].upper()
        eff = f"{issue.get('effort','?')} ({issue.get('time_estimate','?')})"
        lines.append(f"{i:<3} {sev:<9} {eff:<20} {issue['check'][:50]}")
        lines.append(f"    → {issue['fix'][:80]}")
    lines += [
        f"{'─'*70}",
        "",
        "QUICK WINS (effort = low, do these first):",
    ]
    quick = [i for i in ranked if i.get('effort') == 'low']
    for i, issue in enumerate(quick, 1):
        lines.append(f"  {i}. [{issue['severity'].upper()}] {issue['check']} — {issue['fix'][:90]}")
    lines += [
        "",
        "NEXT STEPS:",
        "  1. Implement quick wins (no budget needed)",
        "  2. Add JSON-LD / entity schema → python3 generate_entity_schema.py --interactive",
        "  3. Fix content extractability (FAQ, statistics, self-contained paragraphs)",
        "  4. Check WAF settings — allow AI crawler user-agents",
        "  5. Measure AI citation → python3 build_prompt_set.py (run prompts in ChatGPT/Perplexity)",
        "  6. Re-audit in 6-8 weeks",
        f"{'─'*70}",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
