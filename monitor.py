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

============================================================

CONFIGURATION

============================================================

GITHUB_REPO = "mohamedebeid303-svg/truthnovel-alert"
DATA_FILE = "data.json"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]

REQUEST_TIMEOUT = 30
API_TIMEOUT = 15
MAX_RETRIES = 3

MAX_DISCOVERY_PAGES = 12
MAX_CHAPTER_LINKS = 120

EXPECTED_PROBE_AHEAD = 5
EXPECTED_PROBE_MAX_MISSES = 2
EXPECTED_CHAPTER_CONFIRMED_SCORE = 1100
EXPECTED_CHAPTER_SEARCH_SCORE = 950

USER_AGENT = (
"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
"AppleWebKit/537.36 (KHTML, like Gecko) "
"Chrome/140.0.0.0 Safari/537.36"
)

SESSION = requests.Session()

SESSION.headers.update(
{
"User-Agent": USER_AGENT,
"Accept": (
"text/html,application/xhtml+xml,application/xml;"
"q=0.9,/;q=0.8"
),
"Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
"Cache-Control": "no-cache",
"Pragma": "no-cache",
}
)

============================================================

WORDPRESS API

============================================================

لا نستخدم /api/* العشوائية لأنها كانت تعطي 404 باستمرار.

نحتفظ فقط بالمسارات المفيدة لمواقع WordPress.

============================================================

WORDPRESS_API_PATHS = [
"/wp-json/",
"/wp-json/wp/v2/search",
]

============================================================

HTTP STATUS HANDLING

============================================================

PERMANENT_HTTP_STATUSES = {
400,
401,
403,
404,
405,
406,
410,
411,
412,
413,
415,
422,
423,
424,
426,
428,
431,
451,
}

TEMPORARY_HTTP_STATUSES = {
429,
500,
502,
503,
504,
}

============================================================

SOCIAL / EXTERNAL DOMAINS

============================================================

SOCIAL_DOMAINS = {
"x.com",
"twitter.com",
"facebook.com",
"instagram.com",
"youtube.com",
"youtu.be",
"t.me",
"telegram.me",
"reddit.com",
"pinterest.com",
"linkedin.com",
"discord.com",
"discord.gg",
}

============================================================

GITHUB

============================================================

def github_headers():
return {
"Authorization": f"Bearer {GITHUB_TOKEN}",
"Accept": "application/vnd.github+json",
"X-GitHub-Api-Version": "2022-11-28",
}

def github_file_url():
return (
f"https://api.github.com/repos/"
f"{GITHUB_REPO}/contents/{DATA_FILE}"
)

def load_data():
try:
response = requests.get(
github_file_url(),
headers=github_headers(),
timeout=API_TIMEOUT,
)

    response.raise_for_status()

    payload = response.json()

    content = base64.b64decode(
        payload["content"]
    ).decode("utf-8")

    data = json.loads(content)

    print("[GITHUB] data.json loaded successfully.")

    return data, payload.get("sha")

except Exception as exc:
    print(
        f"[GITHUB] Failed to load data.json: "
        f"{exc}"
    )

    return None, None

def save_data(data, sha):
try:
raw = json.dumps(
data,
ensure_ascii=False,
indent=2,
)

    encoded = base64.b64encode(
        raw.encode("utf-8")
    ).decode("ascii")

    body = {
        "message": "Update chapter monitoring data",
        "content": encoded,
    }

    if sha:
        body["sha"] = sha

    response = requests.put(
        github_file_url(),
        headers=github_headers(),
        json=body,
        timeout=API_TIMEOUT,
    )

    if response.status_code == 409:
        print(
            "[GITHUB] Save conflict (409). "
            "Data was changed remotely."
        )

        return False

    response.raise_for_status()

    print(
        "[GITHUB] data.json saved successfully."
    )

    return True

except Exception as exc:
    print(
        f"[GITHUB] Failed to save data.json: "
        f"{exc}"
    )

    return False

============================================================

TELEGRAM

============================================================

def send_message(chat_id, text):
url = (
f"https://api.telegram.org/"
f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
)

payload = {
    "chat_id": chat_id,
    "text": text,
    "disable_web_page_preview": True,
}

response = requests.post(
    url,
    json=payload,
    timeout=API_TIMEOUT,
)

response.raise_for_status()

result = response.json()

if not result.get("ok"):
    raise RuntimeError(
        f"Telegram API error: {result}"
    )

print(
    f"[TELEGRAM] Message sent to {chat_id}."
)

return True

============================================================

HTTP

============================================================

def request_with_retry(
url,
method="GET",
timeout=REQUEST_TIMEOUT,
**kwargs,
):
last_error = None

# نحتفظ بالـ params الأصلية حتى لا تختفي بعد أول محاولة.
original_params = dict(
    kwargs.pop("params", {}) or {}
)

for attempt in range(
    1,
    MAX_RETRIES + 1,
):
    try:
        params = dict(original_params)

        # منع الكاش.
        params["_monitor"] = time.time_ns()

        response = SESSION.request(
            method,
            url,
            params=params,
            timeout=timeout,
            allow_redirects=True,
            **kwargs,
        )

        status = response.status_code

        # ------------------------------------------------
        # أخطاء دائمة مثل 404:
        # لا داعي لإعادة المحاولة.
        # ------------------------------------------------
        if status in PERMANENT_HTTP_STATUSES:
            print(
                f"[HTTP] Permanent HTTP status "
                f"{status} for {url}. "
                f"No retry."
            )

            return None

        # ------------------------------------------------
        # أخطاء مؤقتة:
        # يمكن إعادة المحاولة.
        # ------------------------------------------------
        if status in TEMPORARY_HTTP_STATUSES:
            raise requests.HTTPError(
                f"Temporary HTTP status "
                f"{status}",
                response=response,
            )

        response.raise_for_status()

        return response

    except Exception as exc:
        last_error = exc

        print(
            f"[HTTP] Attempt "
            f"{attempt}/{MAX_RETRIES} "
            f"failed for {url}: {exc}"
        )

        if attempt < MAX_RETRIES:
            time.sleep(attempt)

print(
    f"[HTTP] Giving up on {url}: "
    f"{last_error}"
)

return None

def download_page(url):
response = request_with_retry(url)

if response is None:
    return None, None

content_type = (
    response.headers.get("Content-Type")
    or ""
).lower()

body = response.text or ""

if (
    "html" not in content_type
    and "xml" not in content_type
    and "json" not in content_type
    and not body.lstrip().startswith(
        ("<", "{", "[")
    )
):
    print(
        f"[HTTP] Unsupported content type "
        f"for {url}: {content_type}"
    )

    return None, None

return body, response.url

============================================================

URL HELPERS

============================================================

def normalize_host(host):
host = (host or "").lower().strip()

if host.startswith("www."):
    host = host[4:]

return host

def same_domain(url_a, url_b):
host_a = normalize_host(
urlparse(url_a).netloc
)

host_b = normalize_host(
    urlparse(url_b).netloc
)

if not host_a or not host_b:
    return False

return host_a == host_b

def is_social_domain(url):
host = normalize_host(
urlparse(url).netloc
)

if not host:
    return False

if host in SOCIAL_DOMAINS:
    return True

return any(
    host.endswith(
        "." + domain
    )
    for domain in SOCIAL_DOMAINS
)

def is_valid_chapter_url(
url,
monitored_url,
):
if not url:
return False

parsed = urlparse(url)

if parsed.scheme not in {
    "http",
    "https",
}:
    return False

# لا نأخذ أرقامًا من X أو Facebook أو غيرها.
if is_social_domain(url):
    return False

# لا نعتبر مواقع خارج الموقع المراقَب مصدرًا لفصل.
if not same_domain(
    url,
    monitored_url,
):
    return False

return True

def strip_monitor_parameter(url):
if not url:
return url

parsed = urlparse(url)

query = parse_qs(
    parsed.query,
    keep_blank_values=True,
)

query.pop(
    "_monitor",
    None,
)

query_parts = []

for key, values in query.items():
    for value in values:
        query_parts.append(
            f"{key}={value}"
        )

new_query = "&".join(
    query_parts
)

return parsed._replace(
    query=new_query
).geturl()

============================================================

NUMBER NORMALIZATION

============================================================

ARABIC_DIGITS = str.maketrans(
"٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
"01234567890123456789",
)

def normalize_number(value):
if value is None:
return None

value = (
    html_module.unescape(
        str(value)
    )
    .translate(ARABIC_DIGITS)
)

value = (
    value
    .replace(",", "")
    .replace("٬", "")
    .strip()
)

match = re.search(
    r"\d+",
    value,
)

if not match:
    return None

try:
    return int(
        match.group(0)
    )

except ValueError:
    return None

============================================================

CHAPTER PATTERNS

============================================================

STRONG_TEXT_PATTERNS = [
re.compile(
r"\bchapter\s*"
r"(?:number|no.?|id)?\s*"
r"[:#-]?\s*(\d+)\b",
re.I,
),
re.compile(
r"\bch.?\s*[:#-]?\s*(\d+)\b",
re.I,
),
re.compile(
r"\bchapter[-_\s]+(\d+)\b",
re.I,
),
re.compile(
r"\bepisode\s*"
r"(?:number|no.?|id)?\s*"
r"[:#-]?\s*(\d+)\b",
re.I,
),
re.compile(
r"الفصل\s*"
r"(?:رقم|رقم الفصل)?\s*"
r"[:#-]?\s*(\d+)",
re.I,
),
re.compile(
r"فصل\s*[:#-]?\s*(\d+)",
re.I,
),
re.compile(
r"第\s*(\d+)\s*章"
),
]

CHAPTER_TITLE_PATTERNS = [
re.compile(
r"^\s*(\d{1,7})\s*[-–—:]\s*.+$"
),
re.compile(
r"^\s*(\d{1,7})\s+.+$"
),
re.compile(
r"^\s*.+?\s*[-–—:]\s*(\d{1,7})\s*$"
),
]

URL_PATTERNS = [
re.compile(
r"(?:chapter|chap|ch|episode)"
r"[-=/]*(\d+)",
re.I,
),
re.compile(
r"/(\d{1,7})(?:[/?#]|$)"
),
re.compile(
r""-_/" (\d{1,7})(?:[-/?.]|$)"
),
]

QUERY_KEYS = {
"chapter",
"chapter_id",
"chapterid",
"chap",
"ch",
"episode",
"episode_id",
"episodeid",
}

@dataclass
class Candidate:
number: int
url: str
source: str
score: int = 0
evidence: str = ""

============================================================

BASIC HELPERS

============================================================

def clean_text(value):
return re.sub(
r"\s+",
" ",
html_module.unescape(
str(value or "")
),
).strip()

def unique_candidates(candidates):
result = {}

for candidate in candidates:
    clean_url = strip_monitor_parameter(
        candidate.url
    )

    key = (
        candidate.number,
        clean_url.rstrip("/"),
    )

    old = result.get(key)

    if (
        old is None
        or candidate.score > old.score
    ):
        candidate.url = clean_url
        result[key] = candidate

return list(
    result.values()
)

def add_candidate(
candidates,
number,
url,
source,
score,
evidence="",
):
if number is None or number < 0:
return

candidates.append(
    Candidate(
        number=int(number),
        url=strip_monitor_parameter(
            url
        ),
        source=source,
        score=score,
        evidence=clean_text(
            evidence
        )[:250],
    )
)

============================================================

EXACT CHAPTER DETECTION

============================================================

def extract_chapter_numbers(text):
text = clean_text(text)

found = []

for pattern in STRONG_TEXT_PATTERNS:
    for match in pattern.finditer(
        text
    ):
        number = normalize_number(
            match.group(1)
        )

        if number is not None:
            found.append(number)

return found

def text_confirms_chapter(
text,
expected,
):
text = clean_text(text)

if not text:
    return False

for pattern in STRONG_TEXT_PATTERNS:
    for match in pattern.finditer(
        text
    ):
        if (
            normalize_number(
                match.group(1)
            )
            == expected
        ):
            return True

for pattern in CHAPTER_TITLE_PATTERNS:
    match = pattern.match(text)

    if match:
        groups = [
            normalize_number(g)
            for g in match.groups()
        ]

        if expected in groups:
            return True

return False

def extract_chapter_number_from_text(
text
):
text = clean_text(text)

numbers = extract_chapter_numbers(
    text
)

if numbers:
    return numbers[0]

for pattern in CHAPTER_TITLE_PATTERNS:
    match = pattern.match(text)

    if match:
        for group in match.groups():
            number = normalize_number(
                group
            )

            if number is not None:
                return number

return None

def is_next_chapter_link_text(text):
text = clean_text(
text
).lower()

return any(
    phrase in text
    for phrase in (
        "التالي",
        "الفصل التالي",
        "next",
        "next chapter",
        "newer",
        "new chapter",
    )
)

============================================================

PAGE URL CHAPTER NUMBER

============================================================

def extract_chapter_number_from_url(
url
):
if not url:
return None

for pattern in URL_PATTERNS:
    match = pattern.search(url)

    if match:
        number = normalize_number(
            match.group(1)
        )

        if number is not None:
            return number

parsed = urlparse(url)

query = parse_qs(
    parsed.query
)

for key, values in query.items():
    if key.lower() in QUERY_KEYS:
        for value in values:
            number = normalize_number(
                value
            )

            if number is not None:
                return number

return None

============================================================

LINK EXTRACTION

============================================================

def extract_from_links(
soup,
page_url,
candidates,
expected_numbers=None,
):
expected_numbers = set(
expected_numbers or []
)

count = 0

for link in soup.find_all(
    "a",
    href=True,
):
    if count >= MAX_CHAPTER_LINKS:
        break

    href = link.get(
        "href",
        "",
    ).strip()

    if not href or href.startswith(
        (
            "javascript:",
            "mailto:",
            "tel:",
            "#",
        )
    ):
        continue

    absolute_url = urljoin(
        page_url,
        href,
    )

    # ----------------------------------------------------
    # مهم:
    # لا نأخذ روابط خارج الموقع أو روابط التواصل
    # الاجتماعي كروابط فصول.
    # ----------------------------------------------------
    if not is_valid_chapter_url(
        absolute_url,
        page_url,
    ):
        continue

    text = clean_text(
        link.get_text(
            " ",
            strip=True,
        )
    )

    title = clean_text(
        link.get(
            "title",
            "",
        )
    )

    aria = clean_text(
        link.get(
            "aria-label",
            "",
        )
    )

    combined_text = clean_text(
        " ".join(
            x
            for x in (
                text,
                title,
                aria,
            )
            if x
        )
    )

    # ----------------------------------------------------
    # أولًا:
    # رقم الفصل في نص الرابط.
    # ----------------------------------------------------
    text_number = (
        extract_chapter_number_from_text(
            combined_text
        )
    )

    if text_number is not None:
        score = 600

        if text_confirms_chapter(
            combined_text,
            text_number,
        ):
            score += 250

        if text_number in expected_numbers:
            score += 500

        if is_next_chapter_link_text(
            combined_text
        ):
            score += 100

        add_candidate(
            candidates,
            text_number,
            absolute_url,
            "link-text",
            score,
            combined_text,
        )

        count += 1
        continue

    # ----------------------------------------------------
    # ثانيًا:
    # رقم الفصل في URL.
    # ----------------------------------------------------
    for pattern in URL_PATTERNS:
        match = pattern.search(
            absolute_url
        )

        if match:
            number = normalize_number(
                match.group(1)
            )

            if number is not None:
                score = 400

                if number in expected_numbers:
                    score += 350

                add_candidate(
                    candidates,
                    number,
                    absolute_url,
                    "link-url",
                    score,
                    absolute_url,
                )

                count += 1
                break

    else:
        # ------------------------------------------------
        # ثالثًا:
        # رقم الفصل في query parameter.
        # ------------------------------------------------
        parsed = urlparse(
            absolute_url
        )

        query = parse_qs(
            parsed.query
        )

        found_query = False

        for key, values in query.items():
            if key.lower() in QUERY_KEYS:
                for value in values:
                    number = normalize_number(
                        value
                    )

                    if number is not None:
                        score = 450

                        if (
                            number
                            in expected_numbers
                        ):
                            score += 350

                        add_candidate(
                            candidates,
                            number,
                            absolute_url,
                            "query-param",
                            score,
                            f"{key}={value}",
                        )

                        count += 1
                        found_query = True
                        break

            if found_query:
                break

============================================================

JSON / JSON-LD / JAVASCRIPT DETECTION

============================================================

def walk_json(
value,
candidates,
page_url,
path="",
):
if isinstance(value, dict):

    for key, item in value.items():
        key_lower = str(
            key
        ).lower()

        if key_lower in {
            "chapter",
            "chapternumber",
            "chapter_number",
            "chapterid",
            "chapter_id",
            "episode",
            "episode_number",
        }:
            number = normalize_number(
                item
            )

            if number is not None:
                add_candidate(
                    candidates,
                    number,
                    page_url,
                    "json",
                    750,
                    f"{path}/{key}: {item}",
                )

        walk_json(
            item,
            candidates,
            page_url,
            f"{path}/{key}",
        )

elif isinstance(value, list):

    for index, item in enumerate(
        value
    ):
        walk_json(
            item,
            candidates,
            page_url,
            f"{path}/{index}",
        )

def inspect_json_scripts(
soup,
page_url,
candidates,
):
for script in soup.find_all(
"script"
):
script_type = (
script.get("type")
or ""
).lower()

    content = (
        script.string
        or script.get_text()
    )

    if not content:
        continue

    if "ld+json" in script_type:
        try:
            parsed = json.loads(
                content
            )

            walk_json(
                parsed,
                candidates,
                page_url,
            )

        except Exception:
            pass

    for pattern in STRONG_TEXT_PATTERNS:
        for match in pattern.finditer(
            content
        ):
            number = normalize_number(
                match.group(1)
            )

            if number is not None:
                add_candidate(
                    candidates,
                    number,
                    page_url,
                    "javascript",
                    500,
                    match.group(0),
                )

def inspect_framework_data(
soup,
page_url,
candidates,
):
for tag in soup.find_all(
attrs={"id": True}
):
tag_id = str(
tag.get(
"id",
"",
)
).lower()

    if tag_id in {
        "__next_data__",
        "__nuxt_data__",
        "__data__",
    }:
        content = tag.get_text()

        try:
            parsed = json.loads(
                content
            )

            walk_json(
                parsed,
                candidates,
                page_url,
            )

        except Exception:
            pass

for tag in soup.find_all(
    attrs={
        "data-chapter": True
    }
):
    number = normalize_number(
        tag.get(
            "data-chapter"
        )
    )

    if number is not None:
        add_candidate(
            candidates,
            number,
            page_url,
            "data-attribute",
            700,
            str(tag),
        )

============================================================

PAGE TEXT / HEADINGS

============================================================

def inspect_page_text(
soup,
page_url,
candidates,
):
page_url_chapter = (
extract_chapter_number_from_url(
page_url
)
)

title = clean_text(
    soup.title.get_text(
        " ",
        strip=True,
    )
    if soup.title
    else ""
)

if title:
    number = (
        extract_chapter_number_from_text(
            title
        )
    )

    if number is not None:

        # إذا كان URL الصفحة يحدد فصلًا آخر،
        # لا نثق بالعنوان وحده إذا كان مختلفًا.
        if (
            page_url_chapter is None
            or number == page_url_chapter
        ):
            add_candidate(
                candidates,
                number,
                page_url,
                "title",
                800,
                title,
            )

for heading in soup.find_all(
    [
        "h1",
        "h2",
        "h3",
        "h4",
    ]
):
    text = clean_text(
        heading.get_text(
            " ",
            strip=True,
        )
    )

    number = (
        extract_chapter_number_from_text(
            text
        )
    )

    if number is None:
        continue

    # ----------------------------------------------------
    # حماية من مشكلة الموقع:
    #
    # صفحة قديمة مثل 2477 يمكن أن تحتوي في قالبها
    # على heading مشترك يقول 2478.
    #
    # إذا كان URL يحدد فصلًا مختلفًا، لا نعتبر
    # heading وحده دليلًا قويًا على فصل جديد.
    # ----------------------------------------------------
    if (
        page_url_chapter is not None
        and number != page_url_chapter
    ):
        continue

    add_candidate(
        candidates,
        number,
        page_url,
        "heading",
        850,
        text,
    )

body_text = clean_text(
    soup.get_text(
        " ",
        strip=True,
    )
)

for number in extract_chapter_numbers(
    body_text
):
    add_candidate(
        candidates,
        number,
        page_url,
        "body-text",
        250,
        "chapter pattern in page text",
    )

============================================================

PAGINATION / RELATED PAGES

============================================================

def discover_related_pages(
soup,
page_url,
):
pages = []

for link in soup.find_all(
    "a",
    href=True,
):
    href = link.get(
        "href",
        "",
    ).strip()

    if not href or href.startswith(
        (
            "javascript:",
            "mailto:",
            "tel:",
            "#",
        )
    ):
        continue

    absolute = urljoin(
        page_url,
        href,
    )

    if not is_valid_chapter_url(
        absolute,
        page_url,
    ):
        continue

    text = clean_text(
        link.get_text(
            " ",
            strip=True,
        )
    )

    lower = text.lower()

    is_relevant = (
        is_next_chapter_link_text(
            text
        )
        or "older" in lower
        or "previous" in lower
        or "الفصل السابق" in text
        or "السابق" in text
        or extract_chapter_number_from_text(
            text
        )
        is not None
    )

    if is_relevant:
        pages.append(
            absolute
        )

return list(
    dict.fromkeys(pages)
)[:MAX_DISCOVERY_PAGES]

def inspect_discovered_pages(
page_url,
candidates,
):
body, final_url = download_page(
page_url
)

if not body:
    return None

soup = BeautifulSoup(
    body,
    "html.parser",
)

actual_url = (
    final_url
    or page_url
)

extract_from_links(
    soup,
    actual_url,
    candidates,
)

inspect_page_text(
    soup,
    actual_url,
    candidates,
)

inspect_json_scripts(
    soup,
    actual_url,
    candidates,
)

inspect_framework_data(
    soup,
    actual_url,
    candidates,
)

return soup

============================================================

WORDPRESS API / SITEMAP

============================================================

def probe_api_and_sitemap(
base_url,
candidates,
):
parsed = urlparse(
base_url
)

origin = (
    f"{parsed.scheme}://"
    f"{parsed.netloc}"
)

# --------------------------------------------------------
# WordPress فقط.
# --------------------------------------------------------
for path in WORDPRESS_API_PATHS:
    url = urljoin(
        origin,
        path,
    )

    response = request_with_retry(
        url,
        timeout=API_TIMEOUT,
    )

    if response is None:
        continue

    content_type = (
        response.headers.get(
            "Content-Type"
        )
        or ""
    ).lower()

    text = response.text or ""

    if (
        "json" in content_type
        or text.lstrip().startswith(
            ("{", "[")
        )
    ):
        try:
            walk_json(
                response.json(),
                candidates,
                response.url,
            )

        except Exception:
            pass

    else:
        for number in extract_chapter_numbers(
            text
        ):
            add_candidate(
                candidates,
                number,
                response.url,
                "api-text",
                300,
                "API chapter pattern",
            )

# --------------------------------------------------------
# Sitemap.
# --------------------------------------------------------
for sitemap_path in (
    "/sitemap.xml",
    "/post-sitemap.xml",
    "/sitemap_index.xml",
):
    url = urljoin(
        origin,
        sitemap_path,
    )

    body, final_url = download_page(
        url
    )

    if not body:
        continue

    soup = BeautifulSoup(
        body,
        "xml",
    )

    for loc in soup.find_all(
        "loc"
    ):
        target = clean_text(
            loc.get_text()
        )

        if not is_valid_chapter_url(
            target,
            base_url,
        ):
            continue

        number = (
            extract_chapter_number_from_text(
                target
            )
        )

        if number is not None:
            add_candidate(
                candidates,
                number,
                target,
                "sitemap",
                450,
                target,
            )

============================================================

DIRECT EXPECTED CHAPTER PROBES

============================================================

def probe_current_page_for_expected_range(
soup,
page_url,
expected_numbers,
candidates,
):
expected_numbers = set(
expected_numbers
)

if not expected_numbers:
    return

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

    target = urljoin(
        page_url,
        href,
    )

    if not is_valid_chapter_url(
        target,
        page_url,
    ):
        continue

    text = clean_text(
        " ".join(
            x
            for x in (
                link.get_text(
                    " ",
                    strip=True,
                ),
                link.get(
                    "title",
                    "",
                ),
                link.get(
                    "aria-label",
                    "",
                ),
            )
            if x
        )
    )

    number = (
        extract_chapter_number_from_text(
            text
        )
    )

    if number in expected_numbers:
        add_candidate(
            candidates,
            number,
            target,
            "DIRECT_CURRENT_PAGE",
            EXPECTED_CHAPTER_CONFIRMED_SCORE,
            text,
        )

        continue

    for pattern in URL_PATTERNS:
        match = pattern.search(
            target
        )

        if match:
            number = normalize_number(
                match.group(1)
            )

            if number in expected_numbers:
                add_candidate(
                    candidates,
                    number,
                    target,
                    "DIRECT_CURRENT_URL",
                    EXPECTED_CHAPTER_CONFIRMED_SCORE,
                    target,
                )

                break

def find_old_chapter_urls(
soup,
page_url,
old_chapter,
):
urls = []

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

    target = urljoin(
        page_url,
        href,
    )

    if not is_valid_chapter_url(
        target,
        page_url,
    ):
        continue

    text = clean_text(
        " ".join(
            x
            for x in (
                link.get_text(
                    " ",
                    strip=True,
                ),
                link.get(
                    "title",
                    "",
                ),
                link.get(
                    "aria-label",
                    "",
                ),
            )
            if x
        )
    )

    text_number = (
        extract_chapter_number_from_text(
            text
        )
    )

    if text_number == old_chapter:
        urls.append(target)
        continue

    for pattern in URL_PATTERNS:
        match = pattern.search(
            target
        )

        if (
            match
            and normalize_number(
                match.group(1)
            )
            == old_chapter
        ):
            urls.append(target)
            break

return list(
    dict.fromkeys(urls)
)

def probe_previous_chapter_page(
soup,
page_url,
old_chapter,
expected_numbers,
candidates,
):
old_urls = find_old_chapter_urls(
soup,
page_url,
old_chapter,
)

for old_url in old_urls[:3]:
    body, final_url = download_page(
        old_url
    )

    if not body:
        continue

    actual_url = (
        final_url
        or old_url
    )

    old_soup = BeautifulSoup(
        body,
        "html.parser",
    )

    probe_current_page_for_expected_range(
        old_soup,
        actual_url,
        expected_numbers,
        candidates,
    )

    for element in old_soup.find_all(
        [
            "title",
            "h1",
            "h2",
            "h3",
        ]
    ):
        text = clean_text(
            element.get_text(
                " ",
                strip=True,
            )
        )

        number = (
            extract_chapter_number_from_text(
                text
            )
        )

        if number not in expected_numbers:
            continue

        # هنا لدينا دليل مباشر من فحص الفصل السابق
        # عن الفصل المتوقع.
        add_candidate(
            candidates,
            number,
            actual_url,
            "DIRECT_PREVIOUS_PAGE",
            EXPECTED_CHAPTER_CONFIRMED_SCORE,
            text,
        )

============================================================

WORDPRESS SEARCH

============================================================

def wp_result_is_exact_chapter(
result,
expected,
):
title = ""

if isinstance(
    result,
    dict,
):
    title_obj = result.get(
        "title"
    )

    if isinstance(
        title_obj,
        dict,
    ):
        title = clean_text(
            title_obj.get(
                "rendered",
                "",
            )
        )

    else:
        title = clean_text(
            title_obj
            or result.get(
                "name",
                "",
            )
        )

# --------------------------------------------------------
# عنوان النتيجة له الأولوية.
# --------------------------------------------------------
title_number = (
    extract_chapter_number_from_text(
        title
    )
)

if title_number == expected:
    return True, title

url = clean_text(
    result.get(
        "url",
        "",
    )
    if isinstance(
        result,
        dict,
    )
    else ""
)

if not is_valid_chapter_url(
    url,
    url,
):
    # لا نرفض هنا كل الروابط إذا كان URL
    # من WordPress API يعاد بطريقة غير معتادة.
    # سيتم فحص النطاق في مكان الاستدعاء.
    pass

for pattern in URL_PATTERNS:
    match = pattern.search(
        url
    )

    if (
        match
        and normalize_number(
            match.group(1)
        )
        == expected
    ):
        return True, url

return (
    False,
    title or url,
)

def probe_wordpress_search(
base_url,
expected_numbers,
candidates,
):
parsed = urlparse(
base_url
)

origin = (
    f"{parsed.scheme}://"
    f"{parsed.netloc}"
)

endpoint = urljoin(
    origin,
    "/wp-json/wp/v2/search",
)

for expected in expected_numbers:
    response = request_with_retry(
        endpoint,
        timeout=API_TIMEOUT,
        params={
            "search": str(
                expected
            ),
            "per_page": 50,
        },
    )

    if response is None:
        continue

    try:
        results = response.json()

    except Exception:
        continue

    if not isinstance(
        results,
        list,
    ):
        continue

    for result in results:
        exact, evidence = (
            wp_result_is_exact_chapter(
                result,
                expected,
            )
        )

        if not exact:
            continue

        target = clean_text(
            result.get(
                "url",
                "",
            )
        )

        if not target:
            continue

        if not is_valid_chapter_url(
            target,
            base_url,
        ):
            continue

        add_candidate(
            candidates,
            expected,
            target,
            "WORDPRESS_EXACT",
            EXPECTED_CHAPTER_SEARCH_SCORE,
            evidence,
        )

        body, final_url = download_page(
            target
        )

        if not body:
            continue

        actual_url = (
            final_url
            or target
        )

        soup = BeautifulSoup(
            body,
            "html.parser",
        )

        for element in soup.find_all(
            [
                "title",
                "h1",
                "h2",
                "h3",
            ]
        ):
            text = clean_text(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if text_confirms_chapter(
                text,
                expected,
            ):
                add_candidate(
                    candidates,
                    expected,
                    actual_url,
                    "WORDPRESS_PAGE_EXACT",
                    EXPECTED_CHAPTER_CONFIRMED_SCORE,
                    text,
                )

def probe_expected_chapters(
base_url,
current_soup,
current_url,
old_chapter,
candidates,
):
expected = [
old_chapter + i
for i in range(
1,
EXPECTED_PROBE_AHEAD + 1,
)
]

# --------------------------------------------------------
# الصفحة الحالية.
# --------------------------------------------------------
probe_current_page_for_expected_range(
    current_soup,
    current_url,
    expected,
    candidates,
)

# --------------------------------------------------------
# صفحة الفصل القديم.
# --------------------------------------------------------
probe_previous_chapter_page(
    current_soup,
    current_url,
    old_chapter,
    expected,
    candidates,
)

# --------------------------------------------------------
# بحث WordPress المباشر.
# --------------------------------------------------------
misses = 0

parsed = urlparse(
    base_url
)

origin = (
    f"{parsed.scheme}://"
    f"{parsed.netloc}"
)

endpoint = urljoin(
    origin,
    "/wp-json/wp/v2/search",
)

for expected_number in expected:
    response = request_with_retry(
        endpoint,
        timeout=API_TIMEOUT,
        params={
            "search": str(
                expected_number
            ),
            "per_page": 50,
        },
    )

    found = False

    if response is not None:
        try:
            results = response.json()

        except Exception:
            results = []

        if isinstance(
            results,
            list,
        ):
            for result in results:
                exact, evidence = (
                    wp_result_is_exact_chapter(
                        result,
                        expected_number,
                    )
                )

                if not exact:
                    continue

                target = clean_text(
                    result.get(
                        "url",
                        "",
                    )
                )

                if not target:
                    continue

                if not is_valid_chapter_url(
                    target,
                    base_url,
                ):
                    continue

                add_candidate(
                    candidates,
                    expected_number,
                    target,
                    "WORDPRESS_EXACT",
                    EXPECTED_CHAPTER_SEARCH_SCORE,
                    evidence,
                )

                found = True

                body, final_url = download_page(
                    target
                )

                if body:
                    actual_url = (
                        final_url
                        or target
                    )

                    soup = BeautifulSoup(
                        body,
                        "html.parser",
                    )

                    for element in soup.find_all(
                        [
                            "title",
                            "h1",
                            "h2",
                            "h3",
                        ]
                    ):
                        text = clean_text(
                            element.get_text(
                                " ",
                                strip=True,
                            )
                        )

                        if text_confirms_chapter(
                            text,
                            expected_number,
                        ):
                            add_candidate(
                                candidates,
                                expected_number,
                                actual_url,
                                "WORDPRESS_PAGE_EXACT",
                                EXPECTED_CHAPTER_CONFIRMED_SCORE,
                                text,
                            )

                break

    if found:
        misses = 0

    else:
        misses += 1

        if (
            misses
            >= EXPECTED_PROBE_MAX_MISSES
        ):
            break

============================================================

CANDIDATE RANKING

============================================================

def rank_candidates(
candidates,
old_chapter,
):
candidates = unique_candidates(
candidates
)

future = [
    c
    for c in candidates
    if c.number > old_chapter
]

if not future:
    return []

def rank_key(candidate):
    direct = (
        1
        if candidate.source.startswith(
            "DIRECT_"
        )
        else 0
    )

    exact = (
        1
        if candidate.source
        in {
            "WORDPRESS_EXACT",
            "WORDPRESS_PAGE_EXACT",
            "DIRECT_CURRENT_PAGE",
            "DIRECT_CURRENT_URL",
            "DIRECT_PREVIOUS_PAGE",
        }
        else 0
    )

    # الأدلة الأقوى أولًا.
    return (
        direct,
        exact,
        candidate.score,
        candidate.number,
    )

return sorted(
    future,
    key=rank_key,
    reverse=True,
)

============================================================

FINAL DETECTION

============================================================

def detect_latest_chapter(
page_url,
old_chapter,
):
candidates = []
visited = set()

body, final_url = download_page(
    page_url
)

if not body:
    print(
        "[DETECT] Could not download "
        "monitored page."
    )

    return None, []

current_url = (
    final_url
    or page_url
)

visited.add(
    strip_monitor_parameter(
        current_url
    )
)

soup = BeautifulSoup(
    body,
    "html.parser",
)

# --------------------------------------------------------
# الروابط.
# --------------------------------------------------------
extract_from_links(
    soup,
    current_url,
    candidates,
)

# --------------------------------------------------------
# العنوان / العناوين / النص.
# --------------------------------------------------------
inspect_page_text(
    soup,
    current_url,
    candidates,
)

# --------------------------------------------------------
# JSON / JavaScript.
# --------------------------------------------------------
inspect_json_scripts(
    soup,
    current_url,
    candidates,
)

inspect_framework_data(
    soup,
    current_url,
    candidates,
)

# --------------------------------------------------------
# فحص مباشر للفصول المتوقعة.
# --------------------------------------------------------
probe_expected_chapters(
    page_url,
    soup,
    current_url,
    old_chapter,
    candidates,
)

# --------------------------------------------------------
# الصفحات المرتبطة.
# --------------------------------------------------------
related_pages = discover_related_pages(
    soup,
    current_url,
)

for related_url in related_pages:
    if (
        len(visited)
        >= MAX_DISCOVERY_PAGES
    ):
        break

    clean_related_url = (
        strip_monitor_parameter(
            related_url
        )
    )

    if clean_related_url in visited:
        continue

    visited.add(
        clean_related_url
    )

    related_soup = (
        inspect_discovered_pages(
            clean_related_url,
            candidates,
        )
    )

    if related_soup is not None:
        extract_from_links(
            related_soup,
            clean_related_url,
            candidates,
            expected_numbers={
                old_chapter + i
                for i in range(
                    1,
                    EXPECTED_PROBE_AHEAD + 1,
                )
            },
        )

# --------------------------------------------------------
# WordPress API + Sitemap.
# --------------------------------------------------------
probe_api_and_sitemap(
    page_url,
    candidates,
)

ranked = rank_candidates(
    candidates,
    old_chapter,
)

print(
    f"[DETECT] Old chapter: "
    f"{old_chapter}"
)

if ranked:
    for candidate in ranked[:15]:
        print(
            f"[CANDIDATE] "
            f"{candidate.number} | "
            f"score={candidate.score} | "
            f"source={candidate.source} | "
            f"{candidate.url} | "
            f"{candidate.evidence}"
        )

    return (
        ranked[0].number,
        ranked,
    )

print(
    "[DETECT] No chapter newer than "
    "the stored chapter was confirmed."
)

return None, []

============================================================

LOGGING

============================================================

def log_detection(
work_name,
old_chapter,
detected,
candidates,
):
print("=" * 70)

print(
    f"[WORK] {work_name}"
)

print(
    f"[WORK] Stored chapter: "
    f"{old_chapter}"
)

print(
    f"[WORK] Detected chapter: "
    f"{detected}"
)

if candidates:
    print(
        f"[WORK] Candidates checked: "
        f"{len(candidates)}"
    )

print("=" * 70)

============================================================

WORK MONITORING

============================================================

def get_chat_id(
user,
user_id=None,
):
for key in (
"chat_id",
"telegram_chat_id",
"id",
):
if (
key in user
and user[key] is not None
):
return user[key]

# في data.json الحالي:
# Telegram chat ID هو مفتاح المستخدم نفسه.
if user_id is not None:
    return user_id

return None

def get_works_from_user(user):
works = user.get(
"works"
)

if isinstance(
    works,
    list,
):
    return works

# دعم البنية القديمة.
works = user.get(
    "novels"
)

if isinstance(
    works,
    list,
):
    return works

return []

def build_notification(
work,
old_chapter,
new_chapter,
page_url,
):
name = clean_text(
work.get("name")
or work.get("title")
or "عمل جديد"
)

if new_chapter > old_chapter + 1:
    chapter_text = (
        f"الفصول الجديدة: "
        f"{old_chapter + 1} "
        f"إلى {new_chapter}"
    )

else:
    chapter_text = (
        f"الفصل الجديد: "
        f"{new_chapter}"
    )

return (
    f"📚 {name}\n\n"
    f"🔔 تم اكتشاف "
    f"{chapter_text}\n"
    f"📖 آخر فصل محفوظ: "
    f"{old_chapter}\n"
    f"🌐 {page_url}"
)

def monitor_work(
user,
work,
user_id=None,
):
name = clean_text(
work.get("name")
or work.get("title")
or "Unnamed work"
)

url = clean_text(
    work.get("url")
    or work.get("link")
    or ""
)

if not url:
    print(
        f"[WORK] {name}: "
        f"missing URL."
    )

    return False

try:
    old_chapter = int(
        work.get(
            "last_chapter",
            0,
        )
    )

except (
    TypeError,
    ValueError,
):
    print(
        f"[WORK] {name}: "
        f"invalid last_chapter."
    )

    return False

chat_id = get_chat_id(
    user,
    user_id,
)

if chat_id is None:
    print(
        f"[WORK] {name}: "
        f"no chat_id found."
    )

    return False

print(
    f"[MONITOR] Checking "
    f"{name} | "
    f"stored={old_chapter} | "
    f"{url}"
)

detected, candidates = (
    detect_latest_chapter(
        url,
        old_chapter,
    )
)

log_detection(
    name,
    old_chapter,
    detected,
    candidates,
)

if (
    detected is None
    or detected <= old_chapter
):
    return False

message = build_notification(
    work,
    old_chapter,
    detected,
    url,
)

# --------------------------------------------------------
# مهم جدًا:
# لا نحدّث last_chapter إلا بعد نجاح Telegram.
# --------------------------------------------------------
try:
    send_message(
        chat_id,
        message,
    )

except Exception as exc:
    print(
        f"[TELEGRAM] Failed to "
        f"notify {chat_id} for "
        f"{name}: {exc}"
    )

    print(
        "[WORK] last_chapter was "
        "NOT updated because "
        "notification failed."
    )

    return False

work["last_chapter"] = detected

print(
    f"[WORK] {name}: "
    f"last_chapter updated "
    f"{old_chapter} -> "
    f"{detected}"
)

return True

def monitor_all_users(data):
changed = False

# ========================================================
# users في data.json الحالي Dictionary وليس List.
# ========================================================

users = (
    data.get(
        "users",
        {},
    )
    if isinstance(
        data,
        dict,
    )
    else {}
)

if isinstance(
    users,
    dict,
):
    user_items = users.items()

elif isinstance(
    users,
    list,
):
    # دعم احتياطي للبنية القديمة.
    user_items = [
        (
            str(
                user.get(
                    "chat_id",
                    user.get(
                        "id",
                        "",
                    ),
                )
            ),
            user,
        )
        for user in users
        if isinstance(
            user,
            dict,
        )
    ]

else:
    print(
        "[DATA] 'users' has "
        "an unsupported format."
    )

    return False

for user_id, user in user_items:

    if not isinstance(
        user,
        dict,
    ):
        continue

    works = get_works_from_user(
        user
    )

    print(
        f"[USER] {user_id}: "
        f"{len(works)} work(s)"
    )

    for work in works:

        if not isinstance(
            work,
            dict,
        ):
            continue

        try:
            if monitor_work(
                user,
                work,
                user_id,
            ):
                changed = True

        except Exception as exc:
            name = work.get(
                "name",
                work.get(
                    "title",
                    "Unnamed work",
                ),
            )

            print(
                f"[WORK] Unexpected "
                f"error in {name}: "
                f"{exc}"
            )

return changed

============================================================

MAIN

============================================================

def main():
print(
"[START] Chapter monitor started."
)

data, sha = load_data()

if data is None:
    print(
        "[STOP] Could not load "
        "data.json."
    )

    return

changed = monitor_all_users(
    data
)

if changed:

    if not save_data(
        data,
        sha,
    ):
        print(
            "[WARNING] Monitoring "
            "found changes, but "
            "data.json could not "
            "be saved."
        )

else:
    print(
        "[DONE] No database "
        "changes were required."
    )

if name == "main":
main()