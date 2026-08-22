"""
Page Selector Module (Multi-Signal & Locale Aware)
Scores, classifies, and prioritizes discovered URLs using URL path, anchor text,
page titles, meta tags, and category diversity while deprioritizing generic locale homepages.
"""

import re
from typing import Dict, List, Tuple
from urllib.parse import urlparse

# Locale/region pattern detector (e.g. /en, /en-us, /de, /fr, /ja, /zh-cn)
LOCALE_ROOT_PATTERN = re.compile(
    r"^/(?:en|en-[a-z]{2}|de|fr|es|it|pt|ja|zh|zh-[a-z]{2}|nl|ru|ko|ar|pl|sv|da|fi|no|tr|cs|hu|ro)/?$",
    re.IGNORECASE
)

# Slugs categorized by relevance for company identity extraction
KEYWORD_WEIGHTS: Dict[str, Tuple[float, str]] = {
    # Imprint, Legal & Registration (Highest priority for legal identity, VAT, reg number)
    "imprint": (15.0, "legal"),
    "impressum": (15.0, "legal"),
    "mentions-legales": (15.0, "legal"),
    "mentions_legales": (15.0, "legal"),
    "legal-notice": (14.0, "legal"),
    "legal_notice": (14.0, "legal"),
    "legal": (12.0, "legal"),
    "company-details": (14.0, "legal"),
    "company-information": (14.0, "legal"),
    "corporate-information": (14.0, "legal"),
    "registration": (13.0, "legal"),
    "registered-office": (14.0, "legal"),
    "terms": (11.0, "legal"),
    "terms-of-use": (11.0, "legal"),
    "terms-and-conditions": (11.0, "legal"),
    "terms-of-service": (11.0, "legal"),
    "privacy-policy": (9.0, "legal"),
    "privacy": (8.0, "legal"),

    # About & Company Overview
    "about": (10.0, "about"),
    "about-us": (12.0, "about"),
    "about_us": (12.0, "about"),
    "who-we-are": (12.0, "about"),
    "who_we_are": (12.0, "about"),
    "our-company": (12.0, "about"),
    "our_company": (12.0, "about"),
    "our-business": (11.0, "about"),
    "group-profile": (12.0, "about"),
    "company-profile": (12.0, "about"),
    "company": (9.0, "about"),
    "corporate": (8.0, "about"),
    "overview": (8.0, "about"),
    "our-story": (9.0, "about"),
    "facts": (7.0, "about"),

    # Contact & Locations
    "contact": (11.0, "contact"),
    "contact-us": (13.0, "contact"),
    "contact_us": (13.0, "contact"),
    "get-in-touch": (11.0, "contact"),
    "where-we-are": (11.0, "contact"),
    "locations": (11.0, "contact"),
    "offices": (10.0, "contact"),
    "headquarters": (12.0, "contact"),
    "global-locations": (11.0, "contact"),
    "help": (10.0, "contact"),
    "help-center": (9.0, "contact"),
    "customer-service": (9.0, "contact"),
    "store-locator": (9.0, "contact"),
    "find-us": (10.0, "contact"),
    "our-offices": (11.0, "contact"),

    # Leadership, Governance & Investors
    "investors": (10.0, "investor"),
    "investor-relations": (12.0, "investor"),
    "leadership": (10.0, "leadership"),
    "management": (9.0, "leadership"),
    "directors": (9.0, "leadership"),
    "board-of-directors": (10.0, "leadership"),
    "governance": (9.0, "leadership"),
    "subsidiaries": (11.0, "about"),
    "group": (8.0, "about"),
    "annual-reports": (9.0, "investor"),
}

# Low value patterns — never useful for company identity extraction
LOW_VALUE_PATTERNS = [
    r"/blog/", r"/news/", r"/article/", r"/posts/", r"/product/",
    r"/products/", r"/item/", r"/shop/", r"/store/", r"/cart/",
    r"/checkout/", r"/category/", r"/tag/", r"/author/", r"/search",
    r"/login", r"/signin", r"/signup", r"/register-user", r"/account",
    r"/download", r"/downloads", r"/docs/", r"/documentation/",
    r"/community/", r"/events/", r"/webinars/", r"/feed/",
    r"/vacancies/", r"/jobs/", r"/careers/", r"/press/",
]


