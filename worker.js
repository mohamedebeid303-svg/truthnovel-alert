const GITHUB_REPO = "mohamedebeid303-svg/truthnovel-alert";
const DATA_FILE = "data.json";

export default {
  async fetch(request, env) {
    try {
      if (request.method !== "POST") {
        return new Response("TruthNovel Bot is running.", {
          status: 200
        });
      }

      const update = await request.json();

      if (update.callback_query) {
        await handleCallback(update, env);
      } else if (update.message) {
        await handleMessage(update.message, env);
      }

      return new Response("OK", { status: 200 });

    } catch (error) {
      console.error(error);

      return new Response("OK", { status: 200 });
    }
  }
};


// =========================
// GitHub
// =========================

async function loadData(env) {

  if (!env.GITHUB_TOKEN) {
    throw new Error("GITHUB_TOKEN is missing");
  }

  const response = await fetch(
    `https://api.github.com/repos/${GITHUB_REPO}/contents/${DATA_FILE}`,
    {
      headers: githubHeaders(env)
    }
  );

  if (!response.ok) {
    const errorText = await response.text();

    throw new Error(
      `GitHub error ${response.status}: ${errorText.slice(0, 300)}`
    );
  }

  const result = await response.json();

  const binary = Uint8Array.from(
    atob(result.content.replace(/\n/g, "")),
    c => c.charCodeAt(0)
  );

  const content = new TextDecoder().decode(binary);

  const data = JSON.parse(content);

  data.users ??= {};

  return {
    data,
    sha: result.sha
  };
}


async function saveData(data, sha, env) {

  const content = JSON.stringify(
    data,
    null,
    2
  );

  const encoded = btoa(
    String.fromCharCode(
      ...new TextEncoder().encode(content)
    )
  );

  const response = await fetch(
    `https://api.github.com/repos/${GITHUB_REPO}/contents/${DATA_FILE}`,
    {
      method: "PUT",

      headers: {
        ...githubHeaders(env),
        "Content-Type": "application/json"
      },

      body: JSON.stringify({
        message: "Update bot data",
        content: encoded,
        sha
      })
    }
  );

  if (!response.ok) {
    throw new Error(
      "Failed to save data.json: " +
      await response.text()
    );
  }
}


function githubHeaders(env) {

  return {
    "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
  };
}


// =========================
// Telegram
// =========================

async function sendMessage(chatId, text, keyboard, env) {

  const body = {
    chat_id: chatId,
    text
  };

  if (keyboard) {
    body.reply_markup = keyboard;
  }

  await fetch(
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`,
    {
      method: "POST",

      headers: {
        "Content-Type": "application/json"
      },

      body: JSON.stringify(body)
    }
  );
}


async function answerCallback(callbackId, env) {

  await fetch(
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/answerCallbackQuery`,
    {
      method: "POST",

      headers: {
        "Content-Type": "application/json"
      },

      body: JSON.stringify({
        callback_query_id: callbackId
      })
    }
  );
}


// =========================
// Keyboards
// =========================

function typeKeyboard() {

  return {
    inline_keyboard: [
      [
        {
          text: "📖 رواية",
          callback_data: "type_رواية"
        },
        {
          text: "🇯🇵 مانجا",
          callback_data: "type_مانجا"
        }
      ],
      [
        {
          text: "🇨🇳 مانها",
          callback_data: "type_مانها"
        },
        {
          text: "🇰🇷 مانهوا",
          callback_data: "type_مانهوا"
        }
      ]
    ]
  };
}


// =========================
// User
// =========================

function ensureUser(data, userId) {

  if (!data.users[userId]) {

    data.users[userId] = {
      works: [],
      state: null
    };
  }
}


// =========================
// /start
// =========================

async function startCommand(chatId, data, env) {

  ensureUser(data, String(chatId));

  await sendMessage(
    chatId,

    "👋 أهلاً بك!\n\n" +
    "📚 هذا البوت يسمح لك بإضافة الأعمال " +
    "التي تريد مراقبتها.\n\n" +
    "/add — إضافة عمل\n" +
    "/list — عرض أعمالك",

    null,

    env
  );
}


// =========================
// /add
// =========================

async function addCommand(chatId, data, sha, env) {

  const userId = String(chatId);

  ensureUser(data, userId);

  data.users[userId].state = {
    step: "waiting_type"
  };

  await saveData(data, sha, env);

  await sendMessage(
    chatId,
    "📚 اختر نوع العمل:",
    typeKeyboard(),
    env
  );
}


// =========================
// /list
// =========================

async function listCommand(chatId, data, env) {

  const userId = String(chatId);

  ensureUser(data, userId);

  const works = data.users[userId].works;

  if (!works.length) {

    await sendMessage(
      chatId,

      "📭 لا توجد أعمال مضافة حتى الآن.\n\n" +
      "استخدم /add لإضافة عمل.",

      null,
      env
    );

    return;
  }

  const categories = {
    "رواية": "📖 الروايات",
    "مانجا": "🇯🇵 المانجا",
    "مانها": "🇨🇳 المانها",
    "مانهوا": "🇰🇷 المانهوا"
  };

  const lines = ["📚 أعمالك:\n"];

  for (const type of Object.keys(categories)) {

    const selected = works.filter(
      work => work.type === type
    );

    if (!selected.length) continue;

    lines.push("");
    lines.push(categories[type]);

    selected.forEach((work, index) => {

      lines.push(
        `${index + 1}. ${work.name} — الفصل ${work.last_chapter}`
      );

    });
  }

  await sendMessage(
    chatId,
    lines.join("\n"),
    null,
    env
  );
}


