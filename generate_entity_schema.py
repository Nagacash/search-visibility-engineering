#!/usr/bin/env python3
"""
generate_entity_schema.py — JSON-LD entity schema generator
Part of the search-visibility-engineering skill.

Generates validated JSON-LD structured data that:
  - Establishes entity identity for AI systems (entity disambiguation)
  - Enables Google rich results (FAQPage, BreadcrumbList, WebSite SearchAction)
  - Signals to Bing Webmaster Tools and Copilot what the brand/business is
  - Passes https://validator.schema.org/ and Google Rich Results Test

Usage:
  # Interactive mode (guided prompts):
  python3 generate_entity_schema.py --interactive

  # From YAML config file:
  python3 generate_entity_schema.py --config examples/nagacodex_config.yaml

  # Minimal one-liner (Organization only):
  python3 generate_entity_schema.py \\
    --name "Naga Codex" \\
    --type ProfessionalService \\
    --url https://nagacodex.cloud \\
    --description "Digital performance consultancy in Hamburg specialising in AI-ready websites." \\
    --city Hamburg --country DE

Outputs ready-to-paste JSON-LD blocks as a <script> tag or raw JSON.
"""

import sys
import json
import argparse
from typing import Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ---------------------------------------------------------------------------
# Schema generators
# ---------------------------------------------------------------------------

def org_schema(
    name: str,
    url: str,
    description: str,
    org_type: str = "Organization",
    logo_url: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    city: Optional[str] = None,
    region: Optional[str] = None,
    country: str = "DE",
    postal_code: Optional[str] = None,
    street_address: Optional[str] = None,
    founding_year: Optional[int] = None,
    same_as: Optional[list] = None,
    services: Optional[list] = None,
    areas_served: Optional[list] = None,
    price_range: Optional[str] = None,
    opening_hours: Optional[list] = None,
) -> dict:
    """
    Generate Organization or LocalBusiness schema.
    LocalBusiness types: LocalBusiness, ProfessionalService, Consulting, TechCompany, etc.
    """

    schema = {
        "@type": org_type,
        "name": name,
        "url": url,
        "description": description,
    }

    if logo_url:
        schema["logo"] = {"@type": "ImageObject", "url": logo_url}
    if email:
        schema["email"] = email
    if phone:
        schema["telephone"] = phone
    if founding_year:
        schema["foundingDate"] = str(founding_year)
    if same_as:
        schema["sameAs"] = same_as  # Wikipedia, LinkedIn, Crunchbase, Wikidata, etc.
    if services:
        schema["hasOfferCatalog"] = {
            "@type": "OfferCatalog",
            "name": f"{name} Services",
            "itemListElement": [
                {"@type": "Offer", "itemOffered": {"@type": "Service", "name": s}}
                for s in services
            ]
        }
    if areas_served:
        schema["areaServed"] = [{"@type": "Place", "name": a} for a in areas_served]
    if price_range:
        schema["priceRange"] = price_range

    # Address (required for LocalBusiness types)
    if city or street_address:
        address = {"@type": "PostalAddress", "addressCountry": country}
        if street_address:
            address["streetAddress"] = street_address
        if city:
            address["addressLocality"] = city
        if region:
            address["addressRegion"] = region
        if postal_code:
            address["postalCode"] = postal_code
        schema["address"] = address

    # Opening hours (LocalBusiness)
    if opening_hours:
        schema["openingHoursSpecification"] = opening_hours

    return schema


def website_schema(
    name: str,
    url: str,
    search_action: bool = True,
    search_url_template: Optional[str] = None,
) -> dict:
    """
    WebSite schema with optional SearchAction (enables Google Sitelinks Search Box).
    """
    schema = {
        "@type": "WebSite",
        "name": name,
        "url": url,
    }
    if search_action:
        template = search_url_template or f"{url.rstrip('/')}/?s={{search_term_string}}"
        schema["potentialAction"] = {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": template,
            },
            "query-input": "required name=search_term_string",
        }
    return schema


