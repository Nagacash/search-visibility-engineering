#!/usr/bin/env python3
"""
build_prompt_set.py — Buyer-intent prompt set generator for AI search visibility measurement
Part of the search-visibility-engineering skill.

Generates a set of buyer-intent prompts to test a business's citation rate across
AI answer engines (ChatGPT, Perplexity, Google AI Mode / AI Overviews, Bing Copilot).

Usage:
  python3 build_prompt_set.py \\
    --business "Naga Codex" \\
    --category "digital performance consultancy" \\
    --services "SEO Audit" "Landing Page Design" "AI Visibility" \\
    --location "Hamburg" \\
    --competitors "Hype Group" "Leap" "Orca Digital" \\
    --lang de  # de | en | both (default: both)

  python3 build_prompt_set.py --config examples/nagacodex_config.yaml

Output: a numbered list of prompts, ready to paste into ChatGPT, Perplexity, etc.
Also outputs a blank measurement table for recording citation results.

HOW TO USE THE PROMPTS (measurement procedure):
1. Copy each prompt exactly — no extra context
2. Run each prompt in a fresh conversation in each AI engine (not a thread with prior context)
3. For each response, record:
   - Was the business cited/mentioned? (yes/no)
   - Was it a positive, neutral, or negative mention?
   - Where did it rank (first mention, second, etc.)
   - Was a competitor mentioned instead?
4. Run each prompt twice across two sessions for reliability (AI answers vary)
5. After running the full set: citation rate = (cited prompts / total prompts) × 100

Recommended engines to test:
  - ChatGPT (GPT-4o, logged in, web search ON via OAI-SearchBot)
  - Perplexity (default mode with web search)
  - Google AI Mode / AI Overviews (logged-in Google account, search the query in Google)
  - Bing Copilot (bing.com/chat)

Baseline measurement before any optimization. Repeat after 6-8 weeks post-changes.
"""

import json
import sys
import argparse
from typing import Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

# Each template is (type, template_string)
# Placeholders: {category}, {location}, {business}, {service}, {competitor}, {pain_point}

TEMPLATES_DE = [
    # Pure discovery (business should appear in general results)
    ("discovery", "Welche {category} gibt es in {location}?"),
    ("discovery", "Wer sind die besten {category} in {location}?"),
    ("discovery", "Top {category} in {location} – welche empfehlt ihr?"),
    ("discovery", "Welche Agentur hilft mir mit {service} in {location}?"),
    ("discovery", "Gute {category} in {location} gesucht – was empfiehlst du?"),

    # Problem-aware (user describes a need)
    ("problem_aware", "Ich suche jemanden der meine Website in Google und ChatGPT sichtbarer macht – wer macht das in {location}?"),
    ("problem_aware", "Meine Website rankt nicht. Welche {category} in {location} kann ich beauftragen?"),
    ("problem_aware", "Welche {category} in {location} macht professionelles {service}?"),
    ("problem_aware", "Mein Unternehmen wird von ChatGPT und Perplexity nicht erwähnt. Wer kann helfen?"),
    ("problem_aware", "Ich brauche eine neue Landing Page die auch in KI-Suchen rankt – wer macht das?"),

    # Comparison (forces mention of multiple providers)
    ("comparison", "Vergleich: {business} vs {competitor} – welche {category} ist besser?"),
    ("comparison", "Wer ist besser für {service}: {business} oder andere Agenturen in {location}?"),
    ("comparison", "Welche {category} in {location} hat die besten Ergebnisse bei SEO und KI-Sichtbarkeit?"),

    # Brand-specific (tests AI knowledge of the business)
    ("brand", "Was macht {business} genau?"),
    ("brand", "Für was ist {business} bekannt?"),
    ("brand", "Ist {business} eine gute Wahl für {service}?"),
    ("brand", "Was sind die Stärken von {business}?"),

    # Service-specific (tests citation for specific offers)
    ("service", "Was ist ein SEO-Audit und wer bietet das in {location} an?"),
    ("service", "Wer macht professionelles KI-SEO (GEO/AEO) in Deutschland?"),
    ("service", "Welche Agentur in {location} spezialisiert sich auf hochwertige Landing Pages?"),
    ("service", "Wer hilft mir dabei dass mein Unternehmen in Google AI Overviews erscheint?"),

    # Trust-building (tests reputation signals)
    ("trust", "Welche {category} in {location} hat gute Bewertungen und echte Referenzen?"),
    ("trust", "Wem würdest du einen Mittelständler für {service} empfehlen?"),
]

