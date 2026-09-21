import os
import re
import json
import base64
import time
import requests
from bs4 import BeautifulSoup


# =========================================================
# CONFIG
# =========================================================

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

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml"
}


# =========================================================
# GITHUB
# =========================================================

def github_headers():

    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }


def load_data():

    response = requests.get(
        GITHUB_API,
        headers=github_headers(),
        timeout=30
    )

    response.raise_for_status()

    result = response.json()

    content = base64.b64decode(
        result["content"]
    ).decode("utf-8")

    data = json.loads(content)

    data.setdefault("users", {})

    return data, result["sha"]


def save_data(data, sha):

    content = json.dumps(
        data,
        ensure_ascii=False,
        indent=2
    )

    encoded = base64.b64encode(
        content.encode("utf-8")
    ).decode("utf-8")

    payload = {
        "message": "Update chapter data",
        "content": encoded,
        "sha": sha
    }

    response = requests.put(
        GITHUB_API,
        headers=github_headers(),
        json=payload,
        timeout=30
    )

    # Another process may have changed data.json.
    # Do not silently overwrite it.
    if response.status_code == 409:
        print(
            "[WARNING] data.json changed while monitoring."
        )
        print(
            "[WARNING] Changes were not overwritten."
        )
        return False

    response.raise_for_status()

    return True


# =========================================================
# TELEGRAM
# =========================================================

def send_message(chat_id, text):

    response = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False
        },
        timeout=30
    )

    response.raise_for_status()


# =========================================================
# WEBSITE
# =========================================================

def download_page(url):

    response = requests.get(
        url,
        headers=HTTP_HEADERS,
        timeout=30,
        allow_redirects=True
    )

    response.raise_for_status()

    return response.text


# =========================================================
# CHAPTER DETECTION
# =========================================================

def normalize_number(value):

    try:

        number = float(value)

        if number.is_integer():
            return int(number)

        return number

    except (ValueError, TypeError):

        return None


def extract_chapter_numbers(text):

    patterns = [

        # English
        r"\bchapter[\s._:#-]*(\d+(?:\.\d+)?)",

        # Arabic
        r"\bالفصل[\s._:#-]*(\d+(?:\.\d+)?)",

        # Chinese
        r"第\s*(\d+(?:\.\d+)?)\s*章",

        # Common URL / title forms
        r"\bchap[\s._:#-]*(\d+(?:\.\d+)?)",

        # "Ch 123"
        r"\bch[\s._:#-]*(\d+(?:\.\d+)?)"
    ]

    chapters = []

    for pattern in patterns:

        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        for match in matches:

            number = normalize_number(match)

            if number is not None:
                chapters.append(number)

    return chapters


