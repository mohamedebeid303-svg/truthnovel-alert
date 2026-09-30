import os
import re
import json
import base64
import time
import html as html_module
from collections import defaultdict
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

GITHUB_REPO = "mohamedebeid303-svg/truthnovel-alert"
DATA_FILE = "data.json"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_FILE}"

REQUEST_TIMEOUT = 30
API_TIMEOUT = 15
MAX_RETRIES = 3

MAX_DISCOVERY_PAGES = 8
MAX_CHAPTER_LINKS = 80

COMMON_API_PATHS = (
    "/wp-json/",
    "/wp-json/wp/v2/posts",
    "/wp-json/wp/v2/pages",
    "/wp-json/wp/v2/search",
    "/api/",
    "/api/chapters",
    "/api/chapter",
    "/api/novels",
    "/api/episodes",
)

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "application/json;q=0.9,"
        "*/*;q=0.8"
    ),
    "Accept-Language": "ar,en;q=0.9",
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
    "DNT": "1",
    "Connection": "keep-alive",
}


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(HTTP_HEADERS)


# ============================================================
# GITHUB
# ============================================================

def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def load_data():
    response = SESSION.get(
        GITHUB_API,
        headers=github_headers(),
        timeout=REQUEST_TIMEOUT,
        params={"_": str(int(time.time()))},
    )

    response.raise_for_status()

    result = response.json()

    content = base64.b64decode(
        result["content"]
    ).decode("utf-8")

    data = json.loads(content)

    data.setdefault("users", {})
    data.setdefault("settings", {})

    return data, result["sha"]


def save_data(data, sha):
    content = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    )

    encoded = base64.b64encode(
        content.encode("utf-8")
    ).decode("utf-8")

    payload = {
        "message": "Update chapter data",
        "content": encoded,
        "sha": sha,
    }

    response = SESSION.put(
        GITHUB_API,
        headers=github_headers(),
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 409:
        print("[WARNING] data.json changed while monitoring.")
        print("[WARNING] Changes were NOT overwritten.")
        return False

    response.raise_for_status()

    return True


# ============================================================
# TELEGRAM
# ============================================================

def send_message(chat_id, text):
    response = SESSION.post(
        f"{TELEGRAM_API}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False,
        },
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()


# ============================================================
# HTTP HELPERS
# ============================================================

def request_with_retry(
    url,
    timeout=REQUEST_TIMEOUT,
    headers=None,
    allow_redirects=True,
):
    last_error = None

    request_headers = dict(HTTP_HEADERS)

    if headers:
        request_headers.update(headers)

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            separator = "&" if "?" in url else "?"

            request_url = (
                f"{url}"
                f"{separator}"
                f"_monitor={int(time.time())}"
            )

            response = SESSION.get(
                request_url,
                headers=request_headers,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )

            if response.status_code in (
                408,
                425,
                429,
                500,
                502,
                503,
                504,
            ):
                raise requests.HTTPError(
                    f"Temporary HTTP status "
                    f"{response.status_code}"
                )

            response.raise_for_status()

            return response

        except (
            requests.RequestException,
            requests.Timeout,
        ) as error:

            last_error = error

            if attempt < MAX_RETRIES:

                wait_time = attempt * 2

                print(
                    f"[RETRY] {url} "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )

                time.sleep(wait_time)

    raise last_error


def download_page(url):
    response = request_with_retry(
        url,
        timeout=REQUEST_TIMEOUT,
    )

    content_type = (
        response.headers
        .get("Content-Type", "")
        .lower()
    )

    text = response.text

    looks_like_document = (
        text.lstrip().startswith(
            (
                "<",
                "{",
                "[",
            )
        )
    )

    if (
        "text/html" not in content_type
        and "application/xhtml+xml" not in content_type
        and "application/json" not in content_type
        and not looks_like_document
    ):
        raise ValueError(
            f"Unsupported content type: {content_type}"
        )

    return (
        text,
        response.url,
        content_type,
    )


# ============================================================
# DIGIT NORMALIZATION
# ============================================================

ARABIC_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)


def normalize_digits(value):
    if value is None:
        return ""

    return str(value).translate(
        ARABIC_DIGITS
    )


def normalize_number(value):
    if value is None:
        return None

    value = normalize_digits(value)
    value = str(value).strip()

    value = value.replace(",", "")
    value = value.replace("،", "")

    try:

        number = float(value)

        if number.is_integer():
            return int(number)

        return number

    except (
        ValueError,
        TypeError,
    ):

        return None


def display_chapter(chapter):
    number = normalize_number(chapter)

    if number is None:
        return "غير معروف"

    if isinstance(number, float) and number.is_integer():
        return str(int(number))

    return str(number)


# ============================================================
# CHAPTER PATTERNS
# ============================================================

