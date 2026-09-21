const GITHUB_REPO = "mohamedebeid303-svg/truthnovel-alert";
const DATA_FILE = "data.json";

const ADMIN_CHAT_ID = "805162451";

const ACTIVE_TIME = 15 * 60 * 1000;


// ======================================================
// MAIN WORKER
// ======================================================

export default {

  async fetch(request, env) {

    try {

      if (request.method !== "POST") {

        return new Response(
          "Sandrone Bot is running.",
          {
            status: 200
          }
        );
      }

      const update = await request.json();

      await ensureBotCommands(env);

      if (update.callback_query) {

        await handleCallback(
          update,
          env
        );

        return new Response("OK");
      }

      if (update.message) {

        await handleMessage(
          update.message,
          env
        );

        return new Response("OK");
      }

      return new Response("OK");

    } catch (error) {

      console.error(
        "SANDRONE WORKER ERROR:",
        error
      );

      return new Response("OK");
    }
  }
};


// ======================================================
// TELEGRAM COMMANDS
// ======================================================

async function ensureBotCommands(env) {

  try {

    const commands = [

      {
        command: "start",
        description: "بدء استخدام البوت"
      },

      {
        command: "add",
        description: "إضافة عمل للمراقبة"
      },

      {
        command: "list",
        description: "عرض أعمالك"
      }
    ];

    await fetch(
      `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/setMyCommands`,
      {
        method: "POST",

        headers: {
          "Content-Type":
            "application/json"
        },

        body: JSON.stringify({

          commands: commands,

          scope: {
            type: "default"
          }
        })
      }
    );

    await fetch(
      `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/setMyCommands`,
      {
        method: "POST",

        headers: {
          "Content-Type":
            "application/json"
        },

        body: JSON.stringify({

          commands: [

            ...commands,

            {
              command: "admin",
              description: "لوحة تحكم المبرمج"
            }

          ],

          scope: {
            type: "chat",

            chat_id:
              Number(ADMIN_CHAT_ID)
          }
        })
      }
    );

  } catch (error) {

    console.error(
      "COMMAND SETUP ERROR:",
      error
    );
  }
}


// ======================================================
// GITHUB - LOAD DATA
// ======================================================

async function loadData(env) {

  const url =
    `https://api.github.com/repos/${GITHUB_REPO}/contents/${DATA_FILE}`;

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
            "Sandrone-Bot"
        }
      }
    );

  if (!response.ok) {

    throw new Error(
      `GitHub LOAD ERROR: ${response.status} ${await response.text()}`
    );
  }

  const result =
    await response.json();

  const decoded =
    atob(
      result.content.replace(/\n/g, "")
    );

  const text =
    new TextDecoder().decode(
      Uint8Array.from(
        decoded,
        c => c.charCodeAt(0)
      )
    );

  let data;

  try {

    data =
      JSON.parse(text);

  } catch {

    data = {};
  }

  if (!data.users) {
    data.users = {};
  }

  if (!data.settings) {

    data.settings = {
      maintenance: false
    };
  }

  if (
    typeof data.settings.maintenance !==
    "boolean"
  ) {

    data.settings.maintenance = false;
  }

  return {
    data,
    sha: result.sha
  };
}


// ======================================================
// GITHUB - SAVE DATA
// ======================================================

async function saveData(
  env,
  data,
  sha
) {

  const url =
    `https://api.github.com/repos/${GITHUB_REPO}/contents/${DATA_FILE}`;

  const json =
    JSON.stringify(
      data,
      null,
      2
    );

  const encoded =
    btoa(
      unescape(
        encodeURIComponent(json)
      )
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

          "Content-Type":
            "application/json",

          "X-GitHub-Api-Version":
            "2022-11-28",

          "User-Agent":
            "Sandrone-Bot"
        },

        body: JSON.stringify({

          message:
            "Update Sandrone bot data",

          content:
            encoded,

          sha:
            sha
        })
      }
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
      `GitHub SAVE ERROR: ${response.status}`
    );
  }

  return await response.json();
}


// ======================================================
// DELETE PREVIOUS BOT MESSAGE
// ======================================================

