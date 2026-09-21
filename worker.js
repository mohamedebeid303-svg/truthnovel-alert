const GITHUB_REPO = "mohamedebeid303-svg/truthnovel-alert";
const DATA_FILE = "data.json";

// =========================
// Admin
// =========================

const ADMIN_CHAT_ID = "805162451";

// المستخدم يعتبر نشطًا إذا تفاعل مع البوت خلال آخر 15 دقيقة
const ACTIVE_TIME = 15 * 60 * 1000;

// =========================
// Worker
// =========================

export default {
async fetch(request, env) {

let update = null;

try {

  if (request.method !== "POST") {

    return new Response(
      "TruthNovel Bot is running.",
      {
        status: 200
      }
    );
  }

  update = await request.json();


  if (update.callback_query) {

    await handleCallback(
      update,
      env
    );

  } else if (update.message) {

    await handleMessage(
      update.message,
      env
    );
  }


  return new Response(
    "OK",
    {
      status: 200
    }
  );


} catch (error) {

  console.error(
    "WORKER ERROR:",
    error?.stack ||
    error?.message ||
    error
  );


  // إرسال الخطأ إلى Telegram
  const chatId =
    update?.message?.chat?.id;


  if (chatId) {

    try {

      await sendMessage(
        chatId,

        "❌ حدث خطأ داخل البوت.\n\n" +
        `الخطأ:\n${error?.message || "Unknown error"}`,

        null,

        env
      );

    } catch (telegramError) {

      console.error(
        "TELEGRAM ERROR:",
        telegramError?.stack ||
        telegramError?.message ||
        telegramError
      );
    }
  }


  return new Response(
    "OK",
    {
      status: 200
    }
  );
}

}
};

// =========================
// GitHub
// =========================

async function loadData(env) {

if (!env.GITHUB_TOKEN) {

throw new Error(
  "GITHUB_TOKEN is missing from Cloudflare Secrets"
);

}

const url =
"https://api.github.com/repos/${GITHUB_REPO}/contents/${DATA_FILE}";

console.log(
"GitHub GET:",
url
);

const response =
await fetch(
url,
{
method: "GET",

    headers: {

      "Authorization":
        `Bearer ${env.GITHUB_TOKEN}`,

      "Accept":
        "application/vnd.github+json",

      "X-GitHub-Api-Version":
        "2022-11-28",

      "User-Agent":
        "TruthNovel-Bot"
    },

    cache:
      "no-store"
  }
);

console.log(
"GitHub response status:",
response.status
);

if (!response.ok) {

const errorText =
  await response.text();


console.error(
  "GitHub API ERROR:",
  response.status,
  errorText
);


throw new Error(
  `GitHub API error ${response.status}: ${errorText.slice(0, 500)}`
);

}

const result =
await response.json();

if (!result.content) {

throw new Error(
  "GitHub returned no file content for data.json"
);

}

let content;

try {

const cleanBase64 =
  result.content.replace(
    /\s/g,
    ""
  );


const binary =
  Uint8Array.from(
    atob(cleanBase64),
    c =>
      c.charCodeAt(0)
  );


content =
  new TextDecoder().decode(
    binary
  );

} catch (error) {

throw new Error(
  "Failed to decode data.json from GitHub: " +
  error.message
);

}

let data;

try {

data =
  JSON.parse(content);

} catch (error) {

throw new Error(
  "data.json contains invalid JSON: " +
  error.message
);

}

data.users ??= {};

data.settings ??= {};

data.settings.maintenance ??= false;

return {
data,
sha: result.sha
};
}

// =========================
// Save GitHub
// =========================

async function saveData(
data,
sha,
env
) {

if (!env.GITHUB_TOKEN) {

throw new Error(
  "GITHUB_TOKEN is missing from Cloudflare Secrets"
);

}

const content =
JSON.stringify(
data,
null,
2
);

const encoded =
btoa(
String.fromCharCode(
...new TextEncoder().encode(
content
)
)
);

const url =
"https://api.github.com/repos/${GITHUB_REPO}/contents/${DATA_FILE}";

console.log(
"GitHub PUT:",
url
);

const response =
await fetch(
url,
{
method: "PUT",

    headers: {

      "Authorization":
        `Bearer ${env.GITHUB_TOKEN}`,

      "Accept":
        "application/vnd.github+json",

      "X-GitHub-Api-Version":
        "2022-11-28",

      "User-Agent":
        "TruthNovel-Bot",

      "Content-Type":
        "application/json"
    },


    body:
      JSON.stringify({

        message:
          "Update bot data",

        content:
          encoded,

        sha
      })
  }
);

console.log(
"GitHub save status:",
response.status
);

if (!response.ok) {

const errorText =
  await response.text();


console.error(
  "GitHub SAVE ERROR:",
  response.status,
  errorText
);


throw new Error(
  `Failed to save data.json. GitHub ${response.status}: ` +
  errorText.slice(0, 500)
);

}
}