STRONG_CHAPTER_PATTERNS = [

    # English
    r"\bchapter\s*(?:no\.?|number)?\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",
    r"\bchap(?:ter)?\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",
    r"\bch\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",

    # Episode
    r"\bepisode\s*(?:no\.?|number)?\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",
    r"\bep\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",

    # Arabic
    r"الفصل\s*(?:رقم)?\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",
    r"فصل\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",

    # Chinese
    r"第\s*(\d+(?:\.\d+)?)\s*章",

    # URL patterns
    r"/chapter/(\d+(?:\.\d+)?)",
    r"/chapter-(\d+(?:\.\d+)?)",
    r"/chapter_(\d+(?:\.\d+)?)",

    r"/chap/(\d+(?:\.\d+)?)",
    r"/chap-(\d+(?:\.\d+)?)",
    r"/chap_(\d+(?:\.\d+)?)",

    r"/ch/(\d+(?:\.\d+)?)",
    r"/ch-(\d+(?:\.\d+)?)",
    r"/ch_(\d+(?:\.\d+)?)",

    # Query parameters
    r"[?&](?:chapter|chap|ch|episode|ep)[=_-](\d+(?:\.\d+)?)",

    # Explicit chapter IDs
    r"\bchapter[-_ ]number\s*[:=]\s*(\d+(?:\.\d+)?)",
    r"\bchapter[-_ ]id\s*[:=]\s*(\d+(?:\.\d+)?)",
]


# ============================================================
# GENERIC NUMERIC POST URL PATTERNS
# ============================================================

URL_START_CHAPTER_PATTERNS = [

    r"/(\d{1,7})(?:[-–—_][^/]+)?/?$",

    r"/(\d{1,7})(?:[-–—_])",

    r"/(\d{1,7})/",
]


# ============================================================
# WEAK CHAPTER PATTERNS
# ============================================================

WEAK_CHAPTER_PATTERNS = [

    r"(?<!\d)(\d{1,7})\s*[-–—:]\s*[^\d\n]{2,}",

    r"(?<!\d)(\d{1,7})\s+[ء-يA-Za-z][ء-يA-Za-z\s\-–—:]{2,}",

    # هذا النمط لا نستخدمه كدليل قوي.
    # لأنه قد يقرأ:
    # "عنوان الفصل-1"
    # على أنه الفصل 1.
    r"[^\d\n]{2,}\s*[-–—:]\s*(\d{1,7})(?!\d)",
]


# ============================================================
# SUSPICIOUS NUMBERS
# ============================================================

YEAR_MIN = 1900
YEAR_MAX = 2100

EXPLICIT_CHAPTER_CONTEXT_PATTERNS = (
    r"\bchapter\b",
    r"\bchap\b",
    r"\bch\b",
    r"\bepisode\b",
    r"\bep\b",
    r"الفصل",
    r"فصل",
    r"章",
)


def has_explicit_chapter_context(text):
    if not text:
        return False

    text = normalize_digits(str(text))

    for pattern in EXPLICIT_CHAPTER_CONTEXT_PATTERNS:

        if re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        ):
            return True

    return False


def is_suspicious_year(
    number,
    context="",
):
    number = normalize_number(number)

    if number is None:
        return False

    if not isinstance(number, int):
        return False

    if YEAR_MIN <= number <= YEAR_MAX:

        if has_explicit_chapter_context(context):
            return False

        return True

    return False


# ============================================================
# JSON FIELD NAMES
# ============================================================

CHAPTER_FIELD_NAMES = {
    "chapter",
    "chapter_number",
    "chapternumber",
    "chapter_no",
    "chapterno",
    "chapter_id",
    "chapterid",
    "chapter_num",
    "chapternum",
    "currentchapter",
    "current_chapter",
    "episode",
    "episode_number",
    "episodenumber",
}


# ============================================================
# CANDIDATE
# ============================================================

class Candidate:

    def __init__(
        self,
        number,
        score,
        source,
        context="",
        url="",
    ):
        self.number = number
        self.score = score
        self.source = source
        self.context = str(
            context or ""
        )[:500]
        self.url = str(
            url or ""
        )[:1000]

    def __repr__(self):
        return (
            f"Candidate("
            f"{self.number}, "
            f"{self.score}, "
            f"{self.source}"
            f")"
        )


def add_candidates(
    candidates,
    numbers,
    score,
    source,
    context="",
    url="",
):

    for number in numbers:

        candidates.append(
            Candidate(
                number=number,
                score=score,
                source=source,
                context=context,
                url=url,
            )
        )


# ============================================================
# MATCHING
# ============================================================