def webpage_schema(
    url: str,
    name: str,
    description: str,
    breadcrumb: Optional[list] = None,
    date_published: Optional[str] = None,
    date_modified: Optional[str] = None,
    author_name: Optional[str] = None,
    author_url: Optional[str] = None,
    page_type: str = "WebPage",
) -> dict:
    """Generate WebPage schema. page_type can be WebPage, AboutPage, ContactPage, ServicePage."""
    schema = {
        "@type": page_type,
        "url": url,
        "name": name,
        "description": description,
    }
    if date_published:
        schema["datePublished"] = date_published
    if date_modified:
        schema["dateModified"] = date_modified
    if author_name:
        author = {"@type": "Person", "name": author_name}
        if author_url:
            author["url"] = author_url
        schema["author"] = author
    if breadcrumb:
        schema["breadcrumb"] = breadcrumb_schema(breadcrumb)
    return schema


def breadcrumb_schema(items: list) -> dict:
    """
    items: list of {"name": str, "url": str} dicts.
    """
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": item["name"],
                "item": item["url"],
            }
            for i, item in enumerate(items)
        ]
    }


def faq_schema(qa_pairs: list) -> dict:
    """
    qa_pairs: list of {"question": str, "answer": str} dicts.
    FAQPage JSON-LD enables Google rich results and makes Q&A directly extractable by AI.
    """
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q["question"],
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": q["answer"],
                }
            }
            for q in qa_pairs
        ]
    }


def service_schema(
    name: str,
    description: str,
    provider_name: str,
    provider_url: str,
    service_type: Optional[str] = None,
    area_served: Optional[list] = None,
    price_range: Optional[str] = None,
    url: Optional[str] = None,
) -> dict:
    schema = {
        "@type": "Service",
        "name": name,
        "description": description,
        "provider": {"@type": "Organization", "name": provider_name, "url": provider_url},
    }
    if service_type:
        schema["serviceType"] = service_type
    if area_served:
        schema["areaServed"] = [{"@type": "Place", "name": a} for a in area_served]
    if price_range:
        schema["offers"] = {"@type": "Offer", "priceRange": price_range}
    if url:
        schema["url"] = url
    return schema


def person_schema(
    name: str,
    url: str,
    job_title: Optional[str] = None,
    works_for_name: Optional[str] = None,
    works_for_url: Optional[str] = None,
    same_as: Optional[list] = None,
    email: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    schema = {"@type": "Person", "name": name, "url": url}
    if job_title:
        schema["jobTitle"] = job_title
    if works_for_name:
        schema["worksFor"] = {"@type": "Organization", "name": works_for_name, "url": works_for_url}
    if same_as:
        schema["sameAs"] = same_as
    if email:
        schema["email"] = email
    if description:
        schema["description"] = description
    return schema


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_graph(schemas: list, context: str = "https://schema.org") -> dict:
    """Wrap multiple schemas in @context + @graph."""
    return {
        "@context": context,
        "@graph": schemas,
    }


def to_script_tag(schema_dict: dict) -> str:
    json_str = json.dumps(schema_dict, indent=2, ensure_ascii=False)
    return f'<script type="application/ld+json">\n{json_str}\n</script>'


# ---------------------------------------------------------------------------
# Config-driven builder
# ---------------------------------------------------------------------------

def build_from_config(config: dict) -> dict:
    schemas = []

    # Organization / LocalBusiness
    org = config.get("organization", {})
    if org:
        schemas.append(org_schema(
            name=org["name"],
            url=org["url"],
            description=org["description"],
            org_type=org.get("type", "Organization"),
            logo_url=org.get("logo_url"),
            email=org.get("email"),
            phone=org.get("phone"),
            city=org.get("city"),
            region=org.get("region"),
            country=org.get("country", "DE"),
            postal_code=org.get("postal_code"),
            street_address=org.get("street_address"),
            founding_year=org.get("founding_year"),
            same_as=org.get("same_as", []),
            services=org.get("services", []),
            areas_served=org.get("areas_served", []),
            price_range=org.get("price_range"),
            opening_hours=org.get("opening_hours"),
        ))

    # WebSite
    website = config.get("website", {})
    if website or org:
        name = (website or org).get("name", org.get("name", ""))
        url = (website or org).get("url", org.get("url", ""))
        schemas.append(website_schema(
            name=name,
            url=url,
            search_action=website.get("search_action", True),
            search_url_template=website.get("search_url_template"),
        ))

    # WebPage
    webpage = config.get("webpage", {})
    if webpage:
        schemas.append(webpage_schema(
            url=webpage["url"],
            name=webpage["name"],
            description=webpage["description"],
            breadcrumb=webpage.get("breadcrumb"),
            date_published=webpage.get("date_published"),
            date_modified=webpage.get("date_modified"),
            author_name=webpage.get("author_name"),
            author_url=webpage.get("author_url"),
            page_type=webpage.get("page_type", "WebPage"),
        ))

    # FAQ
    faq = config.get("faq", [])
    if faq:
        schemas.append(faq_schema(faq))

    # Services
    for svc in config.get("services", []):
        schemas.append(service_schema(
            name=svc["name"],
            description=svc["description"],
            provider_name=org.get("name", "") if org else svc.get("provider_name", ""),
            provider_url=org.get("url", "") if org else svc.get("provider_url", ""),
            service_type=svc.get("service_type"),
            area_served=svc.get("area_served"),
            price_range=svc.get("price_range"),
            url=svc.get("url"),
        ))

    # Person (author/founder)
    for person in config.get("persons", []):
        schemas.append(person_schema(
            name=person["name"],
            url=person["url"],
            job_title=person.get("job_title"),
            works_for_name=org.get("name") if org else person.get("works_for_name"),
            works_for_url=org.get("url") if org else person.get("works_for_url"),
            same_as=person.get("same_as", []),
            email=person.get("email"),
            description=person.get("description"),
        ))

    return build_graph(schemas)


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
        return val if val else default
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)


