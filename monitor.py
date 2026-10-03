import os
import re
import json
import base64
import time
import html as html_module
from collections import defaultdict
from urllib.parse import (
urljoin,
urlparse,
parse_qs,
)

import requests
from bs4 import BeautifulSoup

============================================================

CONFIGURATION

============================================================

GITHUB_REPO = "mohamedebeid303-svg/truthnovel-alert"
DATA_FILE = "data.json"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]

TELEGRAM_API = (
f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
)

GITHUB_API = (
f"https://api.github.com/repos/"
f"{GITHUB_REPO}/contents/{DATA_FILE}"
)

REQUEST_TIMEOUT = 30
API_TIMEOUT = 15
MAX_RETRIES = 3

MAX_DISCOVERY_PAGES = 12
MAX_CHAPTER_LINKS = 120

EXPECTED_PROBE_AHEAD = 5
EXPECTED_PROBE_MAX_MISSES = 2

EXPECTED_CHAPTER_CONFIRMED_SCORE = 1100
EXPECTED_CHAPTER_SEARCH_SCORE = 950

============================================================

API PATHS

============================================================

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

============================================================

HTTP HEADERS

============================================================

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
"/;q=0.8"
),
"Accept-Language": "ar,en;q=0.9",
"Cache-Control": "no-cache, no-store, max-age=0",
"Pragma": "no-cache",
"DNT": "1",
"Connection": "keep-alive",
}

============================================================

SESSION

============================================================

SESSION = requests.Session()
SESSION.headers.update(HTTP_HEADERS)

============================================================

GITHUB

============================================================

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
    params={
        "_": str(int(time.time() * 1000))
    },
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

    print(
        "[WARNING] data.json changed "
        "while monitoring."
    )

    print(
        "[WARNING] Changes were NOT overwritten."
    )

    return False

response.raise_for_status()

return True

============================================================

TELEGRAM

============================================================

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

============================================================

HTTP HELPERS

============================================================

def request_with_retry(
url,
timeout=REQUEST_TIMEOUT,
headers=None,
allow_redirects=True,
params=None,
):

last_error = None

request_headers = dict(HTTP_HEADERS)

if headers:
    request_headers.update(headers)

for attempt in range(
    1,
    MAX_RETRIES + 1,
):

    try:

        request_params = {}

        if params:
            request_params.update(params)

        request_params["_monitor"] = str(
            time.time_ns()
        )

        response = SESSION.get(
            url,
            headers=request_headers,
            timeout=timeout,
            allow_redirects=allow_redirects,
            params=request_params,
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

            error = requests.HTTPError(
                f"Temporary HTTP status "
                f"{response.status_code}"
            )

            error.response = response

            raise error

        response.raise_for_status()

        return response

    except requests.HTTPError as error:

        last_error = error

        status_code = None

        if error.response is not None:
            status_code = (
                error.response.status_code
            )

        if status_code not in (
            408,
            425,
            429,
            500,
            502,
            503,
            504,
        ):

            raise

        if attempt < MAX_RETRIES:

            wait_time = attempt * 2

            print(
                f"[RETRY] {url} "
                f"(attempt "
                f"{attempt + 1}/{MAX_RETRIES})"
            )

            time.sleep(wait_time)

    except (
        requests.Timeout,
        requests.ConnectionError,
    ) as error:

        last_error = error

        if attempt < MAX_RETRIES:

            wait_time = attempt * 2

            print(
                f"[RETRY] {url} "
                f"(attempt "
                f"{attempt + 1}/{MAX_RETRIES})"
            )

            time.sleep(wait_time)

    except requests.RequestException as error:

        last_error = error

        if attempt < MAX_RETRIES:

            wait_time = attempt * 2

            print(
                f"[RETRY] {url} "
                f"(attempt "
                f"{attempt + 1}/{MAX_RETRIES})"
            )

            time.sleep(wait_time)

if last_error:
    raise last_error

raise RuntimeError(
    f"Request failed: {url}"
)

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
    and "application/xhtml+xml"
    not in content_type
    and "application/json"
    not in content_type
    and not looks_like_document
):

    raise ValueError(
        f"Unsupported content type: "
        f"{content_type}"
    )

return (
    text,
    response.url,
    content_type,
)

============================================================