async function deletePreviousBotMessage(
  env,
  chatId,
  data
) {

  // لا نحذف أي رسالة تلقائيًا من حساب المبرمج
  if (
    String(chatId) ===
    String(ADMIN_CHAT_ID)
  ) {

    return;
  }

  const user =
    data.users &&
    data.users[String(chatId)];

  if (!user) {
    return;
  }

  const messageId =
    user.last_bot_message_id;

  if (!messageId) {
    return;
  }

  try {

    const url =
      `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/deleteMessage`;

    const response =
      await fetch(
        url,
        {
          method: "POST",

          headers: {
            "Content-Type":
              "application/json"
          },

          body:
            JSON.stringify({

              chat_id:
                chatId,

              message_id:
                messageId
            })
        }
      );

    if (!response.ok) {

      console.error(
        "DELETE MESSAGE ERROR:",
        await response.text()
      );
    }

  } catch (error) {

    console.error(
      "DELETE MESSAGE EXCEPTION:",
      error
    );
  }

  user.last_bot_message_id =
    null;
}


// ======================================================
// TELEGRAM SEND MESSAGE
// ======================================================

async function sendMessage(
  env,
  chatId,
  text,
  keyboard = null,
  data = null,
  saveMessage = true
) {

  // حذف الرسالة السابقة للمستخدم العادي
  if (
    data &&
    String(chatId) !==
    String(ADMIN_CHAT_ID)
  ) {

    await deletePreviousBotMessage(
      env,
      chatId,
      data
    );
  }

  const url =
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`;

  const body = {

    chat_id:
      chatId,

    text:
      text,

    parse_mode:
      "HTML"
  };

  if (keyboard) {

    body.reply_markup = {
      inline_keyboard:
        keyboard
    };
  }

  const response =
    await fetch(
      url,
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

    return null;
  }

  let result;

  try {

    result =
      await response.json();

  } catch {

    return null;
  }

  // حفظ رقم آخر رسالة أرسلها البوت للمستخدم العادي
  if (
    saveMessage &&
    data &&
    result.ok &&
    result.result &&
    result.result.message_id &&
    String(chatId) !==
      String(ADMIN_CHAT_ID)
  ) {

    if (!data.users[String(chatId)]) {

      data.users[String(chatId)] = {

        works: [],

        state: null,

        first_seen:
          Date.now(),

        last_active:
          Date.now()
      };
    }

    data.users[String(chatId)]
      .last_bot_message_id =
        result.result.message_id;
  }

  return result;
}


// ======================================================
// CALLBACK ANSWER
// ======================================================

async function answerCallback(
  env,
  callbackId,
  text = ""
) {

  const url =
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/answerCallbackQuery`;

  await fetch(
    url,
    {
      method: "POST",

      headers: {
        "Content-Type":
          "application/json"
      },

      body:
        JSON.stringify({

          callback_query_id:
            callbackId,

          text:
            text,

          show_alert:
            false
        })
    }
  );
}


// ======================================================
// USER MANAGEMENT
// ======================================================

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
        Date.now(),

      last_bot_message_id:
        null
    };

    return true;
  }

  // دعم المستخدمين الموجودين مسبقًا في data.json
  if (
    !Object.prototype.hasOwnProperty.call(
      data.users[userId],
      "last_bot_message_id"
    )
  ) {

    data.users[userId]
      .last_bot_message_id =
        null;
  }

  return false;
}


function updateActivity(
  data,
  userId
) {

  if (!data.users[userId]) {
    ensureUser(data, userId);
  }

  data.users[userId].last_active =
    Date.now();
}


// ======================================================
// ADMIN CHECK
// ======================================================

function isAdmin(chatId) {

  return String(chatId) ===
    String(ADMIN_CHAT_ID);
}


// ======================================================
// MAINTENANCE
// ======================================================

function isMaintenance(data) {

  return Boolean(
    data.settings &&
    data.settings.maintenance
  );
}


// ======================================================
// TYPE NAME
// ======================================================

function getTypeName(type) {

  const types = {

    "رواية":
      "الرواية",

    "مانجا":
      "المانجا",

    "مانهوا":
      "المانهوا",

    "مانها":
      "المانها"
  };

  return types[type] || type;
}


// ======================================================
// MAIN MENU
// ======================================================

function mainKeyboard() {

  return [

    [
      {
        text: "➕ إضافة عمل",
        callback_data: "add"
      }
    ],

    [
      {
        text: "📚 أعمالي",
        callback_data: "list"
      }
    ]

  ];
}


