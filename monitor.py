import os
import re
import json
import base64
import time
import html as html_module
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, parse_qs, urlencode

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

GITHUB_REPO = "mohamedebeid303-svg/truthnovel-alert"
DATA_FILE = "data.json"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]

REQUEST_TIMEOUT = 30
API_TIMEOUT = 15
MAX_RETRIES = 3

MAX_DISCOVERY_PAGES = 15
MAX_CHAPTER_LINKS = 180

# How many future chapters should be probed.
EXPECTED_PROBE_AHEAD = 5

# Number of consecutive failed expected chapters
# before stopping ONE probing method.
EXPECTED_PROBE_MAX_MISSES = 3

EXPECTED_CHAPTER_CONFIRMED_SCORE = 1200
EXPECTED_CHAPTER_SEARCH_SCORE = 1050

# Extra direct URL probing.
DIRECT_URL_PROBES_ENABLED = True

# Try several common WordPress/page URL patterns.
DIRECT_URL_MAX_PATTERNS = 20

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
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
)


# ============================================================
# WORDPRESS API
# ============================================================

WORDPRESS_API_PATHS = [
    "/wp-json/",
    "/wp-json/wp/v2/search",
    "/wp-json/wp/v2/posts",
]


# ============================================================
# HTTP STATUS HANDLING
# ============================================================

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
    408,
    425,
    429,
    500,
    502,
    503,
    504,
    521,
    522,
    523,
    524,
}


# ============================================================
# SOCIAL / EXTERNAL DOMAINS
# ============================================================

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


# ============================================================
# GITHUB
# ============================================================

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


# ============================================================
# TELEGRAM
# ============================================================

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


# ============================================================
# HTTP
# ============================================================