DIGIT NORMALIZATION

============================================================

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

number = normalize_number(
    chapter
)

if number is None:
    return "غير معروف"

if (
    isinstance(number, float)
    and number.is_integer()
):

    return str(int(number))

return str(number)

============================================================

CHAPTER PATTERNS

============================================================

STRONG_CHAPTER_PATTERNS = [

r"\bchapter\s*(?:no\.?|number)?\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",
r"\bchap(?:ter)?\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",
r"\bch\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",

r"\bepisode\s*(?:no\.?|number)?\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",
r"\bep\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",

r"الفصل\s*(?:رقم)?\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",
r"فصل\s*[:#._\-–—]?\s*(\d+(?:\.\d+)?)",

r"第\s*(\d+(?:\.\d+)?)\s*章",

r"/chapter/(\d+(?:\.\d+)?)",
r"/chapter-(\d+(?:\.\d+)?)",
r"/chapter_(\d+(?:\.\d+)?)",

r"/chap/(\d+(?:\.\d+)?)",
r"/chap-(\d+(?:\.\d+)?)",
r"/chap_(\d+(?:\.\d+)?)",

r"/ch/(\d+(?:\.\d+)?)",
r"/ch-(\d+(?:\.\d+)?)",
r"/ch_(\d+(?:\.\d+)?)",

r"[?&](?:chapter|chap|ch|episode|ep)[=_-](\d+(?:\.\d+)?)",

r"\bchapter[-_ ]number\s*[:=]\s*(\d+(?:\.\d+)?)",
r"\bchapter[-_ ]id\s*[:=]\s*(\d+(?:\.\d+)?)",

]

============================================================

CHAPTER TITLE PATTERNS

============================================================

CHAPTER_TITLE_PATTERNS = [

# 2478 - كلمة
r"^\s*(\d{1,7})\s*[-–—:._)]\s*[^\d\n]{2,}",

# 2478 كلمة
r"^\s*(\d{1,7})\s+[ء-يA-Za-z][ء-يA-Za-z\s\-–—:]{2,}",

# كلمة - 2478
r"^[^\d\n]{2,}\s*[-–—:]\s*(\d{1,7})\s*$",

]

============================================================

GENERIC NUMERIC POST URL PATTERNS

============================================================

URL_START_CHAPTER_PATTERNS = [

r"/(\d{1,7})(?:[-–—_][^/]+)?/?$",

r"/(\d{1,7})(?:[-–—_])",

r"/(\d{1,7})/",

]

============================================================

WEAK CHAPTER PATTERNS

============================================================

WEAK_CHAPTER_PATTERNS = [

r"(?<!\d)(\d{1,7})\s*[-–—:]\s*[^\d\n]{2,}",

r"(?<!\d)(\d{1,7})\s+[ء-يA-Za-z][ء-يA-Za-z\s\-–—:]{2,}",

r"[^\d\n]{2,}\s*[-–—:]\s*(\d{1,7})(?!\d)",

]

============================================================

SUSPICIOUS NUMBERS

============================================================

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

text = normalize_digits(
    str(text)
)

for pattern in (
    EXPLICIT_CHAPTER_CONTEXT_PATTERNS
):

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

number = normalize_number(
    number
)

if number is None:
    return False

if not isinstance(number, int):
    return False

if YEAR_MIN <= number <= YEAR_MAX:

    if has_explicit_chapter_context(
        context
    ):

        return False

    return True

return False

============================================================

JSON FIELD NAMES

============================================================

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

============================================================

CANDIDATE

============================================================

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

============================================================

MATCHING

============================================================

def extract_matches(
text,
patterns,
):

if not text:
    return []

text = normalize_digits(
    text
)

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

        if isinstance(
            match,
            tuple,
        ):

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

def extract_chapter_title_numbers(text):

if not text:
    return []

return list(
    dict.fromkeys(
        extract_matches(
            text,
            CHAPTER_TITLE_PATTERNS,
        )
    )
)

def extract_url_start_chapters(url):

if not url:
    return []

parsed = urlparse(url)

path = parsed.path or ""

path = normalize_digits(
    path
)

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

        if isinstance(
            match,
            tuple,
        ):

            match = match[0]

        number = normalize_number(
            match
        )

        if number is None:
            continue

        if 1 <= number <= 1000000:
            results.append(number)