// ======================================================
// WORK TYPE KEYBOARD
// ======================================================

function workTypeKeyboard() {

  return [

    [
      {
        text: "📖 رواية",
        callback_data: "type_novel"
      },

      {
        text: "📚 مانجا",
        callback_data: "type_manga"
      }
    ],

    [
      {
        text: "📘 مانهوا",
        callback_data: "type_manhwa"
      },

      {
        text: "📕 مانها",
        callback_data: "type_manhua"
      }
    ]

  ];
}


// ======================================================
// START
// ======================================================

async function startCommand(
  message,
  env,
  data,
  sha
) {

  const userId =
    String(message.chat.id);

  ensureUser(
    data,
    userId
  );

  updateActivity(
    data,
    userId
  );

  await sendMessage(
    env,
    message.chat.id,

    "👋 <b>مرحبًا، معك Sandrone</b>\n\n" +

    "سأساعدك في مراقبة أعمالك وإعلامك عند صدور فصل جديد.\n\n" +

    "اختر أحد الخيارات من القائمة:",

    mainKeyboard(),

    data
  );

  await saveData(
    env,
    data,
    sha
  );
}


// ======================================================
// ADD WORK
// ======================================================

async function addCommand(
  message,
  env,
  data,
  sha
) {

  const userId =
    String(message.chat.id);

  ensureUser(
    data,
    userId
  );

  data.users[userId].state = {

    step:
      "waiting_type"
  };

  updateActivity(
    data,
    userId
  );

  await sendMessage(
    env,
    message.chat.id,

    "➕ <b>إضافة عمل جديد</b>\n\n" +

    "اختر نوع العمل:",

    workTypeKeyboard(),

    data
  );

  await saveData(
    env,
    data,
    sha
  );
}


// ======================================================
// LIST WORKS
// ======================================================

async function listCommand(
  message,
  env,
  data
) {

  const userId =
    String(message.chat.id);

  const user =
    data.users[userId];

  if (
    !user ||
    !user.works ||
    user.works.length === 0
  ) {

    await sendMessage(
      env,
      message.chat.id,

      "📚 لا توجد أعمال مضافة إلى قائمتك حاليًا.\n\n" +
      "اضغط ➕ إضافة عمل لإضافة أول عمل.",

      null,

      data
    );

    return;
  }

  let text =
    "📚 <b>أعمالك المراقبة:</b>\n\n";

  const keyboard = [];

  user.works.forEach(
    (work, index) => {

      const type =
        work.type || "رواية";

      text +=
        `${index + 1}. <b>${escapeHtml(work.name)}</b>\n` +

        `🏷️ النوع: ${escapeHtml(type)}\n` +

        `🔢 آخر فصل: ${work.last_chapter}\n` +

        `🔗 ${escapeHtml(work.url)}\n\n`;

      keyboard.push([

        {
          text:
            `🗑️ حذف ${work.name}`,

          callback_data:
            `remove_${index}`
        }

      ]);
    }
  );

  await sendMessage(
    env,
    message.chat.id,
    text,
    keyboard,
    data
  );
}


// ======================================================
// CALLBACK HANDLER
// ======================================================