def extract_matches(
    text,
    patterns,
):

    if not text:
        return []

    text = normalize_digits(text)

    results = []

    for pattern in patterns:

        try:

            matches = re.findall(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

        except re.error:

            continue

        for match in matches:

            if isinstance(match, tuple):
                match = match[0]

            number = normalize_number(
                match
            )

            if number is None:
                continue

            if number < 1:
                continue

            if number > 1000000:
                continue

            results.append(number)

    return results


def extract_url_start_chapters(url):
    if not url:
        return []

    parsed = urlparse(url)

    path = parsed.path or ""

    path = normalize_digits(path)

    results = []

    for pattern in URL_START_CHAPTER_PATTERNS:

        try:

            matches = re.findall(
                pattern,
                path,
                flags=re.IGNORECASE,
            )

        except re.error:

            continue

        for match in matches:

            if isinstance(match, tuple):
                match = match[0]

            number = normalize_number(match)

            if number is None:
                continue

            if 1 <= number <= 1000000:
                results.append(number)

    return list(dict.fromkeys(results))


# ============================================================
# JSON HELPERS
# ============================================================

def safe_json_loads(raw):

    if not raw:
        return None

    raw = raw.strip()

    try:
        return json.loads(raw)

    except Exception:
        return None


def collect_json_candidates(
    obj,
    candidates,
    source,
    parent_key="",
):

    if isinstance(obj, dict):

        for key, value in obj.items():

            key_lower = (
                str(key)
                .strip()
                .lower()
                .replace("-", "_")
                .replace(" ", "_")
            )

            if key_lower in CHAPTER_FIELD_NAMES:

                number = normalize_number(
                    value
                )

                if (
                    number is not None
                    and 1 <= number <= 1000000
                ):

                    candidates.append(
                        Candidate(
                            number,
                            170,
                            f"{source} field",
                            f"{key}: {value}",
                        )
                    )

            collect_json_candidates(
                value,
                candidates,
                source,
                key_lower,
            )

    elif isinstance(obj, list):

        for item in obj:

            collect_json_candidates(
                item,
                candidates,
                source,
                parent_key,
            )

    elif isinstance(obj, str):

        text = obj

        add_candidates(
            candidates,
            extract_matches(
                text,
                STRONG_CHAPTER_PATTERNS,
            ),
            100,
            source,
            text,
        )

        # الضعيف يبقى منخفضًا جدًا.
        add_candidates(
            candidates,
            extract_matches(
                text,
                WEAK_CHAPTER_PATTERNS,
            ),
            20,
            source,
            text,
        )


def extract_from_json_text(
    raw,
    candidates,
    source,
):

    if not raw:
        return

    data = safe_json_loads(raw)

    if data is not None:

        collect_json_candidates(
            data,
            candidates,
            source,
        )

        return

    add_candidates(
        candidates,
        extract_matches(
            raw,
            STRONG_CHAPTER_PATTERNS,
        ),
        85,
        source,
        raw,
    )


# ============================================================
# HTML DETECTORS
# ============================================================

def extract_from_title(
    soup,
    candidates,
):

    if not soup.title:
        return

    text = soup.title.get_text(
        " ",
        strip=True,
    )

    add_candidates(
        candidates,
        extract_matches(
            text,
            STRONG_CHAPTER_PATTERNS,
        ),
        150,
        "HTML title",
        text,
    )

    add_candidates(
        candidates,
        extract_matches(
            text,
            WEAK_CHAPTER_PATTERNS,
        ),
        25,
        "HTML title",
        text,
    )


def extract_from_headings(
    soup,
    candidates,
):

    for tag in soup.find_all(
        [
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        ]
    ):

        text = tag.get_text(
            " ",
            strip=True,
        )

        if not text:
            continue

        add_candidates(
            candidates,
            extract_matches(
                text,
                STRONG_CHAPTER_PATTERNS,
            ),
            150,
            f"HTML {tag.name}",
            text,
        )

        add_candidates(
            candidates,
            extract_matches(
                text,
                WEAK_CHAPTER_PATTERNS,
            ),
            25,
            f"HTML {tag.name}",
            text,
        )


def is_same_domain(
    base_url,
    target_url,
):

    try:

        base_host = (
            urlparse(base_url)
            .netloc
            .lower()
            .split(":")[0]
        )

        target_host = (
            urlparse(target_url)
            .netloc
            .lower()
            .split(":")[0]
        )

        return (
            base_host == target_host
            or target_host.endswith(
                "." + base_host
            )
            or base_host.endswith(
                "." + target_host
            )
        )

    except Exception:
        return False


def extract_from_links(
    soup,
    page_url,
    candidates,
    old_chapter=None,
):

    old = normalize_number(
        old_chapter
    )

    links_seen = 0

    for link in soup.find_all("a"):

        if links_seen >= MAX_CHAPTER_LINKS:
            break

        href = link.get("href")

        if not href:
            continue

        href = urljoin(
            page_url,
            href,
        )

        if not is_same_domain(
            page_url,
            href,
        ):
            continue

        text = link.get_text(
            " ",
            strip=True,
        )

        # ----------------------------------------------------
        # Strong URL
        # ----------------------------------------------------

        strong_numbers = extract_matches(
            href,
            STRONG_CHAPTER_PATTERNS,
        )

        if strong_numbers:

            add_candidates(
                candidates,
                strong_numbers,
                180,
                "chapter URL",
                text or href,
                href,
            )

            links_seen += 1
            continue

        # ----------------------------------------------------
        # Generic numeric chapter URL
        # ----------------------------------------------------

        url_numbers = extract_url_start_chapters(
            href
        )

        if url_numbers:

            for number in url_numbers:

                score = 180

                if old is not None:

                    difference = (
                        number - old
                    )

                    if difference == 1:
                        score += 100

                    elif 1 < difference <= 10:
                        score += 70

                    elif 10 < difference <= 100:
                        score += 30

                    elif difference < 0:
                        score -= 10

                candidates.append(
                    Candidate(
                        number,
                        score,
                        "site chapter URL",
                        text or href,
                        href,
                    )
                )

            links_seen += 1
            continue

        # ----------------------------------------------------
        # Link text
        # ----------------------------------------------------

        if text:

            add_candidates(
                candidates,
                extract_matches(
                    text,
                    STRONG_CHAPTER_PATTERNS,
                ),
                150,
                "chapter link text",
                text,
                href,
            )

            # لا نعطي weak link text وزنًا كبيرًا.
            add_candidates(
                candidates,
                extract_matches(
                    text,
                    WEAK_CHAPTER_PATTERNS,
                ),
                20,
                "chapter link text",
                text,
                href,
            )

        # ----------------------------------------------------
        # Query parameter
        # ----------------------------------------------------

        parsed = urlparse(href)

        query = parse_qs(
            parsed.query
        )

        for key in (
            "chapter",
            "chap",
            "ch",
            "episode",
            "ep",
        ):

            if key not in query:
                continue

            for value in query[key]:

                number = normalize_number(
                    value
                )

                if number is not None:

                    candidates.append(
                        Candidate(
                            number,
                            180,
                            "chapter query URL",
                            href,
                            href,
                        )
                    )

        links_seen += 1


def extract_from_meta(
    soup,
    candidates,
):

    for tag in soup.find_all("meta"):

        values = []

        for attribute in (
            "content",
            "name",
            "property",
        ):

            value = tag.get(
                attribute
            )

            if value:
                values.append(
                    str(value)
                )

        text = " ".join(values)

        if not text:
            continue

        add_candidates(
            candidates,
            extract_matches(
                text,
                STRONG_CHAPTER_PATTERNS,
            ),
            90,
            "meta",
            text,
        )


def extract_from_data_attributes(
    soup,
    candidates,
):

    for tag in soup.find_all(True):

        for attribute, value in tag.attrs.items():

            if not attribute.startswith(
                "data-"
            ):
                continue

            if isinstance(value, list):
                value = " ".join(value)

            if not isinstance(value, str):
                continue

            attribute_lower = (
                attribute.lower()
            )

            if any(
                word in attribute_lower
                for word in (
                    "chapter",
                    "chap",
                    "episode",
                    "novel",
                )
            ):

                strong_score = 160
                weak_score = 25

            else:

                strong_score = 65
                weak_score = 10

            add_candidates(
                candidates,
                extract_matches(
                    value,
                    STRONG_CHAPTER_PATTERNS,
                ),
                strong_score,
                f"HTML {attribute}",
                value,
            )

            add_candidates(
                candidates,
                extract_matches(
                    value,
                    WEAK_CHAPTER_PATTERNS,
                ),
                weak_score,
                f"HTML {attribute}",
                value,
            )


# ============================================================
# JSON-LD
# ============================================================

def extract_from_json_ld(
    soup,
    candidates,
):

    scripts = soup.find_all(
        "script",
        attrs={
            "type": re.compile(
                r"application/ld\+json",
                re.IGNORECASE,
            )
        },
    )

    for script in scripts:

        raw = script.string

        if not raw:

            raw = script.get_text(
                " ",
                strip=True,
            )

        if not raw:
            continue

        extract_from_json_text(
            raw,
            candidates,
            "JSON-LD",
        )


# ============================================================
# JAVASCRIPT
# ============================================================

def extract_from_scripts(
    soup,
    candidates,
):

    for script in soup.find_all(
        "script"
    ):

        script_type = (
            script.get("type")
            or ""
        ).lower()

        if "ld+json" in script_type:
            continue

        raw = script.string

        if not raw:

            raw = script.get_text(
                " ",
                strip=True,
            )

        if not raw:
            continue

        lower = raw.lower()

        has_chapter_context = any(
            keyword in lower
            for keyword in (
                "chapter",
                "chapternumber",
                "chapter_number",
                "chapterid",
                "chapter_id",
                "chaptertitle",
                "episode",
                "novelchapter",
                "currentchapter",
            )
        )

        if has_chapter_context:

            add_candidates(
                candidates,
                extract_matches(
                    raw,
                    STRONG_CHAPTER_PATTERNS,
                ),
                130,
                "JavaScript",
                raw,
            )

            add_candidates(
                candidates,
                extract_matches(
                    raw,
                    WEAK_CHAPTER_PATTERNS,
                ),
                15,
                "JavaScript",
                raw,
            )

            extract_from_json_text(
                raw,
                candidates,
                "JavaScript JSON",
            )

        else:

            add_candidates(
                candidates,
                extract_matches(
                    raw,
                    STRONG_CHAPTER_PATTERNS,
                ),
                55,
                "JavaScript",
                raw,
            )


# ============================================================
# MODERN FRAMEWORKS
# ============================================================

def extract_from_framework_data(
    soup,
    candidates,
):

    framework_ids = (
        "__NEXT_DATA__",
        "__NUXT__",
        "__NUXT_DATA__",
        "__APOLLO_STATE__",
        "__INITIAL_STATE__",
        "__PRELOADED_STATE__",
    )

    for element_id in framework_ids:

        tag = soup.find(
            id=element_id
        )

        if not tag:
            continue

        raw = tag.get_text(
            " ",
            strip=True,
        )

        if not raw:
            continue

        extract_from_json_text(
            raw,
            candidates,
            f"Framework {element_id}",
        )


# ============================================================
# RAW HTML
# ============================================================

def extract_from_raw_html(
    html,
    candidates,
):

    if not html:
        return

    decoded = html_module.unescape(
        html
    )

    add_candidates(
        candidates,
        extract_matches(
            decoded,
            STRONG_CHAPTER_PATTERNS,
        ),
        60,
        "raw HTML",
        decoded,
    )


# ============================================================
# PAGE TEXT
# ============================================================

def extract_from_page_text(
    soup,
    candidates,
):

    text = soup.get_text(
        " ",
        strip=True,
    )

    if not text:
        return

    add_candidates(
        candidates,
        extract_matches(
            text,
            STRONG_CHAPTER_PATTERNS,
        ),
        65,
        "page text",
        text,
    )

    # الضعيف منخفض جدًا حتى لا يحوّل
    # أرقامًا داخل عناوين فرعية إلى فصول.
    add_candidates(
        candidates,
        extract_matches(
            text,
            WEAK_CHAPTER_PATTERNS,
        ),
        2,
        "page text",
        text,
    )


# ============================================================
# PAGINATION DISCOVERY
# ============================================================

def discover_pagination_urls(
    page_url,
    soup,
):

    urls = set()

    for link in soup.find_all("a"):

        href = link.get("href")

        if not href:
            continue

        href = urljoin(
            page_url,
            href,
        )

        if not is_same_domain(
            page_url,
            href,
        ):
            continue

        text = link.get_text(
            " ",
            strip=True,
        ).lower()

        href_lower = href.lower()

        if (
            "page/" in href_lower
            or "paged=" in href_lower
            or "/page-" in href_lower
            or "page=" in href_lower
        ):

            urls.add(href)

        if any(
            word in text
            for word in (
                "التالي",
                "السابق",
                "next",
                "older",
                "newer",
            )
        ):

            urls.add(href)

    return urls


def discover_related_pages(
    page_url,
    soup,
):

    urls = set()

    pagination = discover_pagination_urls(
        page_url,
        soup,
    )

    urls.update(pagination)

    for link in soup.find_all("a"):

        href = link.get("href")

        if not href:
            continue

        href = urljoin(
            page_url,
            href,
        )

        if not is_same_domain(
            page_url,
            href,
        ):
            continue

        text = link.get_text(
            " ",
            strip=True,
        )

        if not text:
            continue

        numbers = extract_url_start_chapters(
            href
        )

        if numbers:

            urls.add(href)
            continue

        if extract_matches(
            text,
            STRONG_CHAPTER_PATTERNS,
        ):

            urls.add(href)

    return list(urls)


# ============================================================
# URL / API DISCOVERY
# ============================================================

def get_base_url(url):

    parsed = urlparse(url)

    return (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
    )


def discover_api_urls(
    page_url,
    soup,
):

    urls = set()

    base_url = get_base_url(
        page_url
    )

    for path in COMMON_API_PATHS:

        urls.add(
            urljoin(
                base_url,
                path,
            )
        )

    for tag in soup.find_all(
        [
            "a",
            "script",
            "link",
        ]
    ):

        for attribute in (
            "href",
            "src",
        ):

            value = tag.get(
                attribute
            )

            if not value:
                continue

            lower = value.lower()

            if any(
                keyword in lower
                for keyword in (
                    "/api/",
                    "wp-json",
                    "graphql",
                )
            ):

                urls.add(
                    urljoin(
                        page_url,
                        value,
                    )
                )

    return urls


def extract_from_api(
    page_url,
    soup,
    candidates,
):

    api_urls = discover_api_urls(
        page_url,
        soup,
    )

    api_urls = list(api_urls)[:20]

    for api_url in api_urls:

        try:

            response = request_with_retry(
                api_url,
                timeout=API_TIMEOUT,
            )

            content_type = (
                response.headers
                .get(
                    "Content-Type",
                    "",
                )
                .lower()
            )

            body = response.text

            looks_json = (
                "json" in content_type
                or body.lstrip().startswith(
                    (
                        "{",
                        "[",
                    )
                )
            )

            if not looks_json:
                continue

            extract_from_json_text(
                body,
                candidates,
                f"API {api_url}",
            )

        except Exception as error:

            print(
                f"[API] Skipped "
                f"{api_url}: {error}"
            )


# ============================================================
# SITEMAP
# ============================================================

def discover_sitemap_urls(
    page_url,
):

    base_url = get_base_url(
        page_url
    )

    return {
        urljoin(
            base_url,
            "/sitemap.xml",
        ),
        urljoin(
            base_url,
            "/sitemap_index.xml",
        ),
    }


def extract_from_sitemap(
    page_url,
    candidates,
):

    for sitemap_url in discover_sitemap_urls(
        page_url
    ):

        try:

            response = request_with_retry(
                sitemap_url,
                timeout=API_TIMEOUT,
            )

            content = response.text

            if not content:
                continue

            for match in re.findall(
                r"<loc>\s*(.*?)\s*</loc>",
                content,
                flags=re.IGNORECASE,
            ):

                loc = html_module.unescape(
                    match.strip()
                )

                numbers = extract_url_start_chapters(
                    loc
                )

                if numbers:

                    add_candidates(
                        candidates,
                        numbers,
                        170,
                        "sitemap chapter URL",
                        loc,
                        loc,
                    )

                strong = extract_matches(
                    loc,
                    STRONG_CHAPTER_PATTERNS,
                )

                if strong:

                    add_candidates(
                        candidates,
                        strong,
                        140,
                        "sitemap",
                        loc,
                        loc,
                    )

        except Exception as error:

            print(
                f"[SITEMAP] Skipped "
                f"{sitemap_url}: {error}"
            )


# ============================================================
# CHAPTER DISCOVERY FROM RELATED PAGES
# ============================================================

def discover_chapters_from_related_pages(
    page_url,
    soup,
    old_chapter,
    candidates,
):

    related_urls = discover_related_pages(
        page_url,
        soup,
    )

    related_urls = list(
        dict.fromkeys(
            related_urls
        )
    )

    related_urls = related_urls[
        :MAX_DISCOVERY_PAGES
    ]

    if not related_urls:

        print(
            "[DISCOVERY] No related chapter "
            "pages found on main page."
        )

        return

    print(
        f"[DISCOVERY] Checking "
        f"{len(related_urls)} related pages..."
    )

    for related_url in related_urls:

        try:

            related_html, final_url, _ = (
                download_page(
                    related_url
                )
            )

            related_soup = BeautifulSoup(
                related_html,
                "html.parser",
            )

            numbers = extract_url_start_chapters(
                final_url
            )

            add_candidates(
                candidates,
                numbers,
                220,
                "discovered chapter page URL",
                final_url,
                final_url,
            )

            if related_soup.title:

                title = related_soup.title.get_text(
                    " ",
                    strip=True,
                )

                add_candidates(
                    candidates,
                    extract_matches(
                        title,
                        WEAK_CHAPTER_PATTERNS,
                    ),
                    30,
                    "discovered chapter title",
                    title,
                    final_url,
                )

                add_candidates(
                    candidates,
                    extract_matches(
                        title,
                        STRONG_CHAPTER_PATTERNS,
                    ),
                    180,
                    "discovered chapter title",
                    title,
                    final_url,
                )

            for h1 in related_soup.find_all(
                "h1"
            )[:3]:

                text = h1.get_text(
                    " ",
                    strip=True,
                )

                add_candidates(
                    candidates,
                    extract_matches(
                        text,
                        WEAK_CHAPTER_PATTERNS,
                    ),
                    30,
                    "discovered H1",
                    text,
                    final_url,
                )

                add_candidates(
                    candidates,
                    extract_matches(
                        text,
                        STRONG_CHAPTER_PATTERNS,
                    ),
                    180,
                    "discovered H1",
                    text,
                    final_url,
                )

        except Exception as error:

            print(
                "[DISCOVERY] Skipped "
                f"{related_url}: {error}"
            )


# ============================================================
# CANDIDATE FILTERING
# ============================================================

def candidate_is_reasonable(
    candidate,
    old_chapter=None,
):

    number = normalize_number(
        candidate.number
    )

    if number is None:
        return False

    if number < 1:
        return False

    if number > 1000000:
        return False

    old = normalize_number(
        old_chapter
    )

    if is_suspicious_year(
        number,
        candidate.context,
    ):

        print(
            "[FILTER] Rejected suspicious "
            f"year-like number: {number}"
        )

        return False

    if old is not None:

        if number > old + 100000:
            return False

    return True


# ============================================================
# EVIDENCE SCORING
# ============================================================

def evidence_key(item):
    context = re.sub(
        r"\s+",
        " ",
        item.context.strip().lower(),
    )

    url = item.url.strip().lower()

    return (
        item.source,
        context[:250],
        url[:400],
    )


def score_number_group(
    number,
    items,
    old,
):

    # --------------------------------------------------------
    # لا نجمع كل التكرارات كما كان يحدث سابقًا.
    #
    # نفس العنوان قد يظهر 20 أو 30 مرة في الصفحة.
    # لا يجب أن يجعل هذا الرقم أقوى 30 مرة.
    # --------------------------------------------------------

    unique_evidence = {}

    for item in items:

        key = evidence_key(item)

        existing = unique_evidence.get(
            key
        )

        if existing is None:
            unique_evidence[key] = item

        elif item.score > existing.score:
            unique_evidence[key] = item

    unique_items = list(
        unique_evidence.values()
    )

    # --------------------------------------------------------
    # لكل مصدر نأخذ أقوى دليل بدل جمع التكرارات.
    # --------------------------------------------------------

    source_best = {}

    for item in unique_items:

        source = item.source

        existing = source_best.get(
            source
        )

        if existing is None:
            source_best[source] = item

        elif item.score > existing.score:
            source_best[source] = item

    total_score = sum(
        item.score
        for item in source_best.values()
    )

    # --------------------------------------------------------
    # تنوع المصادر
    # --------------------------------------------------------

    source_count = len(
        source_best
    )

    if source_count >= 2:
        total_score += 50

    if source_count >= 3:
        total_score += 70

    if source_count >= 4:
        total_score += 90

    # --------------------------------------------------------
    # العلاقة مع آخر فصل
    # --------------------------------------------------------

    if old is not None:

        difference = number - old

        # نفس الفصل الحالي
        if difference == 0:
            total_score += 250

        # الفصل التالي
        elif difference == 1:
            total_score += 500

        # عدة فصول جديدة
        elif 1 < difference <= 10:
            total_score += 300

        elif 10 < difference <= 100:
            total_score += 120

        # فصل قديم
        elif difference < 0:

            distance = abs(difference)

            if distance <= 2:
                total_score -= 40

            elif distance <= 10:
                total_score -= 120

            elif distance <= 100:
                total_score -= 500

            else:
                total_score -= 1200

        # قفزة ضخمة
        elif difference > 100:

            explicit_evidence = any(
                (
                    has_explicit_chapter_context(
                        evidence.context
                    )
                    or "chapter URL"
                    in evidence.source
                    or "site chapter URL"
                    in evidence.source
                    or "discovered chapter page URL"
                    in evidence.source
                    or "sitemap chapter URL"
                    in evidence.source
                )
                for evidence in unique_items
            )

            if explicit_evidence:
                total_score += 20
            else:
                total_score -= 1000

        # ----------------------------------------------------
        # حماية إضافية:
        #
        # إذا كان الفصل الحالي في الآلاف، فلا ينبغي
        # أن يهزم رقم صغير جدًا مثل 1 بسبب عنوان فرعي.
        # ----------------------------------------------------

        if (
            old >= 20
            and number <= 10
            and difference < -10
        ):

            weak_only = all(
                evidence.score <= 70
                for evidence in unique_items
            )

            if weak_only:

                total_score -= 5000

    # --------------------------------------------------------
    # عدد الأدلة الفريدة له قيمة، لكن بحد أقصى.
    # --------------------------------------------------------

    total_score += min(
        len(unique_items) * 8,
        80,
    )

    return total_score


# ============================================================
# CANDIDATE RANKING
# ============================================================

def rank_candidates(
    candidates,
    old_chapter=None,
):

    valid = [
        candidate
        for candidate in candidates
        if candidate_is_reasonable(
            candidate,
            old_chapter,
        )
    ]

    if not valid:
        return []

    grouped = defaultdict(list)

    for candidate in valid:

        grouped[
            candidate.number
        ].append(candidate)

    ranked = []

    old = normalize_number(
        old_chapter
    )

    for number, items in grouped.items():

        total_score = score_number_group(
            number,
            items,
            old,
        )

        ranked.append(
            {
                "number": number,
                "score": total_score,
                "evidence": items,
            }
        )

    ranked.sort(
        key=lambda item: (
            item["score"],
            item["number"],
        ),
        reverse=True,
    )

    return ranked


# ============================================================
# FINAL DETECTION
# ============================================================

def extract_latest_chapter(
    html,
    page_url,
    old_chapter=None,
):

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    candidates = []

    # HTML
    extract_from_title(
        soup,
        candidates,
    )

    extract_from_headings(
        soup,
        candidates,
    )

    extract_from_links(
        soup,
        page_url,
        candidates,
        old_chapter,
    )

    extract_from_meta(
        soup,
        candidates,
    )

    extract_from_data_attributes(
        soup,
        candidates,
    )

    # Structured data
    extract_from_json_ld(
        soup,
        candidates,
    )

    extract_from_framework_data(
        soup,
        candidates,
    )

    # JavaScript
    extract_from_scripts(
        soup,
        candidates,
    )

    # Raw HTML + visible text
    extract_from_raw_html(
        html,
        candidates,
    )

    extract_from_page_text(
        soup,
        candidates,
    )

    # Related chapter pages
    discover_chapters_from_related_pages(
        page_url,
        soup,
        old_chapter,
        candidates,
    )

    # API
    extract_from_api(
        page_url,
        soup,
        candidates,
    )

    # Sitemap
    extract_from_sitemap(
        page_url,
        candidates,
    )

    # Ranking
    ranked = rank_candidates(
        candidates,
        old_chapter,
    )

    if not ranked:
        return None, None, []

    best = ranked[0]

    return (
        best["number"],
        best,
        ranked[:10],
    )


# ============================================================
# DETECTION LOG
# ============================================================

def print_detection_details(
    best,
    ranked,
):

    if not best:

        print(
            "[DETECTION] "
            "No reliable chapter found."
        )

        return

    print(
        "[DETECTION] Chapter: "
        f"{display_chapter(best['number'])}"
    )

    print(
        "[DETECTION] Score: "
        f"{best['score']}"
    )

    print(
        "[DETECTION] Evidence:"
    )

    # عرض أفضل الأدلة فقط
    evidence_sorted = sorted(
        best["evidence"],
        key=lambda item: item.score,
        reverse=True,
    )

    shown = set()

    count = 0

    for evidence in evidence_sorted:

        key = evidence_key(
            evidence
        )

        if key in shown:
            continue

        shown.add(key)

        context = (
            evidence.context
            .replace("\n", " ")
            .strip()
        )

        print(
            f"  - {evidence.source} "
            f"| +{evidence.score}"
        )

        if evidence.url:

            print(
                f"    URL: {evidence.url[:220]}"
            )

        if context:

            print(
                f"    {context[:180]}"
            )

        count += 1

        if count >= 8:
            break

    if len(ranked) > 1:

        print(
            "[DETECTION] "
            "Other candidates:"
        )

        for item in ranked[
            1:8
        ]:

            print(
                "  - "
                f"{display_chapter(item['number'])} "
                f"(score {item['score']})"
            )


# ============================================================
# MONITOR ONE WORK
# ============================================================

def monitor_work(
    chat_id,
    work,
):

    name = work.get(
        "name",
        "عمل بدون اسم",
    )

    url = work.get(
        "url"
    )

    old_chapter = work.get(
        "last_chapter"
    )

    if not url:

        print(
            f"[SKIP] {name}: URL missing"
        )

        return False

    print("")
    print(
        "--------------------------------------------------"
    )

    print(
        f"[CHECK] {name}"
    )

    print(
        f"        {url}"
    )

    print(
        f"[OLD] Last recorded chapter: "
        f"{display_chapter(old_chapter)}"
    )

    # --------------------------------------------------------
    # Download main page
    # --------------------------------------------------------

    try:

        (
            html,
            final_url,
            content_type,
        ) = download_page(
            url
        )

    except Exception as error:

        print(
            f"[ERROR] Could not access {url}"
        )

        print(
            f"        {error}"
        )

        return False

    print(
        f"[HTTP] Final URL: {final_url}"
    )

    print(
        f"[HTTP] Content-Type: {content_type}"
    )

    # --------------------------------------------------------
    # Detection
    # --------------------------------------------------------

    try:

        (
            latest_chapter,
            best,
            ranked,
        ) = extract_latest_chapter(
            html,
            final_url,
            old_chapter,
        )

    except Exception as error:

        print(
            "[ERROR] Chapter detection failed."
        )

        print(error)

        return False

    print(
        "[RESULT] "
        f"{display_chapter(old_chapter)} "
        "-> "
        f"{display_chapter(latest_chapter)}"
    )

    print_detection_details(
        best,
        ranked,
    )

    # --------------------------------------------------------
    # Unknown
    # --------------------------------------------------------

    if latest_chapter is None:

        print(
            "[UNKNOWN] "
            f"Could not detect chapter "
            f"for {name}"
        )

        return False

    # --------------------------------------------------------
    # First initialization
    # --------------------------------------------------------

    if old_chapter is None:

        work["last_chapter"] = (
            latest_chapter
        )

        print(
            "[INIT] "
            "First chapter recorded."
        )

        return True

    old_number = normalize_number(
        old_chapter
    )

    if old_number is None:

        work["last_chapter"] = (
            latest_chapter
        )

        print(
            "[RESET] "
            "Invalid previous chapter."
        )

        return True

    # --------------------------------------------------------
    # No new chapter
    # --------------------------------------------------------

    if latest_chapter <= old_number:

        print(
            "[OK] No new chapter."
        )

        return False

    # --------------------------------------------------------
    # New chapters
    # --------------------------------------------------------

    difference = (
        latest_chapter
        - old_number
    )

    print(
        "[NEW] "
        f"Detected {difference} new chapter(s)."
    )

    # --------------------------------------------------------
    # Message
    # --------------------------------------------------------

    if difference == 1:

        message = (
            "🔔 فصل جديد!\n\n"
            f"📖 {name}\n"
            f"📚 الفصل "
            f"{display_chapter(latest_chapter)}\n\n"
            f"🔗 {url}"
        )

    else:

        message = (
            "🔔 فصول جديدة!\n\n"
            f"📖 {name}\n"
            f"📚 أحدث فصل: "
            f"{display_chapter(latest_chapter)}\n"
            f"📌 آخر فصل مسجل: "
            f"{display_chapter(old_number)}\n"
            f"📈 عدد الفصول الجديدة: "
            f"{display_chapter(difference)}\n"
            f"🆕 الفصول المكتشفة: "
            f"{display_chapter(old_number + 1)}"
            f" → "
            f"{display_chapter(latest_chapter)}\n\n"
            f"🔗 {url}"
        )

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    try:

        send_message(
            chat_id,
            message,
        )

    except Exception as error:

        print(
            "[ERROR] "
            "Telegram notification failed."
        )

        print(error)

        return False

    # --------------------------------------------------------
    # Save new chapter
    # --------------------------------------------------------

    work["last_chapter"] = (
        latest_chapter
    )

    print(
        "[SAVED] "
        "Chapter updated to "
        f"{display_chapter(latest_chapter)}."
    )

    return True


# ============================================================
# ALL USERS
# ============================================================

def monitor_all_users(
    data
):

    changed = False

    users = data.get(
        "users",
        {},
    )

    print(
        f"[INFO] Total users: "
        f"{len(users)}"
    )

    for chat_id, user in users.items():

        if not isinstance(
            user,
            dict,
        ):
            continue

        works = user.get(
            "works",
            [],
        )

        if not isinstance(
            works,
            list,
        ):
            continue

        print("")
        print(
            f"[USER] {chat_id}"
        )

        print(
            f"[WORKS] {len(works)}"
        )

        for work in works:

            if not isinstance(
                work,
                dict,
            ):
                continue

            try:

                result = monitor_work(
                    chat_id,
                    work,
                )

                if result:
                    changed = True

            except Exception as error:

                print(
                    "[ERROR] "
                    "Unexpected work error:"
                )

                print(error)

    return changed


# ============================================================
# MAIN
# ============================================================

def main():

    print("")
    print(
        "=================================================="
    )

    print(
        "       TRUTHNOVEL CHAPTER MONITOR"
    )

    print(
        "       Advanced Multi-Layer Detector"
    )

    print(
        "=================================================="
    )

    print(
        "[INFO] Monitor interval is controlled "
        "by GitHub Actions."
    )

    print(
        "[INFO] Main page + chapter URLs + "
        "related pages + API + sitemap."
    )

    # --------------------------------------------------------
    # Load
    # --------------------------------------------------------

    try:

        data, sha = load_data()

    except Exception as error:

        print(
            "[FATAL] "
            "Could not load data.json"
        )

        print(error)

        raise

    # --------------------------------------------------------
    # Monitor
    # --------------------------------------------------------

    try:

        changed = monitor_all_users(
            data
        )

    except Exception as error:

        print(
            "[FATAL] "
            "Monitoring failed."
        )

        print(error)

        raise

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    if changed:

        print("")
        print(
            "[SAVE] "
            "Updating data.json..."
        )

        try:

            success = save_data(
                data,
                sha,
            )

            if success:

                print(
                    "[SAVE] "
                    "data.json updated."
                )

            else:

                print(
                    "[WARNING] "
                    "data.json was not updated."
                )

        except Exception as error:

            print(
                "[ERROR] "
                "Could not save data.json"
            )

            print(error)

            raise

    else:

        print("")
        print(
            "[SAVE] "
            "No changes."
        )

    print("")
    print(
        "[DONE]"
    )

    print(
        "=================================================="
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()