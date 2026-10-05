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
    if TELEGRAM_BOT_TOKEN
    else None
)

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3

# أقصى عدد صفحات يمكن فحصها أثناء الزحف
MAX_CRAWL_PAGES = 35

# أقصى عدد فصول مستقبلية يمكن البحث عنها
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
# RUNTIME CACHE
# ============================================================

# يمنع إعادة فحص نفس الصفحة عدة مرات
PAGE_CACHE = {}

# يخزن نتائج التحقق من الروابط أثناء التشغيل الحالي
VERIFY_CACHE = {}


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

    return normalize_url(
        urljoin(base_url, href)
    )


# ============================================================
# CHAPTER NUMBER EXTRACTION
# ============================================================

URL_PATTERNS = [

    # /2479-عنوان-الفصل
    re.compile(
        r"/(\d{1,7})(?:[-_/?.#]|$)",
        re.I,
    ),

    # chapter-2479
    re.compile(
        r"(?:chapter|chap|episode)[-_ /]*(\d{1,7})",
        re.I,
    ),

    # /chapter/2479
    re.compile(
        r"/chapter[s]?/(\d{1,7})(?:[-_/?.#]|$)",
        re.I,
    ),

    # ?chapter=2479
    re.compile(
        r"[?&](?:chapter|chap|episode)=(\d{1,7})",
        re.I,
    ),

    # chapter_2479
    re.compile(
        r"(?:chapter|chap|episode)[_-](\d{1,7})",
        re.I,
    ),
]


TITLE_PATTERNS = [

    # 2479 - تطورات الأوضاع
    re.compile(
        r"^\s*(\d{1,7})\s*[-–—:]\s*.+$",
        re.I,
    ),

    # الفصل 2479
    re.compile(
        r"(?:الفصل|فصل|chapter|chap|episode)"
        r"\s*#?\s*(\d{1,7})",
        re.I,
    ),

    # Chapter 2479
    re.compile(
        r"^\s*chapter\s*#?\s*(\d{1,7})\b",
        re.I,
    ),

    # 2479 فقط
    re.compile(
        r"^\s*(\d{1,7})\s*$",
        re.I,
    ),
]