async function handleCallback(
  update,
  env
) {

  const callback =
    update.callback_query;

  const chatId =
    String(
      callback.message.chat.id
    );

  const action =
    callback.data;

  const {
    data,
    sha
  } =
    await loadData(env);

  ensureUser(
    data,
    chatId
  );

  updateActivity(
    data,
    chatId
  );


  // ==========================================
  // ADD
  // ==========================================

  if (action === "add") {

    await answerCallback(
      env,
      callback.id
    );

    data.users[chatId].state = {

      step:
        "waiting_type"
    };

    await sendMessage(
      env,
      chatId,

      "➕ <b>إضافة عمل جديد</b>\n\n" +

      "اختر نوع العمل:",

      workTypeKeyboard(),

      data
    );

    await saveData(
      env,
      data,
      sha
    );

    return;
  }


  // ==========================================
  // WORK TYPE
  // ==========================================

  if (
    action === "type_novel" ||
    action === "type_manga" ||
    action === "type_manhwa" ||
    action === "type_manhua"
  ) {

    const types = {

      type_novel:
        "رواية",

      type_manga:
        "مانجا",

      type_manhwa:
        "مانهوا",

      type_manhua:
        "مانها"
    };

    const selectedType =
      types[action];

    const typeName =
      getTypeName(selectedType);

    data.users[chatId].state = {

      step:
        "waiting_name",

      type:
        selectedType
    };

    await answerCallback(
      env,
      callback.id,
      `تم اختيار: ${selectedType}`
    );

    await sendMessage(
      env,
      chatId,

      `🏷️ <b>نوع العمل:</b> ${escapeHtml(selectedType)}\n\n` +

      `✏️ الآن أرسل اسم ${escapeHtml(typeName)}.`,

      null,

      data
    );

    await saveData(
      env,
      data,
      sha
    );

    return;
  }


  // ==========================================
  // LIST
  // ==========================================

  if (action === "list") {

    await answerCallback(
      env,
      callback.id
    );

    await listCommand(
      callback.message,
      env,
      data
    );

    return;
  }


  // ==========================================
  // REMOVE
  // ==========================================

  if (
    action.startsWith("remove_")
  ) {

    const index =
      Number(
        action.split("_")[1]
      );

    const user =
      data.users[chatId];

    if (
      !user ||
      !user.works ||
      !user.works[index]
    ) {

      await answerCallback(
        env,
        callback.id,
        "العمل غير موجود."
      );

      return;
    }

    const work =
      user.works[index];

    const type =
      work.type || "رواية";

    const typeName =
      getTypeName(type);

    await answerCallback(
      env,
      callback.id
    );

    await sendMessage(
      env,
      chatId,

      `⚠️ هل أنت متأكد من حذف ${escapeHtml(typeName)}:\n\n` +

      `<b>${escapeHtml(work.name)}</b>\n\n` +

      `سيتم إزالته من قائمتك فقط.`,

      [

        [
          {
            text: "✅ نعم، احذف",
            callback_data:
              `confirm_remove_${index}`
          },

          {
            text: "❌ إلغاء",
            callback_data:
              "cancel_remove"
          }
        ]

      ],

      data
    );

    return;
  }


  // ==========================================
  // CONFIRM REMOVE
  // ==========================================

  if (
    action.startsWith("confirm_remove_")
  ) {

    const index =
      Number(
        action.split("_")[2]
      );

    const user =
      data.users[chatId];

    if (
      !user ||
      !user.works ||
      !user.works[index]
    ) {

      await answerCallback(
        env,
        callback.id,
        "العمل غير موجود."
      );

      return;
    }

    const removed =
      user.works.splice(
        index,
        1
      )[0];

    const type =
      removed.type || "رواية";

    const typeName =
      getTypeName(type);

    await answerCallback(
      env,
      callback.id,
      "تم الحذف."
    );

    await sendMessage(
      env,
      chatId,

      `🗑️ تم حذف ${escapeHtml(typeName)} ` +
      `<b>${escapeHtml(removed.name)}</b> من قائمتك.`,

      null,

      data
    );

    await saveData(
      env,
      data,
      sha
    );

    return;
  }


  // ==========================================
  // CANCEL REMOVE
  // ==========================================

  if (
    action === "cancel_remove"
  ) {

    await answerCallback(
      env,
      callback.id,
      "تم الإلغاء."
    );

    await sendMessage(
      env,
      chatId,
      "❌ تم إلغاء عملية الحذف.",
      null,
      data
    );

    return;
  }


  // ==========================================
  // ADMIN PANEL
  // ==========================================

  if (
    action === "admin_panel"
  ) {

    if (!isAdmin(chatId)) {

      await answerCallback(
        env,
        callback.id,
        "غير مصرح."
      );

      return;
    }

    await answerCallback(
      env,
      callback.id
    );

    await adminPanel(
      chatId,
      env,
      data
    );

    return;
  }


  // ==========================================
  // MAINTENANCE
  // ==========================================

  if (
    action === "maintenance_on" ||
    action === "maintenance_off"
  ) {

    if (!isAdmin(chatId)) {

      await answerCallback(
        env,
        callback.id,
        "غير مصرح."
      );

      return;
    }

    data.settings.maintenance =
      action === "maintenance_on";

    await answerCallback(
      env,
      callback.id
    );

    if (
      data.settings.maintenance
    ) {

      await broadcastMaintenance(
        env,
        data
      );

      await sendMessage(
        env,
        chatId,

        "🔴 <b>تم تفعيل وضع الصيانة.</b>\n\n" +
        "المستخدمون العاديون لن يتمكنوا من استخدام البوت حتى إيقاف الصيانة."
      );

    } else {

      await sendMessage(
        env,
        chatId,

        "🟢 <b>تم إيقاف وضع الصيانة.</b>\n\n" +
        "عاد البوت للعمل للمستخدمين."
      );
    }

    await saveData(
      env,
      data,
      sha
    );

    return;
  }


  // ==========================================
  // ADMIN WORKS
  // ==========================================

  if (
    action === "admin_works"
  ) {

    if (!isAdmin(chatId)) {

      await answerCallback(
        env,
        callback.id,
        "غير مصرح."
      );

      return;
    }

    await answerCallback(
      env,
      callback.id
    );

    await showAllWorks(
      chatId,
      env,
      data
    );

    return;
  }
}


