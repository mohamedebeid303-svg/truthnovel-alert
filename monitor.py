import os
import re
import json
import base64
import time
import html as html_module
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

GITHUB_REPO = "mohamedebeid303-svg/truthnovel-alert"
DATA_FILE = "data.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

TELEGRAM_API = (
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    if TELEGRAM_BOT_TOKEN else None
)

# مهلات أقصر لتقليل انتظار المواقع البطيئة.
REQUEST_TIMEOUT = 12
MAX_RETRIES = 2
RETRY_BACKOFF = 1.0

# حدود البحث الاحتياطي.
MAX_CRAWL_PAGES = 20
MAX_AHEAD = 20
MAX_SITEMAPS = 8

# لا نعتمد على أرقام موجودة داخل النص العادي.
USE_BODY_NUMBERS = False

# إعادة التحقق من وضع الصيانة دوريًا أثناء التشغيل.
# لا يُجرى طلب GitHub جديد قبل كل فصل إذا كانت الفحوص متقاربة.
MAINTENANCE_CACHE_SECONDS = 10


# ============================================================
# HTTP SESSION / RUNTIME CACHE
# ============================================================

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
})

# تُخزّن الصفحات الناجحة فقط خلال دورة التشغيل الحالية.
PAGE_CACHE = {}

# تُخزّن نتائج التحقق الإيجابية فقط؛ الإخفاق المؤقت لا يُحبس في الذاكرة.
VERIFY_CACHE = {}

_MAINTENANCE_CACHE = {
    "checked_at": 0.0,
    "enabled": False,
}


# ============================================================
# DATA CLASS
# ============================================================

@dataclass
class ChapterCandidate:
    number: int
    url: str
    score: int
    source: str
    evidence: str = ""


# ============================================================
# BASIC HELPERS
# ============================================================

def normalize_digits(text):
    if not text:
        return ""
    table = str.maketrans(
        "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
        "01234567890123456789",
    )
    return str(text).translate(table)