return list(
    dict.fromkeys(results)
)

============================================================

EXACT NUMBER DETECTION

============================================================

def contains_exact_number(
text,
number,
):

if not text:
    return False

normalized_text = normalize_digits(
    text
)

normalized_number = normalize_number(
    number
)

if normalized_number is None:
    return False

if (
    isinstance(
        normalized_number,
        float,
    )
    and normalized_number.is_integer()
):

    normalized_number = int(
        normalized_number
    )

pattern = (
    rf"(?<!\d)"
    rf"{re.escape(str(normalized_number))}"
    rf"(?!\d)"
)

return bool(
    re.search(
        pattern,
        normalized_text,
    )
)

def text_confirms_chapter(
text,
chapter_number,
):

if not text:
    return False

normalized = normalize_digits(
    text
).strip()

# أولًا: الصيغ الصريحة
strong_matches = extract_matches(
    normalized,
    STRONG_CHAPTER_PATTERNS,
)

target = normalize_number(
    chapter_number
)

if target in strong_matches:
    return True

# ثانيًا: عناوين مثل:
# 2478 - كلمة
# 2478: كلمة
# 2478 – كلمة
title_matches = extract_chapter_title_numbers(
    normalized
)

if target in title_matches:
    return True

return False

def extract_chapter_number_from_text(text):

if not text:
    return None

strong = extract_matches(
    text,
    STRONG_CHAPTER_PATTERNS,
)

if strong:
    return strong[0]

title_numbers = extract_chapter_title_numbers(
    text
)

if title_numbers:
    return title_numbers[0]

return None

def is_next_chapter_link_text(
text,
):

if not text:
    return False

normalized = (
    normalize_digits(text)
    .strip()
    .lower()
)

next_words = (
    "التالي",
    "الفصل التالي",
    "الموضوع التالي",
    "next",
    "next chapter",
    "newer",
    "newer chapter",
)

return any(
    word in normalized
    for word in next_words
)

============================================================

JSON HELPERS

