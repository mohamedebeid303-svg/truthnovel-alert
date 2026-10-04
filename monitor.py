import os
import re
import json
import base64
import time
import html as html_module
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, parse_qs, quote

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
    if TELEGRAM_BOT_TOKEN
    else None
)

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3

# أقصى عدد صفحات يمكن فحصها أثناء البحث
MAX_CRAWL_PAGES = 35

# أقصى عدد فصول مستقبلية نفحصها
MAX_AHEAD = 20

# لا نعتمد على أرقام موجودة داخل النص العادي
USE_BODY_NUMBERS = False


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
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
    }
)


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

    url = url.strip()

    parsed = urlparse(url)

    # إزالة fragment
    parsed = parsed._replace(fragment="")

    return parsed.geturl()


def same_domain(url_a, url_b):
    try:
        a = urlparse(url_a).netloc.lower()
        b = urlparse(url_b).netloc.lower()

        if a.startswith("www."):
            a = a[4:]

        if b.startswith("www."):
            b = b[4:]

        return a == b
    except Exception:
        return False


def absolute_url(base_url, href):
    if not href:
        return ""

    href = html_module.unescape(href.strip())

    if href.startswith("#"):
        return ""

    return normalize_url(urljoin(base_url, href))


# ============================================================
# CHAPTER NUMBER EXTRACTION
# ============================================================

# مهم جدًا:
# الموقع يستخدم روابط من الشكل:
# /2479-تطورات-الأوضاع/
#
# لذلك يجب السماح بالـ "-" بعد الرقم.

URL_PATTERNS = [
    # /2479-عنوان-الفصل
    re.compile(r"/(\d{1,7})(?:[-_/?.#]|$)", re.I),

    # chapter-2479
    re.compile(r"(?:chapter|chap|episode)[-_ /]*(\d{1,7})", re.I),

    # /chapter/2479
    re.compile(r"/chapter[s]?/(\d{1,7})(?:[-_/?.#]|$)", re.I),

    # ?chapter=2479
    re.compile(r"[?&](?:chapter|chap|episode)=(\d{1,7})", re.I),

    # chapter_2479
    re.compile(r"(?:chapter|chap|episode)[_-](\d{1,7})", re.I),
]


TITLE_PATTERNS = [
    # 2479 - تطورات الأوضاع
    re.compile(r"^\s*(\d{1,7})\s*[-–—:]\s*.+$", re.I),

    # الفصل 2479
    re.compile(
        r"(?:الفصل|فصل|chapter|chap|episode)\s*#?\s*(\d{1,7})",
        re.I,
    ),

    # Chapter 2479
    re.compile(r"^\s*chapter\s*#?\s*(\d{1,7})\b", re.I),

    # 2479 فقط
    re.compile(r"^\s*(\d{1,7})\s*$", re.I),
]


def extract_number_from_url(url):
    if not url:
        return None

    url = normalize_digits(url)

    # تجاهل أرقام الدومين أو أرقام لا علاقة لها بمسار الفصل
    parsed = urlparse(url)

    path = parsed.path or ""

    for pattern in URL_PATTERNS:
        match = pattern.search(path)

        if match:
            try:
                number = int(match.group(1))

                if 1 <= number <= 10_000_000:
                    return number
            except Exception:
                pass

    query = parse_qs(parsed.query)

    for key in ("chapter", "chap", "episode"):
        values = query.get(key)

        if values:
            match = re.search(r"\d{1,7}", values[0])

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
            except Exception:
                pass

    return None


def extract_explicit_chapter_number(text):
    """
    لا نعتبر أي رقم عادي فصلًا.
    يجب أن يكون الرقم في سياق واضح للفصل.
    """

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
            except Exception:
                pass

    return None


# ============================================================
# HTTP
# ============================================================

PERMANENT_STATUSES = {
    400,
    401,
    403,
    404,
    405,
    410,
    422,
}


