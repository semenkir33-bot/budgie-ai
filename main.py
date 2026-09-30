import telebot
from telebot import types
from flask import Flask
from threading import Thread
import json
import os
import io
import re
import secrets
import string
from datetime import datetime, timedelta
from openai import OpenAI

# --- НАСТРОЙКИ ---
BOT_TOKEN = '8809529844:AAFXK1UrSb5Oyv5HxykRUlb7I5iKwQ-NyF8'
OWNER_ID = 7650109118

AI_BASE_URL = 'https://ai.starimg.ru/v1'
AI_API_KEY = 'sk-star-95c321119305274cf0c6fd8d2219664bca359494051da7e81949f7864280e457'
DEFAULT_MODEL = 'gemini-3.7-flash'

AVAILABLE_MODELS = [
    'gemini-3.7-flash',
    'deepseek-v4.1-flash',
    'deepseek-v4-pro',
    'claude-haiku-4-5',
    'claude-sonnet-4-6',
    'gpt-5.6-sol',
    'gpt-6-astra',
    'qwen3.8-flash'
]

DB_FILE = 'ai_users.json'

ai_client = OpenAI(
    api_key=AI_API_KEY,
    base_url=AI_BASE_URL
)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask('')

chat_histories = {}

def load_db():
    if not os.path.exists(DB_FILE):
        data = {
            "users": {
                str(OWNER_ID): {
                    "model": DEFAULT_MODEL,
                    "expires_at": "unlimited"
                }
            },
            "tokens": {}
        }
        save_db(data)
        return data
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def is_user_allowed(user_id):
    if user_id == OWNER_ID:
        return True
    db = load_db()
    uid = str(user_id)
    if uid not in db["users"]:
        return False
    exp = db["users"][uid].get("expires_at")
    if exp == "unlimited":
        return True
    try:
        exp_date = datetime.strptime(exp, "%Y-%m-%d %H:%M")
        return datetime.now() < exp_date
    except Exception:
        return False

# --- ВЕБ-СЕРВЕР ДЛЯ РАБОТЫ 24/7 НА ХОСТИНГЕ ---
@app.route('/')
def home():
    return "AI Bot is running"

# --- КОМАНДЫ ДЛЯ ВЛАДЕЛЬЦА ---
@bot.message_handler(commands=['keygen'])
def handle_keygen(message):
    if message.from_user.id != OWNER_ID:
        return
    parts = message.text.split()
    model = parts[1] if len(parts) > 1 else DEFAULT_MODEL
    days = int(parts[2]) if len(parts) > 2 else 30

    token = "PASS-" + ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    
    db = load_db()
    db["tokens"][token] = {
        "model": model,
        "days": days
    }
    save_db(db)

    bot.send_message(
        message.chat.id,
        f"🔑 <b>Одноразовый пароль готов:</b>\n\n"
        f"Код: <code>{token}</code>\n"
        f"Модель: <code>{model}</code>\n"
        f"Срок: {days} дн.\n\n"
        f"<i>Пароль удалится из базы сразу после ввода пользователем.</i>",
        parse_mode='HTML'
    )