============================================================

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

    add_candidates(
        candidates,
        extract_matches(
            obj,
            STRONG_CHAPTER_PATTERNS,
        ),
        100,
        source,
        obj,
    )

    add_candidates(
        candidates,
        extract_chapter_title_numbers(
            obj
        ),
        90,
        f"{source} chapter title",
        obj,
    )

    add_candidates(
        candidates,
        extract_matches(
            obj,
            WEAK_CHAPTER_PATTERNS,
        ),
        20,
        source,
        obj,
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

add_candidates(
    candidates,
    extract_chapter_title_numbers(
        raw
    ),
    75,
    f"{source} chapter title",
    raw,
)

============================================================

HTML DETECTORS

============================================================

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
    extract_chapter_title_numbers(
        text
    ),
    150,
    "HTML chapter title",
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
        extract_chapter_title_numbers(
            text
        ),
        145,
        f"HTML {tag.name} chapter title",
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

    # ====================================================
    # IMPORTANT FIX
    #
    # إذا كان نص الرابط نفسه يقول:
    # 2478 - كلمة
    #
    # نعتمد رقم النص بدل رقم الـURL.
    #
    # هذا ضروري للمواقع التي يكون فيها:
    # URL = /2474-...
    # لكن عنوان الصفحة = 2478 - ...
    # ====================================================

    text_chapter = extract_chapter_number_from_text(
        text
    )

    if text_chapter is not None:

        score = 230

        if old is not None:

            difference = (
                text_chapter - old
            )

            if difference == 1:
                score += 180

            elif 1 < difference <= 10:
                score += 120

            elif difference < 0:
                score -= 20

        source = (
            "chapter number from link text"
        )

        if is_next_chapter_link_text(
            text
        ):

            score += 250

            source = (
                "next chapter number "
                "from link text"
            )

        candidates.append(
            Candidate(
                text_chapter,
                score,
                source,
                text,
                href,
            )
        )

        links_seen += 1

        # لا نستخدم رقم الـURL هنا إذا كان
        # نص الرابط يعطي رقم فصل واضحًا.
        continue

    # ====================================================
    # إذا لم نجد رقمًا في النص، نفحص URL
    # ====================================================

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

        add_candidates(
            candidates,
            extract_chapter_title_numbers(
                text
            ),
            140,
            "chapter title link text",
            text,
            href,
        )

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

    parsed = urlparse(
        href
    )

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

    add_candidates(
        candidates,
        extract_chapter_title_numbers(
            text
        ),
        70,
        "meta chapter title",
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

        if isinstance(
            value,
            list,
        ):

            value = " ".join(
                value
            )

        if not isinstance(
            value,
            str,
        ):

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
            extract_chapter_title_numbers(
                value
            ),
            max(
                strong_score - 20,
                20,
            ),
            f"HTML {attribute} chapter title",
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

============================================================

JSON-LD

============================================================

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

============================================================

JAVASCRIPT

============================================================

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
            extract_chapter_title_numbers(
                raw
            ),
            100,
            "JavaScript chapter title",
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

============================================================

MODERN FRAMEWORKS

============================================================

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

============================================================

RAW HTML

============================================================

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

add_candidates(
    candidates,
    extract_chapter_title_numbers(
        decoded
    ),
    50,
    "raw HTML chapter title",
    decoded,
)

============================================================

PAGE TEXT

============================================================

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

# عناوين الفصول الواضحة داخل الصفحة
add_candidates(
    candidates,
    extract_chapter_title_numbers(
        text
    ),
    60,
    "page chapter title",
    text,
)

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

============================================================

PAGINATION DISCOVERY

============================================================

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

urls.update(
    pagination
)

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

    # ====================================================
    # IMPORTANT FIX:
    #
    # حتى إذا كان الـURL لا يحتوي رقم الفصل الصحيح،
    # نضيف الرابط إذا كان نصه يحتوي عنوان فصل واضح.
    # ====================================================

    if extract_chapter_number_from_text(
        text
    ) is not None:

        urls.add(href)
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

============================================================

URL / API DISCOVERY

============================================================

def get_base_url(url):

parsed = urlparse(
    url
)

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

api_urls = list(
    api_urls
)[:20]

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

============================================================

SITEMAP

============================================================

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

============================================================

CHAPTER DISCOVERY FROM RELATED PAGES

============================================================

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

        (
            related_html,
            final_url,
            _,
        ) = download_page(
            related_url
        )

        related_soup = BeautifulSoup(
            related_html,
            "html.parser",
        )

        # =================================================
        # أولًا: رقم الفصل من النص/عنوان الصفحة
        # =================================================

        page_text_candidates = []

        if related_soup.title:

            title = related_soup.title.get_text(
                " ",
                strip=True,
            )

            page_text_candidates.append(
                (
                    title,
                    "discovered chapter title",
                    220,
                )
            )

        for tag_name in (
            "h1",
            "h2",
            "h3",
            "h4",
        ):

            for tag in related_soup.find_all(
                tag_name
            )[:3]:

                text = tag.get_text(
                    " ",
                    strip=True,
                )

                if text:

                    page_text_candidates.append(
                        (
                            text,
                            f"discovered {tag_name}",
                            220,
                        )
                    )

        for text, source, score in page_text_candidates:

            number = extract_chapter_number_from_text(
                text
            )

            if number is not None:

                candidates.append(
                    Candidate(
                        number,
                        score,
                        source,
                        text,
                        final_url,
                    )
                )

        # =================================================
        # ثانيًا: رقم الـURL كطبقة احتياطية
        # =================================================

        numbers = extract_url_start_chapters(
            final_url
        )

        add_candidates(
            candidates,
            numbers,
            160,
            "discovered chapter page URL",
            final_url,
            final_url,
        )

    except Exception as error:

        print(
            "[DISCOVERY] Skipped "
            f"{related_url}: {error}"
        )

============================================================

DIRECT PROBE - CURRENT PAGE

============================================================

def probe_current_page_for_expected(
soup,
page_url,
expected_chapter,
candidates,
):

found = False

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

    # ====================================================
    # FIX:
    # افحص نص الرابط أولًا.
    # ====================================================

    text_chapter = extract_chapter_number_from_text(
        text
    )

    if text_chapter == expected_chapter:

        score = 1500

        source = (
            "expected next chapter link text"
        )

        if is_next_chapter_link_text(
            text
        ):

            score += 200

            source = (
                "expected next chapter "
                "link text"
            )

        candidates.append(
            Candidate(
                expected_chapter,
                score,
                source,
                text,
                href,
            )
        )

        print(
            "[PROBE] Current page text "
            f"confirms chapter {expected_chapter}:"
        )

        print(
            f"        {text[:180]}"
        )

        found = True

        continue

    url_numbers = extract_url_start_chapters(
        href
    )

    if expected_chapter in url_numbers:

        if is_next_chapter_link_text(
            text
        ):

            score = 1600

            source = (
                "expected next chapter link"
            )

        else:

            score = 1300

            source = (
                "expected chapter URL"
            )

        candidates.append(
            Candidate(
                expected_chapter,
                score,
                source,
                text or href,
                href,
            )
        )

        print(
            "[PROBE] Current page contains "
            f"chapter {expected_chapter}:"
        )

        print(
            f"        {href}"
        )

        found = True

# title + headings

for tag in soup.find_all(
    [
        "title",
        "h1",
        "h2",
        "h3",
        "h4",
    ]
):

    text = tag.get_text(
        " ",
        strip=True,
    )

    if not text:
        continue

    if not text_confirms_chapter(
        text,
        expected_chapter,
    ):
        continue

    candidates.append(
        Candidate(
            expected_chapter,
            1250,
            "expected chapter heading",
            text,
            page_url,
        )
    )

    print(
        "[PROBE] Current page heading/title "
        f"confirms chapter {expected_chapter}:"
    )

    print(
        f"        {text[:180]}"
    )

    found = True

return found

============================================================

FIND OLD CHAPTER URLS

============================================================

def find_old_chapter_urls(
soup,
page_url,
old_chapter,
):

old_number = normalize_number(
    old_chapter
)

if old_number is None:
    return []

urls = []

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

    # ====================================================
    # FIX:
    # رقم الفصل في نص الرابط له الأولوية.
    # ====================================================

    text_number = extract_chapter_number_from_text(
        text
    )

    if text_number == old_number:

        urls.append(href)

        if len(urls) >= 5:
            break

        continue

    numbers = extract_url_start_chapters(
        href
    )

    if old_number in numbers:

        urls.append(href)

        if len(urls) >= 5:
            break

return list(
    dict.fromkeys(urls)
)

============================================================

DIRECT PROBE - PREVIOUS CHAPTER PAGE

============================================================

def probe_previous_chapter_page(
soup,
page_url,
old_chapter,
expected_chapter,
candidates,
):

old_urls = find_old_chapter_urls(
    soup,
    page_url,
    old_chapter,
)

if not old_urls:

    print(
        "[PROBE] Recorded chapter page "
        f"{old_chapter} was not found "
        "on the main page."
    )

    return False

found = False

for old_url in old_urls:

    try:

        print(
            "[PROBE] Checking recorded "
            "chapter page:"
        )

        print(
            f"        {old_url}"
        )

        (
            old_html,
            old_final_url,
            _,
        ) = download_page(
            old_url
        )

        old_soup = BeautifulSoup(
            old_html,
            "html.parser",
        )

        for link in old_soup.find_all(
            "a"
        ):

            href = link.get("href")

            if not href:
                continue

            href = urljoin(
                old_final_url,
                href,
            )

            if not is_same_domain(
                old_final_url,
                href,
            ):
                continue

            text = link.get_text(
                " ",
                strip=True,
            )

            # =================================================
            # FIX:
            # افحص نص الرابط أولًا.
            # =================================================

            text_number = extract_chapter_number_from_text(
                text
            )

            if text_number == expected_chapter:

                score = 1750

                source = (
                    "expected next chapter "
                    "from previous page text"
                )

                if is_next_chapter_link_text(
                    text
                ):

                    score += 200

                candidates.append(
                    Candidate(
                        expected_chapter,
                        score,
                        source,
                        text,
                        href,
                    )
                )

                print(
                    "[PROBE] Previous chapter page "
                    f"text points to {expected_chapter}:"
                )

                print(
                    f"        {text[:180]}"
                )

                found = True

                continue

            url_numbers = (
                extract_url_start_chapters(
                    href
                )
            )

            if expected_chapter not in url_numbers:
                continue

            if is_next_chapter_link_text(
                text
            ):

                score = 1600

                source = (
                    "expected next chapter "
                    "from previous page"
                )

            else:

                score = 1450

                source = (
                    "expected chapter "
                    "from previous page"
                )

            candidates.append(
                Candidate(
                    expected_chapter,
                    score,
                    source,
                    text or href,
                    href,
                )
            )

            print(
                "[PROBE] Previous chapter page "
                f"points to {expected_chapter}:"
            )

            print(
                f"        {href}"
            )

            found = True

    except Exception as error:

        print(
            "[PROBE] Previous chapter page "
            f"failed: {error}"
        )

return found

============================================================

WORDPRESS SEARCH RESULT HELPERS

============================================================

def get_wp_result_url(item):

if not isinstance(
    item,
    dict,
):
    return ""

for key in (
    "url",
    "link",
):

    value = item.get(
        key
    )

    if value:
        return str(
            value
        )

return ""

def get_wp_result_title(item):

if not isinstance(
    item,
    dict,
):
    return ""

title_data = item.get(
    "title",
    "",
)

if isinstance(
    title_data,
    dict,
):

    return str(
        title_data.get(
            "rendered",
            "",
        )
        or ""
    )

return str(
    title_data or ""
)

def wp_result_is_exact_chapter(
item,
chapter_number,
):

item_url = get_wp_result_url(
    item
)

title = get_wp_result_title(
    item
)

# ========================================================
# IMPORTANT:
# العنوان أصبح له الأولوية على رقم الـURL.
# ========================================================

if text_confirms_chapter(
    title,
    chapter_number,
):

    return True

if chapter_number in extract_url_start_chapters(
    item_url
):

    return True

return False

============================================================

WORDPRESS DIRECT SEARCH

============================================================

def probe_wordpress_search(
page_url,
chapter_number,
candidates,
):

base_url = get_base_url(
    page_url
)

search_url = urljoin(
    base_url,
    "/wp-json/wp/v2/search",
)

print(
    "[WP PROBE] Searching for "
    f"chapter {chapter_number}..."
)

try:

    response = request_with_retry(
        search_url,
        timeout=API_TIMEOUT,
        params={
            "search": str(
                chapter_number
            ),
            "per_page": "50",
        },
    )

except Exception as error:

    print(
        "[WP PROBE] Search failed for "
        f"{chapter_number}: {error}"
    )

    return False

try:

    data = response.json()

except Exception:

    print(
        "[WP PROBE] Invalid JSON for "
        f"{chapter_number}."
    )

    return False

if not isinstance(
    data,
    list,
):

    return False

found = False

for item in data:

    if not wp_result_is_exact_chapter(
        item,
        chapter_number,
    ):
        continue

    item_url = get_wp_result_url(
        item
    )

    title = get_wp_result_title(
        item
    )

    confirmed = False
    final_url = item_url

    if item_url:

        try:

            (
                chapter_html,
                checked_url,
                _,
            ) = download_page(
                item_url
            )

            final_url = (
                checked_url
                or item_url
            )

            chapter_soup = BeautifulSoup(
                chapter_html,
                "html.parser",
            )

            # --------------------------------------------
            # العنوان هو المرجع الأقوى
            # --------------------------------------------

            texts_to_check = []

            if chapter_soup.title:

                texts_to_check.append(
                    chapter_soup.title.get_text(
                        " ",
                        strip=True,
                    )
                )

            for tag_name in (
                "h1",
                "h2",
                "h3",
                "h4",
            ):

                for tag in chapter_soup.find_all(
                    tag_name
                )[:3]:

                    texts_to_check.append(
                        tag.get_text(
                            " ",
                            strip=True,
                        )
                    )

            for text in texts_to_check:

                if text_confirms_chapter(
                    text,
                    chapter_number,
                ):

                    confirmed = True
                    break

            # --------------------------------------------
            # URL كمرجع احتياطي
            # --------------------------------------------

            if not confirmed:

                if chapter_number in extract_url_start_chapters(
                    final_url
                ):

                    confirmed = True

        except Exception as error:

            print(
                "[WP PROBE] Could not verify "
                f"chapter page {chapter_number}: "
                f"{error}"
            )

    # حتى إذا لم نتمكن من فتح الصفحة،
    # عنوان نتيجة WordPress نفسه يمكن أن يؤكد الفصل.
    if not confirmed:

        if text_confirms_chapter(
            title,
            chapter_number,
        ):

            confirmed = True

    if confirmed:

        score = (
            EXPECTED_CHAPTER_CONFIRMED_SCORE
        )

        source = (
            "WordPress expected chapter "
            "confirmed"
        )

    else:

        score = (
            EXPECTED_CHAPTER_SEARCH_SCORE
        )

        source = (
            "WordPress expected chapter"
        )

    candidates.append(
        Candidate(
            chapter_number,
            score,
            source,
            title or item_url,
            final_url or item_url,
        )
    )

    print(
        "[WP PROBE] FOUND chapter "
        f"{chapter_number}"
        f" | confirmed={confirmed}"
    )

    if title:

        print(
            f"        {title[:180]}"
        )

    if final_url:

        print(
            f"        {final_url[:250]}"
        )

    found = True

    break

if not found:

    print(
        "[WP PROBE] Chapter "
        f"{chapter_number} not found."
    )

return found

============================================================

DIRECT EXPECTED CHAPTER PROBE

============================================================

def probe_expected_chapters(
page_url,
soup,
old_chapter,
candidates,
):

old_number = normalize_number(
    old_chapter
)

if old_number is None:
    return

if not isinstance(
    old_number,
    (int, float),
):
    return

if (
    isinstance(
        old_number,
        float,
    )
    and not old_number.is_integer()
):

    return

old_number = int(
    old_number
)

first_expected = (
    old_number + 1
)

print("")
print(
    "[PROBE] ========================================="
)

print(
    "[PROBE] DIRECT EXPECTED-CHAPTER PROBE"
)

print(
    f"[PROBE] Recorded chapter: {old_number}"
)

print(
    f"[PROBE] Will check up to: "
    f"{old_number + EXPECTED_PROBE_AHEAD}"
)

# --------------------------------------------------------
# 1. الصفحة الرئيسية
# --------------------------------------------------------

probe_current_page_for_expected(
    soup,
    page_url,
    first_expected,
    candidates,
)

# --------------------------------------------------------
# 2. صفحة الفصل السابق
# --------------------------------------------------------

probe_previous_chapter_page(
    soup,
    page_url,
    old_number,
    first_expected,
    candidates,
)

# --------------------------------------------------------
# 3. WordPress Search
# --------------------------------------------------------

consecutive_misses = 0

for offset in range(
    EXPECTED_PROBE_AHEAD
):

    chapter_number = (
        first_expected
        + offset
    )

    found = probe_wordpress_search(
        page_url,
        chapter_number,
        candidates,
    )

    if found:

        consecutive_misses = 0

    else:

        consecutive_misses += 1

        if (
            consecutive_misses
            >= EXPECTED_PROBE_MAX_MISSES
        ):

            print(
                "[PROBE] Stopping future "
                "chapter search after "
                f"{consecutive_misses} "
                "consecutive misses."
            )

            break

print(
    "[PROBE] ========================================="
)

============================================================

CANDIDATE FILTERING

============================================================

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

============================================================

EVIDENCE SCORING

============================================================

def evidence_key(item):

context = re.sub(
    r"\s+",
    " ",
    item.context.strip().lower(),
)

url = (
    item.url
    .strip()
    .lower()
)

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

unique_evidence = {}

for item in items:

    key = evidence_key(
        item
    )

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

source_count = len(
    source_best
)

if source_count >= 2:
    total_score += 50

if source_count >= 3:
    total_score += 70

if source_count >= 4:
    total_score += 90

if old is not None:

    difference = number - old

    if difference == 0:

        total_score += 250

    elif difference == 1:

        total_score += 500

    elif 1 < difference <= 10:

        total_score += 300

    elif 10 < difference <= 100:

        total_score += 120

    elif difference < 0:

        distance = abs(
            difference
        )

        if distance <= 2:

            total_score -= 40

        elif distance <= 10:

            total_score -= 120

        elif distance <= 100:

            total_score -= 500

        else:

            total_score -= 1200

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
                or "expected chapter"
                in evidence.source
                or "WordPress expected"
                in evidence.source
                or "chapter title"
                in evidence.source
                or "chapter number"
                in evidence.source
            )
            for evidence in unique_items
        )

        if explicit_evidence:

            total_score += 20

        else:

            total_score -= 1000

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

