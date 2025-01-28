import os
import sys
import random
import requests

from flask import Flask, request, abort

from linebot.v3 import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent, UserSource
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    TextMessage,
    ReplyMessageRequest
)
from linebot.v3.exceptions import InvalidSignatureError

# 例: Azure OpenAI を使う場合のimport
from openai import AzureOpenAI

app = Flask(__name__)

channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
channel_secret = os.getenv("LINE_CHANNEL_SECRET")

if not channel_access_token or not channel_secret:
    print("Specify LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET as environment variable.")
    sys.exit(1)

azure_openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
azure_openai_api_key = os.getenv("AZURE_OPENAI_API_KEY")
azure_openai_api_version = os.getenv("AZURE_OPENAI_API_VERSION")
azure_openai_model = os.getenv("AZURE_OPENAI_MODEL")

if not (azure_openai_endpoint and azure_openai_api_key and azure_openai_api_version and azure_openai_model):
    raise Exception(
        "Please set the environment variables for AzureOpenAI "
        "(AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_API_VERSION, AZURE_OPENAI_MODEL)."
    )

hotpepper_api_key = os.getenv("HOTPEPPER_API_KEY")
if not hotpepper_api_key:
    raise Exception("Please set the environment variable HOTPEPPER_API_KEY for HotPepper Gourmet API usage.")

# ============================
# 2) 初期化
# ============================
handler = WebhookHandler(channel_secret)
configuration = Configuration(access_token=channel_access_token)

# Azure OpenAI クライアント
ai = AzureOpenAI(
    azure_endpoint=azure_openai_endpoint,
    api_key=azure_openai_api_key,
    api_version=azure_openai_api_version
)

# チャット履歴
chat_history = []

def init_chat_history():
    """おうどんBotのキャラ設定をsystemロールに付与し、履歴をリセット"""
    chat_history.clear()
    system_role = {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": (
                    "あなたは、香川県に50年住む陽気なおじさんです。丁寧な讃岐弁と絵文字を多用します。"
                    "香川、讃岐うどんについて詳しく、うどんの魅力を語ることが大好き。"
                    "お店紹介も可能で、ホットペッパーAPIの情報を参照して香川県内のうどん屋を紹介します。"
                    "ユーザーの質問には優しく、うどんに絡めて答えてあげてください。"
                ),
            },
        ],
    }
    chat_history.append(system_role)

def fetch_random_udon_shop_in_kagawa():

    endpoint = "http://webservice.recruit.co.jp/hotpepper/gourmet/v1/?key=d8f2e16d5bb67360&large_area=Z082"
    params = {
        "keyword": "うどん",
        "format": "json",
        "count": 500
    }

    resp = requests.get(endpoint, params=params)
    if resp.status_code != 200:
        return None

    data = resp.json()
    if "results" not in data or "shop" not in data["results"]:
        return None

    shops = data["results"]["shop"]
    if not shops:
        return None

    # ▼ チェーン店だけ除外するフィルタ ▼
    chain_blacklist = ["こがね製麺", "はなまるうどん"]
    filtered = []
    for s in shops:
        name = s.get("name", "")
        # チェーン店なら除外
        if any(chain in name for chain in chain_blacklist):
            continue
        filtered.append(s)

    # もしフィルタ後に0件になった場合、フィルタなしで検索結果からランダム
    if not filtered:
        filtered = shops

    return random.choice(filtered) if filtered else None

def get_ai_response(from_user, text):

    hotpepper_summary = ""

    if "おすすめ" in text:
        shop = fetch_random_udon_shop_in_kagawa()
        if shop:
            shop_name = shop.get("name", "店名不明")
            # 住所は不要、表示せずに省略
            hotpepper_summary = (
                f"香川県内の『おすすめのうどん屋』をランダムで1軒紹介しますよ。\n"
                f"店名: {shop_name}\n"
                "(チェーン店でない or うどんと明記してあるお店を頑張って選んだつもりです。)"
            )
        else:
            hotpepper_summary = (
                "香川県内でうどん屋が見つからなかったみたい…ごめんね。\n"
                "もしかするとフィルタで除外されすぎたかもしれません。"
            )

    user_message_content = text
    if hotpepper_summary:
        user_message_content += "\n\n【HotPepperでランダム検索】\n" + hotpepper_summary

    user_msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": user_message_content},
        ],
    }
    chat_history.append(user_msg)

    parameters = {
        "model": azure_openai_model,
        "max_tokens": 150,
        "temperature": 0.7,
        "frequency_penalty": 0,
        "presence_penalty": 0,
        "stop": ["\n"],
        "stream": False,
    }

    # AIから返信を取得
    ai_response = ai.chat.completions.create(messages=chat_history, **parameters)
    res_text = ai_response.choices[0].message.content

    ai_msg = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": res_text},
        ],
    }
    chat_history.append(ai_msg)

    return res_text

def generate_response(from_user, text):


    if text in ["リセット", "初期化", "クリア", "reset", "clear"]:
        init_chat_history()
        return [TextMessage(text="チャット履歴リセットしたで。またうどんの話ようけしよな！")]

    res_text = get_ai_response(from_user, text)
    return [TextMessage(text=res_text)]

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError as e:
        abort(400, e)

    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    text = event.message.text

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        if isinstance(event.source, UserSource):
            profile = line_bot_api.get_profile(event.source.user_id)

            if len(chat_history) == 0:
                init_chat_history()

            response_messages = generate_response(profile.display_name, text)
        else:
            response_messages = [
                TextMessage(text="ユーザー情報が取得できなかったよ。"),
                TextMessage(text=f"メッセージ：{text}")
            ]

        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=response_messages
            )
        )

if __name__ == "__main__":
    init_chat_history()
    app.run(host="0.0.0.0", port=8000, debug=True)

