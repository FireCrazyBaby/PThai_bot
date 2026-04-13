import logging
import os
import re
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import google.generativeai as genai
import edge_tts
from dotenv import load_dotenv

# 1. 加载环境变量
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not GEMINI_API_KEY or not TG_BOT_TOKEN:
    raise ValueError("找不到 API Key 或 Token，请检查 .env 文件是否配置正确！")

genai.configure(api_key=GEMINI_API_KEY)

# 初始化 Gemini 大脑
model = genai.GenerativeModel(
    model_name="gemini-3-flash-preview",
    system_instruction="""
你是我专属的泰语老师 Ajarn（อาจารย์），专门针对我的当前水平和学习目标来教学。

【我的当前水平】
- 泰语字母大致能认，但看整句话还是读不懂
- 掌握了一些非常基础的词汇和句子
- 听力和口语是我目前最强的部分
- 认单词和写泰文是我的弱项
- 日常缺少真实对话练习的机会

【我的学习目标】
- 近期：能自然回应泰国朋友，达到实习水平
- 长期：看懂路牌、广告，能用泰文写作

【你的教学原则】
1. 语言：中文交流，泰语附带罗马音和中文释义
2. 优先输入：贴近泰国朋友日常聊天的语气
3. 以口语带动书写：先听读，后认写
4. 循序渐进：每次聚焦 1-2 个点
5. 及时纠错：温和指出并给出示范
6. 鼓励为主：多给正向反馈

【每次互动的风格】
- 轻松对话，像朋友一样
- 如果我问词句，给出：① 泰文 ② 罗马音 ③ 中文意思 ④ 真实场景例句
- 每次出一道小练习（情景对话、看图说话等）

【⚠️ 关键：语音跟读输出要求 ⚠️】
为了方便我练习听力和跟读，请在你每次回复的最后，单独起一行，把你希望我听或跟读的纯泰语部分放在方括号里，格式必须严格如下：
[AUDIO: 这里写纯泰文]
注意：这个括号里千万不要出现任何中文、标点符号或罗马音，只要一句或几句纯泰文即可。

开始时，请用轻松的方式自我介绍，问我今天想练习什么，或者给一个简单的日常对话场景热身。
"""
)

user_chats = {}

def get_or_create_chat(user_id):
    if user_id not in user_chats:
        user_chats[user_id] = model.start_chat(history=[])
    return user_chats[user_id]

async def send_thai_audio(update: Update, text: str):
    match = re.search(r'\[AUDIO:\s*(.*?)\s*\]', text)
    if match:
        thai_text = match.group(1)
        if thai_text.strip():
            try:
                print(f"正在生成自然泰语语音: {thai_text}")
                audio_file = f"tts_{update.message.from_user.id}.mp3"
                communicate = edge_tts.Communicate(thai_text, "th-TH-PremwadeeNeural")
                await communicate.save(audio_file)
                with open(audio_file, 'rb') as audio:
                    await update.message.reply_voice(voice=audio)
                os.remove(audio_file)
            except Exception as e:
                print(f"生成语音失败: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    user_id = update.message.from_user.id
    print(f"收到文字消息: {user_text}")
    try:
        chat = get_or_create_chat(user_id)
        response = chat.send_message(user_text)
        await update.message.reply_text(response.text)
        await send_thai_audio(update, response.text)
    except Exception as e:
        await update.message.reply_text("哎呀，Ajarn 的脑子卡住了，稍等一下哦...")
        print(f"文字处理报错: {e}")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    status_msg = await update.message.reply_text("Ajarn 正在听你的发音，请稍等... 🎧")
    voice_file = await update.message.voice.get_file()
    temp_ogg = f"temp_{user_id}.ogg"
    await voice_file.download_to_drive(temp_ogg)
    try:
        uploaded_file = genai.upload_file(path=temp_ogg, mime_type="audio/ogg")
        prompt = "这是我的泰语发音或提问，请先识别我说了什么，然后根据你的教学大纲给出指导。记得在结尾用 [AUDIO: xxx] 给出正确的泰文发音示范。"
        chat = get_or_create_chat(user_id)
        response = chat.send_message([uploaded_file, prompt])
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_msg.message_id)
        await update.message.reply_text(response.text)
        await send_thai_audio(update, response.text)
    except Exception as e:
        print(f"语音处理报错: {e}")
        await update.message.reply_text("哎呀，老师刚才没听清，能再说一遍吗？")
    finally:
        if os.path.exists(temp_ogg):
            os.remove(temp_ogg)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    status_msg = await update.message.reply_text("Ajarn 正在看你发来的图片... 👀")
    photo_file = await update.message.photo[-1].get_file()
    temp_jpg = f"temp_img_{user_id}.jpg"
    await photo_file.download_to_drive(temp_jpg)
    try:
        uploaded_file = genai.upload_file(path=temp_jpg)
        caption = update.message.caption or "请看这张图片，用泰语教我图片里的物品怎么说，或者相关的实用对话。"
        chat = get_or_create_chat(user_id)
        response = chat.send_message([uploaded_file, caption])
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_msg.message_id)
        await update.message.reply_text(response.text)
        await send_thai_audio(update, response.text)
    except Exception as e:
        print(f"图片处理报错: {e}")
        await update.message.reply_text("哎呀，这张图太模糊了，老师看不清~")
    finally:
        if os.path.exists(temp_jpg):
            os.remove(temp_jpg)


# ==========================================
# 👻 新增的黑魔法：幽灵网页服务器 👻
# ==========================================
class GhostServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"<h1>PThai Bot is alive!</h1>")

def run_ghost_server():
    # Render 会自动分配一个 PORT 环境变量，骗过它的关键就在这里
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), GhostServer)
    print(f"\n👻 幽灵服务器启动成功！正在监听端口 {port}...")
    server.serve_forever()
# ==========================================


if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.WARNING)
    
    # 在主程序运行前，单独开一个线程跑幽灵服务器
    threading.Thread(target=run_ghost_server, daemon=True).start()
    
    app = ApplicationBuilder().token(TG_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    print("🚀 PThai 老师 4.0 (支持白嫖 Render Free 节点版) 已上线！")
    app.run_polling()