total_score += min(
    len(unique_items) * 8,
    80,
)

return total_score

============================================================

DIRECT PROBE EVIDENCE

============================================================

def has_direct_probe_evidence(
item,
):

for evidence in item.get(
    "evidence",
    [],
):

    source = (
        evidence.source
        or ""
    ).lower()

    if (
        "expected" in source
        or "wordpress expected" in source
        or "next chapter" in source
    ):

        return True

return False

============================================================

CANDIDATE RANKING

============================================================

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

# ========================================================
# IMPORTANT:
#
# إذا وُجدت فصول مستقبلية مؤكدة عبر الـProbe،
# نختار أعلى فصل مستقبلي مؤكد.
# ========================================================

direct_probe_candidates = [
    item
    for item in ranked
    if (
        has_direct_probe_evidence(
            item
        )
        and old is not None
        and item["number"] > old
    )
]

if direct_probe_candidates:

    highest_verified = max(
        direct_probe_candidates,
        key=lambda item: item["number"],
    )

    ranked.remove(
        highest_verified
    )

    ranked.insert(
        0,
        highest_verified,
    )

    print(
        "[RANKING] Direct probe verified "
        f"new chapter: "
        f"{display_chapter(highest_verified['number'])}"
    )

return ranked

============================================================

FINAL DETECTION

