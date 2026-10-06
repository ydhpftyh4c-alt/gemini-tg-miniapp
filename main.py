import os
import sys
import uuid
import base64
import logging
import asyncio
from io import BytesIO
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

import httpx
import pypdf
import docx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, Form
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
# Support Render.com auto-assigned external URL or .env
WEBAPP_URL = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("WEBAPP_URL", "http://localhost:8080")
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
PORT = int(os.getenv("PORT", "8080"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gemini_app")

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

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
    inline_media: Optional[Dict[str, str]] = None,
    save_to_db: bool = True,
    msg_type: str = "chat",
    media_url: str = ""
) -> str:
    if model_name not in SUPPORTED_MODELS:
        model_name = DEFAULT_MODEL

    history = await db.get_history_for_gemini(user_id=user_id, limit=20)
    
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
                
        if save_to_db and user_id != 0:
            prompt_summary = user_message if user_message else "[Файл]"
            await db.add_message(user_id=user_id, role="user", content=prompt_summary, msg_type="chat")
            await db.add_message(user_id=user_id, role="model", content=answer_text, msg_type=msg_type, media_url=media_url)
        
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
        "✨ **Возможности:**\n"
        "• 📱 Кнопка **«Открыть»** слева — Mini App с историей, файлами и выбором моделей\n"
        "• 🎨 *«Нарисуй спорткар»* ➔ создам изображение\n"
        "• 📊 *«Сделай презентацию про космос»* ➔ сгенерирую PowerPoint (.pptx)\n"
        "• 📄 *«Отчёт в ворде по AI»* ➔ соберу Word (.docx)\n"
        "• 📑 *«Сделай PDF документ»* ➔ сверстаю PDF (.pdf)\n"
        "• 📎 Присылайте фото, документы или голосовые — я разберусь!",
        parse_mode="Markdown",
        reply_markup=get_webapp_keyboard()
    )