def request_with_retry(
    url,
    method="GET",
    timeout=REQUEST_TIMEOUT,
    **kwargs,
):
    last_error = None

    original_params = dict(
        kwargs.pop("params", {}) or {}
    )

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):
        try:
            params = dict(original_params)

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

            if status in PERMANENT_HTTP_STATUSES:
                print(
                    f"[HTTP] Permanent HTTP status "
                    f"{status} for {url}. "
                    f"No retry."
                )

                return None

            if status in TEMPORARY_HTTP_STATUSES:
                raise requests.HTTPError(
                    f"Temporary HTTP status {status}",
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


# ============================================================
# URL HELPERS
# ============================================================

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
        host.endswith("." + domain)
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

    if is_social_domain(url):
        return False

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

    new_query = urlencode(
        query,
        doseq=True,
    )

    return parsed._replace(
        query=new_query
    ).geturl()


def normalize_url(url):
    return strip_monitor_parameter(
        url
    ).rstrip("/")


# ============================================================
# NUMBER NORMALIZATION
# ============================================================

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


# ============================================================
# CHAPTER PATTERNS
# ============================================================

STRONG_TEXT_PATTERNS = [
    re.compile(
        r"\bchapter\s*"
        r"(?:number|no\.?|id)?\s*"
        r"[:#-]?\s*(\d+)\b",
        re.I,
    ),
    re.compile(
        r"\bch\.?\s*[:#-]?\s*(\d+)\b",
        re.I,
    ),
    re.compile(
        r"\bchapter[-_\s]+(\d+)\b",
        re.I,
    ),
    re.compile(
        r"\bepisode\s*"
        r"(?:number|no\.?|id)?\s*"
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
        r"[-_=/\s]*(\d+)",
        re.I,
    ),
    re.compile(
        r"/(\d{1,7})(?:[/?#]|$)"
    ),
    re.compile(
        r"-(\d{1,7})(?:[-/?.]|$)"
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


# ============================================================
# NAVIGATION KEYWORDS
# ============================================================

NEXT_KEYWORDS = (
    "next",
    "next chapter",
    "next post",
    "newer",
    "newer post",
    "new chapter",
    "following",
    "التالي",
    "الفصل التالي",
    "الموضوع التالي",
    "الجزء التالي",
    "الفصل الجديد",
)

PREVIOUS_KEYWORDS = (
    "previous",
    "previous chapter",
    "previous post",
    "older",
    "older post",
    "prev",
    "prev chapter",
    "السابق",
    "الفصل السابق",
    "الموضوع السابق",
    "الجزء السابق",
)


@dataclass
class Candidate:
    number: int
    url: str
    source: str
    score: int = 0
    evidence: str = ""


# ============================================================
# BASIC HELPERS
# ============================================================

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
            normalize_url(clean_url),
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


# ============================================================
# EXACT CHAPTER DETECTION
# ============================================================

def extract_chapter_numbers(text):
    text = clean_text(text)

    found = []

    for pattern in STRONG_TEXT_PATTERNS:
        for match in pattern.finditer(text):
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
        for match in pattern.finditer(text):
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


def extract_chapter_number_from_text(text):
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


def text_contains_keyword(
    text,
    keywords,
):
    lower = clean_text(text).lower()

    return any(
        keyword.lower() in lower
        for keyword in keywords
    )


def is_next_chapter_link_text(text):
    return text_contains_keyword(
        text,
        NEXT_KEYWORDS,
    )


def is_previous_chapter_link_text(text):
    return text_contains_keyword(
        text,
        PREVIOUS_KEYWORDS,
    )


# ============================================================
# PAGE URL CHAPTER NUMBER
# ============================================================

def extract_chapter_number_from_url(url):
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


# ============================================================
# LINK METADATA
# ============================================================

def get_link_text(link):
    return clean_text(
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
                link.get(
                    "data-tooltip",
                    "",
                ),
                link.get(
                    "data-title",
                    "",
                ),
            )
            if x
        )
    )


def get_link_target(
    link,
    page_url,
):
    href = link.get(
        "href",
        "",
    ).strip()

    if not href:
        return None

    if href.startswith(
        (
            "javascript:",
            "mailto:",
            "tel:",
            "#",
        )
    ):
        return None

    target = urljoin(
        page_url,
        href,
    )

    if not is_valid_chapter_url(
        target,
        page_url,
    ):
        return None

    return target


# ============================================================
# LINK EXTRACTION
# ============================================================

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

        absolute_url = get_link_target(
            link,
            page_url,
        )

        if not absolute_url:
            continue

        combined_text = get_link_text(
            link
        )

        text_number = (
            extract_chapter_number_from_text(
                combined_text
            )
        )

        if text_number is not None:
            score = 650

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
                score += 200

            if is_previous_chapter_link_text(
                combined_text
            ):
                score += 50

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

        url_number = (
            extract_chapter_number_from_url(
                absolute_url
            )
        )

        if url_number is not None:
            score = 500

            if url_number in expected_numbers:
                score += 450

            if is_next_chapter_link_text(
                combined_text
            ):
                score += 200

            add_candidate(
                candidates,
                url_number,
                absolute_url,
                "link-url",
                score,
                combined_text or absolute_url,
            )

            count += 1
            continue

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
                        score = 500

                        if number in expected_numbers:
                            score += 450

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


# ============================================================
# NEXT / PREVIOUS NAVIGATION EXTRACTION
# ============================================================

def extract_navigation_links(
    soup,
    page_url,
):
    next_links = []
    previous_links = []

    # --------------------------------------------------------
    # rel="next" / rel="prev"
    # --------------------------------------------------------

    for link in soup.find_all(
        "a",
        href=True,
    ):
        target = get_link_target(
            link,
            page_url,
        )

        if not target:
            continue

        rel = link.get(
            "rel",
            [],
        )

        if isinstance(
            rel,
            str,
        ):
            rel = [rel]

        rel_lower = {
            str(x).lower()
            for x in rel
        }

        if "next" in rel_lower:
            next_links.append(target)

        if (
            "prev" in rel_lower
            or "previous" in rel_lower
        ):
            previous_links.append(target)

    # --------------------------------------------------------
    # Text / aria / title
    # --------------------------------------------------------

    for link in soup.find_all(
        "a",
        href=True,
    ):
        target = get_link_target(
            link,
            page_url,
        )

        if not target:
            continue

        text = get_link_text(
            link
        )

        if is_next_chapter_link_text(
            text
        ):
            next_links.append(target)

        if is_previous_chapter_link_text(
            text
        ):
            previous_links.append(target)

    return (
        list(dict.fromkeys(next_links)),
        list(dict.fromkeys(previous_links)),
    )


# ============================================================
# JSON / JSON-LD / JAVASCRIPT DETECTION
# ============================================================

def walk_json(
    value,
    candidates,
    page_url,
    path="",
):
    if isinstance(value, dict):
        for key, item in value.items():
            key_lower = str(key).lower()

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
        for index, item in enumerate(value):
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
    for script in soup.find_all("script"):
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

    # Extra data attributes.
    for tag in soup.find_all():
        for attr_name, attr_value in tag.attrs.items():
            attr_lower = str(
                attr_name
            ).lower()

            if (
                "chapter" not in attr_lower
                and "episode" not in attr_lower
            ):
                continue

            if isinstance(
                attr_value,
                list,
            ):
                attr_value = " ".join(
                    map(
                        str,
                        attr_value,
                    )
                )

            number = normalize_number(
                attr_value
            )

            if number is not None:
                add_candidate(
                    candidates,
                    number,
                    page_url,
                    "data-attribute-extra",
                    650,
                    f"{attr_name}={attr_value}",
                )


# ============================================================
# PAGE TEXT / HEADINGS
# ============================================================

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

        if (
            number is not None
            and (
                page_url_chapter is None
                or number == page_url_chapter
            )
        ):
            add_candidate(
                candidates,
                number,
                page_url,
                "title",
                900,
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

        # If URL has a chapter number and heading has
        # another number, the heading is not automatically
        # trusted unless it strongly confirms a chapter.
        if (
            page_url_chapter is not None
            and number != page_url_chapter
            and not text_confirms_chapter(
                text,
                number,
            )
        ):
            continue

        add_candidate(
            candidates,
            number,
            page_url,
            "heading",
            900,
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


# ============================================================
# PAGE CHAPTER CONFIRMATION
# ============================================================

def page_confirms_chapter(
    soup,
    page_url,
    expected,
):
    evidence = []

    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    url_number = (
        extract_chapter_number_from_url(
            page_url
        )
    )

    if url_number == expected:
        evidence.append(
            f"URL={expected}"
        )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    if soup.title:
        title = clean_text(
            soup.title.get_text(
                " ",
                strip=True,
            )
        )

        if text_confirms_chapter(
            title,
            expected,
        ):
            evidence.append(
                f"title={title}"
            )

    # --------------------------------------------------------
    # Headings
    # --------------------------------------------------------

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

        if text_confirms_chapter(
            text,
            expected,
        ):
            evidence.append(
                f"heading={text}"
            )

    # --------------------------------------------------------
    # Strong page text
    # --------------------------------------------------------

    body_text = clean_text(
        soup.get_text(
            " ",
            strip=True,
        )
    )

    if text_confirms_chapter(
        body_text,
        expected,
    ):
        evidence.append(
            "body-text"
        )

    # --------------------------------------------------------
    # Structured data
    # --------------------------------------------------------

    for tag in soup.find_all(
        attrs={"data-chapter": True}
    ):
        number = normalize_number(
            tag.get(
                "data-chapter"
            )
        )

        if number == expected:
            evidence.append(
                "data-chapter"
            )

    return (
        len(evidence) > 0,
        evidence[:5],
    )


def inspect_candidate_page(
    url,
    expected,
    candidates,
    source,
):
    if not url:
        return False

    body, final_url = download_page(
        url
    )

    if not body:
        return False

    actual_url = (
        final_url
        or url
    )

    soup = BeautifulSoup(
        body,
        "html.parser",
    )

    confirmed, evidence = (
        page_confirms_chapter(
            soup,
            actual_url,
            expected,
        )
    )

    if confirmed:
        add_candidate(
            candidates,
            expected,
            actual_url,
            source,
            EXPECTED_CHAPTER_CONFIRMED_SCORE,
            " | ".join(evidence),
        )

        return True

    return False


# ============================================================
# PAGINATION / RELATED PAGES
# ============================================================

def discover_related_pages(
    soup,
    page_url,
):
    pages = []

    next_links, previous_links = (
        extract_navigation_links(
            soup,
            page_url,
        )
    )

    pages.extend(
        next_links
    )

    pages.extend(
        previous_links
    )

    for link in soup.find_all(
        "a",
        href=True,
    ):
        target = get_link_target(
            link,
            page_url,
        )

        if not target:
            continue

        text = get_link_text(
            link
        )

        lower = text.lower()

        is_relevant = (
            is_next_chapter_link_text(
                text
            )
            or is_previous_chapter_link_text(
                text
            )
            or "older" in lower
            or "previous" in lower
            or "prev" in lower
            or "newer" in lower
            or extract_chapter_number_from_text(
                text
            )
            is not None
            or extract_chapter_number_from_url(
                target
            )
            is not None
        )

        if is_relevant:
            pages.append(
                target
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


# ============================================================
# WORDPRESS API / SITEMAP
# ============================================================

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
    # WordPress APIs
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
    # Sitemaps
    # --------------------------------------------------------

    for sitemap_path in (
        "/sitemap.xml",
        "/post-sitemap.xml",
        "/sitemap_index.xml",
        "/wp-sitemap.xml",
        "/wp-sitemap-posts-post-1.xml",
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

        for loc in soup.find_all("loc"):
            target = clean_text(
                loc.get_text()
            )

            if not is_valid_chapter_url(
                target,
                base_url,
            ):
                continue

            number = (
                extract_chapter_number_from_url(
                    target
                )
            )

            if number is None:
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
                    500,
                    target,
                )


# ============================================================
# CURRENT PAGE EXPECTED CHAPTER PROBING
# ============================================================

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

    # --------------------------------------------------------
    # Normal links
    # --------------------------------------------------------

    for link in soup.find_all(
        "a",
        href=True,
    ):
        target = get_link_target(
            link,
            page_url,
        )

        if not target:
            continue

        text = get_link_text(
            link
        )

        number = (
            extract_chapter_number_from_text(
                text
            )
        )

        if number is None:
            number = (
                extract_chapter_number_from_url(
                    target
                )
            )

        if number in expected_numbers:
            add_candidate(
                candidates,
                number,
                target,
                "DIRECT_CURRENT_PAGE",
                EXPECTED_CHAPTER_CONFIRMED_SCORE,
                text or target,
            )

    # --------------------------------------------------------
    # Navigation links
    # --------------------------------------------------------

    next_links, _ = (
        extract_navigation_links(
            soup,
            page_url,
        )
    )

    for target in next_links:
        number = (
            extract_chapter_number_from_url(
                target
            )
        )

        if number in expected_numbers:
            add_candidate(
                candidates,
                number,
                target,
                "DIRECT_NAVIGATION",
                EXPECTED_CHAPTER_CONFIRMED_SCORE + 100,
                target,
            )


# ============================================================
# FIND OLD CHAPTER URLS
# ============================================================

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
        target = get_link_target(
            link,
            page_url,
        )

        if not target:
            continue

        text = get_link_text(
            link
        )

        text_number = (
            extract_chapter_number_from_text(
                text
            )
        )

        if text_number == old_chapter:
            urls.append(target)
            continue

        url_number = (
            extract_chapter_number_from_url(
                target
            )
        )

        if url_number == old_chapter:
            urls.append(target)

    return list(
        dict.fromkeys(urls)
    )


# ============================================================
# PREVIOUS CHAPTER PAGE PROBING
# ============================================================

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

    # Add explicit previous-navigation URLs.
    _, previous_links = (
        extract_navigation_links(
            soup,
            page_url,
        )
    )

    old_urls.extend(
        previous_links
    )

    old_urls = list(
        dict.fromkeys(old_urls)
    )

    for old_url in old_urls[:5]:
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

        # Also follow NEXT from the old chapter.
        next_links, _ = (
            extract_navigation_links(
                old_soup,
                actual_url,
            )
        )

        for next_url in next_links[:3]:
            for expected in expected_numbers:
                inspect_candidate_page(
                    next_url,
                    expected,
                    candidates,
                    "DIRECT_PREVIOUS_NEXT",
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

            if number in expected_numbers:
                add_candidate(
                    candidates,
                    number,
                    actual_url,
                    "DIRECT_PREVIOUS_PAGE",
                    EXPECTED_CHAPTER_CONFIRMED_SCORE,
                    text,
                )


# ============================================================
# WORDPRESS SEARCH
# ============================================================

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

    url_number = (
        extract_chapter_number_from_url(
            url
        )
    )

    if url_number == expected:
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
                "search": str(expected),
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

            inspect_candidate_page(
                target,
                expected,
                candidates,
                "WORDPRESS_PAGE_EXACT",
            )


# ============================================================
# DIRECT URL GENERATION
# ============================================================

def build_direct_url_patterns(
    base_url,
    old_chapter,
    expected,
):
    parsed = urlparse(
        base_url
    )

    origin = (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
    )

    old_url_number = (
        extract_chapter_number_from_url(
            base_url
        )
    )

    patterns = []

    # --------------------------------------------------------
    # Replace numeric chapter in current URL.
    # --------------------------------------------------------

    if old_url_number is not None:
        replaced = re.sub(
            rf"(?<!\d){old_url_number}(?!\d)",
            str(expected),
            base_url,
            count=1,
        )

        patterns.append(
            replaced
        )

    # --------------------------------------------------------
    # Common direct WordPress formats.
    # --------------------------------------------------------

    patterns.extend(
        [
            urljoin(
                origin,
                f"/chapter-{expected}/",
            ),
            urljoin(
                origin,
                f"/chapter/{expected}/",
            ),
            urljoin(
                origin,
                f"/chap-{expected}/",
            ),
            urljoin(
                origin,
                f"/ch-{expected}/",
            ),
            urljoin(
                origin,
                f"/ch{expected}/",
            ),
            urljoin(
                origin,
                f"/episode-{expected}/",
            ),
            urljoin(
                origin,
                f"/episode/{expected}/",
            ),
            urljoin(
                origin,
                f"/chapter/{expected}",
            ),
            urljoin(
                origin,
                f"/?chapter={expected}",
            ),
            urljoin(
                origin,
                f"/?chapter_id={expected}",
            ),
            urljoin(
                origin,
                f"/?ch={expected}",
            ),
            urljoin(
                origin,
                f"/?episode={expected}",
            ),
        ]
    )

    # --------------------------------------------------------
    # If current path has a numeric component, replace it.
    # --------------------------------------------------------

    path = parsed.path

    numeric_match = re.search(
        r"(?<!\d)(\d{2,7})(?!\d)",
        path,
    )

    if numeric_match:
        old_number = numeric_match.group(1)

        replaced_path = path.replace(
            old_number,
            str(expected),
            1,
        )

        patterns.append(
            parsed._replace(
                path=replaced_path,
                query="",
            ).geturl()
        )

    # --------------------------------------------------------
    # Generic slug-like WordPress pattern.
    # --------------------------------------------------------

    slug_match = re.search(
        r"^(.*?)(\d{2,7})(.*)$",
        path.rstrip("/"),
    )

    if slug_match:
        prefix = slug_match.group(1)
        suffix = slug_match.group(3)

        patterns.append(
            urljoin(
                origin,
                f"{prefix}{expected}{suffix}/",
            )
        )

    # Remove duplicates.
    result = []

    for url in patterns:
        if not is_valid_chapter_url(
            url,
            base_url,
        ):
            continue

        normalized = normalize_url(
            url
        )

        if normalized not in {
            normalize_url(x)
            for x in result
        }:
            result.append(url)

        if len(result) >= DIRECT_URL_MAX_PATTERNS:
            break

    return result


def probe_direct_urls(
    base_url,
    old_chapter,
    expected_numbers,
    candidates,
):
    if not DIRECT_URL_PROBES_ENABLED:
        return

    for expected in expected_numbers:
        urls = build_direct_url_patterns(
            base_url,
            old_chapter,
            expected,
        )

        for target in urls:
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

            confirmed, evidence = (
                page_confirms_chapter(
                    soup,
                    actual_url,
                    expected,
                )
            )

            if confirmed:
                add_candidate(
                    candidates,
                    expected,
                    actual_url,
                    "DIRECT_URL_PROBE",
                    EXPECTED_CHAPTER_CONFIRMED_SCORE,
                    " | ".join(evidence),
                )

                print(
                    f"[DIRECT] Confirmed "
                    f"chapter {expected} "
                    f"at {actual_url}"
                )

                break


# ============================================================
# EXPECTED CHAPTER PROBING
# ============================================================

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

    print(
        f"[PROBE] Expected chapters: "
        f"{expected}"
    )

    # --------------------------------------------------------
    # Current page.
    # --------------------------------------------------------

    probe_current_page_for_expected_range(
        current_soup,
        current_url,
        expected,
        candidates,
    )

    # --------------------------------------------------------
    # Previous chapter page.
    # --------------------------------------------------------

    probe_previous_chapter_page(
        current_soup,
        current_url,
        old_chapter,
        expected,
        candidates,
    )

    # --------------------------------------------------------
    # Direct URL probes.
    # --------------------------------------------------------

    probe_direct_urls(
        base_url,
        old_chapter,
        expected,
        candidates,
    )

    # --------------------------------------------------------
    # WordPress search.
    # --------------------------------------------------------

    probe_wordpress_search(
        base_url,
        expected,
        candidates,
    )


# ============================================================
# CANDIDATE RANKING
# ============================================================

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
                "DIRECT_PREVIOUS_NEXT",
                "DIRECT_NAVIGATION",
                "DIRECT_URL_PROBE",
            }
            else 0
        )

        confirmed = (
            1
            if candidate.score
            >= EXPECTED_CHAPTER_CONFIRMED_SCORE
            else 0
        )

        return (
            confirmed,
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


# ============================================================
# FINAL DETECTION
# ============================================================

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
        normalize_url(
            current_url
        )
    )

    soup = BeautifulSoup(
        body,
        "html.parser",
    )

    print(
        f"[DETECT] Current page: "
        f"{current_url}"
    )

    # --------------------------------------------------------
    # Main page inspection.
    # --------------------------------------------------------

    extract_from_links(
        soup,
        current_url,
        candidates,
    )

    inspect_page_text(
        soup,
        current_url,
        candidates,
    )

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
    # Expected chapter probes.
    # --------------------------------------------------------

    probe_expected_chapters(
        page_url,
        soup,
        current_url,
        old_chapter,
        candidates,
    )

    # --------------------------------------------------------
    # Related pages.
    # --------------------------------------------------------

    related_pages = discover_related_pages(
        soup,
        current_url,
    )

    print(
        f"[DISCOVERY] Related pages found: "
        f"{len(related_pages)}"
    )

    for related_url in related_pages:
        if (
            len(visited)
            >= MAX_DISCOVERY_PAGES
        ):
            break

        clean_related_url = (
            normalize_url(
                related_url
            )
        )

        if clean_related_url in visited:
            continue

        visited.add(
            clean_related_url
        )

        print(
            f"[DISCOVERY] Checking: "
            f"{clean_related_url}"
        )

        related_soup = (
            inspect_discovered_pages(
                clean_related_url,
                candidates,
            )
        )

        if related_soup is None:
            continue

        expected_numbers = {
            old_chapter + i
            for i in range(
                1,
                EXPECTED_PROBE_AHEAD + 1,
            )
        }

        extract_from_links(
            related_soup,
            clean_related_url,
            candidates,
            expected_numbers=expected_numbers,
        )

        # Follow next/previous from discovered page.
        next_links, previous_links = (
            extract_navigation_links(
                related_soup,
                clean_related_url,
            )
        )

        for navigation_url in (
            next_links[:2]
            + previous_links[:2]
        ):
            clean_navigation_url = (
                normalize_url(
                    navigation_url
                )
            )

            if (
                clean_navigation_url
                in visited
            ):
                continue

            if (
                len(visited)
                >= MAX_DISCOVERY_PAGES
            ):
                break

            visited.add(
                clean_navigation_url
            )

            inspect_discovered_pages(
                clean_navigation_url,
                candidates,
            )

    # --------------------------------------------------------
    # API / sitemap.
    # --------------------------------------------------------

    probe_api_and_sitemap(
        page_url,
        candidates,
    )

    # --------------------------------------------------------
    # Ranking.
    # --------------------------------------------------------

    ranked = rank_candidates(
        candidates,
        old_chapter,
    )

    print(
        f"[DETECT] Old chapter: "
        f"{old_chapter}"
    )

    if ranked:
        print(
            f"[DETECT] Future candidates: "
            f"{len(ranked)}"
        )

        for candidate in ranked[:20]:
            print(
                f"[CANDIDATE] "
                f"{candidate.number} | "
                f"score={candidate.score} | "
                f"source={candidate.source} | "
                f"{candidate.url} | "
                f"{candidate.evidence}"
            )

        # ----------------------------------------------------
        # Final safety check:
        #
        # We prefer candidates that have strong confirmation.
        # ----------------------------------------------------

        strongest = ranked[0]

        if (
            strongest.score
            >= EXPECTED_CHAPTER_CONFIRMED_SCORE
        ):
            print(
                f"[DETECT] Confirmed latest "
                f"candidate: "
                f"{strongest.number}"
            )

            return (
                strongest.number,
                ranked,
            )

        # A lower score candidate is still allowed if it
        # came from a highly reliable exact WordPress result.
        if strongest.source in {
            "WORDPRESS_EXACT",
            "WORDPRESS_PAGE_EXACT",
        }:
            print(
                f"[DETECT] Confirmed via "
                f"WordPress exact result: "
                f"{strongest.number}"
            )

            return (
                strongest.number,
                ranked,
            )

        print(
            "[DETECT] Future candidates "
            "were found, but none reached "
            "the required confirmation level."
        )

        return None, ranked

    print(
        "[DETECT] No chapter newer than "
        "the stored chapter was confirmed."
    )

    return None, []


# ============================================================
# LOGGING
# ============================================================

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


# ============================================================
# WORK MONITORING
# ============================================================

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

    return (
        f"🔔 فصل جديد!\n\n"
        f"📖 {name}\n"
        f"📚 الفصل {new_chapter}\n\n"
        f"🔗 {page_url}"
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


# ============================================================
# MAIN
# ============================================================

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


if __name__ == "__main__":
    main()