============================================================

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

extract_from_json_ld(
    soup,
    candidates,
)

extract_from_framework_data(
    soup,
    candidates,
)

extract_from_scripts(
    soup,
    candidates,
)

extract_from_raw_html(
    html,
    candidates,
)

extract_from_page_text(
    soup,
    candidates,
)

discover_chapters_from_related_pages(
    page_url,
    soup,
    old_chapter,
    candidates,
)

extract_from_api(
    page_url,
    soup,
    candidates,
)

extract_from_sitemap(
    page_url,
    candidates,
)

probe_expected_chapters(
    page_url,
    soup,
    old_chapter,
    candidates,
)

ranked = rank_candidates(
    candidates,
    old_chapter,
)

if not ranked:

    return (
        None,
        None,
        [],
    )

best = ranked[0]

return (
    best["number"],
    best,
    ranked[:10],
)

============================================================

DETECTION LOG

============================================================

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
            f"    URL: "
            f"{evidence.url[:220]}"
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

    for item in ranked[1:8]:

        print(
            "  - "
            f"{display_chapter(item['number'])} "
            f"(score {item['score']})"
        )

============================================================

MONITOR ONE WORK

============================================================

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
    "[OLD] Last recorded chapter: "
    f"{display_chapter(old_chapter)}"
)

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
    f"[HTTP] Content-Type: "
    f"{content_type}"
)

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

    print(
        error
    )

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