@bot.message_handler(commands=['model'])
def handle_choose_model(message):
    if not is_user_allowed(message.from_user.id):
        return
    if message.from_user.id != OWNER_ID:
        db = load_db()
        current = db["users"].get(str(message.from_user.id), {}).get("model", DEFAULT_MODEL)
        bot.send_message(message.chat.id, f"Вам назначена модель: <code>{current}</code>", parse_mode='HTML')
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(text=m, callback_data=f"setmodel_{m}") for m in AVAILABLE_MODELS]
    markup.add(*buttons)
    bot.send_message(message.chat.id, "Выберите активную модель для себя:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('setmodel_'))
def callback_set_model(call):
    if call.from_user.id != OWNER_ID:
        return
    selected_model = call.data.replace('setmodel_', '')
    db = load_db()
    db["users"][str(OWNER_ID)]["model"] = selected_model
    save_db(db)
    bot.edit_message_text(f"✅ Установлена модель: <b>{selected_model}</b>", call.message.chat.id, call.message.message_id, parse_mode='HTML')

@bot.message_handler(commands=['clear'])
def handle_clear(message):
    chat_histories[message.from_user.id] = []
    bot.send_message(message.chat.id, "🧹 Контекст диалога очищен.")

# --- ГЕНЕРАЦИЯ КАРТИНОК ---
@bot.message_handler(commands=['draw', 'img'])
def handle_draw(message):
    if not is_user_allowed(message.from_user.id):
        bot.send_message(message.chat.id, "🔒 Сначала активируйте доступ по паролю.")
        return
    
    prompt = message.text.replace('/draw', '').replace('/img', '').strip()
    if not prompt:
        bot.send_message(message.chat.id, "Пример: <code>/draw футуристичный город ночью</code>", parse_mode='HTML')
        return

    sent_msg = bot.send_message(message.chat.id, "🎨 <i>Генерирую изображение...</i>", parse_mode='HTML')
    try:
        response = ai_client.images.generate(
            prompt=prompt,
            n=1,
            size="1024x1024"
        )
        image_url = response.data[0].url
        bot.send_photo(message.chat.id, image_url, caption=f"🖼 <b>{prompt}</b>", parse_mode='HTML')
        bot.delete_message(message.chat.id, sent_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка генерации: {e}", message.chat.id, sent_msg.message_id)

# --- ДИАЛОГ И АВТОМАТИЧЕСКАЯ ОТПРАВКА КОДА ФАЙЛОМ ---
@bot.message_handler(func=lambda m: True)
def handle_chat_and_auth(message):
    user_id = message.from_user.id
    text = message.text.strip()
    db = load_db()

    # 1. Проверка авторизации
    if not is_user_allowed(user_id):
        if text in db["tokens"]:
            token_data = db["tokens"].pop(text)
            exp_date = datetime.now() + timedelta(days=token_data["days"])
            db["users"][str(user_id)] = {
                "model": token_data["model"],
                "expires_at": exp_date.strftime("%Y-%m-%d %H:%M"),
                "username": message.from_user.username or message.from_user.first_name
            }
            save_db(db)
            bot.send_message(
                message.chat.id,
                f"🎉 <b>Доступ активирован!</b>\n\nМодель: <code>{token_data['model']}</code>\nДействует до: {exp_date.strftime('%d.%m.%Y')}\n\nПишите любой запрос в чат.",
                parse_mode='HTML'
            )
            return
        else:
            bot.send_message(message.chat.id, "🔒 Доступ закрыт. Отправьте одноразовый пароль:")
            return

    # 2. Общение с нейросетью
    user_model = db["users"].get(str(user_id), {}).get("model", DEFAULT_MODEL)
    
    if user_id not in chat_histories:
        chat_histories[user_id] = []
        
    chat_histories[user_id].append({"role": "user", "content": text})
    chat_histories[user_id] = chat_histories[user_id][-6:]

    sent_msg = bot.send_message(message.chat.id, "💭 <i>Думаю...</i>", parse_mode='HTML')
    
    try:
        messages_payload = [{"role": "system", "content": "Ты продвинутый ассистент и программист. Отвечай прямо. Если пишешь код, оформляй его блоками с указанием языка (```python, ```html и т.д.)."}] + chat_histories[user_id]
        
        response = ai_client.chat.completions.create(
            model=user_model,
            messages=messages_payload,
            max_tokens=2000,
            temperature=0.7
        )
        answer = response.choices[0].message.content
        chat_histories[user_id].append({"role": "assistant", "content": answer})
        
        try:
            bot.edit_message_text(answer, message.chat.id, sent_msg.message_id, parse_mode='Markdown')
        except Exception:
            bot.edit_message_text(answer, message.chat.id, sent_msg.message_id)

        # Поиск кода и упаковка в файл
        code_blocks = re.findall(r'```([a-zA-Z0-9_]*)\n(.*?)```', answer, re.DOTALL)
        if code_blocks:
            for idx, (lang, code_text) in enumerate(code_blocks):
                lang = lang.lower().strip() or "txt"
                ext_map = {"python": "py", "py": "py", "javascript": "js", "js": "js", "html": "html", "css": "css", "json": "json", "bash": "sh", "sh": "sh"}
                ext = ext_map.get(lang, "txt")
                
                file_data = io.BytesIO(code_text.strip().encode('utf-8'))
                file_data.name = f"code_{idx + 1}.{ext}" if len(code_blocks) > 1 else f"script.{ext}"
                
                bot.send_document(message.chat.id, file_data, caption=f"📄 Файл с кодом ({lang})")

    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {e}", message.chat.id, sent_msg.message_id)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port)).start()
    bot.infinity_polling()
          
