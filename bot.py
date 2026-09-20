import os
import re
import requests
from bs4 import BeautifulSoup

SITE_URL = "https://truthnovel.top/"
LAST_CHAPTER_FILE = "last_chapter.txt"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = "805162451"

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def get_last_chapter():
    with open(LAST_CHAPTER_FILE, "r", encoding="utf-8") as file:
        return int(file.read().strip())


def save_last_chapter(chapter_number):
    with open(LAST_CHAPTER_FILE, "w", encoding="utf-8") as file:
        file.write(str(chapter_number))


def get_chapters():
    response = requests.get(
        SITE_URL,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    chapters = []

    for link in soup.find_all("a", href=True):
        text = link.get_text(" ", strip=True)

        match = re.match(r"^(\d+)\s*[-–—]\s*(.+)$", text)

        if not match:
            continue

        chapter_number = int(match.group(1))
        title = match.group(2).strip()
        chapter_url = link["href"]

        if chapter_url.startswith("/"):
            chapter_url = "https://truthnovel.top" + chapter_url
        elif chapter_url.startswith("http://"):
            chapter_url = chapter_url.replace("http://", "https://", 1)

        chapters.append({
            "number": chapter_number,
            "title": title,
            "url": chapter_url
        })

    unique_chapters = {}

    for chapter in chapters:
        unique_chapters[chapter["number"]] = chapter

    return sorted(
        unique_chapters.values(),
        key=lambda chapter: chapter["number"]
    )


def send_telegram_message(chapter):
    message = (
        "🔔 فصل جديد من سيد الحقيقة!\n\n"
        f"📖 الفصل: {chapter['number']}\n"
        f"📝 العنوان: {chapter['title']}\n\n"
        f"🔗 {chapter['url']}"
    )

    response = requests.post(
        TELEGRAM_URL,
        data={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": False
        },
        timeout=30
    )

    response.raise_for_status()


def main():
    last_chapter = get_last_chapter()
    chapters = get_chapters()

    if not chapters:
        print("لم يتم العثور على أي فصول.")
        return

    new_chapters = [
        chapter
        for chapter in chapters
        if chapter["number"] > last_chapter
    ]

    if not new_chapters:
        print(f"لا توجد فصول جديدة. آخر فصل: {last_chapter}")
        return

    print(
        f"تم العثور على {len(new_chapters)} فصل/فصول جديدة "
        f"ابتداءً من {new_chapters[0]['number']}."
    )

    for chapter in new_chapters:
        print(f"إرسال الفصل {chapter['number']}...")
        send_telegram_message(chapter)

    newest_chapter = new_chapters[-1]["number"]
    save_last_chapter(newest_chapter)

    print(f"تم الحفظ. آخر فصل الآن: {newest_chapter}")


if __name__ == "__main__":
    main()