if latest_chapter is None:

    print(
        "[UNKNOWN] "
        f"Could not detect chapter "
        f"for {name}"
    )

    return False

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

if latest_chapter <= old_number:

    print(
        "[OK] No new chapter."
    )

    return False

difference = (
    latest_chapter
    - old_number
)

print(
    "[NEW] "
    f"Detected {difference} "
    "new chapter(s)."
)

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

    print(
        error
    )

    return False

work["last_chapter"] = (
    latest_chapter
)

print(
    "[SAVED] "
    "Chapter updated to "
    f"{display_chapter(latest_chapter)}."
)

return True

============================================================

ALL USERS

============================================================

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

            print(
                error
            )

return changed

============================================================

MAIN

============================================================

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

print(
    "[INFO] Direct expected-chapter "
    "probing is ENABLED."
)

print(
    "[INFO] Expected probe ahead: "
    f"{EXPECTED_PROBE_AHEAD}"
)

try:

    data, sha = load_data()

except Exception as error:

    print(
        "[FATAL] "
        "Could not load data.json"
    )

    print(
        error
    )

    raise

try:

    changed = monitor_all_users(
        data
    )

except Exception as error:

    print(
        "[FATAL] "
        "Monitoring failed."
    )

    print(
        error
    )

    raise

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

        print(
            error
        )

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

============================================================

ENTRY POINT

============================================================

if name == "main":
main()