def score_url(url: str, start_url: str) -> Tuple[float, str]:
    """Score a URL based on path, keywords, depth, and locale penalties."""
    parsed = urlparse(url)
    path = parsed.path.lower()

    parsed_start = urlparse(start_url)
    if parsed.netloc == parsed_start.netloc and (path in ("", "/", "/index.html", "/index.php")):
        return 20.0, "homepage"

    # Locale homepage detection: penalize bare locale homepages like /en, /de, /fr
    if LOCALE_ROOT_PATTERN.match(path):
        return 1.5, "homepage"

    # Penalize low-value paths
    for pattern in LOW_VALUE_PATTERNS:
        if re.search(pattern, path):
            return 0.5, "general"

    score = 1.0
    category = "general"

    clean_path = path.replace("/", " ").replace("-", " ").replace("_", " ").replace(".", " ")
    tokens = clean_path.split()

    best_score = 1.0
    best_cat = "general"
    additive_bonus = 0.0

    for kw, (weight, cat) in KEYWORD_WEIGHTS.items():
        kw_tokens = kw.replace("-", " ").replace("_", " ").split()
        if all(t in tokens for t in kw_tokens) or kw in path:
            if weight > best_score:
                best_score = weight
                best_cat = cat
            else:
                # Secondary keyword match on same URL adds a bonus
                # e.g. /terms/about-barnesandnoble matches both "terms" and "about"
                additive_bonus += min(weight * 0.25, 3.0)

    score = best_score + additive_bonus
    category = best_cat

    # Path depth bonus/penalty
    path_depth = len([p for p in path.split("/") if p])
    if path_depth == 1:
        score += 1.5
    elif path_depth == 2:
        score += 1.0
    elif path_depth > 4:
        score *= 0.7

    return round(score, 2), category


class PageSelector:
    """Selects and prioritizes relevant URLs for company identity extraction."""

    def __init__(self, start_url: str, max_pages: int = 20):
        self.start_url = start_url
        self.max_pages = max_pages

    def select_relevant_pages(self, discovered_urls: List[str]) -> List[Tuple[str, float, str]]:
        """Score discovered URLs and select a category-diverse candidate set."""
        scored: List[Tuple[str, float, str]] = []
        for url in discovered_urls:
            score, cat = score_url(url, self.start_url)
            scored.append((url, score, cat))

        # Sort descending by score
        scored.sort(key=lambda x: x[1], reverse=True)

        selected: List[Tuple[str, float, str]] = []
        seen_urls = set()

        # 1. Include main homepage first
        for item in scored:
            if item[2] == "homepage" and item[1] >= 10.0 and item[0] not in seen_urls:
                selected.append(item)
                seen_urls.add(item[0])
                break

        # 2. Select diverse high-scoring pages (at least one per category if possible)
        categories_added = set()
        for item in scored:
            if len(selected) >= self.max_pages:
                break
            url, score, cat = item
            if url not in seen_urls and score >= 3.0:
                selected.append(item)
                seen_urls.add(url)
                categories_added.add(cat)

        # 3. Backfill remaining slots with best available URLs
        if len(selected) < self.max_pages:
            for item in scored:
                if len(selected) >= self.max_pages:
                    break
                if item[0] not in seen_urls:
                    selected.append(item)
                    seen_urls.add(item[0])

        print(f"\n[2/5] Selected {len(selected)} high-priority pages (max limit: {self.max_pages}):")
        for idx, (url, score, cat) in enumerate(selected[:10], 1):
            print(f"      {idx:2d}. [{cat.upper():10s}] (score: {score:4.1f}) {url}")
        if len(selected) > 10:
            print(f"      ... and {len(selected) - 10} more pages.")

        return selected