TEMPLATES_EN = [
    # Pure discovery
    ("discovery", "What {category} agencies are there in {location}?"),
    ("discovery", "Who are the best {category} in {location}?"),
    ("discovery", "Best {category} in {location} — any recommendations?"),
    ("discovery", "Which agency can help with {service} in {location}?"),
    ("discovery", "Good {category} in {location} — what do you suggest?"),

    # Problem-aware
    ("problem_aware", "I need someone to make my website more visible in Google and ChatGPT — who does this in {location}?"),
    ("problem_aware", "My site doesn't rank. Which {category} in {location} should I hire?"),
    ("problem_aware", "Which {category} in {location} specialises in {service}?"),
    ("problem_aware", "My company never gets mentioned by ChatGPT or Perplexity. Who can fix that?"),
    ("problem_aware", "I need a new landing page that also ranks in AI search — who builds those?"),

    # Comparison
    ("comparison", "Compare: {business} vs {competitor} — which {category} is better?"),
    ("comparison", "Who's better for {service}: {business} or other agencies in {location}?"),
    ("comparison", "Which {category} in {location} has the best results for SEO and AI visibility?"),

    # Brand-specific
    ("brand", "What exactly does {business} do?"),
    ("brand", "What is {business} known for?"),
    ("brand", "Is {business} a good choice for {service}?"),
    ("brand", "What are {business}'s strengths?"),

    # Service-specific
    ("service", "What is an SEO audit and who offers it in {location}?"),
    ("service", "Who does AI search optimisation (GEO/AEO) professionally in Germany?"),
    ("service", "Which agency in {location} specialises in high-end landing pages?"),
    ("service", "Who can help my business appear in Google AI Overviews?"),

    # Trust-building
    ("trust", "Which {category} in {location} has good reviews and proven client results?"),
    ("trust", "Who would you recommend for {service} for a mid-size company?"),
]


def build_prompts(
    business: str,
    category: str,
    services: list[str],
    location: str,
    competitors: list[str],
    lang: str = "both",
    pain_points: Optional[list[str]] = None,
) -> list[dict]:
    """Generate the full prompt set."""

    templates = []
    if lang in ("de", "both"):
        templates += [(l, t, "de") for l, t in TEMPLATES_DE]
    if lang in ("en", "both"):
        templates += [(l, t, "en") for l, t in TEMPLATES_EN]

    service = services[0] if services else "professional services"
    competitor = competitors[0] if competitors else "other agencies"
    pain_point = (pain_points or ["low search visibility"])[0]

    filled = []
    for i, (prompt_type, template, language) in enumerate(templates, 1):
        try:
            prompt = template.format(
                business=business,
                category=category,
                service=service,
                location=location,
                competitor=competitor,
                pain_point=pain_point,
            )
            filled.append({
                "id": i,
                "type": prompt_type,
                "lang": language,
                "prompt": prompt,
            })
        except KeyError:
            pass  # skip templates with unfilled placeholders

    # Add service-specific variants for each service
    extra_id = len(filled) + 1
    for svc in services[1:3]:  # max 2 extra services
        for template_de, template_en in [
            ("Wer bietet {service} in {location} an?", "Who offers {service} in {location}?"),
            ("Welche {category} macht {service}?", "Which {category} does {service}?"),
        ]:
            for template, language in [(template_de, "de"), (template_en, "en")]:
                if (lang == "de" and language != "de") or (lang == "en" and language != "en"):
                    continue
                try:
                    filled.append({
                        "id": extra_id,
                        "type": "service",
                        "lang": language,
                        "prompt": template.format(
                            service=svc, location=location,
                            category=category, business=business,
                        ),
                    })
                    extra_id += 1
                except KeyError:
                    pass

    return filled