// =========================
// Telegram
// =========================

async function sendMessage(
chatId,
text,
keyboard,
env
) {

if (!env.TELEGRAM_BOT_TOKEN) {

console.error(
  "TELEGRAM_BOT_TOKEN is missing"
);

return;

}

const body = {
chat_id: chatId,
text
};

if (keyboard) {

body.reply_markup =
  keyboard;

}

const response =
await fetch(
"https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage",

  {
    method: "POST",

    headers: {
      "Content-Type":
        "application/json"
    },

    body:
      JSON.stringify(body)
  }
);

if (!response.ok) {

const errorText =
  await response.text();


console.error(
  "Telegram sendMessage ERROR:",
  response.status,
  errorText
);

}
}

// =========================
// Callback Answer
// =========================

async function answerCallback(
callbackId,
env
) {

const response =
await fetch(
"https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/answerCallbackQuery",

  {
    method: "POST",

    headers: {
      "Content-Type":
        "application/json"
    },

    body:
      JSON.stringify({

        callback_query_id:
          callbackId
      })
  }
);

if (!response.ok) {

console.error(
  "Telegram callback ERROR:",
  response.status,
  await response.text()
);

}
}

// =========================
// Keyboards
// =========================

function typeKeyboard() {

return {

inline_keyboard: [

  [
    {
      text:
        "📖 رواية",

      callback_data:
        "type_رواية"
    },

    {
      text:
        "🇯🇵 مانجا",

      callback_data:
        "type_مانجا"
    }
  ],

  [

    {
      text:
        "🇨🇳 مانها",

      callback_data:
        "type_مانها"
    },

    {
      text:
        "🇰🇷 مانهوا",

      callback_data:
        "type_مانهوا"
    }
  ]
]

};
}

// =========================
// User
// =========================

function ensureUser(
data,
userId
) {

if (!data.users[userId]) {

data.users[userId] = {

  works: [],

  state: null,

  first_seen:
    Date.now(),

  last_active:
    Date.now()
};

return true;

}

if (
!data.users[userId].works
) {

data.users[userId].works = [];

}

if (
!("state" in data.users[userId])
) {

data.users[userId].state =
  null;

}

if (
!data.users[userId].first_seen
) {

data.users[userId].first_seen =
  Date.now();

}

if (
!data.users[userId].last_active
) {

data.users[userId].last_active =
  Date.now();

}

return false;
}

// =========================
// Activity
// =========================

function updateActivity(
user
) {

const now =
Date.now();

const previous =
user.last_active || 0;

user.last_active =
now;

// نقلل عدد عمليات الحفظ في GitHub
// فلا نحفظ النشاط في كل رسالة
return (
now - previous >
5 * 60 * 1000
);
}

// =========================
// Maintenance Message
// =========================

function maintenanceMessage() {

return (
"🔧 البوت في وضع الصيانة حاليًا.\n\n" +
"نقوم بإجراء بعض التحديثات والتحسينات.\n" +
"⏳ سيعود للعمل بمجرد انتهاء الصيانة.\n\n" +
"شكرًا لصبركم ❤️"
);
}

// =========================
// /start
// =========================