def ask_list(prompt: str) -> list:
    print(f"{prompt} (one per line, blank line to finish):")
    items = []
    while True:
        try:
            line = input("  → ").strip()
            if not line:
                break
            items.append(line)
        except (KeyboardInterrupt, EOFError):
            break
    return items


def interactive_mode() -> dict:
    print("\n=== Entity Schema Generator — Interactive Mode ===\n")
    print("Build JSON-LD for your business. Press Enter to accept defaults.\n")

    config = {}

    # Organization
    print("─── ORGANIZATION ───────────────────────────────")
    org_type = ask("Business type (Organization/LocalBusiness/ProfessionalService/Consulting)", "ProfessionalService")
    name = ask("Business name")
    url = ask("Website URL (https://...)")
    description = ask("One-sentence description (appears in AI search summaries!)")
    city = ask("City", "Hamburg")
    country = ask("Country code", "DE")
    postal_code = ask("Postal code (optional)")
    email = ask("Contact email (optional)")
    phone = ask("Phone (optional, format: +49-40-1234567)")

    print("\nSameAs URLs (LinkedIn, Wikipedia, Wikidata, Crunchbase, etc.)")
    print("These are critical for entity disambiguation in AI search.")
    same_as = ask_list("Paste URLs")

    print("\nService names (short, e.g. 'SEO Audit', 'Landing Page Design')")
    services = ask_list("Service names")

    print("\nAreas served (cities, regions, or 'Germany')")
    areas = ask_list("Areas served")

    config["organization"] = {
        "type": org_type, "name": name, "url": url, "description": description,
        "city": city, "country": country, "same_as": same_as, "services": services,
        "areas_served": areas,
    }
    if postal_code: config["organization"]["postal_code"] = postal_code
    if email: config["organization"]["email"] = email
    if phone: config["organization"]["phone"] = phone

    # WebPage
    print("\n─── WEBPAGE (optional, for homepage) ───────────")
    do_webpage = ask("Add WebPage schema for homepage? (y/n)", "y").lower() == "y"
    if do_webpage:
        page_url = ask("Page URL", url)
        page_name = ask("Page title", name)
        page_desc = ask("Page description (use meta description)", description)
        author = ask("Author name (optional)")
        config["webpage"] = {
            "url": page_url, "name": page_name, "description": page_desc,
            "page_type": "WebPage",
        }
        if author:
            author_url = ask("Author URL (personal site or LinkedIn)")
            config["webpage"]["author_name"] = author
            config["webpage"]["author_url"] = author_url

    # FAQ
    print("\n─── FAQ (optional, unlocks Google FAQ rich results) ─────")
    do_faq = ask("Add FAQ schema? (y/n)", "n").lower() == "y"
    if do_faq:
        faq_items = []
        print("Enter Q&A pairs (blank question to stop):")
        while True:
            q = ask("  Question").strip()
            if not q:
                break
            a = ask("  Answer").strip()
            if a:
                faq_items.append({"question": q, "answer": a})
        config["faq"] = faq_items

    # Person
    print("\n─── PERSON (optional, for author/founder page) ────────────")
    do_person = ask("Add Person schema for founder/author? (y/n)", "n").lower() == "y"
    if do_person:
        person_name = ask("Full name")
        person_url = ask("Profile URL (your website or LinkedIn)")
        job_title = ask("Job title")
        person_same_as = ask_list("Social/profile URLs (LinkedIn, XING, etc.)")
        config["persons"] = [{
            "name": person_name, "url": person_url,
            "job_title": job_title, "same_as": person_same_as,
        }]

    return config


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_schema(schema: dict) -> list[str]:
    """Basic structural validation — not a full schema.org validator."""
    warnings = []
    graph = schema.get("@graph", [])

    org = next((s for s in graph if s.get("@type") in (
        "Organization", "LocalBusiness", "ProfessionalService", "Consulting", "Corporation"
    )), None)

    if not org:
        warnings.append("No Organization/LocalBusiness type found — entity identity unclear to AI systems.")
    else:
        if not org.get("sameAs"):
            warnings.append(
                "sameAs is empty — add Wikipedia, LinkedIn, Wikidata, or Crunchbase URLs. "
                "sameAs links are how AI systems disambiguate your entity from others with similar names."
            )
        if not org.get("description"):
            warnings.append("Organization missing description — used by AI systems in summaries.")

    if not any(s.get("@type") == "WebSite" for s in graph):
        warnings.append("No WebSite schema — add to enable Sitelinks Search Box eligibility.")

    return warnings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate validated JSON-LD entity schema for SEO + AI visibility"
    )
    parser.add_argument("--config", metavar="FILE",
                        help="YAML config file (see examples/nagacodex_config.yaml)")
    parser.add_argument("--interactive", action="store_true",
                        help="Interactive guided mode")
    parser.add_argument("--output", choices=["script", "json"], default="script",
                        help="Output as <script> tag (default) or raw JSON")
    parser.add_argument("--save", metavar="FILE")

    # Quick one-liner flags
    parser.add_argument("--name")
    parser.add_argument("--type", dest="org_type", default="ProfessionalService")
    parser.add_argument("--url")
    parser.add_argument("--description")
    parser.add_argument("--city")
    parser.add_argument("--country", default="DE")
    parser.add_argument("--email")
    parser.add_argument("--phone")
    parser.add_argument("--same-as", nargs="*", default=[])

    args = parser.parse_args()

    # Build config
    if args.interactive:
        config = interactive_mode()
    elif args.config:
        if not HAS_YAML:
            print("Install pyyaml: pip3 install pyyaml", file=sys.stderr)
            sys.exit(1)
        with open(args.config, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    elif args.name and args.url:
        config = {
            "organization": {
                "type": args.org_type,
                "name": args.name,
                "url": args.url,
                "description": args.description or f"{args.name} — professional services.",
                "city": args.city,
                "country": args.country,
                "email": args.email,
                "phone": args.phone,
                "same_as": args.same_as,
            }
        }
    else:
        parser.print_help()
        print("\nExample:\n  python3 generate_entity_schema.py --interactive")
        print("  python3 generate_entity_schema.py --config examples/nagacodex_config.yaml")
        sys.exit(1)

    schema = build_from_config(config)
    warnings = validate_schema(schema)

    if warnings:
        print("\n⚠  Validation warnings:", file=sys.stderr)
        for w in warnings:
            print(f"   • {w}", file=sys.stderr)
        print(file=sys.stderr)

    output = to_script_tag(schema) if args.output == "script" else json.dumps(schema, indent=2, ensure_ascii=False)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Saved to {args.save}", file=sys.stderr)
    else:
        print(output)

    print("\n✓ Validate at:", file=sys.stderr)
    print("  https://validator.schema.org/", file=sys.stderr)
    print("  https://search.google.com/test/rich-results", file=sys.stderr)


if __name__ == "__main__":
    main()