// ======================================================
// MESSAGE HANDLER
// ======================================================

async function handleMessage(
  message,
  env
) {

  const chatId =
    String(message.chat.id);

  const text =
    (message.text || "").trim();

  const {
    data,
    sha
  } =
    await loadData(env);

  const isAdminUser =
    isAdmin(chatId);

  ensureUser(
    data,
    chatId
  );


  // ==========================================
  // START
  // ==========================================

  if (
    text === "/start"
  ) {

    await startCommand(
      message,
      env,
      data,
      sha
    );

    return;
  }


  // ==========================================
  // ADMIN
  // ==========================================

  if (
    text === "/admin"
  ) {

    if (!isAdminUser) {

      await sendMessage(
        env,
        chatId,
        "⛔ هذا الأمر متاح للمبرمج فقط."
      );

      return;
    }

    await adminPanel(
      chatId,
      env,
      data
    );

    return;
  }


  // ==========================================
  // MAINTENANCE
  // ==========================================

  if (
    isMaintenance(data) &&
    !isAdminUser
  ) {

    await sendMessage(
      env,
      chatId,

      "🔧 <b>البوت في وضع الصيانة حاليًا.</b>\n\n" +
      "يرجى المحاولة مرة أخرى لاحقًا.",

      null,

      data
    );

    return;
  }


  // ==========================================
  // ACTIVITY
  // ==========================================

  const now =
    Date.now();

  const previousActivity =
    data.users[chatId].last_active || 0;

  const shouldSaveActivity =
    now - previousActivity >
    ACTIVE_TIME;

  updateActivity(
    data,
    chatId
  );


  // ==========================================
  // ADD
  // ==========================================

  if (
    text === "/add"
  ) {

    await addCommand(
      message,
      env,
      data,
      sha
    );

    return;
  }


  // ==========================================
  // LIST
  // ==========================================

  if (
    text === "/list"
  ) {

    await listCommand(
      message,
      env,
      data
    );

    if (shouldSaveActivity) {

      await saveData(
        env,
        data,
        sha
      );
    }

    return;
  }


  // ==========================================
  // USER STATE
  // ==========================================

  const user =
    data.users[chatId];

  if (
    !user.state
  ) {

    await sendMessage(
      env,
      chatId,

      "اختر أمرًا من القائمة 👇",

      mainKeyboard(),

      data
    );

    if (shouldSaveActivity) {

      await saveData(
        env,
        data,
        sha
      );
    }

    return;
  }


  // ==========================================
  // WAITING FOR TYPE
  // ==========================================

  if (
    user.state.step ===
    "waiting_type"
  ) {

    await sendMessage(
      env,
      chatId,

      "🏷️ <b>اختر نوع العمل أولًا:</b>",

      workTypeKeyboard(),

      data
    );

    return;
  }


  // ==========================================
  // WAITING FOR NAME
  // ==========================================

  if (
    user.state.step ===
    "waiting_name"
  ) {

    const type =
      user.state.type;

    const typeName =
      getTypeName(type);

    if (!text) {

      await sendMessage(
        env,
        chatId,

        `❌ أرسل اسم ${escapeHtml(typeName)}.`,

        null,

        data
      );

      return;
    }

    user.state = {

      step:
        "waiting_url",

      type:
        type,

      name:
        text
    };

    await sendMessage(
      env,
      chatId,

      `🔗 الآن أرسل رابط صفحة ${escapeHtml(typeName)}.`,

      null,

      data
    );

    await saveData(
      env,
      data,
      sha
    );

    return;
  }


  // ==========================================
  // WAITING FOR URL
  // ==========================================

  if (
    user.state.step ===
    "waiting_url"
  ) {

    const url =
      text;

    const type =
      user.state.type;

    const typeName =
      getTypeName(type);

    if (
      !/^https?:\/\/\S+$/i.test(url)
    ) {

      await sendMessage(
        env,
        chatId,

        `❌ الرابط غير صحيح.\n\n` +

        `أرسل رابط صفحة ${escapeHtml(typeName)} يبدأ بـ <b>http://</b> أو <b>https://</b>.`,

        null,

        data
      );

      return;
    }

    try {

      const response =
        await fetch(
          url,
          {
            method: "GET"
          }
        );

      if (
        !response.ok
      ) {

        await sendMessage(
          env,
          chatId,

          `⚠️ تمكنت من الوصول إلى الرابط لكن الموقع أعاد حالة غير طبيعية.\n\n` +

          `إذا كنت متأكدًا من الرابط، أرسل رابط صفحة ${escapeHtml(typeName)} مرة أخرى.`,

          null,

          data
        );

        return;
      }

    } catch {

      await sendMessage(
        env,
        chatId,

        `⚠️ لم أتمكن من الوصول إلى رابط ${escapeHtml(typeName)}.\n\n` +

        "تأكد من أن الرابط صحيح ويمكن فتحه.",

        null,

        data
      );

      return;
    }

    user.state = {

      step:
        "waiting_chapter",

      type:
        type,

      name:
        user.state.name,

      url:
        url
    };

    await sendMessage(
      env,
      chatId,

      `🔢 ممتاز.\n\n` +

      `أرسل رقم آخر فصل صدر من ${escapeHtml(typeName)} حاليًا.\n\n` +

      "مثال: <b>125</b>",

      null,

      data
    );

    await saveData(
      env,
      data,
      sha
    );

    return;
  }


  // ==========================================
  // WAITING FOR CHAPTER
  // ==========================================

  if (
    user.state.step ===
    "waiting_chapter"
  ) {

    const type =
      user.state.type;

    const typeName =
      getTypeName(type);

    const normalizedText =
      normalizeDigits(text);

    const chapter =
      Number(normalizedText);

    if (
      !Number.isInteger(chapter) ||
      chapter < 0
    ) {

      await sendMessage(
        env,
        chatId,

        `❌ أرسل رقم فصل صحيح لـ ${escapeHtml(typeName)}.\n\n` +

        "مثال: <b>125</b>",

        null,

        data
      );

      return;
    }

    user.works.push({

      type:
        type,

      name:
        user.state.name,

      url:
        user.state.url,

      last_chapter:
        chapter
    });

    const workName =
      user.state.name;

    user.state =
      null;

    await sendMessage(
      env,
      chatId,

      `✅ <b>تمت إضافة ${escapeHtml(typeName)} بنجاح!</b>\n\n` +

      `📖 ${escapeHtml(workName)}\n` +

      `🔢 آخر فصل: ${chapter}\n\n` +

      `سيحتفظ Sandrone بهذه ${escapeHtml(typeName)} ضمن قائمتك.`,

      null,

      data
    );

    await saveData(
      env,
      data,
      sha
    );

    return;
  }


  // ==========================================
  // SAVE ACTIVITY
  // ==========================================

  if (
    shouldSaveActivity
  ) {

    await saveData(
      env,
      data,
      sha
    );
  }
}