def request_with_retry(
    url,
    method="GET",
    params=None,
    headers=None,
    allow_redirects=True,
):
    for attempt in range(1, MAX_RETRIES + 1):

        try:
            response = SESSION.request(
                method,
                url,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=allow_redirects,
            )

            if response.status_code == 200:
                return response

            if response.status_code in PERMANENT_STATUSES:
                print(
                    f"[HTTP] Permanent HTTP status "
                    f"{response.status_code} for {url}. No retry."
                )
                return response

            print(
                f"[HTTP] Status {response.status_code} "
                f"for {url} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )

        except requests.RequestException as exc:
            print(
                f"[HTTP] Request error for {url}: {exc} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )

        if attempt < MAX_RETRIES:
            time.sleep(1.5 * attempt)

    return None


def get_page(url):
    response = request_with_retry(url)

    if response is None:
        return None, ""

    if response.status_code != 200:
        return response, ""

    return response, response.text


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

    response = request_with_retry(
        url,
        headers=github_headers(),
    )

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


def save_data(data, sha):
    url = (
        f"https://api.github.com/repos/"
        f"{GITHUB_REPO}/contents/{DATA_FILE}"
    )

    content = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    )

    encoded = base64.b64encode(
        content.encode("utf-8")
    ).decode("ascii")

    payload = {
        "message": "Update chapter monitor data",
        "content": encoded,
        "sha": sha,
    }

    response = requests.put(
        url,
        headers=github_headers(),
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"GitHub save failed: "
            f"HTTP {response.status_code}: {response.text[:500]}"
        )

    print("[GITHUB] data.json saved successfully.")


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
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            print(
                f"[TELEGRAM] Failed: "
                f"HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
            return False

        result = response.json()

        if not result.get("ok"):
            print(f"[TELEGRAM] API error: {result}")
            return False

        return True

    except requests.RequestException as exc:
        print(f"[TELEGRAM] Request error: {exc}")
        return False


# ============================================================
# HTML / CHAPTER VERIFICATION
# ============================================================

def page_title(soup):
    title = soup.find("title")

    if title:
        return clean_text(title.get_text(" ", strip=True))

    return ""


def get_headings(soup):
    headings = []

    for tag in soup.find_all(
        ["h1", "h2", "h3", "article", "main"],
        limit=30,
    ):
        text = clean_text(tag.get_text(" ", strip=True))

        if text:
            headings.append(text)

    return headings


def verify_chapter_page(
    url,
    expected_number=None,
):
    """
    أهم دالة في النظام.

    لا يكفي أن يحتوي الرابط على 2484.
    يجب فتح الصفحة والتأكد أن الصفحة نفسها
    تثبت رقم الفصل في العنوان أو H1 أو breadcrumb
    أو في URL بصيغة فصل واضحة.
    """

    response, html = get_page(url)

    if response is None:
        return None

    if response.status_code != 200:
        return None

    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    title = page_title(soup)
    headings = get_headings(soup)

    # --------------------------------------------------------
    # 1. رقم الفصل من العنوان
    # --------------------------------------------------------

    title_number = extract_number_from_title(title)

    # --------------------------------------------------------
    # 2. رقم الفصل من H1/H2/H3/article/main
    # --------------------------------------------------------

    heading_numbers = []

    for heading in headings:
        number = extract_number_from_title(heading)

        if number is None:
            number = extract_explicit_chapter_number(heading)

        if number is not None:
            heading_numbers.append(number)

    # --------------------------------------------------------
    # 3. رقم الفصل من الرابط
    # --------------------------------------------------------

    url_number = extract_number_from_url(url)

    # --------------------------------------------------------
    # القرار
    # --------------------------------------------------------

    strong_numbers = []

    if title_number is not None:
        strong_numbers.append(title_number)

    strong_numbers.extend(heading_numbers)

    # إذا كان لدينا رقم متوقع، نحتاج تطابقًا قويًا.
    if expected_number is not None:

        # العنوان/H1 هو أقوى دليل
        if expected_number in strong_numbers:
            return {
                "number": expected_number,
                "url": normalize_url(url),
                "title": title,
                "evidence": (
                    f"title/headings confirm {expected_number}"
                ),
            }

        # إذا لم يظهر في العنوان ولكن الرابط نفسه
        # واضح جدًا للفصل، لا نقبله إلا إذا كان
        # الصفحة تحتوي على كلمة فصل.
        if url_number == expected_number:

            combined = " ".join(
                [title] + headings
            ).lower()

            chapter_words = (
                "chapter",
                "chap",
                "episode",
                "الفصل",
                "فصل",
            )

            if any(word in combined for word in chapter_words):
                return {
                    "number": expected_number,
                    "url": normalize_url(url),
                    "title": title,
                    "evidence": (
                        f"URL + chapter wording confirm "
                        f"{expected_number}"
                    ),
                }

        return None

    # إذا لم يكن لدينا رقم متوقع:
    # لا نقبل الصفحة إلا بوجود دليل قوي.
    if strong_numbers:
        unique = set(strong_numbers)

        if len(unique) == 1:
            number = next(iter(unique))

            if 1 <= number <= 10_000_000:
                return {
                    "number": number,
                    "url": normalize_url(url),
                    "title": title,
                    "evidence": (
                        f"verified by title/headings"
                    ),
                }

    # URL واضح + كلمة chapter في الصفحة
    if url_number is not None:

        combined = " ".join(
            [title] + headings
        ).lower()

        chapter_words = (
            "chapter",
            "chap",
            "episode",
            "الفصل",
            "فصل",
        )

        if any(word in combined for word in chapter_words):
            return {
                "number": url_number,
                "url": normalize_url(url),
                "title": title,
                "evidence": "verified by URL + chapter wording",
            }

    return None


# ============================================================
# LINK ANALYSIS
# ============================================================

def analyze_links(page_url, soup):
    candidates = []

    for link in soup.find_all("a", href=True):

        href = link.get("href", "").strip()

        if not href:
            continue

        url = absolute_url(page_url, href)

        if not url:
            continue

        if not same_domain(page_url, url):
            continue

        text = clean_text(
            link.get_text(" ", strip=True)
        )

        aria = clean_text(
            link.get("aria-label", "")
        )

        title = clean_text(
            link.get("title", "")
        )

        combined = " ".join(
            x for x in [text, aria, title]
            if x
        )

        number_from_text = extract_explicit_chapter_number(
            combined
        )

        number_from_url = extract_number_from_url(url)

        number = number_from_text

        if number is None:
            number = number_from_url

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

        if any(
            word in lower
            for word in (
                "chapter",
                "الفصل",
                "فصل",
                "episode",
            )
        ):
            score += 40
            evidence.append("chapter keyword")

        # روابط التنقل بين الفصول
        if any(
            word in lower
            for word in (
                "السابق",
                "الموضوع السابق",
                "previous",
                "prev",
                "التالي",
                "الموضوع التالي",
                "next",
            )
        ):
            score += 30
            evidence.append("chapter navigation")

        candidates.append(
            ChapterCandidate(
                number=number,
                url=url,
                score=score,
                source="link",
                evidence=", ".join(evidence),
            )
        )

    return candidates


# ============================================================
# PAGE CRAWLING
# ============================================================

def inspect_page_for_candidates(
    url,
    expected_number=None,
):
    response, html = get_page(url)

    if response is None:
        return [], None, ""

    if response.status_code != 200:
        return [], None, ""

    if not html:
        return [], None, ""

    soup = BeautifulSoup(html, "html.parser")

    candidates = []

    # الصفحة نفسها
    verified = verify_chapter_page(
        url,
        expected_number=expected_number,
    )

    if verified:
        candidates.append(
            ChapterCandidate(
                number=verified["number"],
                url=verified["url"],
                score=1000,
                source="verified_page",
                evidence=verified["evidence"],
            )
        )

    # الروابط
    candidates.extend(
        analyze_links(url, soup)
    )

    return candidates, soup, html


def crawl_chapter_pages(
    start_url,
    old_chapter,
):
    """
    يبدأ من الصفحة الرئيسية/الصفحة المكتشفة
    ويبحث عن صفحات الفصول الحقيقية.

    لا يستخدم أرقام body العشوائية.
    """

    queue = [start_url]
    visited = set()

    verified = {}

    while queue and len(visited) < MAX_CRAWL_PAGES:

        current = queue.pop(0)

        current = normalize_url(current)

        if not current:
            continue

        if current in visited:
            continue

        visited.add(current)

        print(f"[CRAWL] {current}")

        candidates, soup, html = (
            inspect_page_for_candidates(current)
        )

        # ----------------------------------------------------
        # تحقق من المرشحين الموجودين في الروابط
        # ----------------------------------------------------

        for candidate in candidates:

            if candidate.number <= old_chapter:
                continue

            if candidate.number > old_chapter + MAX_AHEAD:
                # لا نقبل قفزة ضخمة
                continue

            # إذا كان المرشح نفسه صفحة الفصل
            result = verify_chapter_page(
                candidate.url,
                expected_number=candidate.number,
            )

            if result:
                number = result["number"]

                if number <= old_chapter:
                    continue

                previous = verified.get(number)

                new_candidate = ChapterCandidate(
                    number=number,
                    url=result["url"],
                    score=1200,
                    source="verified_link",
                    evidence=result["evidence"],
                )

                if (
                    previous is None
                    or new_candidate.score > previous.score
                ):
                    verified[number] = new_candidate

                # نضيف صفحة الفصل نفسها إلى قائمة الزحف
                if candidate.url not in visited:
                    queue.append(candidate.url)

        # ----------------------------------------------------
        # نبحث أيضًا عن روابط السابق/التالي
        # ----------------------------------------------------

        if soup:

            for link in soup.find_all(
                "a",
                href=True,
            ):

                href = link.get("href", "").strip()

                if not href:
                    continue

                url = absolute_url(
                    current,
                    href,
                )

                if not url:
                    continue

                if not same_domain(
                    start_url,
                    url,
                ):
                    continue

                text = clean_text(
                    link.get_text(
                        " ",
                        strip=True,
                    )
                ).lower()

                title = clean_text(
                    link.get(
                        "title",
                        "",
                    )
                ).lower()

                aria = clean_text(
                    link.get(
                        "aria-label",
                        "",
                    )
                ).lower()

                combined = " ".join(
                    [text, title, aria]
                )

                number = (
                    extract_explicit_chapter_number(
                        combined
                    )
                    or extract_number_from_url(url)
                )

                is_navigation = any(
                    word in combined
                    for word in (
                        "السابق",
                        "الموضوع السابق",
                        "previous",
                        "prev",
                        "التالي",
                        "الموضوع التالي",
                        "next",
                    )
                )

                if is_navigation and url not in visited:
                    queue.insert(0, url)

                elif (
                    number is not None
                    and old_chapter < number <= old_chapter + MAX_AHEAD
                    and url not in visited
                ):
                    queue.append(url)

    return verified


# ============================================================
# WORDPRESS
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

    if response is None:
        return None

    if response.status_code != 200:
        return None

    try:
        return response.json()
    except Exception:
        return None


def wordpress_candidates(site_url, old_chapter):
    parsed = urlparse(site_url)

    base = f"{parsed.scheme}://{parsed.netloc}"

    candidates = []

    # --------------------------------------------------------
    # REST API: posts
    # --------------------------------------------------------

    endpoints = [
        "/wp-json/wp/v2/posts",
        "/wp-json/wp/v2/pages",
    ]

    for endpoint in endpoints:

        url = base + endpoint

        data = wordpress_json(
            url,
            params={
                "per_page": 100,
                "orderby": "date",
                "order": "desc",
                "_fields": "id,date,link,title,slug",
            },
        )

        if not isinstance(data, list):
            continue

        for item in data:

            link = item.get("link", "")

            title_obj = item.get("title") or {}

            title = clean_text(
                title_obj.get("rendered", "")
            )

            number = (
                extract_number_from_title(title)
                or extract_explicit_chapter_number(title)
                or extract_number_from_url(link)
            )

            if number is None:
                continue

            if not (
                old_chapter
                < number
                <= old_chapter + MAX_AHEAD
            ):
                continue

            candidates.append(
                (number, link)
            )

    # --------------------------------------------------------
    # WordPress search لكل فصل متوقع
    # --------------------------------------------------------

    search_url = base + "/wp-json/wp/v2/search"

    for number in range(
        old_chapter + 1,
        old_chapter + MAX_AHEAD + 1,
    ):

        data = wordpress_json(
            search_url,
            params={
                "search": str(number),
                "per_page": 20,
                "_fields": "id,title,url,type",
            },
        )

        if not isinstance(data, list):
            continue

        for item in data:

            link = item.get("url", "")

            title_obj = item.get("title") or {}

            title = clean_text(
                title_obj.get("rendered", "")
            )

            found = (
                extract_number_from_title(title)
                or extract_explicit_chapter_number(title)
                or extract_number_from_url(link)
            )

            if found != number:
                continue

            candidates.append(
                (number, link)
            )

    # --------------------------------------------------------
    # تحقق من جميع النتائج
    # --------------------------------------------------------

    verified = {}

    for number, link in candidates:

        if not link:
            continue

        result = verify_chapter_page(
            link,
            expected_number=number,
        )

        if not result:
            continue

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
    parsed = urlparse(site_url)

    base = f"{parsed.scheme}://{parsed.netloc}"

    feed_urls = [
        urljoin(base, "/feed/"),
        urljoin(base, "/rss/"),
        urljoin(base, "/feed"),
        urljoin(base, "/rss"),
    ]

    verified = {}

    for feed_url in feed_urls:

        response = request_with_retry(feed_url)

        if response is None:
            continue

        if response.status_code != 200:
            continue

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        # حتى إذا كان السيرفر لا يرسل XML بشكل صحيح
        # نحاول تحليله طالما أنه 200.
        try:
            soup = BeautifulSoup(
                response.text,
                "xml",
            )
        except Exception:
            continue

        for item in soup.find_all(
            ["item", "entry"]
        ):

            title_tag = item.find("title")

            if not title_tag:
                continue

            title = clean_text(
                title_tag.get_text(
                    " ",
                    strip=True,
                )
            )

            number = (
                extract_number_from_title(title)
                or extract_explicit_chapter_number(title)
            )

            if number is None:
                continue

            if not (
                old_chapter
                < number
                <= old_chapter + MAX_AHEAD
            ):
                continue

            link = ""

            link_tag = item.find("link")

            if link_tag:

                if link_tag.get("href"):
                    link = link_tag.get("href")
                else:
                    link = link_tag.get_text(
                        strip=True
                    )

            link = absolute_url(
                site_url,
                link,
            )

            if not link:
                continue

            result = verify_chapter_page(
                link,
                expected_number=number,
            )

            if result:
                verified[number] = ChapterCandidate(
                    number=number,
                    url=result["url"],
                    score=1400,
                    source="feed_verified",
                    evidence=result["evidence"],
                )

    return verified


# ============================================================
# SITEMAP
# ============================================================

def sitemap_candidates(site_url, old_chapter):
    parsed = urlparse(site_url)

    base = f"{parsed.scheme}://{parsed.netloc}"

    sitemap_urls = [
        urljoin(base, "/sitemap.xml"),
        urljoin(base, "/wp-sitemap.xml"),
        urljoin(base, "/sitemap_index.xml"),
        urljoin(base, "/post-sitemap.xml"),
    ]

    urls_to_check = list(sitemap_urls)

    visited = set()

    verified = {}

    while urls_to_check and len(visited) < 10:

        sitemap_url = urls_to_check.pop(0)

        if sitemap_url in visited:
            continue

        visited.add(sitemap_url)

        response = request_with_retry(
            sitemap_url
        )

        if response is None:
            continue

        if response.status_code != 200:
            continue

        try:
            soup = BeautifulSoup(
                response.text,
                "xml",
            )
        except Exception:
            continue

        # sitemap index
        for loc in soup.find_all("loc"):

            value = clean_text(
                loc.get_text(
                    " ",
                    strip=True,
                )
            )

            if not value:
                continue

            number = extract_number_from_url(
                value
            )

            if number is not None:
                if (
                    old_chapter
                    < number
                    <= old_chapter + MAX_AHEAD
                ):
                    result = verify_chapter_page(
                        value,
                        expected_number=number,
                    )

                    if result:
                        verified[number] = ChapterCandidate(
                            number=number,
                            url=result["url"],
                            score=1300,
                            source="sitemap_verified",
                            evidence=result["evidence"],
                        )

            elif value.endswith(".xml"):
                if value not in visited:
                    urls_to_check.append(value)

    return verified


# ============================================================
# DISCOVER VERIFIED CHAPTERS
# ============================================================

def discover_verified_chapters(
    site_url,
    old_chapter,
):
    all_verified = {}

    def merge(source):
        for number, candidate in source.items():

            if number <= old_chapter:
                continue

            if number > old_chapter + MAX_AHEAD:
                continue

            previous = all_verified.get(number)

            if (
                previous is None
                or candidate.score > previous.score
            ):
                all_verified[number] = candidate

    # --------------------------------------------------------
    # 1. الصفحة الرئيسية
    # --------------------------------------------------------

    print("[DISCOVER] Inspecting main page...")

    crawl_results = crawl_chapter_pages(
        site_url,
        old_chapter,
    )

    merge(crawl_results)

    # --------------------------------------------------------
    # 2. WordPress
    # --------------------------------------------------------

    print("[DISCOVER] Checking WordPress API...")

    try:
        merge(
            wordpress_candidates(
                site_url,
                old_chapter,
            )
        )
    except Exception as exc:
        print(
            f"[WORDPRESS] Error: {exc}"
        )

    # --------------------------------------------------------
    # 3. RSS
    # --------------------------------------------------------

    print("[DISCOVER] Checking RSS...")

    try:
        merge(
            feed_candidates(
                site_url,
                old_chapter,
            )
        )
    except Exception as exc:
        print(
            f"[RSS] Error: {exc}"
        )

    # --------------------------------------------------------
    # 4. Sitemap
    # --------------------------------------------------------

    print("[DISCOVER] Checking sitemap...")

    try:
        merge(
            sitemap_candidates(
                site_url,
                old_chapter,
            )
        )
    except Exception as exc:
        print(
            f"[SITEMAP] Error: {exc}"
        )

    return all_verified


# ============================================================
# CONTIGUOUS CHAPTER CHECK
# ============================================================

def get_contiguous_latest(
    verified,
    old_chapter,
):
    """
    لا نسمح بالقفز.

    مثال:

    verified = 2480, 2481, 2482
    old = 2479

    النتيجة = 2482

    أما:

    verified = 2484 فقط
    old = 2479

    النتيجة = 2479

    وبالتالي 2484 لن يتم اعتباره فصلًا جديدًا.
    """

    current = old_chapter

    while True:

        next_number = current + 1

        if next_number not in verified:
            break

        current = next_number

    return current


# ============================================================
# MESSAGE
# ============================================================

def chapter_message(
    work_name,
    chapter_number,
    chapter_url,
):
    return (
        "🔔 <b>فصل جديد!</b>\n\n"
        f"📖 <b>{html_module.escape(work_name)}</b>\n"
        f"📚 <b>الفصل {chapter_number}</b>\n\n"
        f"🔗 {html_module.escape(chapter_url)}"
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

    if isinstance(works, list):
        return works

    return []


# ============================================================
# MONITOR ONE WORK
# ============================================================

def monitor_work(
    chat_id,
    work,
):
    if not isinstance(work, dict):
        return False

    name = (
        work.get("name")
        or work.get("title")
        or "Unknown"
    )

    url = (
        work.get("url")
        or work.get("link")
    )

    if not url:
        print(
            f"[SKIP] {name}: no URL."
        )
        return False

    try:
        stored = int(
            work.get(
                "last_chapter",
                0,
            )
        )
    except Exception:
        stored = 0

    print(
        f"[MONITOR] Checking {name} | "
        f"stored={stored} | {url}"
    )

    verified = discover_verified_chapters(
        url,
        stored,
    )

    if not verified:
        print(
            "[DETECT] No verified future "
            "chapter was found."
        )
        return False

    print(
        "[DETECT] Verified chapters: "
        + ", ".join(
            str(x)
            for x in sorted(verified)
        )
    )

    # --------------------------------------------------------
    # أهم خطوة:
    # نأخذ فقط التسلسل المتصل.
    # --------------------------------------------------------

    latest_contiguous = get_contiguous_latest(
        verified,
        stored,
    )

    print(
        f"[DETECT] Contiguous latest: "
        f"{latest_contiguous}"
    )

    if latest_contiguous <= stored:

        # مثال:
        # stored 2479
        # verified 2484
        #
        # لن يتم إرسال شيء.

        print(
            "[DETECT] Future candidates exist, "
            "but none forms a valid contiguous "
            "chapter after the stored chapter."
        )

        return False

    changed = False

    # --------------------------------------------------------
    # إرسال كل فصل بالترتيب
    # --------------------------------------------------------

    for chapter_number in range(
        stored + 1,
        latest_contiguous + 1,
    ):

        candidate = verified.get(
            chapter_number
        )

        if not candidate:
            print(
                f"[STOP] Chapter "
                f"{chapter_number} is not verified."
            )
            break

        print(
            f"[NEW] Confirmed chapter "
            f"{chapter_number}: "
            f"{candidate.url}"
        )

        message = chapter_message(
            name,
            chapter_number,
            candidate.url,
        )

        success = send_telegram_message(
            chat_id,
            message,
        )

        if not success:

            print(
                f"[STOP] Telegram failed for "
                f"chapter {chapter_number}. "
                f"State will NOT advance."
            )

            break

        # لا نغير last_chapter إلا بعد
        # نجاح إرسال الإشعار.
        work["last_chapter"] = chapter_number

        changed = True

        print(
            f"[STATE] {name}: "
            f"last_chapter updated to "
            f"{chapter_number}"
        )

        # حماية بسيطة من rate limits
        time.sleep(0.5)

    return changed


# ============================================================
# MAIN
# ============================================================

def main():
    print(
        "[START] Chapter monitor started."
    )

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

    if not GITHUB_TOKEN:
        raise RuntimeError(
            "GITHUB_TOKEN is not configured."
        )

    data, sha = load_data()

    users = get_users(data)

    if isinstance(users, dict):

        iterable = users.items()

    elif isinstance(users, list):

        iterable = []

        for user in users:

            if not isinstance(user, dict):
                continue

            chat_id = (
                user.get("chat_id")
                or user.get("id")
            )

            if chat_id is not None:
                iterable.append(
                    (str(chat_id), user)
                )

    else:
        print(
            "[ERROR] Unsupported users format."
        )
        return

    changed = False

    for chat_id, user in iterable:

        chat_id = str(chat_id)

        works = get_user_works(user)

        print(
            f"[USER] {chat_id}: "
            f"{len(works)} work(s)"
        )

        for work in works:

            try:

                work_changed = monitor_work(
                    chat_id,
                    work,
                )

                if work_changed:
                    changed = True

            except Exception as exc:

                print(
                    f"[ERROR] Work monitoring failed "
                    f"for user {chat_id}: {exc}"
                )

    if changed:

        save_data(
            data,
            sha,
        )

        print(
            "[DONE] Database changes saved."
        )

    else:

        print(
            "[DONE] No database changes were required."
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()