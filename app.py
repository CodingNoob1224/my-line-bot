import re
import sqlite3
import datetime
import threading
from flask import Flask, request, abort
from apscheduler.schedulers.background import BackgroundScheduler
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, PushMessageRequest
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

# Line Bot 配置
configuration = Configuration(access_token='Altz4l3gumCVYc3aN1kAIpX5S8Cb4r+A/tw5ULmWRjqtuQbBM6OR543opMo+9RGRZIOPFSSacl48Xlq0IQjJLQuTzJix6Tyg3ZbAlc+6RT0vdXgiKTRYRnzkAMDTmJsbWp8cj9XIx4W/Ki8Z4jIpZAdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('c8051dced141334dc1942068805a6d50')

# 創建 SQLite 資料庫
conn = sqlite3.connect("todo_list.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS todos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        task TEXT,
        task_time TEXT,
        remind_time TEXT
    )
""")
conn.commit()

# 定時任務
scheduler = BackgroundScheduler()


def send_reminders():
    """ 每分鐘檢查有沒有需要提醒的事項 """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("SELECT user_id, task FROM todos WHERE remind_time = ?", (now,))
    reminders = cursor.fetchall()

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        for user_id, task in reminders:
            line_bot_api.push_message(
                PushMessageRequest(to=user_id, messages=[TextMessage(text=f"提醒：{task}")])
            )
    
    # 刪除已提醒的事項
    cursor.execute("DELETE FROM todos WHERE remind_time = ?", (now,))
    conn.commit()


def send_daily_summary():
    """ 每天晚上 9 點發送明天的代辦事項 """
    tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    cursor.execute("SELECT user_id, task, task_time FROM todos WHERE task_time LIKE ?", (f"{tomorrow}%",))
    tasks = cursor.fetchall()

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        summary = {}
        for user_id, task, task_time in tasks:
            summary.setdefault(user_id, []).append(f"{task_time}: {task}")

        for user_id, tasks in summary.items():
            message = "明天的代辦事項：\n" + "\n".join(tasks)
            line_bot_api.push_message(
                PushMessageRequest(to=user_id, messages=[TextMessage(text=message)])
            )


# 設定定時任務
scheduler.add_job(send_reminders, 'interval', minutes=1)  # 每分鐘檢查提醒
scheduler.add_job(send_daily_summary, 'cron', hour=21, minute=0)  # 每天 21:00 發送明日代辦事項
scheduler.start()


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)

    return 'OK'


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()

    # 判斷是否符合格式（日期+時間+內容，或有提醒時間）
    match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (.+?)(?: 提醒 (\d{4}-\d{2}-\d{2} \d{2}:\d{2}))?", text)
    if match:
        task_time, task, remind_time = match.groups()

        # 存入資料庫
        cursor.execute("INSERT INTO todos (user_id, task, task_time, remind_time) VALUES (?, ?, ?, ?)", 
                       (user_id, task, task_time, remind_time))
        conn.commit()

        # 回覆訊息
        reply_text = f"✅ 已加入代辦清單：{task_time} {task}"
        if remind_time:
            reply_text += f"\n⏰ 提醒時間：{remind_time}"

    elif text == "明天有什麼":
        # 查詢明天代辦事項
        tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        cursor.execute("SELECT task, task_time FROM todos WHERE user_id=? AND task_time LIKE ?", 
                       (user_id, f"{tomorrow}%"))
        tasks = cursor.fetchall()
        if tasks:
            reply_text = "📋 明天的代辦事項：\n" + "\n".join([f"{t[1]}: {t[0]}" for t in tasks])
        else:
            reply_text = "✅ 明天沒有代辦事項！"

    elif text == "刪除所有":
        # 清空用戶的待辦事項
        cursor.execute("DELETE FROM todos WHERE user_id=?", (user_id,))
        conn.commit()
        reply_text = "🗑️ 已清空所有代辦事項！"

    else:
        reply_text = "請輸入格式：\nYYYY-MM-DD HH:MM 代辦事項 [提醒 YYYY-MM-DD HH:MM]"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)])
        )


if __name__ == "__main__":
    threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": 8080}).start()
