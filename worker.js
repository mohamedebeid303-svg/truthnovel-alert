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

      return new Response("OK", {
        status: 200
      });

    } catch (error) {

      console.error(
        "WORKER ERROR:",
        error?.stack || error?.message || error
      );

      // إرسال الخطأ إلى Telegram للمساعدة في التشخيص
      if (update?.message?.chat?.id) {

        const chatId = update.message.chat.id;

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

      return new Response("OK", {
        status: 200
      });
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
    `https://api.github.com/repos/${GITHUB_REPO}/contents/${DATA_FILE}`;

  console.log("GitHub GET:", url);

  const response = await fetch(
    url,
    {
      method: "GET",

      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "TruthNovel-Bot"
      },

      cache: "no-store"
    }
  );

  console.log(
    "GitHub response status:",
    response.status
  );

  if (!response.ok) {

    const errorText = await response.text();

    console.error(
      "GitHub API ERROR:",
      response.status,
      errorText
    );

    throw new Error(
      `GitHub API error ${response.status}: ${errorText.slice(0, 500)}`
    );
  }

  const result = await response.json();

  if (!result.content) {
    throw new Error(
      "GitHub returned no file content for data.json"
    );
  }

  let content;

  try {

    const cleanBase64 =
      result.content.replace(/\s/g, "");

    const binary = Uint8Array.from(
      atob(cleanBase64),
      c => c.charCodeAt(0)
    );

    content =
      new TextDecoder().decode(binary);

  } catch (error) {

    throw new Error(
      "Failed to decode data.json from GitHub: " +
      error.message
    );
  }

  let data;

  try {

    data = JSON.parse(content);

  } catch (error) {

    throw new Error(
      "data.json contains invalid JSON: " +
      error.message
    );
  }

  data.users ??= {};

  return {
    data,
    sha: result.sha
  };
}


async function saveData(data, sha, env) {

  if (!env.GITHUB_TOKEN) {
    throw new Error(
      "G