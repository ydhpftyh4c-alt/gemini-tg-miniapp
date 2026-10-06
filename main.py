import os
import sys
import base64
import logging
import asyncio
from io import BytesIO
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    MenuButtonWebApp,
    BufferedInputFile,
    FSInputFile
)

import db
import generators

load_dotenv()

# --- Config ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8080")
DEFAULT_MODEL = "gemini-3.8-flash"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gemini_app")

SUPPORTED_MODELS = {
    "gemini-3.8-flash": "⚡ Gemini 3.8 Flash (Быстрый)",
    "gemini-3.8-flash-lite": "🚀 Gemini Flash Lite (Ультра-скорость)",
    "gemini-2.5-pro": "🧠 Gemini 2.5 Pro (Глубокий анализ)"
}

# --- Gemini API Client ---
async def call_gemini_api(
    user_id: int,
    user_message: str = "",
    model_name: str = DEFAULT_MODEL,
    system_prompt: str = "",
    inline_media: Optional[Dict[str, str]] = None
) -> str:
    if model_name not in SUPPORTED_MODELS:
        model_name = DEFAULT_MODEL

    history = await db.get_history(user_id=user_id, limit=20)
    
    current_parts = []
    if user_message:
        current_parts.append({"text": user_message})
    if inline_media:
        current_parts.append({
            "inline_data": {
                "mime_type": inline_media["mime_type"],
                "data": inline_media["data"]
            }
        })
        
    contents = list(history)
    contents.append({
        "role": "user",
        "parts": current_parts
    })
    
    payload: Dict[str, Any] = {"contents": contents}
    if system_prompt:
        payload["system_instruction"] = {
            "parts": [{"text": system_prompt}]
        }
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
    
    async with httpx.AsyncClient(timeout=40.0) as client:
        resp = await client.post(url, json=payload)
        data = resp.json()
        
        if resp.status_code != 200:
            error_msg = data.get("error", {}).get("message", f"HTTP {resp.status_code}")
            logger.error(f"Gemini API error ({model_name}): {error_msg}")
            
            if model_name != "gemini-3.8-flash":
                logger.info("Falling back to gemini-3.8-flash...")
                fallback_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={GEMINI_API_KEY}"
                fallback_resp = await client.post(fallback_url, json=payload)
                if fallback_resp.status_code == 200:
                    data = fallback_resp.json()
                else:
                    raise Exception(error_msg)
            else:
                raise Exception(error_msg)
                
        candidates = data.get("candidates", [])
        if not candidates:
            return "Не удалось получить ответ от модели."
            
        answer_text = ""
        parts = candidates[0].get("content", {}).get("parts", [])
        for p in parts:
            if "text" in p:
                answer_text += p["text"]
                
        prompt_summary = user_message if user_message else "[Медиафайл]"
        await db.add_message(user_id=user_id, role="user", content=prompt_summary)
        await db.add_message(user_id=user_id, role="model", content=answer_text)
        
        return answer_text

# --- Telegram Bot ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def get_webapp_keyboard():
    target_url = WEBAPP_URL if WEBAPP_URL.startswith("http") else f"https://{WEBAPP_URL}"
    if not target_url.endswith("/app"):
        target_url = f"{target_url.rstrip('/')}/app"
        
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✦ Открыть Gemini Mini App",
                    web_app=WebAppInfo(url=target_url)
                )
            ]
        ]
    )

@dp.message(CommandStart())
async def handle_start(message: types.Message):
    await message.answer(
        "👋 **Добро пожаловать в Gemini AI SuperBot!**\n\n"
        "✨ **Доступные команды:**\n"
        "• 📱 Кнопка **«Открыть»** — запуск Mini App чата\n"
        "• 🎨 `/image <запрос>` — генерация изображений (Flux / Nano)\n"
        "• 📄 `/docx <тема>` — создание Word отчёта (.docx)\n"
        "• 📑 `/pdf <тема>` — создание документа в PDF (.pdf)\n"
        "• 📊 `/pptx <тема>` — генерация PowerPoint презентации (.pptx)\n"
        "• 📸 Отправка фото — анализ и распознавание\n"
        "• 🎤 Голосовые — транскрибация и ответы голосом\n"
        "• 🧹 `/clear` — очистка истории",
        parse_mode="Markdown",
        reply_markup=get_webapp_keyboard()
    )