// ======================================================
// NORMALIZE ARABIC DIGITS
// ======================================================

function normalizeDigits(
  text
) {

  return String(text)

    .replace(
      /[٠-٩]/g,
      digit =>
        String(
          "٠١٢٣٤٥٦٧٨٩"
            .indexOf(digit)
        )
    )

    .replace(
      /[۰-۹]/g,
      digit =>
        String(
          "۰۱۲۳۴۵۶۷۸۹"
            .indexOf(digit)
        )
    );
}


// ======================================================
// ADMIN PANEL
// ======================================================

async function adminPanel(
  chatId,
  env,
  data
) {

  if (!isAdmin(chatId)) {

    return;
  }

  const users =
    Object.keys(
      data.users || {}
    );

  const now =
    Date.now();

  let activeUsers =
    0;

  let totalWorks =
    0;

  users.forEach(
    userId => {

      const user =
        data.users[userId];

      if (!user) {
        return;
      }

      if (
        now -
        (user.last_active || 0)
        <=
        ACTIVE_TIME
      ) {

        activeUsers++;
      }

      totalWorks +=
        Array.isArray(user.works)
          ? user.works.length
          : 0;
    }
  );

  const maintenance =
    isMaintenance(data);

  const text =

    "🛠️ <b>لوحة تحكم Sandrone</b>\n\n" +

    `👥 إجمالي المستخدمين: <b>${users.length}</b>\n` +

    `🟢 المستخدمون النشطون: <b>${activeUsers}</b>\n` +

    `📚 إجمالي الأعمال المراقبة: <b>${totalWorks}</b>\n\n` +

    `🔧 وضع الصيانة: <b>${maintenance ? "🔴 مفعّل" : "🟢 متوقف"}</b>`;

  // هذه رسالة إدارية، لذلك لا نمرر data
  // وبالتالي لا تدخل في نظام الحذف

  await sendMessage(
    env,
    chatId,
    text,

    [

      [
        {
          text: "📚 جميع الأعمال",
          callback_data:
            "admin_works"
        }
      ],

      [

        {
          text:
            maintenance
              ? "🟢 إيقاف الصيانة"
              : "🔴 تفعيل الصيانة",

          callback_data:
            maintenance
              ? "maintenance_off"
              : "maintenance_on"
        }

      ]

    ]
  );
}