// =========================
// Callback
// =========================

async function handleCallback(update, env) {

  const query = update.callback_query;

  const chatId = query.message.chat.id;

  const userId = String(chatId);

  await answerCallback(
    query.id,
    env
  );

  if (!query.data.startsWith("type_")) {
    return;
  }

  const workType = query.data.replace(
    "type_",
    ""
  );

  const {
    data,
    sha
  } = await loadData(env);

  ensureUser(data, userId);

  data.users[userId].state = {
    step: "waiting_name",
    type: workType
  };

  await saveData(
    data,
    sha,
    env
  );

  await sendMessage(
    chatId,

    `✅ تم اختيار: ${workType}\n\n` +
    "✏️ الآن أرسل اسم العمل:",

    null,
    env
  );
}


// =========================
// Message
// =========================

async function handleMessage(message, env) {

  const chatId = message.chat.id;

  const userId = String(chatId);

  // حماية البوت ليعمل مع حسابك فقط
  if (
    env.ADMIN_CHAT_ID &&
    String(chatId) !== String(env.ADMIN_CHAT_ID)
  ) {
    return;
  }

  const {
    data,
    sha
  } = await loadData(env);

  ensureUser(data, userId);

  const user = data.users[userId];

  const text = (
    message.text || ""
  ).trim();


  // /start

  if (text === "/start") {

    await startCommand(
      chatId,
      data,
      env
    );

    return;
  }


  // /add

  if (text === "/add") {

    await addCommand(
      chatId,
      data,
      sha,
      env
    );

    return;
  }


  // /list

  if (text === "/list") {

    await listCommand(
      chatId,
      data,
      env
    );

    return;
  }


  const state = user.state;

  if (!state) {
    return;
  }


  // =========================
  // Name
  // =========================

  if (state.step === "waiting_name") {

    state.name = text;

    state.step = "waiting_url";

    await saveData(
      data,
      sha,
      env
    );

    await sendMessage(
      chatId,

      "🔗 أرسل الآن رابط العمل أو الصفحة " +
      "التي تريد مراقبتها:",

      null,
      env
    );

    return;
  }


  // =========================
  // URL
  // =========================

  if (state.step === "waiting_url") {

    if (!/^https?:\/\//i.test(text)) {

      await sendMessage(
        chatId,

        "❌ الرابط غير صحيح.\n\n" +
        "يجب أن يبدأ بـ https:// أو http://\n\n" +
        "🔗 أرسل الرابط مرة أخرى:",

        null,
        env
      );

      return;
    }

    await sendMessage(
      chatId,
      "🔍 جارٍ فحص الرابط...",
      null,
      env
    );

    try {

      const response = await fetch(
        text,
        {
          method: "GET",
          headers: {
            "User-Agent": "Mozilla/5.0"
          }
        }
      );

      if (!response.ok) {
        throw new Error("URL unavailable");
      }

    } catch {

      await sendMessage(
        chatId,

        "❌ لا أستطيع الوصول إلى هذا الرابط.\n\n" +
        "تأكد من أن الرابط صحيح ويمكن الوصول إليه.",

        null,
        env
      );

      return;
    }

    state.url = text;

    state.step = "waiting_chapter";

    await saveData(
      data,
      sha,
      env
    );

    await sendMessage(
      chatId,

      "✅ تمكنت من الوصول إلى الصفحة بنجاح!\n\n" +
      `📖 الاسم: ${state.name}\n` +
      `🏷️ النوع: ${state.type}\n\n` +
      "🔢 أرسل الآن رقم آخر فصل صدر حاليًا:",

      null,
      env
    );

    return;
  }


  // =========================
  // Chapter
  // =========================

  if (state.step === "waiting_chapter") {

    const chapter = Number(
      text.replace(/[^\d.]/g, "")
    );

    if (
      !Number.isFinite(chapter) ||
      chapter < 0
    ) {

      await sendMessage(
        chatId,

        "❌ رقم الفصل غير صحيح.\n\n" +
        "🔢 أرسل رقم آخر فصل صدر حاليًا:",

        null,
        env
      );

      return;
    }

    user.works.push({
      type: state.type,
      name: state.name,
      url: state.url,
      last_chapter: chapter
    });

    user.state = null;

    await saveData(
      data,
      sha,
      env
    );

    await sendMessage(
      chatId,

      "✅ تمت إضافة العمل بنجاح!\n\n" +
      `📖 ${state.name}\n` +
      `🏷️ ${state.type}\n` +
      `🔢 آخر فصل: ${chapter}\n\n` +
      "📚 يمكنك استخدام /list لعرض أعمالك.",

      null,
      env
    );

    return;
  }
}