@dp.message(Command("clear"))
async def handle_clear(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    await db.clear_history(user_id)
    await message.answer("🧹 История диалога успешно очищена!")

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

@dp.message(F.document)
async def handle_document(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    doc_info = message.document
    caption = message.caption or "Проанализируй этот документ и сделай краткое резюме ключевых моментов:"
    
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    status_msg = await message.answer(f"📄 Читаю документ «{doc_info.file_name}»...")
    
    try:
        buffer = BytesIO()
        await message.bot.download(doc_info.file_id, destination=buffer)
        raw_bytes = buffer.getvalue()
        
        # Extract text
        extracted_text = extract_text_from_file(doc_info.file_name, raw_bytes)
        combined_prompt = f"Пользователь прикрепил файл '{doc_info.file_name}':\n\n--- СОДЕРЖИМОЕ ФАЙЛА ---\n{extracted_text[:12000]}\n--- КОНЕЦ ФАЙЛА ---\n\nЗапрос: {caption}"
        
        reply = await call_gemini_api(
            user_id=user_id,
            user_message=combined_prompt,
            model_name="gemini-3.8-flash"
        )
        await status_msg.edit_text(reply)
    except Exception as e:
        logger.error(f"Doc error: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка обработки документа: {e}")

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

@dp.message(F.text)
async def handle_direct_message(message: types.Message):
    user_id = message.from_user.id if message.from_user else 0
    user_text = message.text or ""
    
    intent = await generators.detect_intent(user_text, call_gemini_api)
    intent_type = intent.get("type", "chat")
    topic = intent.get("topic", user_text)
    
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

# --- Document Text Extractor ---
def extract_text_from_file(filename: str, content: bytes) -> str:
    ext = filename.lower().split(".")[-1]
    if ext == "pdf":
        try:
            reader = pypdf.PdfReader(BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages[:20]]
            return "\n".join(pages)
        except Exception as e:
            return f"[Ошибка чтения PDF: {e}]"
    elif ext == "docx":
        try:
            d = docx.Document(BytesIO(content))
            return "\n".join([p.text for p in d.paragraphs if p.text])
        except Exception as e:
            return f"[Ошибка чтения DOCX: {e}]"
    else:
        for enc in ["utf-8", "cp1251", "latin-1"]:
            try:
                return content.decode(enc)
            except Exception:
                pass
        return "[Текстовый контент]"

# --- FastAPI WebApp ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    bot_task = asyncio.create_task(start_bot_polling())
    yield
    bot_task.cancel()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

class AttachedFileInfo(BaseModel):
    filename: str
    file_type: str  # "image" or "doc"
    mime_type: str
    b64_data: str = ""
    extracted_text: str = ""
    preview_url: str = ""

class WebChatRequest(BaseModel):
    message: str
    user_id: int = 0
    model: str = DEFAULT_MODEL
    system_prompt: str = ""
    attached_file: Optional[AttachedFileInfo] = None

# 1. File Upload API for Mini App
@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    try:
        content = await file.read()
        filename = file.filename or "file"
        content_type = file.content_type or "application/octet-stream"
        
        is_image = content_type.startswith("image/") or filename.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif'))
        
        if is_image:
            b64_data = base64.b64encode(content).decode("utf-8")
            unique_name = f"upload_{uuid.uuid4().hex[:8]}_{filename}"
            filepath = os.path.join(UPLOADS_DIR, unique_name)
            with open(filepath, "wb") as f:
                f.write(content)
            return JSONResponse({
                "filename": filename,
                "file_type": "image",
                "mime_type": content_type if content_type.startswith("image/") else "image/jpeg",
                "b64_data": b64_data,
                "preview_url": f"/static/uploads/{unique_name}",
                "extracted_text": ""
            })
        else:
            extracted_text = extract_text_from_file(filename, content)
            return JSONResponse({
                "filename": filename,
                "file_type": "doc",
                "mime_type": content_type,
                "b64_data": "",
                "preview_url": "",
                "extracted_text": extracted_text[:15000]
            })
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# 2. History API for Mini App
@app.get("/api/history")
async def api_history(user_id: int = 0):
    try:
        history = await db.get_ui_history(user_id=user_id, limit=50)
        return JSONResponse({"history": history})
    except Exception as e:
        logger.error(f"History error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# 3. Chat API with attached files and intent detection
@app.post("/api/chat")
async def api_chat(req: WebChatRequest):
    try:
        # Check if an image is attached
        if req.attached_file and req.attached_file.file_type == "image":
            prompt = req.message or "Что изображено на этой картинке? Опиши подробно."
            reply = await call_gemini_api(
                user_id=req.user_id,
                user_message=prompt,
                model_name=req.model,
                inline_media={"mime_type": req.attached_file.mime_type, "data": req.attached_file.b64_data}
            )
            return JSONResponse({"reply": reply, "type": "chat", "model": req.model})

        # Check if a document is attached
        if req.attached_file and req.attached_file.file_type == "doc":
            prompt_text = req.message or "Проанализируй данный документ и выдели главное:"
            combined = (
                f"Пользователь прикрепил документ '{req.attached_file.filename}':\n\n"
                f"--- СОДЕРЖИМОЕ ДОКУМЕНТА ---\n{req.attached_file.extracted_text}\n--- КОНЕЦ ДОКУМЕНТА ---\n\n"
                f"Инструкция: {prompt_text}"
            )
            reply = await call_gemini_api(
                user_id=req.user_id,
                user_message=combined,
                model_name=req.model
            )
            return JSONResponse({"reply": reply, "type": "chat", "model": req.model})

        # Intent detection
        intent = await generators.detect_intent(req.message, call_gemini_api)
        intent_type = intent.get("type", "chat")
        topic = intent.get("topic", req.message)
        
        if intent_type == "image":
            res = await generators.generate_image(topic, call_gemini_api)
            msg_reply = f"✨ Сгенерировано изображение по запросу: «{topic}»"
            if req.user_id != 0:
                await db.add_message(req.user_id, "user", req.message)
                await db.add_message(req.user_id, "model", msg_reply, msg_type="image", media_url=res["url"])
            return JSONResponse({
                "reply": msg_reply,
                "type": "image",
                "media_url": res["url"],
                "filename": res["filename"],
                "model": req.model
            })
            
        if intent_type == "pptx":
            res = await generators.generate_pptx(topic, call_gemini_api)
            msg_reply = f"📊 Презентация PowerPoint на тему «{topic}» успешно создана!"
            if req.user_id != 0:
                await db.add_message(req.user_id, "user", req.message)
                await db.add_message(req.user_id, "model", msg_reply, msg_type="file", media_url=res["url"])
            return JSONResponse({
                "reply": msg_reply,
                "type": "file",
                "file_type": "pptx",
                "media_url": res["url"],
                "filename": res["filename"],
                "model": req.model
            })
            
        if intent_type == "docx":
            res = await generators.generate_docx(topic, call_gemini_api)
            msg_reply = f"📄 Документ Microsoft Word на тему «{topic}» готов!"
            if req.user_id != 0:
                await db.add_message(req.user_id, "user", req.message)
                await db.add_message(req.user_id, "model", msg_reply, msg_type="file", media_url=res["url"])
            return JSONResponse({
                "reply": msg_reply,
                "type": "file",
                "file_type": "docx",
                "media_url": res["url"],
                "filename": res["filename"],
                "model": req.model
            })
            
        if intent_type == "pdf":
            res = await generators.generate_pdf(topic, call_gemini_api)
            msg_reply = f"📑 PDF документ на тему «{topic}» готов!"
            if req.user_id != 0:
                await db.add_message(req.user_id, "user", req.message)
                await db.add_message(req.user_id, "model", msg_reply, msg_type="file", media_url=res["url"])
            return JSONResponse({
                "reply": msg_reply,
                "type": "file",
                "file_type": "pdf",
                "media_url": res["url"],
                "filename": res["filename"],
                "model": req.model
            })
            
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
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