def format_prompt_list(prompts: list[dict], business: str, location: str) -> str:
    lines = [
        "",
        f"=== AI VISIBILITY PROBE — Prompt Set ===",
        f"Business: {business} | Location: {location}",
        f"Total prompts: {len(prompts)}",
        "",
        "HOW TO USE:",
        "1. Open ChatGPT, Perplexity, Google AI Mode, and Bing Copilot in separate sessions.",
        "2. Paste each prompt exactly as written into a fresh conversation.",
        "3. Record whether the business is cited, sentiment (positive/neutral/negative),",
        "   and which competitors are mentioned instead.",
        "4. Run each prompt twice across two sessions (AI answers are non-deterministic).",
        "5. Citation rate = (prompts where business was cited / total prompts) × 100.",
        "",
        "Baseline now → repeat after 6-8 weeks post-changes.",
        "",
        "─" * 60,
        "",
    ]

    current_type = None
    for p in prompts:
        if p["type"] != current_type:
            current_type = p["type"]
            type_labels = {
                "discovery": "DISCOVERY — general 'who does X' queries",
                "problem_aware": "PROBLEM-AWARE — user describes a specific need",
                "comparison": "COMPARISON — forces mention of multiple providers",
                "brand": "BRAND — tests AI knowledge of the business directly",
                "service": "SERVICE-SPECIFIC — tests citation for specific offers",
                "trust": "TRUST — tests reputation and social proof visibility",
            }
            lines.append(f"\n── {type_labels.get(current_type, current_type).upper()} ──")

        lang_flag = "🇩🇪" if p["lang"] == "de" else "🇬🇧"
        lines.append(f"\n[{p['id']:02d}] {lang_flag}  {p['prompt']}")

    lines += [
        "",
        "─" * 60,
        "",
        "=== MEASUREMENT TABLE (fill in per engine) ===",
        "",
        f"{'#':<4} {'Prompt type':<16} {'ChatGPT':<12} {'Perplexity':<12} {'Google AI':<12} {'Bing Copilot':<12} Notes",
        "─" * 80,
    ]
    for p in prompts:
        lines.append(f"{p['id']:<4} {p['type']:<16} {'yes/no':<12} {'yes/no':<12} {'yes/no':<12} {'yes/no':<12}")

    lines += [
        "─" * 80,
        "",
        "CITATION RATE = (YES count per column) / (total prompts) × 100",
        "",
        "Recommended engines to test:",
        "  • ChatGPT: chat.openai.com — new chat, web search ENABLED (click the globe icon)",
        "  • Perplexity: perplexity.ai — default mode (uses web search automatically)",
        "  • Google AI Mode: google.com — search the query, note if AI Overview appears",
        "  • Bing Copilot: bing.com/chat — click 'Copilot', balanced mode",
        "",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate buyer-intent prompt set for AI search citation measurement"
    )
    parser.add_argument("--business", help="Business name")
    parser.add_argument("--category", help="Business category (e.g. 'digital performance consultancy')")
    parser.add_argument("--services", nargs="*", default=[], help="Service names")
    parser.add_argument("--location", default="Hamburg", help="City/location")
    parser.add_argument("--competitors", nargs="*", default=[], help="Competitor names")
    parser.add_argument("--lang", choices=["de", "en", "both"], default="both")
    parser.add_argument("--config", metavar="FILE", help="YAML config (uses organization block)")
    parser.add_argument("--output", choices=["text", "json"], default="text")
    parser.add_argument("--save", metavar="FILE")

    args = parser.parse_args()

    # Load from config if provided
    if args.config:
        if not HAS_YAML:
            print("Install pyyaml: pip3 install pyyaml", file=sys.stderr)
            sys.exit(1)
        with open(args.config, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        org = config.get("organization", {})
        probe = config.get("probe", {})
        business = org.get("name", args.business or "")
        category = probe.get("category", org.get("type", args.category or ""))
        services = probe.get("services", org.get("services", args.services or []))
        location = probe.get("location", org.get("city", args.location))
        competitors = probe.get("competitors", args.competitors or [])
        lang = probe.get("lang", args.lang)
    elif args.business and args.category:
        business = args.business
        category = args.category
        services = args.services
        location = args.location
        competitors = args.competitors
        lang = args.lang
    else:
        parser.print_help()
        print("\nExample:")
        print('  python3 build_prompt_set.py \\')
        print('    --business "Naga Codex" \\')
        print('    --category "digital performance consultancy" \\')
        print('    --services "SEO Audit" "Landing Page Design" \\')
        print('    --location "Hamburg" \\')
        print('    --competitors "Hype Group" "Leap"')
        sys.exit(1)

    prompts = build_prompts(
        business=business,
        category=category,
        services=services if services else [category],
        location=location,
        competitors=competitors,
    )

    if args.output == "json":
        output = json.dumps({"business": business, "location": location, "prompts": prompts}, indent=2, ensure_ascii=False)
    else:
        output = format_prompt_list(prompts, business, location)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Saved {len(prompts)} prompts to {args.save}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