def clean_text(text):
    if not text:
        return ""
    text = html_module.unescape(str(text))
    text = normalize_digits(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_url(url):
    if not url:
        return ""
    url = str(url).strip()
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def same_domain(url_a, url_b):
    try:
        a = urlparse(url_a).netloc.lower()
        b = urlparse(url_b).netloc.lower()
        if a.startswith("www."):
            a = a[4:]
        if b.startswith("www."):
            b = b[4:]
        return bool(a) and a == b
    except Exception:
        return False


def absolute_url(base_url, href):
    if not href:
        return ""
    href = html_module.unescape(str(href).strip())
    if href.startswith("#"):
        return ""
    return normalize_url(urljoin(base_url, href))


def site_root(site_url):
    parsed = urlparse(site_url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


# ============================================================
# CHAPTER NUMBER EXTRACTION
# ============================================================

URL_PATTERNS = [
    re.compile(r"/(\d{1,7})(?:[-_/?.#]|$)", re.I),
    re.compile(r"(?:chapter|chap|episode)[-_ /]*(\d{1,7})", re.I),
    re.compile(r"/chapter[s]?/(\d{1,7})(?:[-_/?.#]|$)", re.I),
    re.compile(r"[?&](?:chapter|chap|episode)=(\d{1,7})", re.I),
    re.compile(r"(?:chapter|chap|episode)[_-](\d{1,7})", re.I),
]

TITLE_PATTERNS = [
    re.compile(r"^\s*(\d{1,7})\s*[-–—:]\s*.+$", re.I),
    re.compile(
        r"(?:الفصل|فصل|chapter|chap|episode)\s*#?\s*(\d{1,7})",
        re.I,
    ),
    re.compile(r"^\s*chapter\s*#?\s*(\d{1,7})\b", re.I),
    re.compile(r"^\s*(\d{1,7})\s*$", re.I),
]


def extract_number_from_url(url):
    if not url:
        return None

    parsed = urlparse(normalize_digits(url))
    path = parsed.path or ""

    for pattern in URL_PATTERNS:
        match = pattern.search(path)
        if match:
            try:
                number = int(match.group(1))
                if 1 <= number <= 10_000_000:
                    return number
            except (TypeError, ValueError):
                pass

    query = parse_qs(parsed.query)
    for key in ("chapter", "chap", "episode"):
        values = query.get(key)
        if values:
            match = re.search(r"\d{1,7}", normalize_digits(values[0]))
            if match:
                number = int(match.group())
                if 1 <= number <= 10_000_000:
                    return number

    return None


def extract_number_from_title(text):
    text = clean_text(text)
    if not text:
        return None

    for pattern in TITLE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                number = int(match.group(1))
                if 1 <= number <= 10_000_000:
                    return number
            except (TypeError, ValueError):
                pass

    return None


def extract_explicit_chapter_number(text):
    """لا نعتبر أي رقم عادي فصلًا؛ يجب أن يظهر في سياق فصل واضح."""
    text = clean_text(text)
    if not text:
        return None

    patterns = [
        r"(?:الفصل|فصل)\s*#?\s*(\d{1,7})",
        r"(?:chapter|chap|episode)\s*#?\s*(\d{1,7})",
        r"^\s*(\d{1,7})\s*[-–—:]\s*.+$",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            try:
                number = int(match.group(1))
                if 1 <= number <= 10_000_000:
                    return number
            except (TypeError, ValueError):
                pass

    return None


# ============================================================
# HTTP
# ============================================================

PERMANENT_STATUSES = {400, 401, 403, 404, 405, 410, 422}


def request_with_retry(
    url,
    method="GET",
    params=None,
    headers=None,
    allow_redirects=True,
    timeout=None,
):
    timeout = timeout or REQUEST_TIMEOUT

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = SESSION.request(
                method,
                url,
                params=params,
                headers=headers,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )

            if response.status_code == 200:
                return response

            if response.status_code in PERMANENT_STATUSES:
                print(
                    f"[HTTP] Permanent HTTP {response.status_code}: {url}"
                )
                return response

            # لا نكرر 429 بسرعة؛ نحترم Retry-After إن كان موجودًا.
            if response.status_code == 429 and attempt < MAX_RETRIES:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = min(float(retry_after), 5.0)
                except (TypeError, ValueError):
                    delay = RETRY_BACKOFF * attempt
                print(f"[HTTP] Rate limited: {url}; retrying after {delay}s")
                time.sleep(max(0.5, delay))
                continue

            print(
                f"[HTTP] Status {response.status_code} for {url} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )

        except requests.RequestException as exc:
            print(
                f"[HTTP] Request error for {url}: {exc} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)

    return None


def get_page(url):
    url = normalize_url(url)
    if not url:
        return None, ""

    cached = PAGE_CACHE.get(url)
    if cached:
        return cached["response"], cached["html"]

    response = request_with_retry(url)

    # لا نخزّن فشل الاتصال أو حالات HTTP غير 200؛ قد ينجح الطلب لاحقًا.
    if response is None or response.status_code != 200:
        return response, ""

    html = response.text or ""
    if html:
        PAGE_CACHE[url] = {
            "response": response,
            "html": html,
        }

    return response, html


# ============================================================
# HTML HELPERS
# ============================================================

def page_title(soup):
    title = soup.find("title")
    if title:
        return clean_text(title.get_text(" ", strip=True))
    return ""


def get_h1_texts(soup):
    results = []
    for tag in soup.find_all("h1", limit=10):
        value = clean_text(tag.get_text(" ", strip=True))
        if value:
            results.append(value)
    return results


def get_h2_h3_texts(soup):
    results = []
    for tag in soup.find_all(["h2", "h3"], limit=20):
        value = clean_text(tag.get_text(" ", strip=True))
        if value:
            results.append(value)
    return results


def get_breadcrumb_texts(soup):
    results = []
    selectors = [
        '[aria-label*="breadcrumb" i]',
        '[class*="breadcrumb" i]',
        '[id*="breadcrumb" i]',
    ]

    for selector in selectors:
        try:
            elements = soup.select(selector)
        except Exception:
            continue

        for element in elements:
            value = clean_text(element.get_text(" ", strip=True))
            if value:
                results.append(value)

    return results[:10]


# ============================================================
# PROTECTION / CHALLENGE DETECTION
# ============================================================

PROTECTION_TITLES = (
    "just a moment",
    "attention required",
    "cloudflare",
    "checking your browser",
    "verify you are human",
    "verify you are a human",
    "security check",
    "access denied",
    "please wait",
    "ddos protection",
    "bot verification",
    "human verification",
    "enable javascript",
)

PROTECTION_MARKERS = (
    "cf-chl-",
    "cf-challenge",
    "challenge-platform",
    "turnstile",
    "cloudflare",
    "verify you are human",
    "checking your browser",
    "just a moment",
    "attention required",
    "access denied",
    "ddos protection",
    "bot verification",
)


def detect_protection_page(soup, html):
    title_lower = page_title(soup).lower()

    for marker in PROTECTION_TITLES:
        if marker in title_lower:
            return True, f"title contains protection marker: {marker}"

    sample = clean_text(html[:100000]).lower()
    matched = [marker for marker in PROTECTION_MARKERS if marker in sample]

    if len(matched) >= 2:
        return True, "multiple protection markers: " + ", ".join(matched[:5])

    if "cf-chl-" in sample or "challenge-platform" in sample:
        return True, "Cloudflare challenge detected"

    return False, ""


# ============================================================
# CHAPTER VERIFICATION
# ============================================================

def verify_chapter_html(url, html, expected_number=None):
    """
    يتحقق من HTML الموجود بالفعل دون إعادة طلب الصفحة أو تحليلها مرتين.
    URL وحده لا يكفي لإثبات رقم الفصل.
    """
    url = normalize_url(url)
    if not url or not html:
        return None

    cache_key = (url, expected_number)
    cached = VERIFY_CACHE.get(cache_key)
    if cached:
        return cached

    soup = BeautifulSoup(html, "html.parser")
    title = page_title(soup)
    h1_texts = get_h1_texts(soup)
    h2_h3_texts = get_h2_h3_texts(soup)
    breadcrumb_texts = get_breadcrumb_texts(soup)
    url_number = extract_number_from_url(url)

    print(f"[VERIFY] Chapter {expected_number}")
    print(f"[VERIFY] URL: {url}")
    print(f"[VERIFY] Title: {title or '(empty)'}")
    print("[VERIFY] H1: " + (" | ".join(h1_texts[:5]) if h1_texts else "(none)"))
    print(f"[VERIFY] URL Number: {url_number}")

    protected, reason = detect_protection_page(soup, html)
    if protected:
        print(f"[PROTECTION] {reason}")
        print("[VERIFY] RESULT: REJECTED")
        return None

    evidence_candidates = []

    title_number = extract_number_from_title(title)
    if title_number is not None:
        evidence_candidates.append((title_number, "TITLE", title))

    for heading in h1_texts:
        number = (
            extract_number_from_title(heading)
            or extract_explicit_chapter_number(heading)
        )
        if number is not None:
            evidence_candidates.append((number, "H1", heading))

    for heading in h2_h3_texts:
        number = (
            extract_number_from_title(heading)
            or extract_explicit_chapter_number(heading)
        )
        if number is not None:
            evidence_candidates.append((number, "H2/H3", heading))

    for breadcrumb in breadcrumb_texts:
        number = (
            extract_number_from_title(breadcrumb)
            or extract_explicit_chapter_number(breadcrumb)
        )
        if number is not None:
            evidence_candidates.append((number, "BREADCRUMB", breadcrumb))

    for number, source, value in evidence_candidates[:10]:
        print(f"[VERIFY] Evidence: {source} -> {number} | {value[:180]}")

    if expected_number is not None:
        matching = [
            item for item in evidence_candidates
            if item[0] == expected_number
        ]

        if not matching:
            if url_number == expected_number:
                print(
                    "[VERIFY] URL matches, but page headings/title "
                    "do not confirm the expected chapter."
                )
            print("[VERIFY] RESULT: REJECTED")
            return None

        priority = {
            "TITLE": 100,
            "H1": 90,
            "H2/H3": 70,
            "BREADCRUMB": 50,
        }
        matching.sort(
            key=lambda item: priority.get(item[1], 0),
            reverse=True,
        )
        _, source, _ = matching[0]

        result = {
            "number": expected_number,
            "url": url,
            "title": title,
            "h1": h1_texts[0] if h1_texts else "",
            "evidence": f"{source} confirms {expected_number}",
        }
        VERIFY_CACHE[cache_key] = result
        print(f"[VERIFY] RESULT: VERIFIED ({source})")
        return result

    unique_numbers = {item[0] for item in evidence_candidates}
    if len(unique_numbers) == 1:
        number = next(iter(unique_numbers))
        result = {
            "number": number,
            "url": url,
            "title": title,
            "h1": h1_texts[0] if h1_texts else "",
            "evidence": "title/headings/breadcrumb confirm chapter",
        }
        VERIFY_CACHE[cache_key] = result
        print("[VERIFY] RESULT: VERIFIED")
        return result

    print("[VERIFY] RESULT: REJECTED (no unique explicit chapter number)")
    return None


def verify_chapter_page(url, expected_number=None):
    url = normalize_url(url)
    if not url:
        return None

    cache_key = (url, expected_number)
    cached = VERIFY_CACHE.get(cache_key)
    if cached:
        return cached

    response, html = get_page(url)
    if response is None or response.status_code != 200 or not html:
        status = response.status_code if response is not None else "REQUEST FAILED"
        print(f"[VERIFY] {expected_number}: {status}")
        return None

    return verify_chapter_html(url, html, expected_number)


# ============================================================
# LINK ANALYSIS
# ============================================================

NAVIGATION_WORDS = (
    "السابق",
    "الموضوع السابق",
    "previous",
    "prev",
    "التالي",
    "الموضوع التالي",
    "next",
)


def analyze_links(page_url, soup):
    candidates = []

    for link in soup.find_all("a", href=True):
        url = absolute_url(page_url, link.get("href", ""))
        if not url or not same_domain(page_url, url):
            continue

        text = clean_text(link.get_text(" ", strip=True))
        aria = clean_text(link.get("aria-label", ""))
        title = clean_text(link.get("title", ""))
        combined = " ".join(value for value in (text, aria, title) if value)

        number_from_text = extract_explicit_chapter_number(combined)
        number_from_url = extract_number_from_url(url)
        number = (
            number_from_text
            if number_from_text is not None
            else number_from_url
        )
        if number is None:
            continue

        score = 0
        evidence = []

        if number_from_text is not None:
            score += 100
            evidence.append("explicit chapter text")

        if number_from_url is not None:
            score += 70
            evidence.append("chapter-like URL")

        lower = combined.lower()
        if any(word in lower for word in ("chapter", "الفصل", "فصل", "episode")):
            score += 40
            evidence.append("chapter keyword")

        if any(word in lower for word in NAVIGATION_WORDS):
            score += 30
            evidence.append("chapter navigation")

        candidates.append(ChapterCandidate(
            number=number,
            url=url,
            score=score,
            source="link",
            evidence=", ".join(evidence),
        ))

    return candidates


# ============================================================
# PAGE CRAWLING
# ============================================================

def inspect_page_for_candidates(url):
    response, html = get_page(url)
    if response is None or response.status_code != 200 or not html:
        return [], None, ""

    soup = BeautifulSoup(html, "html.parser")

    protected, reason = detect_protection_page(soup, html)
    if protected:
        print(f"[PROTECTION] {url} | {reason}")
        return [], soup, html

    candidates = []

    # استخدم soup/HTML الموجودين بدل طلب الصفحة وتحليلها مرة ثانية.
    verified = verify_chapter_html(url, html)
    if verified:
        candidates.append(ChapterCandidate(
            number=verified["number"],
            url=verified["url"],
            score=1000,
            source="verified_page",
            evidence=verified["evidence"],
        ))

    candidates.extend(analyze_links(url, soup))
    return candidates, soup, html


def crawl_chapter_pages(start_url, old_chapter):
    start_url = normalize_url(start_url)
    queue = [start_url]
    visited = set()
    verified = {}

    while queue and len(visited) < MAX_CRAWL_PAGES:
        current = normalize_url(queue.pop(0))
        if not current or current in visited:
            continue

        visited.add(current)
        print(f"[CRAWL] {current}")

        candidates, soup, _ = inspect_page_for_candidates(current)

        for candidate in candidates:
            if not (old_chapter < candidate.number <= old_chapter + MAX_AHEAD):
                continue

            result = verify_chapter_page(
                candidate.url,
                expected_number=candidate.number,
            )
            if not result:
                continue

            number = result["number"]
            previous = verified.get(number)
            new_candidate = ChapterCandidate(
                number=number,
                url=result["url"],
                score=1200,
                source="verified_link",
                evidence=result["evidence"],
            )

            if previous is None or new_candidate.score > previous.score:
                verified[number] = new_candidate

            if candidate.url not in visited and candidate.url not in queue:
                queue.append(candidate.url)

        # أولوية روابط السابق/التالي، ثم الروابط التي تحمل رقم فصل.
        if soup:
            navigation_urls = []
            numbered_urls = []

            for link in soup.find_all("a", href=True):
                url = absolute_url(current, link.get("href", ""))
                if not url or not same_domain(start_url, url):
                    continue
                if url in visited or url in queue:
                    continue

                text = clean_text(link.get_text(" ", strip=True)).lower()
                title = clean_text(link.get("title", "")).lower()
                aria = clean_text(link.get("aria-label", "")).lower()
                combined = " ".join((text, title, aria))

                number = (
                    extract_explicit_chapter_number(combined)
                    or extract_number_from_url(url)
                )
                is_navigation = any(word in combined for word in NAVIGATION_WORDS)

                if is_navigation:
                    navigation_urls.append(url)
                elif number is not None and old_chapter < number <= old_chapter + MAX_AHEAD:
                    numbered_urls.append(url)

            # أضف كل رابط مرة واحدة مع الحفاظ على ترتيب الأولوية.
            for url in reversed(navigation_urls):
                if url not in queue and url not in visited:
                    queue.insert(0, url)

            for url in numbered_urls:
                if url not in queue and url not in visited:
                    queue.append(url)

    return verified


# ============================================================
# WORDPRESS API
# ============================================================

def wordpress_json(url, params=None):
    response = request_with_retry(
        url,
        params=params,
        headers={
            "Accept": "application/json",
            "User-Agent": SESSION.headers["User-Agent"],
        },
    )

    if response is None or response.status_code != 200:
        return None

    try:
        payload = response.json()
        return payload if isinstance(payload, list) else None
    except (ValueError, requests.RequestException):
        return None


def wordpress_candidates(site_url, old_chapter):
    """
    طلبا posts/pages فقط في المسار المعتاد.
    أزيلت حلقة البحث التي كانت ترسل طلبًا مستقلًا لكل رقم فصل
    (وقد تصل إلى 20 طلبًا لكل رواية).
    """
    base = site_root(site_url)
    if not base:
        return {}

    candidates = {}
    endpoints = (
        "/wp-json/wp/v2/posts",
        "/wp-json/wp/v2/pages",
    )

    for endpoint in endpoints:
        data = wordpress_json(
            base + endpoint,
            params={
                "per_page": 50,
                "orderby": "date",
                "order": "desc",
                "_fields": "id,date,link,title,slug",
            },
        )

        if not data:
            continue

        for item in data:
            if not isinstance(item, dict):
                continue

            link = item.get("link", "")
            title_obj = item.get("title") or {}
            title = clean_text(
                title_obj.get("rendered", "")
                if isinstance(title_obj, dict)
                else title_obj
            )

            number = (
                extract_number_from_title(title)
                or extract_explicit_chapter_number(title)
                or extract_number_from_url(link)
            )

            if number is None or not (old_chapter < number <= old_chapter + MAX_AHEAD):
                continue

            if link:
                candidates[number] = link

    verified = {}
    for number, link in candidates.items():
        result = verify_chapter_page(link, expected_number=number)
        if result:
            verified[number] = ChapterCandidate(
                number=number,
                url=result["url"],
                score=1500,
                source="wordpress_verified",
                evidence=result["evidence"],
            )

    return verified


# ============================================================
# RSS / FEED
# ============================================================

def feed_candidates(site_url, old_chapter):
    base = site_root(site_url)
    if not base:
        return {}

    # نجرب المسارات الشائعة، لكن نتوقف عند أول Feed صالح.
    feed_urls = (
        urljoin(base, "/feed/"),
        urljoin(base, "/rss/"),
        urljoin(base, "/feed"),
        urljoin(base, "/rss"),
    )

    verified = {}

    for feed_url in feed_urls:
        response = request_with_retry(feed_url)
        if response is None or response.status_code != 200:
            continue

        try:
            soup = BeautifulSoup(response.text, "xml")
        except Exception:
            continue

        items = soup.find_all(["item", "entry"])
        if not items:
            continue

        for item in items:
            title_tag = item.find("title")
            if not title_tag:
                continue

            title = clean_text(title_tag.get_text(" ", strip=True))
            number = (
                extract_number_from_title(title)
                or extract_explicit_chapter_number(title)
            )

            if number is None or not (old_chapter < number <= old_chapter + MAX_AHEAD):
                continue

            link = ""
            link_tag = item.find("link")
            if link_tag:
                link = (
                    link_tag.get("href", "")
                    or link_tag.get_text(strip=True)
                )

            link = absolute_url(site_url, link)
            if not link:
                continue

            result = verify_chapter_page(link, expected_number=number)
            if result:
                verified[number] = ChapterCandidate(
                    number=number,
                    url=result["url"],
                    score=1400,
                    source="feed_verified",
                    evidence=result["evidence"],
                )

        # إذا كان Feed صالحًا واحتوى عناصر، لا داعي لطلب
        # ثلاثة مسارات أخرى في كل دورة.
        break

    return verified


# ============================================================
# SITEMAP
# ============================================================

def sitemap_candidates(site_url, old_chapter):
    base = site_root(site_url)
    if not base:
        return {}

    sitemap_urls = [
        urljoin(base, "/sitemap.xml"),
        urljoin(base, "/wp-sitemap.xml"),
        urljoin(base, "/sitemap_index.xml"),
        urljoin(base, "/post-sitemap.xml"),
    ]

    urls_to_check = list(sitemap_urls)
    visited = set()
    verified = {}

    while urls_to_check and len(visited) < MAX_SITEMAPS:
        sitemap_url = urls_to_check.pop(0)
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)

        response = request_with_retry(sitemap_url)
        if response is None or response.status_code != 200:
            continue

        try:
            soup = BeautifulSoup(response.text, "xml")
        except Exception:
            continue

        for loc in soup.find_all("loc"):
            value = clean_text(loc.get_text(" ", strip=True))
            if not value:
                continue

            number = extract_number_from_url(value)
            if number is not None:
                if old_chapter < number <= old_chapter + MAX_AHEAD:
                    result = verify_chapter_page(value, expected_number=number)
                    if result:
                        verified[number] = ChapterCandidate(
                            number=number,
                            url=result["url"],
                            score=1300,
                            source="sitemap_verified",
                            evidence=result["evidence"],
                        )
            elif value.lower().endswith(".xml") and value not in visited:
                urls_to_check.append(value)

    return verified


# ============================================================
# CONTIGUOUS CHAPTER CHECK
# ============================================================

def get_contiguous_latest(verified, old_chapter):
    """لا يسمح بالقفز فوق فصل غير متحقق منه."""
    current = old_chapter
    while current + 1 in verified:
        current += 1
    return current


def has_contiguous_chapter(verified, old_chapter):
    return get_contiguous_latest(verified, old_chapter) > old_chapter


# ============================================================
# DISCOVER VERIFIED CHAPTERS
# ============================================================

def discover_verified_chapters(site_url, old_chapter):
    """
    ترتيب البحث:
      1) الزحف إلى صفحة العمل وروابط الفصول.
      2) WordPress عند عدم تكوّن سلسلة متصلة.
      3) RSS عند استمرار وجود فجوة.
      4) Sitemap كملاذ أخير.

    نتوقف مبكرًا فقط إذا عثر مصدر على سلسلة متصلة تبدأ
    مباشرة بعد last_chapter. الفصول غير المتصلة لا توقف
    البحث الاحتياطي، حتى لا نفوّت فصلًا مفقودًا.
    """
    all_verified = {}

    def merge(source):
        for number, candidate in source.items():
            if not (old_chapter < number <= old_chapter + MAX_AHEAD):
                continue

            previous = all_verified.get(number)
            if previous is None or candidate.score > previous.score:
                all_verified[number] = candidate

    def log_contiguous(source_name):
        latest = get_contiguous_latest(all_verified, old_chapter)
        if latest > old_chapter:
            print(
                f"[DISCOVER] {source_name} produced a contiguous "
                f"chain through chapter {latest}."
            )
            return True
        return False

    print("[DISCOVER] Stage 1/4: crawling main page...")
    try:
        merge(crawl_chapter_pages(site_url, old_chapter))
    except Exception as exc:
        print(f"[CRAWL] Error: {exc}")

    if log_contiguous("CRAWL"):
        return all_verified

    print("[DISCOVER] Stage 2/4: checking WordPress API...")
    try:
        merge(wordpress_candidates(site_url, old_chapter))
    except Exception as exc:
        print(f"[WORDPRESS] Error: {exc}")

    if log_contiguous("WORDPRESS"):
        return all_verified

    print("[DISCOVER] Stage 3/4: checking RSS...")
    try:
        merge(feed_candidates(site_url, old_chapter))
    except Exception as exc:
        print(f"[RSS] Error: {exc}")

    if log_contiguous("RSS"):
        return all_verified

    print("[DISCOVER] Stage 4/4: checking sitemap...")
    try:
        merge(sitemap_candidates(site_url, old_chapter))
    except Exception as exc:
        print(f"[SITEMAP] Error: {exc}")

    return all_verified


# ============================================================
# GITHUB
# ============================================================

def github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "truthnovel-alert-monitor",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def load_data():
    url = (
        f"https://api.github.com/repos/"
        f"{GITHUB_REPO}/contents/{DATA_FILE}"
    )

    response = request_with_retry(url, headers=github_headers())
    if response is None:
        raise RuntimeError("Unable to contact GitHub.")

    if response.status_code != 200:
        raise RuntimeError(
            f"GitHub load failed: HTTP {response.status_code}"
        )

    payload = response.json()
    encoded = payload.get("content", "")
    sha = payload.get("sha")

    if not encoded:
        raise RuntimeError("GitHub returned empty data.json.")

    content = base64.b64decode(encoded).decode("utf-8")
    data = json.loads(content)

    print("[GITHUB] data.json loaded successfully.")
    return data, sha


# ============================================================
# GITHUB SAVE / SAFE MERGE
# ============================================================

def github_save_data(data, sha):
    url = (
        f"https://api.github.com/repos/"
        f"{GITHUB_REPO}/contents/{DATA_FILE}"
    )

    content = json.dumps(data, ensure_ascii=False, indent=2)
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")

    payload = {
        "message": "Update chapter monitor data",
        "content": encoded,
        "sha": sha,
    }

    response = SESSION.put(
        url,
        headers=github_headers(),
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code in (200, 201):
        print("[GITHUB] data.json saved successfully.")
        return response.json()

    if response.status_code == 409:
        raise RuntimeError("GITHUB_SHA_CONFLICT")

    raise RuntimeError(
        f"GitHub save failed: HTTP {response.status_code}: "
        f"{response.text[:500]}"
    )


def clone_data(data):
    return json.loads(json.dumps(data, ensure_ascii=False))


def work_identity(work):
    if not isinstance(work, dict):
        return None

    url = str(work.get("url", "")).strip()
    if url:
        return ("url", normalize_url(url))

    name = str(work.get("name", work.get("title", ""))).strip()
    work_type = str(work.get("type", "")).strip()
    if name:
        return ("fallback", work_type, name)

    return None


def build_monitor_updates(original_data, monitored_data):
    """استخراج تغييرات last_chapter فقط."""
    updates = {}

    original_users = original_data.get("users", {})
    monitored_users = monitored_data.get("users", {})

    if not isinstance(original_users, dict):
        original_users = {}
    if not isinstance(monitored_users, dict):
        monitored_users = {}

    for user_id, monitored_user in monitored_users.items():
        if not isinstance(monitored_user, dict):
            continue

        monitored_works = monitored_user.get("works", [])
        if not isinstance(monitored_works, list):
            continue

        original_user = original_users.get(user_id, {})
        if not isinstance(original_user, dict):
            original_user = {}

        original_works = original_user.get("works", [])
        if not isinstance(original_works, list):
            original_works = []

        original_by_identity = {}
        for original_work in original_works:
            identity = work_identity(original_work)
            if identity is not None:
                original_by_identity[identity] = original_work

        for monitored_work in monitored_works:
            if not isinstance(monitored_work, dict):
                continue

            identity = work_identity(monitored_work)
            if identity is None:
                continue

            original_work = original_by_identity.get(identity)
            if not isinstance(original_work, dict):
                continue

            old_chapter = original_work.get("last_chapter")
            new_chapter = monitored_work.get("last_chapter")

            if new_chapter == old_chapter:
                continue

            updates.setdefault(user_id, {})[identity] = new_chapter

    return updates


def merge_monitor_updates(latest_data, updates):
    users = latest_data.get("users", {})
    if not isinstance(users, dict):
        return latest_data

    for user_id, work_updates in updates.items():
        latest_user = users.get(user_id)
        if not isinstance(latest_user, dict):
            continue

        latest_works = latest_user.get("works", [])
        if not isinstance(latest_works, list):
            continue

        for latest_work in latest_works:
            if not isinstance(latest_work, dict):
                continue

            identity = work_identity(latest_work)
            if identity not in work_updates:
                continue

            new_chapter = work_updates[identity]
            current_chapter = latest_work.get("last_chapter")

            try:
                if (
                    current_chapter is not None
                    and new_chapter is not None
                    and float(current_chapter) > float(new_chapter)
                ):
                    print(
                        "[MERGE] Keeping newer last_chapter="
                        f"{current_chapter} for user {user_id}."
                    )
                    continue
            except (TypeError, ValueError):
                pass

            latest_work["last_chapter"] = new_chapter
            print(
                f"[MERGE] Updated user={user_id} "
                f"last_chapter={new_chapter}"
            )

    return latest_data


def save_data_with_retry(data, original_data, sha, max_attempts=3):
    updates = build_monitor_updates(original_data, data)
    if not updates:
        print("[GITHUB] No monitor-specific updates to merge.")

    current_data = data
    current_sha = sha

    for attempt in range(1, max_attempts + 1):
        try:
            github_save_data(current_data, current_sha)
            print(f"[GITHUB] Save successful on attempt {attempt}.")
            return current_data

        except RuntimeError as error:
            if str(error) != "GITHUB_SHA_CONFLICT":
                raise

            print(
                f"[GITHUB] SHA conflict on attempt "
                f"{attempt}/{max_attempts}."
            )

            if attempt >= max_attempts:
                raise RuntimeError(
                    "GitHub save failed after multiple SHA conflict retries."
                )

            latest_data, latest_sha = load_data()
            current_data = merge_monitor_updates(latest_data, updates)
            current_sha = latest_sha

    raise RuntimeError("GitHub save retry failed.")


# ============================================================
# MAINTENANCE MODE
# ============================================================

def is_maintenance_enabled(data):
    settings = data.get("settings")
    if not isinstance(settings, dict):
        return False
    return bool(settings.get("maintenance", False))


def maintenance_still_enabled(force=False):
    """
    فحص الصيانة مع cache قصيرة لتجنب تحميل data.json من GitHub
    قبل كل فصل على نحو متكرر. عند تعذر التحقق نتوقف احتياطيًا.
    """
    now = time.monotonic()
    age = now - _MAINTENANCE_CACHE["checked_at"]

    if (
        not force
        and _MAINTENANCE_CACHE["checked_at"] > 0
        and age < MAINTENANCE_CACHE_SECONDS
    ):
        return _MAINTENANCE_CACHE["enabled"]

    try:
        latest_data, _ = load_data()
        enabled = is_maintenance_enabled(latest_data)

        _MAINTENANCE_CACHE["checked_at"] = now
        _MAINTENANCE_CACHE["enabled"] = enabled

        if enabled:
            print("[MAINTENANCE] Maintenance enabled; stopping notifications.")

        return enabled

    except Exception as exc:
        print(f"[MAINTENANCE] Unable to verify state: {exc}")
        print("[MAINTENANCE] Fail-safe: stopping notification.")
        return True


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(chat_id, text):
    if not TELEGRAM_API:
        print("[TELEGRAM] TELEGRAM_BOT_TOKEN is missing.")
        return False

    url = f"{TELEGRAM_API}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            print(
                f"[TELEGRAM] Failed: HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
            return False

        result = response.json()
        if not result.get("ok"):
            print(f"[TELEGRAM] API error: {result}")
            return False

        return True

    except (requests.RequestException, ValueError) as exc:
        print(f"[TELEGRAM] Request/API error: {exc}")
        return False


# ============================================================
# MESSAGE
# ============================================================

def chapter_message(work_name, chapter_number, chapter_url):
    return (
        "🔔 <b>فصل جديد!</b>\n\n"
        f"📖 <b>{html_module.escape(str(work_name))}</b>\n"
        f"📚 <b>الفصل {chapter_number}</b>\n\n"
        f"🔗 {html_module.escape(str(chapter_url))}"
    )


# ============================================================
# WORK NORMALIZATION
# ============================================================

def get_users(data):
    users = data.get("users")
    if users is None:
        data["users"] = {}
        return data["users"]
    return users


def get_user_works(user):
    if not isinstance(user, dict):
        return []

    works = user.get("works")
    if works is None:
        user["works"] = []
        return user["works"]

    return works if isinstance(works, list) else []


# ============================================================
# MONITOR ONE WORK
# ============================================================

def monitor_work(chat_id, work):
    if not isinstance(work, dict):
        return False

    name = work.get("name") or work.get("title") or "Unknown"
    url = work.get("url") or work.get("link")

    if not url:
        print(f"[SKIP] {name}: no URL.")
        return False

    try:
        stored = int(work.get("last_chapter", 0))
    except (TypeError, ValueError):
        stored = 0

    print(
        f"[MONITOR] Checking {name} | stored={stored} | {url}"
    )

    started_at = time.perf_counter()
    verified = discover_verified_chapters(url, stored)
    elapsed = time.perf_counter() - started_at
    print(f"[TIMING] Discovery for {name}: {elapsed:.2f}s")

    if not verified:
        print("[DETECT] No verified future chapter was found.")
        return False

    print(
        "[DETECT] Verified chapters: "
        + ", ".join(str(number) for number in sorted(verified))
    )

    latest_contiguous = get_contiguous_latest(verified, stored)
    print(f"[DETECT] Contiguous latest: {latest_contiguous}")

    if latest_contiguous <= stored:
        print(
            "[DETECT] Candidates exist, but no verified contiguous "
            "chapter follows the stored chapter."
        )
        return False

    changed = False

    for chapter_number in range(stored + 1, latest_contiguous + 1):
        candidate = verified.get(chapter_number)

        if not candidate:
            print(f"[STOP] Chapter {chapter_number} is not verified.")
            break

        # التحقق من الصيانة قبل كل إرسال؛ cache قصيرة تقلل طلبات GitHub.
        if maintenance_still_enabled():
            print("[STOP] Maintenance mode is active.")
            break

        print(
            f"[NEW] Confirmed chapter {chapter_number}: {candidate.url}"
        )

        message = chapter_message(name, chapter_number, candidate.url)
        success = send_telegram_message(chat_id, message)

        if not success:
            print(
                f"[STOP] Telegram failed for chapter {chapter_number}. "
                "State will NOT advance."
            )
            break

        # لا تتغير الحالة إلا بعد نجاح Telegram.
        work["last_chapter"] = chapter_number
        changed = True

        print(
            f"[STATE] {name}: last_chapter updated to {chapter_number}"
        )

        time.sleep(0.5)

    return changed


# ============================================================
# MAIN
# ============================================================

def main():
    print("[START] Chapter monitor started.")

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")

    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not configured.")

    run_started = time.perf_counter()
    data, sha = load_data()
    original_data = clone_data(data)

    if is_maintenance_enabled(data):
        print("[MAINTENANCE] Maintenance mode is enabled.")
        print("[MAINTENANCE] Monitoring and notifications are paused.")
        print("[DONE] No database changes were required.")
        return

    users = get_users(data)

    if isinstance(users, dict):
        iterable = list(users.items())

    elif isinstance(users, list):
        iterable = []
        for user in users:
            if not isinstance(user, dict):
                continue

            chat_id = user.get("chat_id") or user.get("id")
            if chat_id is not None:
                iterable.append((str(chat_id), user))

    else:
        print("[ERROR] Unsupported users format.")
        return

    changed = False

    for chat_id, user in iterable:
        chat_id = str(chat_id)
        works = get_user_works(user)

        print(f"[USER] {chat_id}: {len(works)} work(s)")

        for work in works:
            try:
                if monitor_work(chat_id, work):
                    changed = True
            except Exception as exc:
                print(
                    f"[ERROR] Work monitoring failed for user "
                    f"{chat_id}: {exc}"
                )

    if changed:
        try:
            save_data_with_retry(data, original_data, sha)
        except Exception as exc:
            print("[GITHUB] FINAL SAVE ERROR:")
            print(f"[GITHUB] {exc}")
            raise

        print("[DONE] Database changes saved.")
    else:
        print("[DONE] No database changes were required.")

    print(
        f"[TIMING] Total run time: "
        f"{time.perf_counter() - run_started:.2f}s"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