@dp.message(Command("clear"))
async def handle_clear(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    await db.clear_history(user_id)
    await message.answer("🧹 История диалога успешно очищена!")

# 1. Image Generation Handler
@dp.message(Command("image", "img"))
async def handle_image_cmd(message: types.Message):
    prompt = message.text.split(maxsplit=1)
    if len(prompt) < 2 or not prompt[1].strip():
        await message.answer("💡 Напишите запрос после команды, например:\n`/image неоновый киберпанк город с летающими машинами`", parse_mode="Markdown")
        return
        
    user_prompt = prompt[1].strip()
    status_msg = await message.answer("🎨 Генерирую изображение по вашему запросу...")
    
    try:
        img_bytes, enhanced_prompt = await generators.generate_image_bytes(user_prompt, call_gemini_api)
        input_file = BufferedInputFile(img_bytes, filename="generated.jpg")
        await message.answer_photo(
            photo=input_file,
            caption=f"✨ **Запрос:** {user_prompt}\n🔍 *Промт:* `{enhanced_prompt[:150]}...`",
            parse_mode="Markdown"
        )
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка генерации фото: {e}")

# 2. DOCX Word Report Handler
@dp.message(Command("docx"))
async def handle_docx_cmd(message: types.Message):
    topic = message.text.split(maxsplit=1)
    if len(topic) < 2 or not topic[1].strip():
        await message.answer("💡 Укажите тему для Word документа, например:\n`/docx Бизнес-план для кофейни`", parse_mode="Markdown")
        return
        
    user_topic = topic[1].strip()
    status_msg = await message.answer("📄 Составляю и верстаю Word документ (.docx)...")
    
    try:
        file_path = await generators.generate_docx_file(user_topic, call_gemini_api)
        input_file = FSInputFile(file_path, filename=f"{user_topic[:30]}.docx")
        await message.answer_document(
            document=input_file,
            caption=f"✅ Готово! Ваш документ Word на тему:\n**{user_topic}**",
            parse_mode="Markdown"
        )
        await status_msg.delete()
    except Exception as e:
        logger.error(f"DOCX error: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка при создании DOCX: {e}")

# 3. PDF Document Handler
@dp.message(Command("pdf"))
async def handle_pdf_cmd(message: types.Message):
    topic = message.text.split(maxsplit=1)
    if len(topic) < 2 or not topic[1].strip():
        await message.answer("💡 Укажите тему для PDF, например:\n`/pdf Анализ рынка криптовалют`", parse_mode="Markdown")
        return
        
    user_topic = topic[1].strip()
    status_msg = await message.answer("📑 Генерирую PDF документ...")
    
    try:
        file_path = await generators.generate_pdf_file(user_topic, call_gemini_api)
        input_file = FSInputFile(file_path, filename=f"{user_topic[:30]}.pdf")
        await message.answer_document(
            document=input_file,
            caption=f"✅ Готово! Ваш PDF документ на тему:\n**{user_topic}**",
            parse_mode="Markdown"
        )
        await status_msg.delete()
    except Exception as e:
        logger.error(f"PDF error: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка при создании PDF: {e}")

# 4. PPTX PowerPoint Presentation Handler
@dp.message(Command("pptx"))
async def handle_pptx_cmd(message: types.Message):
    topic = message.text.split(maxsplit=1)
    if len(topic) < 2 or not topic[1].strip():
        await message.answer("💡 Укажите тему презентации, например:\n`/pptx Введение в машинное обучение`", parse_mode="Markdown")
        return
        
    user_topic = topic[1].strip()
    status_msg = await message.answer("📊 Генерирую слайды презентации PowerPoint (.pptx)...")
    
    try:
        file_path = await generators.generate_pptx_file(user_topic, call_gemini_api)
        input_file = FSInputFile(file_path, filename=f"{user_topic[:30]}.pptx")
        await message.answer_document(
            document=input_file,
            caption=f"✅ Готово! Презентация PowerPoint на тему:\n**{user_topic}**",
            parse_mode="Markdown"
        )
        await status_msg.delete()
    except Exception as e:
        logger.error(f"PPTX error: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка при создании презентации: {e}")

# Photo & Voice handlers
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    caption = message.caption or "Что изображено на этом фото? Опиши подробно."
    
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    status_msg = await message.answer("🔍 Анализирую изображение...")
    
    try:
        photo = message.photo[-1]
        buffer = BytesIO()
        await message.bot.download(photo.file_id, destination=buffer)
        b64_image = base64.b64encode(buffer.getvalue()).decode("utf-8")
        
        reply = await call_gemini_api(
            user_id=user_id,
            user_message=caption,
            model_name="gemini-3.8-flash",
            inline_media={"mime_type": "image/jpeg", "data": b64_image}
        )
        await status_msg.edit_text(reply)
    except Exception as e:
        logger.error(f"Photo analysis error: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка при анализе фото: {e}")

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    status_msg = await message.answer("🎧 Слушаю аудиосообщение...")
    
    try:
        voice = message.voice
        buffer = BytesIO()
        await message.bot.download(voice.file_id, destination=buffer)
        b64_voice = base64.b64encode(buffer.getvalue()).decode("utf-8")
        
        reply = await call_gemini_api(
            user_id=user_id,
            user_message="Внимательно послушай это аудиосообщение и дай подробный и полезный ответ:",
            model_name="gemini-3.8-flash",
            inline_media={"mime_type": "audio/ogg", "data": b64_voice}
        )
        await status_msg.edit_text(reply)
    except Exception as e:
        logger.error(f"Voice analysis error: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка при обработке голоса: {e}")

@dp.message(F.text)
async def handle_direct_message(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    user_text = message.text or ""
    
    # Check if user asked to draw something in plain text
    lower_text = user_text.lower().strip()
    if lower_text.startswith("нарисуй ") or lower_text.startswith("сгенерируй фото ") or lower_text.startswith("картинка "):
        clean_p = re.sub(r"^(нарисуй|сгенерируй фото|картинка)\s*", "", user_text, flags=re.IGNORECASE)
        status_msg = await message.answer("🎨 Рисую изображение...")
        try:
            img_bytes, _ = await generators.generate_image_bytes(clean_p, call_gemini_api)
            input_file = BufferedInputFile(img_bytes, filename="art.jpg")
            await message.answer_photo(photo=input_file, caption=f"✨ **Результат:** {clean_p}")
            await status_msg.delete()
            return
        except Exception as e:
            await status_msg.edit_text(f"⚠️ Ошибка рисования: {e}")
            return
            
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    try:
        reply = await call_gemini_api(
            user_id=user_id,
            user_message=user_text,
            model_name="gemini-3.8-flash"
        )
        await message.answer(reply)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")

async def start_bot_polling():
    target_url = WEBAPP_URL if WEBAPP_URL.startswith("http") else f"https://{WEBAPP_URL}"
    if not target_url.endswith("/app"):
        target_url = f"{target_url.rstrip('/')}/app"
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Открыть",
                web_app=WebAppInfo(url=target_url)
            )
        )
        logger.info(f"Menu button set: {target_url}")
    except Exception as e:
        logger.warning(f"Could not update menu button: {e}")
        
    logger.info("Starting Telegram bot polling...")
    await dp.start_polling(bot)

# --- FastAPI WebApp ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    bot_task = asyncio.create_task(start_bot_polling())
    yield
    bot_task.cancel()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

class WebChatRequest(BaseModel):
    message: str
    user_id: int = 0
    model: str = DEFAULT_MODEL
    system_prompt: str = ""

@app.post("/api/chat")
async def api_chat(req: WebChatRequest):
    try:
        reply = await call_gemini_api(
            user_id=req.user_id,
            user_message=req.message,
            model_name=req.model,
            system_prompt=req.system_prompt
        )
        return JSONResponse({"reply": reply, "model": req.model})
    except Exception as e:
        logger.error(f"Chat API error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/clear")
async def api_clear(req: WebChatRequest):
    await db.clear_history(req.user_id)
    return JSONResponse({"status": "cleared"})

@app.get("/app")
async def get_app():
    return FileResponse("static/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False)
