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
        "👋 **Добро пожаловать в Gemini AI!**\n\n"
        "✨ **Я понимаю естественную речь без слеш-команд:**\n"
        "• *«Нарисуй киберпанк город»* ➔ сгенерирую фото\n"
        "• *«Сделай презентацию про квантовую физику»* ➔ создам PowerPoint (.pptx)\n"
        "• *«Подготовь отчёт в ворде по рынку AI»* ➔ соберу документ Word (.docx)\n"
        "• *«Сделай PDF документ с резюме»* ➔ сформирую PDF (.pdf)\n"
        "• 📸 Присылайте любые фото для анализа\n"
        "• 🎤 Записывайте голосовые вопросы\n"
        "• 📱 Кнопка **«Открыть»** слева — Mini App с выбором моделей!",
        parse_mode="Markdown",
        reply_markup=get_webapp_keyboard()
    )

@dp.message(Command("clear"))
async def handle_clear(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    await db.clear_history(user_id)
    await message.answer("🧹 История диалога успешно очищена!")

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
    status_msg = await message.answer("🎧 Слушаю голосовое сообщение...")
    
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

# Natural language handler with Intent Detection
@dp.message(F.text)
async def handle_direct_message(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    user_text = message.text or ""
    
    intent = await generators.detect_intent(user_text, call_gemini_api)
    intent_type = intent.get("type", "chat")
    topic = intent.get("topic", user_text)
    
    # 1. Image generation
    if intent_type == "image":
        status_msg = await message.answer("🎨 Генерирую изображение по вашему запросу...")
        try:
            res = await generators.generate_image(topic, call_gemini_api)
            input_file = BufferedInputFile(res["bytes"], filename=res["filename"])
            await message.answer_photo(photo=input_file, caption=f"✨ **Результат:** {topic}")
            await status_msg.delete()
            return
        except Exception as e:
            await status_msg.edit_text(f"⚠️ Ошибка генерации фото: {e}")
            return

    # 2. PowerPoint Presentation
    if intent_type == "pptx":
        status_msg = await message.answer(f"📊 Составляю презентацию PowerPoint на тему: **{topic}**...")
        try:
            res = await generators.generate_pptx(topic, call_gemini_api)
            input_file = FSInputFile(res["filepath"], filename=f"{topic[:30]}.pptx")
            await message.answer_document(document=input_file, caption=f"✅ Готово! Презентация:\n**{topic}**")
            await status_msg.delete()
            return
        except Exception as e:
            await status_msg.edit_text(f"⚠️ Ошибка создания презентации: {e}")
            return

    # 3. Word DOCX Document
    if intent_type == "docx":
        status_msg = await message.answer(f"📄 Формирую Word документ на тему: **{topic}**...")
        try:
            res = await generators.generate_docx(topic, call_gemini_api)
            input_file = FSInputFile(res["filepath"], filename=f"{topic[:30]}.docx")
            await message.answer_document(document=input_file, caption=f"✅ Готово! Word документ:\n**{topic}**")
            await status_msg.delete()
            return
        except Exception as e:
            await status_msg.edit_text(f"⚠️ Ошибка создания DOCX: {e}")
            return

    # 4. PDF Document
    if intent_type == "pdf":
        status_msg = await message.answer(f"📑 Верстаю PDF документ на тему: **{topic}**...")
        try:
            res = await generators.generate_pdf(topic, call_gemini_api)
            input_file = FSInputFile(res["filepath"], filename=f"{topic[:30]}.pdf")
            await message.answer_document(document=input_file, caption=f"✅ Готово! PDF документ:\n**{topic}**")
            await status_msg.delete()
            return
        except Exception as e:
            await status_msg.edit_text(f"⚠️ Ошибка создания PDF: {e}")
            return

    # 5. Normal chat
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
        intent = await generators.detect_intent(req.message, call_gemini_api)
        intent_type = intent.get("type", "chat")
        topic = intent.get("topic", req.message)
        
        # 1. Image
        if intent_type == "image":
            res = await generators.generate_image(topic, call_gemini_api)
            return JSONResponse({
                "reply": f"✨ Сгенерировано изображение по запросу: «{topic}»",
                "type": "image",
                "media_url": res["url"],
                "filename": res["filename"],
                "model": req.model
            })
            
        # 2. PPTX Presentation
        if intent_type == "pptx":
            res = await generators.generate_pptx(topic, call_gemini_api)
            return JSONResponse({
                "reply": f"📊 Презентация PowerPoint на тему «{topic}» успешно создана!",
                "type": "file",
                "file_type": "pptx",
                "media_url": res["url"],
                "filename": res["filename"],
                "model": req.model
            })
            
        # 3. DOCX Word
        if intent_type == "docx":
            res = await generators.generate_docx(topic, call_gemini_api)
            return JSONResponse({
                "reply": f"📄 Документ Microsoft Word на тему «{topic}» готов!",
                "type": "file",
                "file_type": "docx",
                "media_url": res["url"],
                "filename": res["filename"],
                "model": req.model
            })
            
        # 4. PDF
        if intent_type == "pdf":
            res = await generators.generate_pdf(topic, call_gemini_api)
            return JSONResponse({
                "reply": f"📑 PDF документ на тему «{topic}» готов!",
                "type": "file",
                "file_type": "pdf",
                "media_url": res["url"],
                "filename": res["filename"],
                "model": req.model
            })
            
        # 5. Normal Chat
        reply = await call_gemini_api(
            user_id=req.user_id,
            user_message=req.message,
            model_name=req.model,
            system_prompt=req.system_prompt
        )
        return JSONResponse({
            "reply": reply,
            "type": "chat",
            "model": req.model
        })
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