async function startCommand(
chatId,
data,
env
) {

ensureUser(
data,
String(chatId)
);

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

async function addCommand(
chatId,
data,
sha,
env
) {

const userId =
String(chatId);

ensureUser(
data,
userId
);

data.users[userId].state = {

step:
  "waiting_type"

};

await saveData(
data,
sha,
env
);

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

async function listCommand(
chatId,
data,
env
) {

const userId =
String(chatId);

ensureUser(
data,
userId
);

const works =
data.users[userId].works;

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

"رواية":
  "📖 الروايات",

"مانجا":
  "🇯🇵 المانجا",

"مانها":
  "🇨🇳 المانها",

"مانهوا":
  "🇰🇷 المانهوا"

};

const lines = [
"📚 أعمالك:\n"
];

for (
const type of Object.keys(
categories
)
) {

const selected =
  works.filter(
    work =>
      work.type === type
  );


if (!selected.length) {
  continue;
}


lines.push("");

lines.push(
  categories[type]
);


selected.forEach(
  (work, index) => {

    lines.push(
      `${index + 1}. ${work.name} — الفصل ${work.last_chapter}`
    );
  }
);

}

const keyboard = {

inline_keyboard: [

  [
    {
      text:
        "🗑️ إزالة من القائمة",

      callback_data:
        "remove_menu"
    }
  ]
]

};

await sendMessage(
chatId,

lines.join("\n"),

keyboard,

env

);
}

// =========================
// Remove Menu
// =========================

async function removeMenu(
chatId,
data,
env
) {

const userId =
String(chatId);

ensureUser(
data,
userId
);

const works =
data.users[userId].works;

if (!works.length) {

await sendMessage(
  chatId,

  "📭 لا توجد أعمال لإزالتها.",

  null,

  env
);

return;

}

const keyboard = {

inline_keyboard: []

};

works.forEach(
(work, index) => {

  keyboard.inline_keyboard.push(
    [
      {
        text:
          `🗑️ ${work.name}`,

        callback_data:
          `remove_${index}`
      }
    ]
  );
}

);

keyboard.inline_keyboard.push(

[
  {
    text:
      "❌ إلغاء",

    callback_data:
      "remove_cancel"
  }
]

);

await sendMessage(
chatId,

"🗑️ اختر العمل الذي تريد إزالته:",

keyboard,

env

);
}

// =========================
// Confirm Remove
// =========================

async function confirmRemove(
chatId,
index,
data,
env
) {

const userId =
String(chatId);

ensureUser(
data,
userId
);

const works =
data.users[userId].works;

const work =
works[index];

if (!work) {

await sendMessage(
  chatId,

  "❌ هذا العمل لم يعد موجودًا.",

  null,

  env
);

return;

}

const keyboard = {

inline_keyboard: [

  [

    {
      text:
        "✅ نعم، إزالة",

      callback_data:
        `confirm_remove_${index}`
    },

    {
      text:
        "❌ إلغاء",

      callback_data:
        "remove_cancel"
    }
  ]
]

};

await sendMessage(
chatId,

"⚠️ هل أنت متأكد من إزالة هذا العمل؟\n\n" +

`📖 ${work.name}\n` +

`🏷️ ${work.type}\n` +

`🔢 آخر فصل: ${work.last_chapter}`,

keyboard,

env

);
}

// =========================
// Perform Remove
// =========================

async function performRemove(
chatId,
index,
data,
sha,
env
) {

const userId =
String(chatId);

ensureUser(
data,
userId
);

const works =
data.users[userId].works;

const work =
works[index];

if (!work) {

await sendMessage(
  chatId,

  "❌ العمل غير موجود.",

  null,

  env
);

return;

}

const removedName =
work.name;

works.splice(
index,
1
);

await saveData(
data,
sha,
env
);

await sendMessage(
chatId,

"✅ تمت إزالة العمل بنجاح.\n\n" +

`🗑️ ${removedName}`,

null,

env

);
}

// =========================
// Admin Keyboard
// =========================

function adminKeyboard(
maintenance
) {

return {

inline_keyboard: [

  [

    {
      text:
        "📊 الإحصائيات",

      callback_data:
        "admin_stats"
    },

    {
      text:
        "📚 الأعمال المراقبة",

      callback_data:
        "admin_works"
    }
  ],

  [

    {
      text:
        maintenance
          ? "🟢 إيقاف الصيانة"
          : "🔧 تشغيل الصيانة",

      callback_data:
        maintenance
          ? "admin_maintenance_off"
          : "admin_maintenance_on"
    }
  ]
]

};
}

// =========================
// Admin Panel
// =========================

async function adminPanel(
chatId,
data,
env
) {

if (
String(chatId) !==
ADMIN_CHAT_ID
) {
return;
}

const maintenance =
Boolean(
data.settings.maintenance
);

await sendMessage(
chatId,

"🛠️ لوحة تحكم المبرمج\n\n" +

`🔧 حالة الصيانة: ${
  maintenance
    ? "🟠 مفعّلة"
    : "🟢 متوقفة"
}\n\n` +

"اختر ما تريد:",

adminKeyboard(
  maintenance
),

env

);
}

// =========================
// Admin Stats
// =========================

async function adminStats(
chatId,
data,
env
) {

if (
String(chatId) !==
ADMIN_CHAT_ID
) {
return;
}

const users =
Object.values(
data.users
);

const totalUsers =
users.length;

const now =
Date.now();

const activeUsers =
users.filter(
user =>
user.last_active &&
now - user.last_active <=
ACTIVE_TIME
).length;

let totalWorks = 0;

users.forEach(
user => {

  totalWorks +=
    Array.isArray(user.works)
      ? user.works.length
      : 0;
}

);

await sendMessage(
chatId,

"📊 إحصائيات البوت\n\n" +

`👥 إجمالي المستخدمين: ${totalUsers}\n` +

`🟢 المستخدمون النشطون الآن: ${activeUsers}\n` +

`📚 إجمالي الأعمال المراقبة: ${totalWorks}\n\n` +

`🔧 الصيانة: ${
  data.settings.maintenance
    ? "🟠 مفعّلة"
    : "🟢 متوقفة"
}`,

{
  inline_keyboard: [

    [
      {
        text:
          "🔙 لوحة المبرمج",

        callback_data:
          "admin_panel"
      }
    ]
  ]
},

env

);
}

// =========================
// Admin Works
// =========================

async function adminWorks(
chatId,
data,
env
) {

if (
String(chatId) !==
ADMIN_CHAT_ID
) {
return;
}

const lines = [
"📚 جميع الأعمال التي يراقبها البوت\n"
];

let totalWorks = 0;

const users =
Object.entries(
data.users
);

users.forEach(
([userId, user]) => {

  const works =
    user.works || [];


  works.forEach(
    (work, index) => {

      totalWorks++;


      lines.push(
        `${totalWorks}. ${work.name}`
      );

      lines.push(
        `🏷️ النوع: ${work.type}`
      );

      lines.push(
        `🔢 آخر فصل: ${work.last_chapter}`
      );

      lines.push(
        `🔗 الرابط: ${work.url}`
      );

      lines.push(
        `👤 المستخدم: ${userId}`
      );

      lines.push("");
    }
  );
}

);

if (!totalWorks) {

lines.push(
  "📭 لا توجد أعمال مراقبة حاليًا."
);

}

await sendMessage(
chatId,

lines.join("\n"),

{
  inline_keyboard: [

    [
      {
        text:
          "🔙 لوحة المبرمج",

        callback_data:
          "admin_panel"
      }
    ]
  ]
},

env

);
}

// =========================
// Broadcast Maintenance
// =========================

async function broadcastMaintenance(
data,
env
) {

const users =
Object.keys(
data.users
);

for (
const userId of users
) {

try {

  await sendMessage(
    userId,

    maintenanceMessage(),

    null,

    env
  );

} catch (error) {

  console.error(
    "Maintenance broadcast error:",
    userId,
    error
  );
}

}
}

// =========================
// Toggle Maintenance
// =========================

async function setMaintenance(
chatId,
enabled,
data,
sha,
env
) {

if (
String(chatId) !==
ADMIN_CHAT_ID
) {
return;
}

data.settings.maintenance =
enabled;

await saveData(
data,
sha,
env
);

if (enabled) {

await broadcastMaintenance(
  data,
  env
);

}

await sendMessage(
chatId,

enabled

  ? "🔧 تم تشغيل وضع الصيانة.\n\n" +
    "📢 تم إرسال إشعار للمستخدمين."

  : "🟢 تم إيقاف وضع الصيانة.\n\n" +
    "✅ البوت عاد للعمل.",

adminKeyboard(
  enabled
),

env

);
}

// =========================
// Callback
// =========================

async function handleCallback(
update,
env
) {

const query =
update.callback_query;

const chatId =
query.message.chat.id;

const userId =
String(chatId);

await answerCallback(
query.id,
env
);

const {
data,
sha
} =
await loadData(env);

ensureUser(
data,
userId
);

const user =
data.users[userId];

// =========================
// Admin protection
// =========================

const isAdmin =
userId ===
ADMIN_CHAT_ID;

// تحديث النشاط
const shouldSaveActivity =
updateActivity(user);

// =========================
// Maintenance protection
// =========================

if (
data.settings.maintenance &&
!isAdmin
) {

await sendMessage(
  chatId,

  maintenanceMessage(),

  null,

  env
);

return;

}

// =========================
// Admin callbacks
// =========================

if (
query.data ===
"admin_panel"
) {

if (!isAdmin) {
  return;
}


await adminPanel(
  chatId,
  data,
  env
);

return;

}

if (
query.data ===
"admin_stats"
) {

if (!isAdmin) {
  return;
}


await adminStats(
  chatId,
  data,
  env
);

return;

}

if (
query.data ===
"admin_works"
) {

if (!isAdmin) {
  return;
}


await adminWorks(
  chatId,
  data,
  env
);

return;

}

if (
query.data ===
"admin_maintenance_on"
) {

if (!isAdmin) {
  return;
}


await setMaintenance(
  chatId,
  true,
  data,
  sha,
  env
);

return;

}

if (
query.data ===
"admin_maintenance_off"
) {

if (!isAdmin) {
  return;
}


await setMaintenance(
  chatId,
  false,
  data,
  sha,
  env
);

return;

}

// =========================
// Remove menu
// =========================

if (
query.data ===
"remove_menu"
) {

await removeMenu(
  chatId,
  data,
  env
);

return;

}

// =========================
// Remove cancel
// =========================

if (
query.data ===
"remove_cancel"
) {

await sendMessage(
  chatId,

  "❌ تم إلغاء عملية الإزالة.",

  null,

  env
);

return;

}

// =========================
// Remove selection
// =========================

if (
query.data.startsWith(
"remove_"
) &&
!query.data.startsWith(
"remove_cancel"
)
) {

const index =
  Number(
    query.data.replace(
      "remove_",
      ""
    )
  );


if (
  Number.isInteger(index)
) {

  await confirmRemove(
    chatId,
    index,
    data,
    env
  );
}

return;

}

// =========================
// Confirm removal
// =========================

if (
query.data.startsWith(
"confirm_remove_"
)
) {

const index =
  Number(
    query.data.replace(
      "confirm_remove_",
      ""
    )
  );


if (
  Number.isInteger(index)
) {

  await performRemove(
    chatId,
    index,
    data,
    sha,
    env
  );
}

return;

}

// =========================
// Type selection
// =========================

if (
!query.data.startsWith(
"type_"
)
) {

return;

}

const workType =
query.data.replace(
"type_",
""
);

data.users[userId].state = {

step:
  "waiting_name",

type:
  workType

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

async function handleMessage(
message,
env
) {

const chatId =
message.chat.id;

const userId =
String(chatId);

const isAdmin =
userId ===
ADMIN_CHAT_ID;

const text =
(message.text || "").trim();

// =========================
// Load Data
// =========================

const {
data,
sha
} =
await loadData(env);

const isNewUser =
ensureUser(
data,
userId
);

const user =
data.users[userId];

// =========================
// Activity
// =========================

const shouldSaveActivity =
updateActivity(
user
);

// =========================
// Admin
// =========================

if (
text === "/admin"
) {

if (!isAdmin) {

  await sendMessage(
    chatId,

    "❌ هذا الأمر غير متاح.",

    null,

    env
  );

  return;
}


await adminPanel(
  chatId,
  data,
  env
);

return;

}

// =========================
// Maintenance
// =========================

if (
data.settings.maintenance &&
!isAdmin
) {

await sendMessage(
  chatId,

  maintenanceMessage(),

  null,

  env
);

return;

}

// =========================
// Save new user/activity
// =========================

if (
isNewUser ||
shouldSaveActivity
) {

await saveData(
  data,
  sha,
  env
);

}

// =========================
// /start
// =========================

if (
text === "/start"
) {

await startCommand(
  chatId,
  data,
  env
);

return;

}

// =========================
// /add
// =========================

if (
text === "/add"
) {

await addCommand(
  chatId,
  data,
  sha,
  env
);

return;

}

// =========================
// /list
// =========================

if (
text === "/list"
) {

await listCommand(
  chatId,
  data,
  env
);

return;

}

const state =
user.state;

if (!state) {
return;
}

// =========================
// Name
// =========================

if (
state.step ===
"waiting_name"
) {

state.name =
  text;


state.step =
  "waiting_url";


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

if (
state.step ===
"waiting_url"
) {

if (
  !/^https?:\/\//i.test(text)
) {

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

  const response =
    await fetch(
      text,
      {

        method:
          "GET",

        headers: {

          "User-Agent":
            "Mozilla/5.0"
        }
      }
    );


  if (!response.ok) {

    throw new Error(
      "URL unavailable"
    );
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


state.url =
  text;


state.step =
  "waiting_chapter";


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

if (
state.step ===
"waiting_chapter"
) {

const chapter =
  Number(
    text.replace(
      /[^\d.]/g,
      ""
    )
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

  type:
    state.type,

  name:
    state.name,

  url:
    state.url,

  last_chapter:
    chapter
});


user.state =
  null;


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