def extract_latest_chapter(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # -----------------------------------------------------
    # 1. Page title
    # -----------------------------------------------------

    if soup.title:

        title = soup.title.get_text(
            " ",
            strip=True
        )

        chapters = extract_chapter_numbers(title)

        if chapters:
            return max(chapters)


    # -----------------------------------------------------
    # 2. H1 / H2 / H3
    # -----------------------------------------------------

    headings = []

    for tag in soup.find_all(
        ["h1", "h2", "h3", "h4"]
    ):

        text = tag.get_text(
            " ",
            strip=True
        )

        if text:
            headings.append(text)

    chapters = extract_chapter_numbers(
        " ".join(headings)
    )

    if chapters:
        return max(chapters)


    # -----------------------------------------------------
    # 3. Links
    # -----------------------------------------------------

    links = []

    for link in soup.find_all("a"):

        text = link.get_text(
            " ",
            strip=True
        )

        href = link.get(
            "href",
            ""
        )

        if text:
            links.append(text)

        if href:
            links.append(href)

    chapters = extract_chapter_numbers(
        " ".join(links)
    )

    if chapters:
        return max(chapters)


    # -----------------------------------------------------
    # 4. Full page text
    # -----------------------------------------------------

    page_text = soup.get_text(
        " ",
        strip=True
    )

    chapters = extract_chapter_numbers(
        page_text
    )

    if chapters:
        return max(chapters)


    return None


# =========================================================
# CHAPTER DISPLAY
# =========================================================

def display_chapter(chapter):

    if chapter is None:
        return "غير معروف"

    try:

        number = float(chapter)

        if number.is_integer():
            return str(int(number))

        return str(number)

    except (ValueError, TypeError):

        return str(chapter)


# =========================================================
# MONITOR ONE WORK
# =========================================================

def monitor_work(chat_id, work):

    name = work.get(
        "name",
        "عمل بدون اسم"
    )

    url = work.get("url")

    old_chapter = work.get(
        "last_chapter"
    )

    if not url:

        print(
            f"[SKIP] {name}: URL missing"
        )

        return False


    print(
        f"[CHECK] {name}"
    )

    print(
        f"        {url}"
    )


    # -----------------------------------------------------
    # Download page
    # -----------------------------------------------------

    try:

        html = download_page(url)

    except Exception as error:

        print(
            f"[ERROR] Could not access {url}"
        )

        print(
            f"        {error}"
        )

        return False


    # -----------------------------------------------------
    # Detect chapter
    # -----------------------------------------------------

    latest_chapter = extract_latest_chapter(
        html
    )

    if latest_chapter is None:

        print(
            f"[UNKNOWN] Could not detect "
            f"chapter for {name}"
        )

        return False


    print(
        f"[RESULT] "
        f"{display_chapter(old_chapter)}"
        f" -> "
        f"{display_chapter(latest_chapter)}"
    )


    # -----------------------------------------------------
    # First monitoring
    # -----------------------------------------------------

    if old_chapter is None:

        work["last_chapter"] = latest_chapter

        print(
            "[INIT] First chapter recorded."
        )

        return True


    old_number = normalize_number(
        old_chapter
    )

    if old_number is None:

        work["last_chapter"] = latest_chapter

        print(
            "[RESET] Invalid previous chapter."
        )

        return True


    # -----------------------------------------------------
    # Nothing new
    # -----------------------------------------------------

    if latest_chapter <= old_number:

        print(
            "[OK] No new chapter."
        )

        return False


    # -----------------------------------------------------
    # NEW CHAPTER
    # -----------------------------------------------------

    print(
        "[NEW] New chapter detected!"
    )


    # We send one notification containing
    # the newest chapter available.
    message = (
        "🔔 فصل جديد!\n\n"
        f"📖 {name}\n"
        f"📚 الفصل {display_chapter(latest_chapter)}\n\n"
        f"🔗 {url}"
    )


    try:

        send_message(
            chat_id,
            message
        )

    except Exception as error:

        print(
            "[ERROR] Telegram notification failed."
        )

        print(error)

        # IMPORTANT:
        # Do not update last_chapter if Telegram
        # notification failed.
        return False


    # -----------------------------------------------------
    # Save new chapter in memory
    # -----------------------------------------------------

    work["last_chapter"] = latest_chapter

    print(
        "[SAVED] Chapter updated."
    )

    return True


# =========================================================
# ALL USERS
# =========================================================

def monitor_all_users(data):

    changed = False

    users = data.get(
        "users",
        {}
    )


    print(
        f"[INFO] Total users: {len(users)}"
    )


    for chat_id, user in users.items():

        works = user.get(
            "works",
            []
        )


        print(
            ""
        )

        print(
            f"[USER] {chat_id}"
        )

        print(
            f"[WORKS] {len(works)}"
        )


        for work in works:

            try:

                result = monitor_work(
                    chat_id,
                    work
                )

                if result:
                    changed = True

            except Exception as error:

                print(
                    "[ERROR] Unexpected work error:"
                )

                print(error)


    return changed


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "=========================================="
    )

    print(
        "      TRUTHNOVEL CHAPTER MONITOR"
    )

    print(
        "=========================================="
    )


    # -----------------------------------------------------
    # Load users and works
    # -----------------------------------------------------

    data, sha = load_data()


    # -----------------------------------------------------
    # Monitor everything
    # -----------------------------------------------------

    changed = monitor_all_users(
        data
    )


    # -----------------------------------------------------
    # Save only if something changed
    # -----------------------------------------------------

    if changed:

        print(
            ""
        )

        print(
            "[SAVE] Updating data.json..."
        )

        success = save_data(
            data,
            sha
        )

        if success:

            print(
                "[SAVE] data.json updated."
            )

        else:

            print(
                "[WARNING] data.json was not updated."
            )

    else:

        print(
            ""
        )

        print(
            "[SAVE] No changes."
        )


    print(
        ""
    )

    print(
        "[DONE]"
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()