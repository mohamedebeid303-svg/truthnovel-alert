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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
)

COMMON_API_PATHS = [
    "/wp-json/",
    "/wp-json/wp/v2/posts",
    "/wp-json/wp/v2/pages",
    "/wp-json/wp/v2/search",
    "/api/",
    "/api/chapters",
    "/api/chapter",
    "/api/novels",
    "/api/episodes",
]

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
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_FILE}"


def load_data():
    try:
        response = requests.get(
            github_file_url(),
            headers=github_headers(),
            timeout=API_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        content = base64.b64decode(payload["content"]).decode("utf-8")
        data = json.loads(content)
        print("[GITHUB] data.json loaded successfully.")
        return data, payload.get("sha")
    except Exception as exc:
        print(f"[GITHUB] Failed to load data.json: {exc}")
        return None, None


def save_data(data, sha):
    try:
        raw = json.dumps(data, ensure_ascii=False, indent=2)
        encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")

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
            print("[GITHUB] Save conflict (409). Data was changed remotely.")
            return False

        response.raise_for_status()
        print("[GITHUB] data.json saved successfully.")
        return True
    except Exception as exc:
        print(f"[GITHUB] Failed to save data.json: {exc}")
        return False


# ============================================================
# TELEGRAM
# ============================================================


def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    response = requests.post(url, json=payload, timeout=API_TIMEOUT)
    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")

    print(f"[TELEGRAM] Message sent to {chat_id}.")
    return True


# ============================================================
# HTTP
# ============================================================


def request_with_retry(url, method="GET", timeout=REQUEST_TIMEOUT, **kwargs):
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            params = dict(kwargs.pop("params", {}) or {})
            params["_monitor"] = time.time_ns()

            response = SESSION.request(
                method,
                url,
                params=params,
                timeout=timeout,
                allow_redirects=True,
                **kwargs,
            )

            if response.status_code in {429, 500, 502, 503, 504}:
                raise requests.HTTPError(
                    f"Temporary HTTP status {response.status_code}",
                    response=response,
                )

            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            print(f"[HTTP] Attempt {attempt}/{MAX_RETRIES} failed for {url}: {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(attempt)

    print(f"[HTTP] Giving up on {url}: {last_error}")
    return None


def download_page(url):
    response = request_with_retry(url)
    if response is None:
        return None, None

    content_type = (response.headers.get("Content-Type") or "").lower()
    body = response.text or ""

    if (
        "html" not in content_type
        and "xml" not in content_type
        and "json" not in content_type
        and not body.lstrip().startswith(("<", "{" , "["))
    ):
        print(f"[HTTP] Unsupported content type for {url}: {content_type}")
        return None, None

    return body, response.url


# ============================================================
# NUMBER NORMALIZATION
# ============================================================

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize_number(value):
    if value is None:
        return None

    value = html_module.unescape(str(value)).translate(ARABIC_DIGITS)
    value = value.replace(",", "").replace("٬", "").strip()

    match = re.search(r"\d+", value)
    if not match:
        return None

    try:
        return int(match.group(0))
    except ValueError:
        return None


# ============================================================
# CHAPTER PATTERNS
# ============================================================

STRONG_TEXT_PATTERNS = [
    re.compile(r"\bchapter\s*(?:number|no\.?|id)?\s*[:#-]?\s*(\d+)\b", re.I),
    re.compile(r"\bch\.?\s*[:#-]?\s*(\d+)\b", re.I),
    re.compile(r"\bchapter[-_\s]+(\d+)\b", re.I),
    re.compile(r"\bepisode\s*(?:number|no\.?|id)?\s*[:#-]?\s*(\d+)\b", re.I),
    re.compile(r"الفصل\s*(?:رقم|رقم الفصل)?\s*[:#-]?\s*(\d+)", re.I),
    re.compile(r"فصل\s*[:#-]?\s*(\d+)", re.I),
    re.compile(r"第\s*(\d+)\s*章"),
]

CHAPTER_TITLE_PATTERNS = [
    re.compile(r"^\s*(\d{1,7})\s*[-–—:]\s*.+$"),
    re.compile(r"^\s*(\d{1,7})\s+.+$"),
    re.compile(r"^\s*.+?\s*[-–—:]\s*(\d{1,7})\s*$"),
]

URL_PATTERNS = [
    re.compile(r"(?:chapter|chap|ch|episode)[-_=/]*(\d+)", re.I),
    re.compile(r"/(\d{1,7})(?:[/?#]|$)"),
    re.compile(r"[-_/](\d{1,7})(?:[-_/?.]|$)"),
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


# ============================================================
# BASIC HELPERS
# ============================================================


def clean_text(value):
    return re.sub(r"\s+", " ", html_module.unescape(str(value or ""))).strip()


def unique_candidates(candidates):
    result = {}
    for candidate in candidates:
        key = (candidate.number, candidate.url.rstrip("/"))
        old = result.get(key)
        if old is None or candidate.score > old.score:
            result[key] = candidate
    return list(result.values())


def add_candidate(candidates, number, url, source, score, evidence=""):
    if number is None or number < 0:
        return
    candidates.append(
        Candidate(
            number=int(number),
            url=url,
            source=source,
            score=score,
            evidence=clean_text(evidence)[:250],
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
            number = normalize_number(match.group(1))
            if number is not None:
                found.append(number)

    return found


def text_confirms_chapter(text, expected):
    text = clean_text(text)
    if not text:
        return False

    for pattern in STRONG_TEXT_PATTERNS:
        for match in pattern.finditer(text):
            if normalize_number(match.group(1)) == expected:
                return True

    for pattern in CHAPTER_TITLE_PATTERNS:
        match = pattern.match(text)
        if match:
            groups = [normalize_number(g) for g in match.groups()]
            if expected in groups:
                return True

    return False


def extract_chapter_number_from_text(text):
    text = clean_text(text)

    numbers = extract_chapter_numbers(text)
    if numbers:
        return numbers[0]

    for pattern in CHAPTER_TITLE_PATTERNS:
        match = pattern.match(text)
        if match:
            for group in match.groups():
                number = normalize_number(group)
                if number is not None:
                    return number

    return None


def is_next_chapter_link_text(text):
    text = clean_text(text).lower()
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


# ============================================================
# LINK EXTRACTION
# ============================================================


def extract_from_links(soup, page_url, candidates, expected_numbers=None):
    expected_numbers = set(expected_numbers or [])
    count = 0

    for link in soup.find_all("a", href=True):
        if count >= MAX_CHAPTER_LINKS:
            break

        href = link.get("href", "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue

        absolute_url = urljoin(page_url, href)
        text = clean_text(link.get_text(" ", strip=True))
        title = clean_text(link.get("title", ""))
        aria = clean_text(link.get("aria-label", ""))
        combined_text = clean_text(" ".join(x for x in (text, title, aria) if x))

        # أهم نقطة: إذا كان نص الرابط نفسه يقول 2478 - عنوان الفصل، نعتمد عليه أولًا.
        text_number = extract_chapter_number_from_text(combined_text)
        if text_number is not None:
            score = 600
            if text_confirms_chapter(combined_text, text_number):
                score += 250
            if text_number in expected_numbers:
                score += 500
            if is_next_chapter_link_text(combined_text):
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

        # بعد ذلك نفحص رقم الفصل الموجود في الرابط نفسه.
        for pattern in URL_PATTERNS:
            match = pattern.search(absolute_url)
            if match:
                number = normalize_number(match.group(1))
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
            parsed = urlparse(absolute_url)
            query = parse_qs(parsed.query)
            found_query = False
            for key, values in query.items():
                if key.lower() in QUERY_KEYS:
                    for value in values:
                        number = normalize_number(value)
                        if number is not None:
                            score = 450 + (350 if number in expected_numbers else 0)
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
# JSON / JSON-LD / JAVASCRIPT DETECTION
# ============================================================


def walk_json(value, candidates, page_url, path=""):
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
                number = normalize_number(item)
                if number is not None:
                    add_candidate(
                        candidates,
                        number,
                        page_url,
                        "json",
                        750,
                        f"{path}/{key}: {item}",
                    )
            walk_json(item, candidates, page_url, f"{path}/{key}")

    elif isinstance(value, list):
        for index, item in enumerate(value):
            walk_json(item, candidates, page_url, f"{path}/{index}")


def inspect_json_scripts(soup, page_url, candidates):
    for script in soup.find_all("script"):
        script_type = (script.get("type") or "").lower()
        content = script.string or script.get_text()
        if not content:
            continue

        if "ld+json" in script_type:
            try:
                parsed = json.loads(content)
                walk_json(parsed, candidates, page_url)
            except Exception:
                pass

        for pattern in STRONG_TEXT_PATTERNS:
            for match in pattern.finditer(content):
                number = normalize_number(match.group(1))
                if number is not None:
                    add_candidate(
                        candidates,
                        number,
                        page_url,
                        "javascript",
                        500,
                        match.group(0),
                    )


def inspect_framework_data(soup, page_url, candidates):
    for tag in soup.find_all(attrs={"id": True}):
        tag_id = str(tag.get("id", "")).lower()
        if tag_id in {"__next_data__", "__nuxt_data__", "__data__"}:
            content = tag.get_text()
            try:
                parsed = json.loads(content)
                walk_json(parsed, candidates, page_url)
            except Exception:
                pass

    for tag in soup.find_all(attrs={"data-chapter": True}):
        number = normalize_number(tag.get("data-chapter"))
        if number is not None:
            add_candidate(candidates, number, page_url, "data-attribute", 700, str(tag))


# ============================================================
# PAGE TEXT / HEADINGS
# ============================================================


def inspect_page_text(soup, page_url, candidates):
    title = clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title:
        number = extract_chapter_number_from_text(title)
        if number is not None:
            add_candidate(candidates, number, page_url, "title", 800, title)

    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = clean_text(heading.get_text(" ", strip=True))
        number = extract_chapter_number_from_text(text)
        if number is not None:
            add_candidate(candidates, number, page_url, "heading", 850, text)

    body_text = clean_text(soup.get_text(" ", strip=True))
    for number in extract_chapter_numbers(body_text):
        add_candidate(candidates, number, page_url, "body-text", 250, "chapter pattern in page text")


# ============================================================
# PAGINATION / RELATED PAGES
# ============================================================


def discover_related_pages(soup, page_url):
    pages = []

    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue

        absolute = urljoin(page_url, href)
        text = clean_text(link.get_text(" ", strip=True))
        lower = text.lower()

        is_relevant = (
            is_next_chapter_link_text(text)
            or "older" in lower
            or "previous" in lower
            or "الفصل السابق" in text
            or "السابق" in text
            or extract_chapter_number_from_text(text) is not None
        )

        if is_relevant:
            pages.append(absolute)

    # إزالة التكرار مع الحفاظ على الترتيب.
    return list(dict.fromkeys(pages))[:MAX_DISCOVERY_PAGES]


def inspect_discovered_pages(page_url, candidates):
    body, final_url = download_page(page_url)
    if not body:
        return None

    soup = BeautifulSoup(body, "html.parser")
    extract_from_links(soup, final_url or page_url, candidates)
    inspect_page_text(soup, final_url or page_url, candidates)
    inspect_json_scripts(soup, final_url or page_url, candidates)
    inspect_framework_data(soup, final_url or page_url, candidates)
    return soup


# ============================================================
# API / SITEMAP
# ============================================================


def probe_api_and_sitemap(base_url, candidates):
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    for path in COMMON_API_PATHS:
        url = urljoin(origin, path)
        response = request_with_retry(url, timeout=API_TIMEOUT)
        if response is None:
            continue

        content_type = (response.headers.get("Content-Type") or "").lower()
        text = response.text or ""

        if "json" in content_type or text.lstrip().startswith(("{", "[")):
            try:
                walk_json(response.json(), candidates, response.url)
            except Exception:
                pass
        else:
            for number in extract_chapter_numbers(text):
                add_candidate(candidates, number, response.url, "api-text", 300, "API chapter pattern")

    for sitemap_path in ("/sitemap.xml", "/post-sitemap.xml", "/sitemap_index.xml"):
        url = urljoin(origin, sitemap_path)
        body, final_url = download_page(url)
        if not body:
            continue

        soup = BeautifulSoup(body, "xml")
        for loc in soup.find_all("loc"):
            target = clean_text(loc.get_text())
            number = extract_chapter_number_from_text(target)
            if number is not None:
                add_candidate(candidates, number, target, "sitemap", 450, target)


# ============================================================
# DIRECT EXPECTED CHAPTER PROBES
# ============================================================


def probe_current_page_for_expected_range(soup, page_url, expected_numbers, candidates):
    expected_numbers = set(expected_numbers)
    if not expected_numbers:
        return

    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()
        if not href:
            continue

        target = urljoin(page_url, href)
        text = clean_text(
            " ".join(
                x
                for x in (
                    link.get_text(" ", strip=True),
                    link.get("title", ""),
                    link.get("aria-label", ""),
                )
                if x
            )
        )

        number = extract_chapter_number_from_text(text)
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
            match = pattern.search(target)
            if match:
                number = normalize_number(match.group(1))
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


def find_old_chapter_urls(soup, page_url, old_chapter):
    urls = []

    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()
        if not href:
            continue

        target = urljoin(page_url, href)
        text = clean_text(
            " ".join(
                x
                for x in (
                    link.get_text(" ", strip=True),
                    link.get("title", ""),
                    link.get("aria-label", ""),
                )
                if x
            )
        )

        text_number = extract_chapter_number_from_text(text)
        if text_number == old_chapter:
            urls.append(target)
            continue

        for pattern in URL_PATTERNS:
            match = pattern.search(target)
            if match and normalize_number(match.group(1)) == old_chapter:
                urls.append(target)
                break

    return list(dict.fromkeys(urls))


def probe_previous_chapter_page(soup, page_url, old_chapter, expected_numbers, candidates):
    old_urls = find_old_chapter_urls(soup, page_url, old_chapter)

    for old_url in old_urls[:3]:
        body, final_url = download_page(old_url)
        if not body:
            continue

        actual_url = final_url or old_url
        old_soup = BeautifulSoup(body, "html.parser")
        probe_current_page_for_expected_range(
            old_soup,
            actual_url,
            expected_numbers,
            candidates,
        )

        # عنوان الصفحة نفسها قد يحتوي على الفصل الجديد في صفحات مدمجة/redirect.
        for element in old_soup.find_all(["title", "h1", "h2", "h3"]):
            text = clean_text(element.get_text(" ", strip=True))
            number = extract_chapter_number_from_text(text)
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


def wp_result_is_exact_chapter(result, expected):
    title = ""
    if isinstance(result, dict):
        title_obj = result.get("title")
        if isinstance(title_obj, dict):
            title = clean_text(title_obj.get("rendered", ""))
        else:
            title = clean_text(title_obj or result.get("name", ""))

    # نعطي عنوان النتيجة أولوية على الرقم الموجود في URL.
    title_number = extract_chapter_number_from_text(title)
    if title_number == expected:
        return True, title

    url = clean_text(result.get("url", "") if isinstance(result, dict) else "")
    for pattern in URL_PATTERNS:
        match = pattern.search(url)
        if match and normalize_number(match.group(1)) == expected:
            return True, url

    return False, title or url


def probe_wordpress_search(base_url, expected_numbers, candidates):
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    endpoint = urljoin(origin, "/wp-json/wp/v2/search")

    for expected in expected_numbers:
        response = request_with_retry(
            endpoint,
            timeout=API_TIMEOUT,
            params={"search": str(expected), "per_page": 50},
        )
        if response is None:
            continue

        try:
            results = response.json()
        except Exception:
            continue

        if not isinstance(results, list):
            continue

        for result in results:
            exact, evidence = wp_result_is_exact_chapter(result, expected)
            if not exact:
                continue

            target = clean_text(result.get("url", ""))
            if not target:
                continue

            add_candidate(
                candidates,
                expected,
                target,
                "WORDPRESS_EXACT",
                EXPECTED_CHAPTER_SEARCH_SCORE,
                evidence,
            )

            body, final_url = download_page(target)
            if not body:
                continue

            actual_url = final_url or target
            soup = BeautifulSoup(body, "html.parser")
            for element in soup.find_all(["title", "h1", "h2", "h3"]):
                text = clean_text(element.get_text(" ", strip=True))
                if text_confirms_chapter(text, expected):
                    add_candidate(
                        candidates,
                        expected,
                        actual_url,
                        "WORDPRESS_PAGE_EXACT",
                        EXPECTED_CHAPTER_CONFIRMED_SCORE,
                        text,
                    )


def probe_expected_chapters(base_url, current_soup, current_url, old_chapter, candidates):
    expected = [old_chapter + i for i in range(1, EXPECTED_PROBE_AHEAD + 1)]

    # نفحص الصفحة الحالية كلها مرة واحدة بدل الاكتفاء بـ old+1.
    probe_current_page_for_expected_range(
        current_soup,
        current_url,
        expected,
        candidates,
    )

    # نفحص صفحة الفصل القديم مرة واحدة أيضًا.
    probe_previous_chapter_page(
        current_soup,
        current_url,
        old_chapter,
        expected,
        candidates,
    )

    # بحث WordPress المباشر، مع التوقف بعد عدد من الإخفاقات المتتالية.
    misses = 0
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    endpoint = urljoin(origin, "/wp-json/wp/v2/search")

    for expected_number in expected:
        response = request_with_retry(
            endpoint,
            timeout=API_TIMEOUT,
            params={"search": str(expected_number), "per_page": 50},
        )

        found = False
        if response is not None:
            try:
                results = response.json()
            except Exception:
                results = []

            if isinstance(results, list):
                for result in results:
                    exact, evidence = wp_result_is_exact_chapter(result, expected_number)
                    if not exact:
                        continue

                    target = clean_text(result.get("url", ""))
                    if not target:
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

                    # لا نحتاج لفتح كل نتيجة؛ نفتح أول نتيجة مؤكدة.
                    body, final_url = download_page(target)
                    if body:
                        actual_url = final_url or target
                        soup = BeautifulSoup(body, "html.parser")
                        for element in soup.find_all(["title", "h1", "h2", "h3"]):
                            text = clean_text(element.get_text(" ", strip=True))
                            if text_confirms_chapter(text, expected_number):
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
            if misses >= EXPECTED_PROBE_MAX_MISSES:
                break


# ============================================================
# CANDIDATE RANKING
# ============================================================


def rank_candidates(candidates, old_chapter):
    candidates = unique_candidates(candidates)
    future = [c for c in candidates if c.number > old_chapter]

    if not future:
        return []

    def rank_key(candidate):
        direct = 1 if candidate.source.startswith("DIRECT_") else 0
        exact = 1 if candidate.source in {
            "WORDPRESS_EXACT",
            "WORDPRESS_PAGE_EXACT",
            "DIRECT_CURRENT_PAGE",
            "DIRECT_CURRENT_URL",
            "DIRECT_PREVIOUS_PAGE",
        } else 0
        return (
            direct,
            exact,
            candidate.score,
            candidate.number,
        )

    return sorted(future, key=rank_key, reverse=True)


# ============================================================
# FINAL DETECTION
# ============================================================


def detect_latest_chapter(page_url, old_chapter):
    candidates = []
    visited = set()

    body, final_url = download_page(page_url)
    if not body:
        print("[DETECT] Could not download monitored page.")
        return None, []

    current_url = final_url or page_url
    visited.add(current_url)
    soup = BeautifulSoup(body, "html.parser")

    extract_from_links(soup, current_url, candidates)
    inspect_page_text(soup, current_url, candidates)
    inspect_json_scripts(soup, current_url, candidates)
    inspect_framework_data(soup, current_url, candidates)

    # فحص مباشر للفصول المتوقعة أهم من الأرقام العشوائية الموجودة في الصفحة.
    probe_expected_chapters(
        page_url,
        soup,
        current_url,
        old_chapter,
        candidates,
    )

    # الصفحات المرتبطة مثل التالي/السابق وروابط عناوين الفصول.
    related_pages = discover_related_pages(soup, current_url)
    for related_url in related_pages:
        if len(visited) >= MAX_DISCOVERY_PAGES:
            break
        if related_url in visited:
            continue

        visited.add(related_url)
        related_soup = inspect_discovered_pages(related_url, candidates)
        if related_soup is not None:
            extract_from_links(
                related_soup,
                related_url,
                candidates,
                expected_numbers={old_chapter + i for i in range(1, EXPECTED_PROBE_AHEAD + 1)},
            )

    # WordPress/API/sitemap كطبقة إضافية.
    probe_api_and_sitemap(page_url, candidates)

    ranked = rank_candidates(candidates, old_chapter)

    print(f"[DETECT] Old chapter: {old_chapter}")
    if ranked:
        for candidate in ranked[:15]:
            print(
                f"[CANDIDATE] {candidate.number} | score={candidate.score} | "
                f"source={candidate.source} | {candidate.url} | {candidate.evidence}"
            )
        return ranked[0].number, ranked

    print("[DETECT] No chapter newer than the stored chapter was confirmed.")
    return None, []


# ============================================================
# LOGGING
# ============================================================


def log_detection(work_name, old_chapter, detected, candidates):
    print("=" * 70)
    print(f"[WORK] {work_name}")
    print(f"[WORK] Stored chapter: {old_chapter}")
    print(f"[WORK] Detected chapter: {detected}")
    if candidates:
        print(f"[WORK] Candidates checked: {len(candidates)}")
    print("=" * 70)


# ============================================================
# WORK MONITORING
# ============================================================


def get_chat_id(user):
    for key in ("chat_id", "telegram_chat_id", "id"):
        if key in user and user[key] is not None:
            return user[key]
    return None


def get_works_from_user(user):
    works = user.get("works")
    if isinstance(works, list):
        return works

    # دعم البنية القديمة إن كانت موجودة.
    works = user.get("novels")
    if isinstance(works, list):
        return works

    return []


def build_notification(work, old_chapter, new_chapter, page_url):
    name = clean_text(work.get("name") or work.get("title") or "عمل جديد")

    if new_chapter > old_chapter + 1:
        chapter_text = f"الفصول الجديدة: {old_chapter + 1} إلى {new_chapter}"
    else:
        chapter_text = f"الفصل الجديد: {new_chapter}"

    return (
        f"📚 {name}\n\n"
        f"🔔 تم اكتشاف {chapter_text}\n"
        f"📖 آخر فصل محفوظ: {old_chapter}\n"
        f"🌐 {page_url}"
    )


def monitor_work(user, work):
    name = clean_text(work.get("name") or work.get("title") or "Unnamed work")
    url = clean_text(work.get("url") or work.get("link") or "")

    if not url:
        print(f"[WORK] {name}: missing URL.")
        return False

    try:
        old_chapter = int(work.get("last_chapter", 0))
    except (TypeError, ValueError):
        print(f"[WORK] {name}: invalid last_chapter.")
        return False

    chat_id = get_chat_id(user)
    if chat_id is None:
        print(f"[WORK] {name}: no chat_id found.")
        return False

    print(f"[MONITOR] Checking {name} | stored={old_chapter} | {url}")

    detected, candidates = detect_latest_chapter(url, old_chapter)
    log_detection(name, old_chapter, detected, candidates)

    if detected is None or detected <= old_chapter:
        return False

    message = build_notification(work, old_chapter, detected, url)

    # مهم: لا نحدّث last_chapter إلا بعد نجاح Telegram.
    try:
        send_message(chat_id, message)
    except Exception as exc:
        print(f"[TELEGRAM] Failed to notify {chat_id} for {name}: {exc}")
        print("[WORK] last_chapter was NOT updated because notification failed.")
        return False

    work["last_chapter"] = detected
    print(f"[WORK] {name}: last_chapter updated {old_chapter} -> {detected}")
    return True


def monitor_all_users(data):
    changed = False

    users = data.get("users", []) if isinstance(data, dict) else []
    if not isinstance(users, list):
        print("[DATA] 'users' is not a list.")
        return False

    for user in users:
        if not isinstance(user, dict):
            continue

        works = get_works_from_user(user)
        for work in works:
            if not isinstance(work, dict):
                continue

            try:
                if monitor_work(user, work):
                    changed = True
            except Exception as exc:
                name = work.get("name", work.get("title", "Unnamed work"))
                print(f"[WORK] Unexpected error in {name}: {exc}")

    return changed


# ============================================================
# MAIN
# ============================================================


def main():
    print("[START] Chapter monitor started.")
    data, sha = load_data()

    if data is None:
        print("[STOP] Could not load data.json.")
        return

    changed = monitor_all_users(data)

    if changed:
        if not save_data(data, sha):
            print("[WARNING] Monitoring found changes, but data.json could not be saved.")
    else:
        print("[DONE] No database changes were required.")


if __name__ == "__main__":
    main()