# SKILL: search-visibility-engineering

**Type:** Python CLI toolkit  
**Runtime:** Python 3.9+  
**Dependencies:** `requests beautifulsoup4 lxml pyyaml`  
**Author:** Maurice Holda / Naga Codex — https://nagacodex.cloud

---

## What this skill does

Audits any website for classic search ranking signals (Google, Bing, Yahoo) **and** AI answer-engine citation visibility (ChatGPT via OAI-SearchBot, Perplexity, Google AI Overviews, Bing Copilot). Generates JSON-LD entity schemas, buyer-intent AI probe prompts, and bilingual DE/EN client reports.

---

## Install

```bash
pip3 install requests beautifulsoup4 lxml pyyaml
```

No API keys required for core functionality. Optional: Google PageSpeed Insights API key for Core Web Vitals.

---

## Scripts

### `audit_page.py`

On-page SEO + AI extractability audit. Fetches the URL **without JavaScript** (mirrors what AI crawlers see).

```bash
# Text report (default)
python3 audit_page.py https://example.com

# JSON output (pipe into score_report.py)
python3 audit_page.py https://example.com --output json --save page.json
```

**Args:**
| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `url` | positional | required | URL to audit |
| `--output` | `json\|text` | `text` | Output format |
| `--save` | filename | stdout | Save output to file |

**Returns:** Score 0–100, issues list sorted by severity (critical → high → medium → low), JS-shell detection, JSON-LD parse results, extractability score, E-E-A-T signals.

---

### `audit_technical.py`

Technical SEO + AI crawler access audit. Includes live WAF probe.

```bash
# Text report
python3 audit_technical.py nagacodex.cloud

# With Core Web Vitals
python3 audit_technical.py nagacodex.cloud --psi-key YOUR_API_KEY

# JSON output
python3 audit_technical.py nagacodex.cloud --output json --save tech.json

# Skip CWV (faster)
python3 audit_technical.py nagacodex.cloud --skip-cwv
```

**Args:**
| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `url` | positional | required | Domain or full URL |
| `--output` | `json\|text` | `text` | Output format |
| `--save` | filename | stdout | Save output to file |
| `--psi-key` | string | None | PageSpeed Insights API key |
| `--skip-cwv` | flag | False | Skip PSI call |

**Returns:** robots.txt rules per crawler, WAF probe results per AI user-agent, sitemap status, redirect chain, HTTPS/HSTS, compression, IndexNow, llms.txt presence.

**Crawlers checked:** Googlebot, Bingbot, GPTBot, OAI-SearchBot, ClaudeBot, anthropic-ai, PerplexityBot, Google-Extended, Applebot-Extended, Meta-ExternalAgent.

---

### `generate_entity_schema.py`

JSON-LD entity schema generator. Outputs ready-to-paste `<script type="application/ld+json">` block.

```bash
# Interactive (guided prompts)
python3 generate_entity_schema.py --interactive

# From YAML config
python3 generate_entity_schema.py --config examples/nagacodex_config.yaml

# One-liner
python3 generate_entity_schema.py \
  --name "Naga Codex" \
  --type ProfessionalService \
  --url https://nagacodex.cloud \
  --description "Digital performance consultancy in Hamburg." \
  --city Hamburg --country DE

# Raw JSON (no script tag wrapper)
python3 generate_entity_schema.py --config examples/nagacodex_config.yaml --output json
```

**Args:**
| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--interactive` | flag | — | Guided interactive mode |
| `--config` | filename | — | YAML config file |
| `--name` | string | — | Business name (one-liner mode) |
| `--type` | string | `ProfessionalService` | Schema.org type |
| `--url` | string | — | Website URL |
| `--description` | string | — | One-sentence description |
| `--city` | string | — | City |
| `--country` | string | `DE` | ISO country code |
| `--output` | `script\|json` | `script` | Output format |
| `--save` | filename | stdout | Save to file |

**Generates:** Organization/LocalBusiness, WebSite (+ SearchAction), WebPage, FAQPage, BreadcrumbList, Service, Person.

---

### `build_prompt_set.py`

Generates buyer-intent prompts (DE + EN) for measuring AI citation rate.

```bash
python3 build_prompt_set.py \
  --business "Naga Codex" \
  --category "digital performance consultancy" \
  --services "SEO Audit" "Landing Page Design" \
  --location "Hamburg" \
  --competitors "Hype Group" "Leap" \
  --lang both \
  --save prompts.txt

# From config
python3 build_prompt_set.py --config examples/nagacodex_config.yaml --save prompts.txt
```

**Args:**
| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--business` | string | required | Business name |
| `--category` | string | required | Business category |
| `--services` | list | `[]` | Service names |
| `--location` | string | `Hamburg` | City/region |
| `--competitors` | list | `[]` | Competitor names |
| `--lang` | `de\|en\|both` | `both` | Prompt language |
| `--config` | filename | — | YAML config |
| `--output` | `text\|json` | `text` | Output format |
| `--save` | filename | stdout | Save to file |

**Returns:** 40–50 prompts across 6 types: discovery, problem-aware, comparison, brand, service, trust. Includes a blank measurement table.