// ======================================================
// SHOW ALL WORKS
// ======================================================

async function showAllWorks(
  chatId,
  env,
  data
) {

  let text =
    "📚 <b>جميع الأعمال المراقبة</b>\n\n";

  let total =
    0;

  for (
    const userId of
    Object.keys(data.users || {})
  ) {

    const user =
      data.users[userId];

    if (
      !user ||
      !Array.isArray(user.works)
    ) {
      continue;
    }

    for (
      const work of user.works
    ) {

      total++;

      const type =
        work.type || "رواية";

      text +=

        `👤 <b>المستخدم:</b> ${escapeHtml(userId)}\n` +

        `📖 <b>الاسم:</b> ${escapeHtml(work.name)}\n` +

        `🏷️ <b>النوع:</b> ${escapeHtml(type)}\n` +

        `🔢 <b>آخر فصل:</b> ${work.last_chapter}\n` +

        `🔗 <b>الرابط:</b> ${escapeHtml(work.url)}\n\n` +

        "━━━━━━━━━━━━━━\n\n";
    }
  }

  if (total === 0) {

    text +=
      "لا توجد أعمال مراقبة حاليًا.";

  } else {

    text +=
      `📊 <b>الإجمالي: ${total}</b>`;
  }

  // رسالة إدارية، لا تدخل في الحذف
  await sendMessage(
    env,
    chatId,
    text
  );
}


// ======================================================
// MAINTENANCE BROADCAST
// ======================================================

async function broadcastMaintenance(
  env,
  data
) {

  const message =

    "🔧 <b>تنبيه صيانة Sandrone</b>\n\n" +

    "تم تفعيل وضع الصيانة في Sandrone.\n\n" +

    "لن تتمكن من استخدام البوت مؤقتًا حتى انتهاء الصيانة.";

  for (
    const userId of
    Object.keys(data.users || {})
  ) {

    if (
      String(userId) ===
      String(ADMIN_CHAT_ID)
    ) {
      continue;
    }

    try {

      // رسالة نظامية وليست جزءًا من تنقل المستخدم
      // لذلك لا نحذف الرسالة السابقة ولا نسجلها
      await sendMessage(
        env,
        userId,
        message
      );

    } catch (error) {

      console.error(
        "Broadcast error:",
        userId,
        error
      );
    }
  }
}


// ======================================================
// ESCAPE HTML
// ======================================================

function escapeHtml(
  text
) {

  return String(text)

    .replace(
      /&/g,
      "&amp;"
    )

    .replace(
      /</g,
      "&lt;"
    )

    .replace(
      />/g,
      "&gt;"
    )

    .replace(
      /"/g,
      "&quot;"
    );
}