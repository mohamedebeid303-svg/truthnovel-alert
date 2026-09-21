import os
import re
import json
import base64
import time
import html as html_module
from collections import defaultdict
from urllib.parse import urljoin, urlparse, urldefrag

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

# لا نحاول اكتشاف API عشوائيًا من كل رابط.
# نستخدم فقط المسارات الشائعة والروابط التي تبدو فعلًا كـ API.
COMMON_API_PATHS = (
    "/wp-json/",
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
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
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

    # 409 يعني أن data.json تغير أثناء التشغيل.
    # لا نريد الكتابة فوق التغييرات الجديدة.
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

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            response = SESSION.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )

            # أخطاء مؤقتة يمكن إعادة المحاولة عليها.
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

    # بعض المواقع لا تضبط Content-Type بشكل صحيح.
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

# أنماط قوية: الرقم مرتبط بوضوح بالفصل.
STRONG_CHAPTER_PATTERNS = [

    # English
    r"\bchapter\s*(?:no\.?|number)?\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",
    r"\bchap(?:ter)?\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",
    r"\bch\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",

    # Arabic
    r"\bالفصل\s*(?:رقم)?\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",
    r"فصل\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",

    # Chinese
    r"第\s*(\d+(?:\.\d+)?)\s*章",

    # URL
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

    # بعض المواقع تستخدم chapter-number
    r"\bchapter[-_ ]number\s*[:=]\s*(\d+(?:\.\d+)?)",
    r"\bchapter[-_ ]id\s*[:=]\s*(\d+(?:\.\d+)?)",
]


# أنماط أضعف: مفيدة لمواقع الروايات التي تعرض:
# 2455 - اسم الفصل
# 2455 اسم الفصل
WEAK_CHAPTER_PATTERNS = [

    r"(?<!\d)(\d{1,7})\s*[-–—:]\s*[^\d\n]{2,}",

    r"(?<!\d)(\d{1,7})\s+[ء-يA-Za-z][ء-يA-Za-z\s\-–—:]{2,}",

    r"[^\d\n]{2,}\s*[-–—:]\s*(\d{1,7})(?!\d)",
]


# أسماء حقول JSON الشائعة التي تحمل رقم الفصل.
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
    ):
        self.number = number
        self.score = score
        self.source = source
        self.context = str(
            context or ""
        )[:300]

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
):

    for number in numbers:

        candidates.append(
            Candidate(
                number=number,
                score=score,
                source=source,
                context=context,
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
    """
    تحليل JSON بشكل recursive.
    إذا كان المفتاح نفسه اسمه chapter_number
    نعطي الرقم وزنًا عاليًا.
    """

    if isinstance(obj, dict):

        for key, value in obj.items():

            key_lower = (
                str(key)
                .strip()
                .lower()
                .replace("-", "_")
                .replace(" ", "_")
            )

            # حقل واضح جدًا مثل:
            # chapter_number: 2455
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
                            150,
                            f"{source} field",
                            f"{key}: {value}",
                        )
                    )

            # نواصل النزول داخل JSON
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

        add_candidates(
            candidates,
            extract_matches(
                text,
                WEAK_CHAPTER_PATTERNS,
            ),
            45,
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

    data = safe_json_loads(
        raw
    )

    if data is not None:

        collect_json_candidates(
            data,
            candidates,
            source,
        )

        return

    # JSON غير صالح بالكامل،
    # نحلله كنص.
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
        110,
        "HTML title",
        text,
    )

    add_candidates(
        candidates,
        extract_matches(
            text,
            WEAK_CHAPTER_PATTERNS,
        ),
        55,
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
            115,
            f"HTML {tag.name}",
            text,
        )

        add_candidates(
            candidates,
            extract_matches(
                text,
                WEAK_CHAPTER_PATTERNS,
            ),
            80,
            f"HTML {tag.name}",
            text,
        )