**Engines to test:** ChatGPT (web search ON), Perplexity, Google AI Mode, Bing Copilot.

---

### `score_report.py`

Aggregated scoring + bilingual DE/EN HTML report. Can run both audits automatically.

```bash
# Run everything in one command
python3 score_report.py \
  --url https://example.de \
  --client "Client GmbH" \
  --lang both \
  --save report

# From pre-run audit JSON files
python3 score_report.py \
  --page page.json \
  --tech tech.json \
  --client "Client GmbH" \
  --lang both \
  --save report

# With Core Web Vitals
python3 score_report.py --url https://example.de --client "Client" --psi-key YOUR_KEY
```

**Args:**
| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `--url` | string | — | Run audits for this URL (mutually exclusive with `--page`) |
| `--page` | filename | — | Pre-run page audit JSON |
| `--tech` | filename | — | Pre-run technical audit JSON |
| `--client` | string | `Client` | Client company name |
| `--prepared-by` | string | `Naga Codex` | Report author |
| `--lang` | `de\|en\|both` | `both` | Report language |
| `--format` | `html\|md\|both` | `html` | Output format |
| `--save` | filename | `seo_report` | Base filename (extension added automatically) |
| `--psi-key` | string | None | PageSpeed Insights API key |

**Returns:** `<basename>_de.html` + `<basename>_en.html` — professional client-ready reports with pillar scores, prioritised fix list, and quick-win checklist.

---

## Config file format

`examples/nagacodex_config.yaml` drives both `generate_entity_schema.py` and `build_prompt_set.py`.

```yaml
organization:
  type: ProfessionalService   # Organization | LocalBusiness | Consulting | etc.
  name: "Business Name"
  url: "https://domain.com"
  description: "One sentence."
  city: Hamburg
  country: DE
  email: "contact@..."
  same_as:                    # Critical for AI entity disambiguation
    - "https://linkedin.com/in/..."
    - "https://wikipedia.org/wiki/..."
  services: ["Service 1", "Service 2"]
  areas_served: ["Hamburg", "Deutschland"]

webpage:
  url: "https://domain.com"
  name: "Page title"
  description: "Meta description"
  page_type: WebPage
  author_name: "Full Name"
  author_url: "https://linkedin.com/..."

persons:
  - name: "Full Name"
    url: "https://..."
    job_title: "Title"
    same_as: ["https://linkedin.com/..."]

faq:
  - question: "Question?"
    answer: "Answer."

probe:                        # used by build_prompt_set.py
  category: "business category"
  services: ["Service 1"]
  location: Hamburg
  lang: both                  # de | en | both
  competitors: ["Competitor A", "Competitor B"]
```

---

## Five pillars (weighted scoring)

| Pillar | Weight | What it measures |
|--------|--------|-----------------|
| Retrievability | 25% | Can all crawlers access and read the content? No JS shell, no WAF block. |
| Entity Clarity | 20% | Does the AI know who the business is? JSON-LD, sameAs, structured data. |
| Content Extractability | 25% | Can AI systems quote standalone passages as answers? |
| Classic SEO | 20% | Google/Bing/Yahoo: title, meta, H1, sitemap, Core Web Vitals. |
| Trust & E-E-A-T | 10% | Author, date, Impressum, credentials, external citations. |

---

## Evidence grading

Every lever in the code is tagged:

- **CONFIRMED** — Google Search Central, Bing Webmaster Guidelines, official crawler docs, schema.org
- **MEASURED** — Ahrefs 137K-domain study (June 2026), Princeton GEO paper, third-party studies
- **CLAIMED** — Vendor correlation studies (directionally useful, not definitive)

**On llms.txt:** Ahrefs (June 2026, 137K sites): 97% of llms.txt files receive zero AI crawler traffic. Google has a "mythbusting" section in their generative-AI guidance stating it is not a ranking or AI Overviews factor. The check is included but marked low-priority.

**On Google-Extended:** Blocking `Google-Extended` in robots.txt prevents Gemini *training data* access only. It does **not** affect Google Search rankings or AI Overviews. AI Overviews use the standard Googlebot index. This is one of the most common client misconceptions.

---

## Quick engagement workflow

```bash
# 1. Install
pip3 install requests beautifulsoup4 lxml pyyaml

# 2. Full audit + bilingual report
python3 score_report.py --url https://client.de --client "Client GmbH" --lang both --save client_report

# 3. Generate entity schema
python3 generate_entity_schema.py --interactive > schema_tag.html

# 4. Build AI probe prompt set
python3 build_prompt_set.py --config examples/nagacodex_config.yaml --save prompts.txt

# 5. Run prompts in ChatGPT, Perplexity, Google AI Mode, Bing Copilot
# Record citations in the measurement table at the bottom of prompts.txt

# 6. Repeat audit after 6-8 weeks to measure improvement
```

---

## GitHub
https://github.com/Nagacash/search-visibility-engineering

## Part of the Naga Codex tool stack
Built by Maurice Holda · [nagacodex.cloud](https://nagacodex.cloud) · [linkedin.com/in/maurice-holda](https://linkedin.com/in/maurice-holda)
