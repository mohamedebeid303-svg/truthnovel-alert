import os
import re
import json
import base64
import requests
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]

GITHUB_REPO = "mohamedebeid303-svg/truthnovel-alert"
DATA_FILE = "data.json"

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_FILE}"


# =========================
# GitHub Data
# =========================

def load_data():
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    response = requests.get(GITHUB_API, headers=headers, timeout=30)
    response.raise_for_status()

    result = response.json()

    content = base64.b64decode(result["content"]).decode("utf-8")
    data = json.loads(content)

    data.setdefault("users", {})

    return data, result["sha"]


def save_data(data, sha):
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    content = json.dumps(
        data,
        ensure_ascii=False,
        indent=2
    )

    encoded = base64.b64encode(
        content.encode("utf-8")
    ).decode("utf-8")

    payload = {
        "message": "Update bot data",
        "content": encoded,
        "sha": sha
    }

    response = requests.put(
        GITHUB_API,
        headers=headers,
        json=payload,
        timeout=30
    )

    response.raise_for_status()


# =========================
# Telegram
# =========================

def send_message(chat_id, text, keyboard=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard:
        payload["reply_markup"] = json.dumps(
            keyboard,
            ensure_ascii=False
        )

    response = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        data=payload,
        timeout=30
    )

    response.raise_for_status()


def answer_callback(callback_id):
    requests.post(
        f"{TELEGRAM_API}/answerCallbackQuery",
        data={
            "callback_query_id": callback_id
        },
        timeout=30
    )


# =========================
# Keyboards
# =========================

def type_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📖 رواية",
                    "callback_data": "type_رواية"
                },
                {
                    "text": "🇯🇵 مانجا",
                    "callback_data": "type_مانجا"
                }
            ],
            [
                {
                    "text": "🇨🇳 مانها",
                    "callback_data": "type_مانها"
                },
                {
                    "text": "🇰🇷 مانهوا",
                    "callback_data": "type_مانهوا"
                }
            ]
        ]
    }


# =========================
# URL Check
# =========================

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

        return response.status_code < 400

    except requests.RequestException:
        return False


# =========================
# User
# =========================

def ensure_user(data, user_id):
    data["users"].setdefault(
        user_id,
        {
            "works": [],
            "state": None
        }
    )


# =========================
# Commands
# =========================

def start_add(chat_id, data, sha):
    user_id = str(chat_id)

    ensure_user(data, user_id)

    data["users"][user_id]["state"] = {
        "step": "waiting_type"
    }

    save_data(data, sha)

    send_message(
        chat_id,
        "📚 اختر نوع العمل:",
        type_keyboard()
    )


def show_list(chat_id, data):
    user_id = str(chat_id)

    ensure_user(data, user_id)

    works = data["users"][user_id]["works"]

    if not works:
        send_message(
            chat_id,
            "📭 لا توجد أعمال مضافة حتى الآن.\n\n"
            "استخدم /add لإضافة عمل."
        )
        return

    categories = {
        "رواية": "📖 الروايات",
        "مانجا": "🇯🇵 المانجا",
        "مانها": "🇨🇳 المانها",
        "مانهوا": "🇰🇷 المانهوا"
    }

    lines = ["📚 أعمالك:\n"]

    for work_type, title in categories.items():

        selected = [
            work for work in works
            if work["type"] == work_type
        ]

        if not selected:
            continue

        lines.append(f"\n{title}")

        for i, work in enumerate(selected, 1):
            lines.append(
                f"{i}. {work['name']} — الفصل {work['last_chapter']}"
            )

    send_message(
        chat_id,
        "\n".join(lines)
    )


# =========================
# Callback
# =========================

def handle_callback(update, data, sha):

    query = update["callback_query"]

    chat_id = query["message"]["chat"]["id"]
    user_id = str(chat_id)

    callback_data = query["data"]

    answer_callback(query["id"])

    if not callback_data.startswith("type_"):
        return

    work_type = callback_data.replace(
        "type_",
        "",
        1
    )

    ensure_user(data, user_id)

    data["users"][user_id]["state"] = {
        "step": "waiting_name",
        "type": work_type
    }

    save_data(data, sha)

    send_message(
        chat_id,
        f"✅ تم اختيار: {work_type}\n\n"
        "✏️ الآن أرسل اسم العمل:"
    )


# =========================
# Text
# =========================

def handle_text(message, data, sha):

    chat_id = message["chat"]["id"]
    user_id = str(chat_id)

    text = message.get("text", "").strip()

    ensure_user(data, user_id)

    user = data["users"][user_id]

    # /start
    if text == "/start":

        send_message(
            chat_id,
            "👋 أهلاً بك!\n\n"
            "📚 هذا البوت يراقب الأعمال التي تضيفها "
            "ويرسل لك إشعارًا عند صدور فصل جديد.\n\n"
            "/add — إضافة عمل\n"
            "/list — عرض أعمالك"
        )

        return

    # /add
    if text == "/add":

        start_add(
            chat_id,
            data,
            sha
        )

        return

    # /list
    if text == "/list":

        show_list(
            chat_id,
            data
        )

        return

    state = user.get("state")

    if not state:
        return

    step = state.get("step")

    # =========================
    # Name
    # =========================

    if step == "waiting_name":

        state["name"] = text
        state["step"] = "waiting_url"

        save_data(data, sha)

        send_message(
            chat_id,
            "🔗 أرسل الآن رابط العمل أو الصفحة التي تريد مراقبتها:"
        )

        return

    # =========================
    # URL
    # =========================

    if step == "waiting_url":

        if not re.match(
            r"^https?://",
            text,
            re.IGNORECASE
        ):

            send_message(
                chat_id,
                "❌ الرابط غير صحيح.\n\n"
                "يجب أن يبدأ بـ https:// أو http://\n\n"
                "🔗 أرسل الرابط مرة أخرى:"
            )

            return

        send_message(
            chat_id,
            "🔍 جارٍ فحص الرابط..."
        )

        if not check_url(text):

            send_message(
                chat_id,
                "❌ لا أستطيع الوصول إلى هذا الرابط.\n\n"
                "تأكد من أن الرابط صحيح ويمكن الوصول إليه."
            )

            return

        state["url"] = text
        state["step"] = "waiting_chapter"

        save_data(data, sha)

        send_message(
            chat_id,
            "✅ تمكنت من الوصول إلى الصفحة بنجاح!\n\n"
            f"📖 الاسم: {state['name']}\n"
            f"🏷️ النوع: {state['type']}\n\n"
            "🔢 أرسل الآن رقم آخر فصل صدر حاليًا:"
        )

        return

    # =========================
    # Chapter