def extract_from_links(
    soup,
    page_url,
    candidates,
):

    for link in soup.find_all("a"):

        text = link.get_text(
            " ",
            strip=True,
        )

        href = link.get("href")

        if href:
            href = urljoin(
                page_url,
                href,
            )

        if text:

            add_candidates(
                candidates,
                extract_matches(
                    text,
                    STRONG_CHAPTER_PATTERNS,
                ),
                120,
                "chapter link text",
                text,
            )

            add_candidates(
                candidates,
                extract_matches(
                    text,
                    WEAK_CHAPTER_PATTERNS,
                ),
                95,
                "chapter link text",
                text,
            )

        if href:

            add_candidates(
                candidates,
                extract_matches(
                    href,
                    STRONG_CHAPTER_PATTERNS,
                ),
                130,
                "chapter URL",
                href,
            )


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
            80,
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

                strong_score = 135
                weak_score = 75

            else:

                strong_score = 65
                weak_score = 30

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
                105,
                "JavaScript",
                raw,
            )

            add_candidates(
                candidates,
                extract_matches(
                    raw,
                    WEAK_CHAPTER_PATTERNS,
                ),
                60,
                "JavaScript",
                raw,
            )

            # محاولة تحليل JSON الموجود داخل script
            extract_from_json_text(
                raw,
                candidates,
                "JavaScript JSON",
            )

        else:

            # لا نعطي JavaScript العادي وزنًا كبيرًا.
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
        45,
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
        50,
        "page text",
        text,
    )

    add_candidates(
        candidates,
        extract_matches(
            text,
            WEAK_CHAPTER_PATTERNS,
        ),
        18,
        "page text",
        text,
    )


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

    # API paths شائعة
    for path in COMMON_API_PATHS:

        urls.add(
            urljoin(
                base_url,
                path,
            )
        )

    # الروابط الموجودة داخل الصفحة
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

    # لا نريد عشرات الطلبات في كل دورة.
    # نضع حدًا منطقيًا.
    api_urls = list(api_urls)[:15]

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

    parsed = urlparse(
        page_url
    )

    root = (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
    )

    urls = {
        urljoin(
            base_url,
            "/sitemap.xml",
        ),
        urljoin(
            base_url,
            "/sitemap_index.xml",
        ),
        urljoin(
            base_url,
            "/sitemap_index.xml.gz",
        ),
    }

    return urls


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

            # sitemap غالبًا XML.
            # نبحث فيه عن روابط الفصول.
            add_candidates(
                candidates,
                extract_matches(
                    content,
                    STRONG_CHAPTER_PATTERNS,
                ),
                90,
                "sitemap",
                content,
            )

        except Exception as error:

            print(
                f"[SITEMAP] Skipped "
                f"{sitemap_url}: {error}"
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

    if old is not None:

        # رقم أكبر بمقدار ضخم جدًا غالبًا
        # ليس رقم فصل.
        if number > old + 100000:
            return False

    return True


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

    for number, items in grouped.items():

        total_score = sum(
            item.score
            for item in items
        )

        sources = set(
            item.source
            for item in items
        )

        # تأكيد من مصادر مستقلة.
        if len(sources) >= 2:
            total_score += 35

        if len(sources) >= 3:
            total_score += 40

        if len(sources) >= 4:
            total_score += 45

        # تكرار الرقم في الصفحة.
        total_score += min(
            len(items) * 5,
            40,
        )

        old = normalize_number(
            old_chapter
        )

        if old is not None:

            difference = (
                number - old
            )

            # الفصل التالي مباشرة.
            if difference == 1:
                total_score += 120

            # عدة فصول جديدة.
            elif 1 < difference <= 20:
                total_score += 45

            # الرقم القديم أقل أهمية قليلًا.
            elif difference < 0:
                total_score -= 10

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

    # --------------------------------------------------------
    # HTML
    # --------------------------------------------------------

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
    )

    extract_from_meta(
        soup,
        candidates,
    )

    extract_from_data_attributes(
        soup,
        candidates,
    )

    # --------------------------------------------------------
    # Structured data
    # --------------------------------------------------------

    extract_from_json_ld(
        soup,
        candidates,
    )

    extract_from_framework_data(
        soup,
        candidates,
    )

    # --------------------------------------------------------
    # JavaScript
    # --------------------------------------------------------

    extract_from_scripts(
        soup,
        candidates,
    )

    # --------------------------------------------------------
    # Raw HTML + visible text
    # --------------------------------------------------------

    extract_from_raw_html(
        html,
        candidates,
    )

    extract_from_page_text(
        soup,
        candidates,
    )

    # --------------------------------------------------------
    # API
    # --------------------------------------------------------

    extract_from_api(
        page_url,
        soup,
        candidates,
    )

    # --------------------------------------------------------
    # Sitemap
    # --------------------------------------------------------

    extract_from_sitemap(
        page_url,
        candidates,
    )

    # --------------------------------------------------------
    # Ranking
    # --------------------------------------------------------

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

    for evidence in best[
        "evidence"
    ][:6]:

        context = (
            evidence.context
            .replace("\n", " ")
            .strip()
        )

        print(
            f"  - {evidence.source} "
            f"| +{evidence.score}"
        )

        if context:

            print(
                f"    {context[:180]}"
            )

    if len(ranked) > 1:

        print(
            "[DETECTION] "
            "Other candidates:"
        )

        for item in ranked[
            1:6
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

    # --------------------------------------------------------
    # Download
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
    # New chapter
    # --------------------------------------------------------

    difference = (
        latest_chapter
        - old_number
    )

    print(
        "[NEW] "
        "New chapter detected!"
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
            f"{display_chapter(difference)}\n\n"
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

        # لا نحدث last_chapter.
        # عند التشغيل التالي سيحاول الإرسال مرة أخرى.
        return False

    # --------------------------------------------------------
    # Save new chapter
    # --------------------------------------------------------

    work["last_chapter"] = (
        latest_chapter
    )

    print(
        "[SAVED] "
        "Chapter updated."
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