import os
import re
import json
import requests
from bs4 import BeautifulSoup

SITE_URL = "https://truthnovel.top/"
LAST_CHAPTER_FILE = "last_chapter.txt"
DATA_FILE = "data.json"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# =========================
# Data
# =========================

def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "users": {},
            "update_offset": 0
        }

    with open(DATA_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    data.setdefault("users", {})
    data.setdefault("update_offset", 0)

    return data


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


# =========================
# Telegram
# =========================

def send_message(chat_id, text, keyboard=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False
    }

    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard, ensure_ascii=False)

    response = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        data=payload,
        timeout=30
    )

    response.raise_for_status()


def get_updates(offset):
    response = requests.get(
        f"{TELEGRAM_API}/getUpdates",
        params={
            "offset": offset,
            "timeout": 5
        },
        timeout=15
    )

    response.raise_for_status()
    return response.json()["result"]


# =========================
# Add workflow
# =========================

def type_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "📖 رواية", "callback_data": "type_رواية"},
                {"text": "🇯🇵 مانجا", "callback_data": "type_مانجا"}
            ],
            [
                {"text": "🇨🇳 مانها", "callback_data": "type_مانها"},
                {"text": "🇰🇷 مانهوا", "callback_data": "type_مانهوا"}
            ]
        ]
    }


def start_add(chat_id, data):
    user_id = str(chat_id)

    data["users"].setdefault(
        user_id,
        {
            "works": [],
            "state": None
        }
    )

    data["users"][user_id]["state"] = {
        "step": "waiting_type"
    }

    save_data(data)

    send_message(
        chat_id,
        "📚 اختر نوع العمل:",
        type_keyboard()
    )


def handle_callback(update, data):
    query = update["callback_query"]

    chat_id = query["message"]["chat"]["id"]
    user_id = str(chat_id)

    callback_data = query["data"]

    if not callback_data.startswith("type_"):
        return

    work_type = callback_data.replace("type_", "", 1)

    data["users"].setdefault(
        user_id,
        {
            "works": [],
            "state": None
        }
    )

    data["users"][user_id]["state"] = {
        "step": "waiting_name",
        "type": work_type
    }

    save_data(data)

    requests.post(
        f"{TELEGRAM_API}/answerCallbackQuery",
        data={
            "callback_query_id": query["id"]
        },
        timeout=30
    )

    send_message(
        chat_id,
        f"✅ تم اختيار: {work_type}\n\n"
        "✏️ الآن أرسل اسم العمل:"
    )


def check_url(url):
    try:
        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            allow_redirects=True
        )

        return response.status_code < 400, response.status_code

    except requests.RequestException:
        return False, None


def handle_text_message(message, data):
    chat_id = message["chat"]["id"]
    user_id = str(chat_id)
    text = message.get("text", "").strip()

    data["users"].setdefault(
        user_id,
        {
            "works": [],
            "state": None
        }
    )

    user = data["users"][user_id]

    # =========================
    # Commands
    # =========================

    if text == "/start":
        send_message(
            chat_id,
            "👋 أهلاً بك!\n\n"
            "استخدم /add لإضافة رواية أو مانجا أو مانها أو مانهوا إلى التنبيهات."
        )
        return

    if text == "/add":
        start_add(chat_id, data)
        return

    # =========================
    # Add process
    # =========================

    state = user.get("state")

    if not state:
        return

    step = state.get("step")

    # ---- Name ----

    if step == "waiting_name":
        state["name"] = text
        state["step"] = "waiting_url"

        save_data(data)

        send_message(
            chat_id,
            "🔗 أرسل الآن رابط العمل أو الصفحة التي تريد مراقبتها:"
        )
        return

    # ---- URL ----

    if step == "waiting_url":

        if not re.match(r"^https?://", text, re.IGNORECASE):
            send_message(
                chat_id,
                "❌ الرابط غير صحيح.\n\n"
                "يجب أن يبدأ الرابط بـ:\n"
                "https:// أو http://\n\n"
                "🔗 أرسل الرابط مرة أخرى:"
            )
            return

        send_message(
            chat_id,
            "🔍 جارٍ فحص الرابط...\n\n"
            "انتظر قليلًا."
        )

        accessible, status_code = check_url(text)

        if not accessible:
            send_message(
                chat_id,
                "❌ لا أستطيع الوصول إلى هذا الرابط.\n\n"
                "تأكد من أن الرابط صحيح ويمكن فتحه، ثم أرسله مرة أخرى."
            )
            return

        state["url"] = text
        state["step"] = "waiting_chapter"

        save_data(data)

        send_message(
            chat_id,
            "✅ تمكنت من الوصول إلى الصفحة بنجاح.\n\n"
            f"📖 الاسم: {state['name']}\n"
            f"🏷️ النوع: {state['type']}\n"
            f"🔗 الرابط: {text}\n\n"
            "🔢 الآن أرسل رقم آخر فصل صدر حاليًا:"
        )
        return

    # ---- Last chapter ----

    if step == "waiting_chapter":

        if not text.isdigit():
            send_message(
                chat_id,
                "❌ أرسل رقم الفصل فقط.\n\n"
                "مثال:\n"
                "187"
            )
            return

        last_chapter = int(text)

        work = {
            "name": state["name"],
            "type": state["type"],
            "url": state["url"],
            "last_chapter": last_chapter
        }

        user["works"].append(work)

        user["state"] = None

        save_data(data)

        send_message(
            chat_id,
            "✅ تمت إضافة العمل بنجاح!\n\n"
            f"📖 {work['name']}\n"
            f"🏷️ النوع: {work['type']}\n"
            f"🔢 آخر فصل: {work['last_chapter']}\n\n"
            f"🔔 ستبدأ المراقبة من الفصل {last_chapter + 1}."
        )

        return


# =========================
# Telegram messages