def extract_number_from_url(url):
    if not url:
        return None

    url = normalize_digits(url)
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

    for key in (
        "chapter",
        "chap",
        "episode",
    ):

        values = query.get(key)

        if values:

            match = re.search(
                r"\d{1,7}",
                values[0],
            )

            if match:

                number = int(
                    match.group()
                )

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

        r"(?:الفصل|فصل)"
        r"\s*#?\s*(\d{1,7})",

        r"(?:chapter|chap|episode)"
        r"\s*#?\s*(\d{1,7})",

        r"^\s*(\d{1,7})"
        r"\s*[-–—:]\s*.+$",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I,
        )

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

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

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
                    f"{response.status_code} for {url}. "
                    f"No retry."
                )

                return response

            print(
                f"[HTTP] Status "
                f"{response.status_code} "
                f"for {url} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )

        except requests.RequestException as exc:

            print(
                f"[HTTP] Request error for {url}: "
                f"{exc} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )

        if attempt < MAX_RETRIES:

            time.sleep(
                1.5 * attempt
            )

    return None


def get_page(url):

    url = normalize_url(url)

    if not url:
        return None, ""

    if url in PAGE_CACHE:

        cached = PAGE_CACHE[url]

        return (
            cached["response"],
            cached["html"],
        )

    response = request_with_retry(url)

    if response is None:

        PAGE_CACHE[url] = {
            "response": None,
            "html": "",
        }

        return None, ""

    if response.status_code != 200:

        PAGE_CACHE[url] = {
            "response": response,
            "html": "",
        }

        return response, ""

    html = response.text

    PAGE_CACHE[url] = {
        "response": response,
        "html": html,
    }

    return response, html


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


def detect_protection_page(
    soup,
    html,
):
    title = page_title(soup)

    title_lower = title.lower()

    for marker in PROTECTION_TITLES:

        if marker in title_lower:

            return True, (
                f"title contains protection marker: "
                f"{marker}"
            )

    sample = clean_text(
        html[:100000]
    ).lower()

    marker_hits = 0
    matched = []

    for marker in PROTECTION_MARKERS:

        if marker in sample:

            marker_hits += 1
            matched.append(marker)

    # لا نعتبر كلمة واحدة مثل cloudflare
    # كافية وحدها دائمًا.
    if marker_hits >= 2:

        return True, (
            "multiple protection markers: "
            + ", ".join(matched[:5])
        )

    # حالات Cloudflare الواضحة
    if (
        "cf-chl-" in sample
        or "challenge-platform" in sample
    ):

        return True, (
            "Cloudflare challenge detected"
        )

    return False, ""


# ============================================================
# GITHUB
# ============================================================

def github_headers():

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "truthnovel-alert-monitor",
    }

    if GITHUB_TOKEN:

        headers["Authorization"] = (
            f"Bearer {GITHUB_TOKEN}"
        )

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

        raise RuntimeError(
            "Unable to contact GitHub."
        )

    if response.status_code != 200:

        raise RuntimeError(
            f"GitHub load failed: "
            f"HTTP {response.status_code}"
        )

    payload = response.json()

    encoded = payload.get(
        "content",
        "",
    )

    sha = payload.get(
        "sha"
    )

    if not encoded:

        raise RuntimeError(
            "GitHub returned empty data.json."
        )

    content = base64.b64decode(
        encoded
    ).decode("utf-8")

    data = json.loads(
        content
    )

    print(
        "[GITHUB] data.json loaded successfully."
    )

    return data, sha


def save_data(
    data,
    sha,
):

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

    if response.status_code not in (
        200,
        201,
    ):

        raise RuntimeError(
            f"GitHub save failed: "
            f"HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    print(
        "[GITHUB] data.json saved successfully."
    )


# ============================================================
# MAINTENANCE MODE
# ============================================================

def is_maintenance_enabled(data):
    """
    التحقق من وضع الصيانة.

    إذا كان:
        settings.maintenance = true

    يتوقف monitor.py بالكامل قبل فحص أي رواية
    أو إرسال أي إشعار أو تعديل last_chapter.
    """

    settings = data.get(
        "settings"
    )

    if not isinstance(
        settings,
        dict,
    ):

        return False

    return bool(
        settings.get(
            "maintenance",
            False,
        )
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(
    chat_id,
    text,
):

    if not TELEGRAM_API:

        print(
            "[TELEGRAM] "
            "TELEGRAM_BOT_TOKEN is missing."
        )

        return False

    url = (
        f"{TELEGRAM_API}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
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

            print(
                f"[TELEGRAM] API error: "
                f"{result}"
            )

            return False

        return True

    except requests.RequestException as exc:

        print(
            f"[TELEGRAM] Request error: {exc}"
        )

        return False


# ============================================================
# HTML HELPERS
# ============================================================

def page_title(soup):

    title = soup.find("title")

    if title:

        return clean_text(
            title.get_text(
                " ",
                strip=True,
            )
        )

    return ""


def get_h1_texts(soup):

    results = []

    for tag in soup.find_all(
        "h1",
        limit=10,
    ):

        text = clean_text(
            tag.get_text(
                " ",
                strip=True,
            )
        )

        if text:
            results.append(text)

    return results


def get_h2_h3_texts(soup):

    results = []

    for tag in soup.find_all(
        ["h2", "h3"],
        limit=20,
    ):

        text = clean_text(
            tag.get_text(
                " ",
                strip=True,
            )
        )

        if text:
            results.append(text)

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

            elements = soup.select(
                selector
            )

        except Exception:
            continue

        for element in elements:

            text = clean_text(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if text:
                results.append(text)

    return results[:10]


# ============================================================
# CHAPTER PAGE VERIFICATION
# ============================================================

def verify_chapter_page(
    url,
    expected_number=None,
):
    """
    التحقق الصارم من صفحة الفصل.

    القاعدة الأساسية:

    URL وحده لا يثبت أن الصفحة فصل.

    كلمة chapter في الصفحة لا تثبت ذلك.

    يجب أن يوجد دليل مباشر في:
        1. title
        2. H1
        3. عنوان فصل واضح في H2/H3

    Breadcrumb يمكن استخدامه كدليل مساعد.

    لا نعتمد على article/main/body كدليل أساسي.
    """

    url = normalize_url(url)

    if not url:
        return None

    cache_key = (
        url,
        expected_number,
    )

    if cache_key in VERIFY_CACHE:

        cached = VERIFY_CACHE[
            cache_key
        ]

        return cached

    # --------------------------------------------------------
    # فتح الصفحة
    # --------------------------------------------------------

    response, html = get_page(url)

    if response is None:

        print(
            f"[VERIFY] Chapter "
            f"{expected_number}: "
            f"REQUEST FAILED"
        )

        VERIFY_CACHE[
            cache_key
        ] = None

        return None

    if response.status_code != 200:

        print(
            f"[VERIFY] Chapter "
            f"{expected_number}: "
            f"HTTP {response.status_code}"
        )

        VERIFY_CACHE[
            cache_key
        ] = None

        return None

    if not html:

        print(
            f"[VERIFY] Chapter "
            f"{expected_number}: "
            f"EMPTY PAGE"
        )

        VERIFY_CACHE[
            cache_key
        ] = None

        return None

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    title = page_title(
        soup
    )

    h1_texts = get_h1_texts(
        soup
    )

    h2_h3_texts = get_h2_h3_texts(
        soup
    )

    breadcrumb_texts = (
        get_breadcrumb_texts(
            soup
        )
    )

    url_number = (
        extract_number_from_url(
            url
        )
    )

    # --------------------------------------------------------
    # Log أساسي
    # --------------------------------------------------------

    print(
        f"[VERIFY] Chapter "
        f"{expected_number}"
    )

    print(
        f"[VERIFY] URL: {url}"
    )

    print(
        f"[VERIFY] Title: "
        f"{title or '(empty)'}"
    )

    if h1_texts:

        print(
            "[VERIFY] H1: "
            + " | ".join(
                h1_texts[:5]
            )
        )

    else:

        print(
            "[VERIFY] H1: (none)"
        )

    print(
        f"[VERIFY] URL Number: "
        f"{url_number}"
    )

    # --------------------------------------------------------
    # Protection detection
    # --------------------------------------------------------

    protected, reason = (
        detect_protection_page(
            soup,
            html,
        )
    )

    if protected:

        print(
            "[PROTECTION] "
            "Possible protection/challenge "
            "page detected."
        )

        print(
            f"[PROTECTION] Reason: {reason}"
        )

        print(
            "[VERIFY] RESULT: REJECTED"
        )

        print(
            "[VERIFY] REASON: "
            "Protection/challenge page"
        )

        VERIFY_CACHE[
            cache_key
        ] = None

        return None

    # --------------------------------------------------------
    # استخراج الأدلة القوية
    # --------------------------------------------------------

    evidence_candidates = []

    # TITLE
    title_number = (
        extract_number_from_title(
            title
        )
    )

    if title_number is not None:

        evidence_candidates.append(
            (
                title_number,
                "TITLE",
                title,
            )
        )

    # H1
    for h1 in h1_texts:

        number = (
            extract_number_from_title(
                h1
            )
        )

        if number is None:

            number = (
                extract_explicit_chapter_number(
                    h1
                )
            )

        if number is not None:

            evidence_candidates.append(
                (
                    number,
                    "H1",
                    h1,
                )
            )

    # H2/H3
    for heading in h2_h3_texts:

        number = (
            extract_number_from_title(
                heading
            )
        )

        if number is None:

            number = (
                extract_explicit_chapter_number(
                    heading
                )
            )

        if number is not None:

            evidence_candidates.append(
                (
                    number,
                    "H2/H3",
                    heading,
                )
            )

    # Breadcrumb
    for breadcrumb in breadcrumb_texts:

        number = (
            extract_number_from_title(
                breadcrumb
            )
        )

        if number is None:

            number = (
                extract_explicit_chapter_number(
                    breadcrumb
                )
            )

        if number is not None:

            evidence_candidates.append(
                (
                    number,
                    "BREADCRUMB",
                    breadcrumb,
                )
            )

    # --------------------------------------------------------
    # عرض الأدلة
    # --------------------------------------------------------

    if evidence_candidates:

        for number, source, value in (
            evidence_candidates[:10]
        ):

            print(
                f"[VERIFY] Evidence: "
                f"{source} -> {number} "
                f"| {value[:180]}"
            )

    else:

        print(
            "[VERIFY] Evidence: "
            "No explicit chapter number "
            "found in title/H1/headings/breadcrumb."
        )

    # --------------------------------------------------------
    # إذا كان لدينا expected_number
    # --------------------------------------------------------

    if expected_number is not None:

        matching = [
            item
            for item in evidence_candidates
            if item[0] == expected_number
        ]

        if matching:

            # نفضل TITLE ثم H1
            priority = {
                "TITLE": 100,
                "H1": 90,
                "H2/H3": 70,
                "BREADCRUMB": 50,
            }

            matching.sort(
                key=lambda item:
                priority.get(
                    item[1],
                    0,
                ),
                reverse=True,
            )

            number, source, value = (
                matching[0]
            )

            result = {
                "number": expected_number,
                "url": url,
                "title": title,
                "h1": (
                    h1_texts[0]
                    if h1_texts
                    else ""
                ),
                "evidence": (
                    f"{source} confirms "
                    f"{expected_number}"
                ),
            }

            print(
                "[VERIFY] RESULT: VERIFIED"
            )

            print(
                f"[VERIFY] EVIDENCE: {source}"
            )

            VERIFY_CACHE[
                cache_key
            ] = result

            return result

        # ----------------------------------------------------
        # مهم:
        # URL وحده لا يكفي.
        # ----------------------------------------------------

        if url_number == expected_number:

            print(
                "[VERIFY] URL matches expected "
                "number, but no Title/H1/"
                "heading/breadcrumb evidence "
                "confirms it."
            )

        print(
            "[VERIFY] RESULT: REJECTED"
        )

        print(
            "[VERIFY] REASON: "
            "Title/H1/heading/breadcrumb "
            "does not confirm expected chapter."
        )

        VERIFY_CACHE[
            cache_key
        ] = None

        return None

    # --------------------------------------------------------
    # لا يوجد expected_number
    # --------------------------------------------------------

    unique_numbers = set(
        item[0]
        for item in evidence_candidates
    )

    if len(unique_numbers) == 1:

        number = next(
            iter(unique_numbers)
        )

        result = {
            "number": number,
            "url": url,
            "title": title,
            "h1": (
                h1_texts[0]
                if h1_texts
                else ""
            ),
            "evidence": (
                "title/headings/breadcrumb "
                "confirm chapter"
            ),
        }

        print(
            "[VERIFY] RESULT: VERIFIED"
        )

        VERIFY_CACHE[
            cache_key
        ] = result

        return result

    print(
        "[VERIFY] RESULT: REJECTED"
    )

    print(
        "[VERIFY] REASON: "
        "No unique explicit chapter number."
    )

    VERIFY_CACHE[
        cache_key
    ] = None

    return None


# ============================================================
# LINK ANALYSIS
# ============================================================

def analyze_links(
    page_url,
    soup,
):
    candidates = []

    for link in soup.find_all(
        "a",
        href=True,
    ):

        href = link.get(
            "href",
            "",
        ).strip()

        if not href:
            continue

        url = absolute_url(
            page_url,
            href,
        )

        if not url:
            continue

        if not same_domain(
            page_url,
            url,
        ):
            continue

        text = clean_text(
            link.get_text(
                " ",
                strip=True,
            )
        )

        aria = clean_text(
            link.get(
                "aria-label",
                "",
            )
        )

        title = clean_text(
            link.get(
                "title",
                "",
            )
        )

        combined = " ".join(
            x
            for x in (
                text,
                aria,
                title,
            )
            if x
        )

        number_from_text = (
            extract_explicit_chapter_number(
                combined
            )
        )

        number_from_url = (
            extract_number_from_url(
                url
            )
        )

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

            evidence.append(
                "explicit chapter text"
            )

        if number_from_url is not None:

            score += 70

            evidence.append(
                "chapter-like URL"
            )

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

            evidence.append(
                "chapter keyword"
            )

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

            evidence.append(
                "chapter navigation"
            )

        candidates.append(
            ChapterCandidate(
                number=number,
                url=url,
                score=score,
                source="link",
                evidence=", ".join(
                    evidence
                ),
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

    response, html = get_page(
        url
    )

    if response is None:
        return [], None, ""

    if response.status_code != 200:
        return [], None, ""

    if not html:
        return [], None, ""

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    protected, reason = (
        detect_protection_page(
            soup,
            html,
        )
    )

    if protected:

        print(
            f"[PROTECTION] {url}"
        )

        print(
            f"[PROTECTION] Reason: {reason}"
        )

        return [], soup, html

    candidates = []

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

    candidates.extend(
        analyze_links(
            url,
            soup,
        )
    )

    return (
        candidates,
        soup,
        html,
    )


def crawl_chapter_pages(
    start_url,
    old_chapter,
):

    queue = [
        normalize_url(
            start_url
        )
    ]

    visited = set()
    verified = {}

    while (
        queue
        and len(visited)
        < MAX_CRAWL_PAGES
    ):

        current = queue.pop(0)

        current = normalize_url(
            current
        )

        if not current:
            continue

        if current in visited:
            continue

        visited.add(current)

        print(
            f"[CRAWL] "
            f"{current}"
        )

        candidates, soup, html = (
            inspect_page_for_candidates(
                current
            )
        )

        for candidate in candidates:

            if (
                candidate.number
                <= old_chapter
            ):
                continue

            if (
                candidate.number
                > old_chapter + MAX_AHEAD
            ):
                continue

            result = verify_chapter_page(
                candidate.url,
                expected_number=(
                    candidate.number
                ),
            )

            if result:

                number = result[
                    "number"
                ]

                if (
                    number
                    <= old_chapter
                ):
                    continue

                previous = verified.get(
                    number
                )

                new_candidate = (
                    ChapterCandidate(
                        number=number,
                        url=result["url"],
                        score=1200,
                        source="verified_link",
                        evidence=result[
                            "evidence"
                        ],
                    )
                )

                if (
                    previous is None
                    or new_candidate.score
                    > previous.score
                ):

                    verified[
                        number
                    ] = new_candidate

                if (
                    candidate.url
                    not in visited
                ):

                    queue.append(
                        candidate.url
                    )

        # ----------------------------------------------------
        # السابق / التالي
        # ----------------------------------------------------

        if soup:

            for link in soup.find_all(
                "a",
                href=True,
            ):

                href = link.get(
                    "href",
                    "",
                ).strip()

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
                    [
                        text,
                        title,
                        aria,
                    ]
                )

                number = (
                    extract_explicit_chapter_number(
                        combined
                    )
                    or extract_number_from_url(
                        url
                    )
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

                if (
                    is_navigation
                    and url not in visited
                ):

                    queue.insert(
                        0,
                        url,
                    )

                elif (
                    number is not None
                    and old_chapter
                    < number
                    <= old_chapter + MAX_AHEAD
                    and url not in visited
                ):

                    queue.append(
                        url
                    )

    return verified


# ============================================================
# WORDPRESS
# ============================================================

def wordpress_json(
    url,
    params=None,
):

    response = request_with_retry(
        url,
        params=params,
        headers={
            "Accept": "application/json",
            "User-Agent": SESSION.headers[
                "User-Agent"
            ],
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


def wordpress_candidates(
    site_url,
    old_chapter,
):

    parsed = urlparse(
        site_url
    )

    base = (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
    )

    candidates = []

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
                "_fields": (
                    "id,date,link,title,slug"
                ),
            },
        )

        if not isinstance(
            data,
            list,
        ):
            continue

        for item in data:

            link = item.get(
                "link",
                "",
            )

            title_obj = (
                item.get("title")
                or {}
            )

            title = clean_text(
                title_obj.get(
                    "rendered",
                    "",
                )
            )

            number = (
                extract_number_from_title(
                    title
                )
                or extract_explicit_chapter_number(
                    title
                )
                or extract_number_from_url(
                    link
                )
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
                (
                    number,
                    link,
                )
            )

    # --------------------------------------------------------
    # WordPress search
    # --------------------------------------------------------

    search_url = (
        base
        + "/wp-json/wp/v2/search"
    )

    for number in range(
        old_chapter + 1,
        old_chapter + MAX_AHEAD + 1,
    ):

        data = wordpress_json(
            search_url,
            params={
                "search": str(number),
                "per_page": 20,
                "_fields": (
                    "id,title,url,type"
                ),
            },
        )

        if not isinstance(
            data,
            list,
        ):
            continue

        for item in data:

            link = item.get(
                "url",
                "",
            )

            title_obj = (
                item.get("title")
                or {}
            )

            title = clean_text(
                title_obj.get(
                    "rendered",
                    "",
                )
            )

            found = (
                extract_number_from_title(
                    title
                )
                or extract_explicit_chapter_number(
                    title
                )
                or extract_number_from_url(
                    link
                )
            )

            if found != number:
                continue

            candidates.append(
                (
                    number,
                    link,
                )
            )

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

        verified[
            number
        ] = ChapterCandidate(
            number=number,
            url=result["url"],
            score=1500,
            source="wordpress_verified",
            evidence=result[
                "evidence"
            ],
        )

    return verified


# ============================================================
# RSS / FEED
# ============================================================

def feed_candidates(
    site_url,
    old_chapter,
):

    parsed = urlparse(
        site_url
    )

    base = (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
    )

    feed_urls = [
        urljoin(
            base,
            "/feed/",
        ),
        urljoin(
            base,
            "/rss/",
        ),
        urljoin(
            base,
            "/feed",
        ),
        urljoin(
            base,
            "/rss",
        ),
    ]

    verified = {}

    for feed_url in feed_urls:

        response = request_with_retry(
            feed_url
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

        for item in soup.find_all(
            ["item", "entry"]
        ):

            title_tag = item.find(
                "title"
            )

            if not title_tag:
                continue

            title = clean_text(
                title_tag.get_text(
                    " ",
                    strip=True,
                )
            )

            number = (
                extract_number_from_title(
                    title
                )
                or extract_explicit_chapter_number(
                    title
                )
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

            link_tag = item.find(
                "link"
            )

            if link_tag:

                if link_tag.get(
                    "href"
                ):

                    link = link_tag.get(
                        "href"
                    )

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

                verified[
                    number
                ] = ChapterCandidate(
                    number=number,
                    url=result["url"],
                    score=1400,
                    source="feed_verified",
                    evidence=result[
                        "evidence"
                    ],
                )

    return verified


# ============================================================
# SITEMAP
# ============================================================

def sitemap_candidates(
    site_url,
    old_chapter,
):

    parsed = urlparse(
        site_url
    )

    base = (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
    )

    sitemap_urls = [
        urljoin(
            base,
            "/sitemap.xml",
        ),
        urljoin(
            base,
            "/wp-sitemap.xml",
        ),
        urljoin(
            base,
            "/sitemap_index.xml",
        ),
        urljoin(
            base,
            "/post-sitemap.xml",
        ),
    ]

    urls_to_check = list(
        sitemap_urls
    )

    visited = set()
    verified = {}

    while (
        urls_to_check
        and len(visited) < 10
    ):

        sitemap_url = (
            urls_to_check.pop(0)
        )

        if sitemap_url in visited:
            continue

        visited.add(
            sitemap_url
        )

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

        for loc in soup.find_all(
            "loc"
        ):

            value = clean_text(
                loc.get_text(
                    " ",
                    strip=True,
                )
            )

            if not value:
                continue

            number = (
                extract_number_from_url(
                    value
                )
            )

            if number is not None:

                if (
                    old_chapter
                    < number
                    <= old_chapter + MAX_AHEAD
                ):

                    result = (
                        verify_chapter_page(
                            value,
                            expected_number=(
                                number
                            ),
                        )
                    )

                    if result:

                        verified[
                            number
                        ] = ChapterCandidate(
                            number=number,
                            url=result["url"],
                            score=1300,
                            source=(
                                "sitemap_verified"
                            ),
                            evidence=result[
                                "evidence"
                            ],
                        )

            elif value.endswith(
                ".xml"
            ):

                if value not in visited:

                    urls_to_check.append(
                        value
                    )

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

        for number, candidate in (
            source.items()
        ):

            if number <= old_chapter:
                continue

            if (
                number
                > old_chapter + MAX_AHEAD
            ):
                continue

            previous = (
                all_verified.get(
                    number
                )
            )

            if (
                previous is None
                or candidate.score
                > previous.score
            ):

                all_verified[
                    number
                ] = candidate

    # --------------------------------------------------------
    # 1. Main page
    # --------------------------------------------------------

    print(
        "[DISCOVER] Inspecting main page..."
    )

    crawl_results = (
        crawl_chapter_pages(
            site_url,
            old_chapter,
        )
    )

    merge(
        crawl_results
    )

    # --------------------------------------------------------
    # 2. WordPress
    # --------------------------------------------------------

    print(
        "[DISCOVER] Checking "
        "WordPress API..."
    )

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

    print(
        "[DISCOVER] Checking RSS..."
    )

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

    print(
        "[DISCOVER] Checking sitemap..."
    )

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

    old = 2479

    verified:
        2480
        2481
        2482

    النتيجة:
        2482


    أما:

    verified:
        2484

    النتيجة:
        2479
    """

    current = old_chapter

    while True:

        next_number = (
            current + 1
        )

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
        f"📖 <b>"
        f"{html_module.escape(work_name)}"
        f"</b>\n"
        f"📚 <b>الفصل "
        f"{chapter_number}"
        f"</b>\n\n"
        f"🔗 "
        f"{html_module.escape(chapter_url)}"
    )


# ============================================================
# WORK NORMALIZATION
# ============================================================

def get_users(data):

    users = data.get(
        "users"
    )

    if users is None:

        data["users"] = {}

        return data["users"]

    return users


def get_user_works(user):

    if not isinstance(
        user,
        dict,
    ):

        return []

    works = user.get(
        "works"
    )

    if works is None:

        user["works"] = []

        return user["works"]

    if isinstance(
        works,
        list,
    ):

        return works

    return []


# ============================================================
# MONITOR ONE WORK
# ============================================================

def monitor_work(
    chat_id,
    work,
):

    if not isinstance(
        work,
        dict,
    ):

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

    verified = (
        discover_verified_chapters(
            url,
            stored,
        )
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
            for x in sorted(
                verified
            )
        )
    )

    # --------------------------------------------------------
    # أهم خطوة:
    # التسلسل المتصل فقط
    # --------------------------------------------------------

    latest_contiguous = (
        get_contiguous_latest(
            verified,
            stored,
        )
    )

    print(
        f"[DETECT] Contiguous latest: "
        f"{latest_contiguous}"
    )

    if (
        latest_contiguous
        <= stored
    ):

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

        candidate = (
            verified.get(
                chapter_number
            )
        )

        if not candidate:

            print(
                f"[STOP] Chapter "
                f"{chapter_number} "
                f"is not verified."
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

        success = (
            send_telegram_message(
                chat_id,
                message,
            )
        )

        if not success:

            print(
                f"[STOP] Telegram failed "
                f"for chapter "
                f"{chapter_number}. "
                f"State will NOT advance."
            )

            break

        # لا نغير الحالة إلا بعد
        # نجاح Telegram
        work["last_chapter"] = (
            chapter_number
        )

        changed = True

        print(
            f"[STATE] {name}: "
            f"last_chapter updated to "
            f"{chapter_number}"
        )

        time.sleep(
            0.5
        )

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
            "TELEGRAM_BOT_TOKEN "
            "is not configured."
        )

    if not GITHUB_TOKEN:

        raise RuntimeError(
            "GITHUB_TOKEN "
            "is not configured."
        )

    data, sha = load_data()

    # ========================================================
    # MAINTENANCE MODE
    # ========================================================
    # إذا كانت الصيانة مفعلة في data.json:
    #
    # - لا نفحص أي رواية
    # - لا نزحف لأي موقع
    # - لا نرسل أي إشعار
    # - لا نغير last_chapter
    # - لا نحفظ data.json
    #
    # ونخرج من التشغيل مباشرة.
    # ========================================================

    if is_maintenance_enabled(
        data
    ):

        print(
            "[MAINTENANCE] "
            "Maintenance mode is enabled."
        )

        print(
            "[MAINTENANCE] "
            "Monitoring and chapter "
            "notifications are paused."
        )

        print(
            "[DONE] "
            "No database changes "
            "were required."
        )

        return

    users = get_users(
        data
    )

    if isinstance(
        users,
        dict,
    ):

        iterable = users.items()

    elif isinstance(
        users,
        list,
    ):

        iterable = []

        for user in users:

            if not isinstance(
                user,
                dict,
            ):

                continue

            chat_id = (
                user.get(
                    "chat_id"
                )
                or user.get(
                    "id"
                )
            )

            if chat_id is not None:

                iterable.append(
                    (
                        str(chat_id),
                        user,
                    )
                )

    else:

        print(
            "[ERROR] Unsupported "
            "users format."
        )

        return

    changed = False

    for chat_id, user in iterable:

        chat_id = str(
            chat_id
        )

        works = get_user_works(
            user
        )

        print(
            f"[USER] {chat_id}: "
            f"{len(works)} work(s)"
        )

        for work in works:

            try:

                work_changed = (
                    monitor_work(
                        chat_id,
                        work,
                    )
                )

                if work_changed:

                    changed = True

            except Exception as exc:

                print(
                    f"[ERROR] Work monitoring "
                    f"failed for user "
                    f"{chat_id}: {exc}"
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
            "[DONE] No database changes "
            "were required."
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()