import os
import uuid
import json
import asyncio
import threading
import queue
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file, render_template, url_for
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import qrcode
from PIL import Image
import io
from werkzeug.utils import secure_filename

# Загружаем переменные окружения из .env файла
load_dotenv()

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# Конфигурация CORS для доступа с GitHub Pages
CORS(app, origins=["https://mikawo846.github.io"])
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

db = SQLAlchemy(app)

# Создаем папку для загрузок
UPLOAD_FOLDER = app.config.get('UPLOAD_FOLDER', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
Path(UPLOAD_FOLDER).mkdir(exist_ok=True)


# Переменные окружения
BOT_TOKEN = os.environ.get('TOKEN')
if not BOT_TOKEN:
    raise ValueError("TOKEN environment variable is not set")

ALLOWED_USER_ID = os.environ.get('USER_ID')
if not ALLOWED_USER_ID:
    raise ValueError("USER_ID environment variable is not set")

ALLOWED_USER_ID = int(ALLOWED_USER_ID)

CHANNEL_ID = os.environ.get('CHANNEL_ID')
if not CHANNEL_ID:
    raise ValueError("CHANNEL_ID environment variable is not set")

CHANNEL_ID = int(CHANNEL_ID)

# Модель БД
class Note(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = db.Column(db.String(500), nullable=False)
    text = db.Column(db.Text, nullable=True)
    photos_json = db.Column(db.Text, nullable=True)  # JSON список путей к фото
    created = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'text': self.text,
            'photos': json.loads(self.photos_json) if self.photos_json else [],
            'created': self.created.isoformat() if self.created else None
        }

# Инициализация БД
with app.app_context():
    db.create_all()

# Создаем отдельный event loop для Telegram операций
_telegram_loop = asyncio.new_event_loop()
_telegram_queue = queue.Queue()

def _run_telegram_loop():
    """Фоновый поток для выполнения async операций Telegram"""
    asyncio.set_event_loop(_telegram_loop)
    loop = asyncio.get_event_loop()
    
    async def process_queue():
        while True:
            try:
                task_func, args, kwargs = _telegram_queue.get()
                if task_func is None:  # Сигнал остановки
                    break
                await task_func(*args, **kwargs)
                _telegram_queue.task_done()
            except Exception as e:
                app.logger.error(f"Error in telegram queue: {e}")
            await asyncio.sleep(0.1)  # Небольшая задержка
    
    loop.run_until_complete(process_queue())

# Запускаем фоновый поток
_telegram_thread = threading.Thread(target=_run_telegram_loop, daemon=True)
_telegram_thread.start()

def send_to_channel_sync(text: str, photo_paths: list):
    """Синхронная обертка для отправки в канал"""
    try:
        _telegram_queue.put((send_to_channel, (text, photo_paths), {}))
    except Exception as e:
        app.logger.error(f"Error queueing send_to_channel: {e}")

# Telegram Bot Application
telegram_app = Application.builder().token(BOT_TOKEN).build()
bot = Bot(token=BOT_TOKEN)

# Хранилище для временных данных пользователей (для загрузки фото)
user_states = {}


def is_authorized(user_id: int) -> bool:
    """Проверка авторизации пользователя"""
    return user_id == ALLOWED_USER_ID


def compress_image(file_source, target_path):
    """Сжатие изображения до max 1600x1600, качество 80% JPEG"""
    try:
        # Определяем тип источника
        if hasattr(file_source, 'filename'):  # Flask FileStorage
            img = Image.open(file_source)
        elif isinstance(file_source, str):  # Путь к файлу
            img = Image.open(file_source)
        else:
            return False
        
        # Конвертируем в RGB если нужно (для JPEG)
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Получаем текущие размеры
        width, height = img.size
        
        # Вычисляем новые размеры с сохранением пропорций
        max_size = 1600
        if width > max_size or height > max_size:
            if width > height:
                new_width = max_size
                new_height = int(height * (max_size / width))
            else:
                new_height = max_size
                new_width = int(width * (max_size / height))
            
            # Сжимаем с высококачественным ресайзом
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Сохраняем как JPEG с качеством 80%
        img.save(target_path, 'JPEG', quality=80, optimize=True)
        
        return True
    except Exception as e:
        app.logger.error(f"Error compressing image: {e}")
        return False


def generate_qr_code(data: str) -> io.BytesIO:
    """Генерация QR-кода в PNG формате"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    return img_io


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    
    welcome_text = "👋 Отправь ссылку Ozon/WB/Avito или заметку с фото"
    await update.message.reply_text(welcome_text)


async def qr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /qr <text/link> или QR-кода заметки"""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Использование: /qr <текст или ссылка>")
        return
    
    text_or_link = ' '.join(context.args)
    
    # Проверяем, это QR-код заметки
    if text_or_link.startswith('qrapp:note:'):
        note_id = text_or_link.replace('qrapp:note:', '')
        note = Note.query.filter_by(id=note_id).first()
        
        if not note:
            await update.message.reply_text("❌ Заметка не найдена")
            return
        
        # Формируем текст заметки
        note_text = f"📝 {note.title}\n\n"
        if note.text:
            note_text += note.text
        
        if note.photos_json:
            photos = json.loads(note.photos_json)
            note_text += f"\n\n📷 Фото: {len(photos)} шт."
        
        note_text += f"\n\n🕐 Создано: {note.created.strftime('%Y-%m-%d %H:%M')}"
        
        await update.message.reply_text(note_text, parse_mode='HTML')
        
        # Отправляем фото если есть
        if note.photos_json:
            photos = json.loads(note.photos_json)
            for photo_path in photos[:3]:  # Максимум 3 фото
                try:
                    with open(photo_path, 'rb') as photo_file:
                        await update.message.reply_photo(photo=photo_file)
                except Exception as e:
                    await update.message.reply_text(f"❌ Ошибка отправки фото: {e}")
        
        return
    
    # Обычный QR-код
    qr_image = generate_qr_code(text_or_link)
    
    await update.message.reply_photo(
        photo=qr_image,
        caption=f"📱 QR-код для: {text_or_link[:50]}..."
    )


async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /note"""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    
    # Получаем все заметки пользователя
    notes = Note.query.filter_by(user_id=user_id).order_by(Note.created.desc()).limit(10).all()
    
    keyboard = []
    keyboard.append([InlineKeyboardButton("➕ Новая заметка", callback_data="note_new")])
    
    if notes:
        for note in notes:
            keyboard.append([
                InlineKeyboardButton(
                    f"📝 {note.title[:30]}...",
                    callback_data=f"note_view_{note.id}"
                )
            ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "📋 Заметки:\n\nВыберите действие:"
    await update.message.reply_text(text, reply_markup=reply_markup)


async def view_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /view <id>"""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Использование: /view <id>")
        return
    
    note_id = context.args[0]
    note = Note.query.filter_by(id=note_id, user_id=user_id).first()
    
    if not note:
        await update.message.reply_text("❌ Заметка не найдена.")
        return
    
    await send_note_message(update, note)


async def send_note_message(update: Update, note: Note, edit_message_id: Optional[int] = None):
    """Отправка заметки пользователю"""
    text = f"📝 <b>{note.title}</b>\n\n"
    if note.text:
        text += f"{note.text}\n\n"
    text += f"🆔 ID: <code>{note.id}</code>"
    
    photos = json.loads(note.photos_json) if note.photos_json else []
    
    if photos:
        # Отправляем первое фото с текстом
        photo_path = photos[0]
        if os.path.exists(photo_path):
            with open(photo_path, 'rb') as photo_file:
                if edit_message_id:
                    await update.callback_query.edit_message_caption(
                        caption=text,
                        parse_mode='HTML'
                    )
                else:
                    await update.message.reply_photo(
                        photo=photo_file,
                        caption=text,
                        parse_mode='HTML'
                    )
        
        # Отправляем остальные фото
        for photo_path in photos[1:]:
            if os.path.exists(photo_path):
                with open(photo_path, 'rb') as photo_file:
                    await update.message.reply_photo(photo=photo_file)
    else:
        if edit_message_id:
            await update.callback_query.edit_message_text(
                text=text,
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(text, parse_mode='HTML')
    
    # Отправляем QR-код
    qr_data = f"qrapp:note:{note.id}"
    qr_image = generate_qr_code(qr_data)
    await update.message.reply_photo(
        photo=qr_image,
        caption=f"📱 QR-код заметки"
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback от inline кнопок"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if not is_authorized(user_id):
        await query.edit_message_text("❌ Доступ запрещен.")
        return
    
    data = query.data
    
    if data == "note_new":
        # Инициализируем состояние для новой заметки
        user_states[user_id] = {
            'mode': 'creating_note',
            'photos': [],
            'title': None,
            'text': None,
            'waiting_for': None  # 'title', 'text', или None
        }
        
        keyboard = [
            [InlineKeyboardButton("📷 Добавить фото (до 5)", callback_data="note_add_photo")],
            [InlineKeyboardButton("✏️ Установить заголовок", callback_data="note_set_title")],
            [InlineKeyboardButton("📄 Установить текст", callback_data="note_set_text")],
            [InlineKeyboardButton("💾 Сохранить", callback_data="note_save")],
            [InlineKeyboardButton("❌ Отмена", callback_data="note_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = "📝 Создание новой заметки:\n\n"
        text += "Фото: 0/5\n"
        text += "Заголовок: не установлен\n"
        text += "Текст: не установлен\n\n"
        text += "Выберите действие:"
        
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    elif data == "note_add_photo":
        if user_id not in user_states:
            await query.edit_message_text("❌ Ошибка: состояние не найдено.")
            return
        
        state = user_states[user_id]
        if len(state['photos']) >= 5:
            await query.answer("❌ Максимум 5 фото!", show_alert=True)
            return
        
        await query.edit_message_text(
            "📷 Отправьте фото (можно несколько, но не более 5 всего)"
        )
    
    elif data == "note_set_title":
        if user_id not in user_states:
            await query.edit_message_text("❌ Ошибка: состояние не найдено.")
            return
        user_states[user_id]['waiting_for'] = 'title'
        await query.edit_message_text("✏️ Отправьте заголовок заметки:")
    
    elif data == "note_set_text":
        if user_id not in user_states:
            await query.edit_message_text("❌ Ошибка: состояние не найдено.")
            return
        user_states[user_id]['waiting_for'] = 'text'
        await query.edit_message_text("📄 Отправьте текст заметки:")
    
    elif data == "note_save":
        if user_id not in user_states:
            await query.edit_message_text("❌ Ошибка: состояние не найдено.")
            return
        
        state = user_states[user_id]
        
        if not state.get('title'):
            await query.answer("❌ Установите заголовок!", show_alert=True)
            return
        
        # Сохраняем заметку в БД
        note = Note(
            id=str(uuid.uuid4()),
            title=state['title'],
            text=state.get('text', ''),
            photos_json=json.dumps(state['photos']),
            user_id=user_id
        )
        
        db.session.add(note)
        db.session.commit()
        
        # Удаляем состояние
        del user_states[user_id]
        
        # Отправляем QR-код
        qr_data = f"qrapp:note:{note.id}"
        qr_image = generate_qr_code(qr_data)
        
        await query.edit_message_text("✅ Заметка сохранена!")
        await query.message.reply_photo(
            photo=qr_image,
            caption=f"📱 QR-код заметки: {note.title}"
        )
    
    elif data == "note_cancel":
        if user_id in user_states:
            del user_states[user_id]
        await query.edit_message_text("❌ Создание заметки отменено.")
    
    elif data.startswith("note_view_"):
        note_id = data.replace("note_view_", "")
        note = Note.query.filter_by(id=note_id, user_id=user_id).first()
        
        if not note:
            await query.edit_message_text("❌ Заметка не найдена.")
            return
        
        await send_note_message(update, note, edit_message_id=query.message.message_id)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик обычных сообщений"""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return
    
    # Проверяем, есть ли активное состояние создания заметки
    if user_id in user_states:
        state = user_states[user_id]
        
        if update.message.photo:
            # Сохраняем фото
            if len(state['photos']) >= 5:
                await update.message.reply_text("❌ Максимум 5 фото!")
                return
            
            photo = update.message.photo[-1]  # Берем самое большое фото
            file = await context.bot.get_file(photo.file_id)
            
            # Генерируем безопасное имя файла (всегда .jpg)
            safe_filename = secure_filename(f"{uuid.uuid4()}.jpg")
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
            
            # Скачиваем во временный файл
            temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{uuid.uuid4()}.jpg")
            await file.download_to_drive(temp_path)
            
            # Сжимаем изображение
            if compress_image(temp_path, file_path):
                # Удаляем временный файл
                try:
                    os.remove(temp_path)
                except:
                    pass
                state['photos'].append(file_path)
            else:
                # Если сжатие не удалось, используем временный файл
                state['photos'].append(temp_path)
            
            count = len(state['photos'])
            await update.message.reply_text(f"✅ Фото добавлено ({count}/5)")
            return
        
        elif update.message.text:
            text = update.message.text
            waiting_for = state.get('waiting_for')
            
            if waiting_for == 'title':
                state['title'] = text
                state['waiting_for'] = None
                await update.message.reply_text(f"✅ Заголовок установлен: {text}")
            elif waiting_for == 'text':
                state['text'] = text
                state['waiting_for'] = None
                await update.message.reply_text("✅ Текст установлен")
            elif not state.get('title'):
                # Если заголовок еще не установлен, устанавливаем его
                state['title'] = text
                await update.message.reply_text(f"✅ Заголовок установлен: {text}")
            else:
                # Если заголовок уже есть, устанавливаем текст
                state['text'] = text
                await update.message.reply_text("✅ Текст установлен")
            return
    
    # Если не в режиме создания заметки, просто отвечаем
    await update.message.reply_text(
        "👋 Используйте команды:\n"
        "/start - приветствие\n"
        "/qr <текст> - создать QR-код\n"
        "/note - управление заметками\n"
        "/view <id> - просмотр заметки"
    )


async def send_to_channel(text: str, photo_paths: list):
    """Отправка сообщения с фото в Telegram канал"""
    try:
        if photo_paths:
            # Отправляем первое фото с текстом
            with open(photo_paths[0], 'rb') as photo_file:
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=photo_file,
                    caption=text[:1024] if text else None
                )
            # Отправляем остальные фото
            for photo_path in photo_paths[1:]:
                with open(photo_path, 'rb') as photo_file:
                    await bot.send_photo(chat_id=CHANNEL_ID, photo=photo_file)
        else:
            # Отправляем только текст
            await bot.send_message(chat_id=CHANNEL_ID, text=text[:4096])
    except Exception as e:
        app.logger.error(f"Error sending to channel: {e}")
        raise


# Flask Routes
@app.route('/')
def index():
    """Главная страница с формой создания заметок"""
    return render_template('index.html')


@app.route('/status')
def status():
    """Возвращает JSON со статусом сервиса"""
    try:
        note_count = Note.query.count()
        return jsonify({
            'status': 'ok',
            'service': 'QR Warehouse Notes',
            'notes_count': note_count,
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@app.route('/create_note', methods=['POST'])
def create_note():
    """Создание заметки через веб-интерфейс"""
    try:
        text = request.form.get('text', '')
        photos = request.files.getlist('photos')
        
        # Валидация
        if len(text) > 4096:
            return jsonify({'error': 'Текст заметки превышает 4096 символов'}), 400
        
        if not text.strip() and not photos:
            return jsonify({'error': 'Необходимо указать текст или добавить фото'}), 400
        
        if len(photos) > 5:
            return jsonify({'error': 'Максимум 5 фотографий'}), 400
        
        # Получаем title из первой строки текста
        title = 'Без названия'
        if text.strip():
            first_line = text.strip().split('\n')[0].strip()
            title = first_line[:500] if first_line else 'Без названия'
        
        # Сохраняем фото со сжатием
        photo_paths = []
        for photo in photos:
            if photo.filename:
                # Всегда сохраняем как .jpg
                safe_filename = secure_filename(f"{uuid.uuid4()}.jpg")
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
                
                # Сжимаем изображение
                if compress_image(photo, file_path):
                    photo_paths.append(file_path)
                else:
                    # Если сжатие не удалось, пробуем сохранить оригинал
                    try:
                        photo.save(file_path)
                        photo_paths.append(file_path)
                    except Exception as e:
                        app.logger.error(f"Error saving image: {e}")
                        continue
        
        # Создаем заметку
        note = Note(
            id=str(uuid.uuid4()),
            title=title,
            text=text,
            photos_json=json.dumps(photo_paths) if photo_paths else None,
            user_id=ALLOWED_USER_ID
        )
        
        db.session.add(note)
        db.session.commit()
        
        # Отправляем в Telegram канал
        send_to_channel_sync(text, photo_paths)
        
        # Генерируем QR-код с форматом "qrapp:note:<id>"
        qr_data = f"qrapp:note:{note.id}"
        
        return jsonify({
            'message': 'Заметка создана успешно',
            'note_id': note.id,
            'qr_url': f'/qr?data={qr_data}'
        })
        
    except Exception as e:
        app.logger.error(f"Error creating note: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Ошибка при создании заметки: {str(e)}'}), 500


@app.route('/open_qr', methods=['POST'])
def open_qr():
    """Открытие заметки по QR-коду"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400
        
        qr_data = data.get('data', '')
        if not qr_data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Извлекаем ID из формата "qrapp:note:<id>" или "note:<id>"
        note_id = None
        if qr_data.startswith('qrapp:note:'):
            note_id = qr_data.replace('qrapp:note:', '')
        elif qr_data.startswith('note:'):
            note_id = qr_data.replace('note:', '')
        else:
            # Попробуем считать что весь текст - это ID
            note_id = qr_data
        
        note = Note.query.filter_by(id=note_id).first()
        if not note:
            return jsonify({'error': 'Note not found'}), 404
        
        # Генерируем HTML для отображения заметки
        photos = json.loads(note.photos_json) if note.photos_json else []
        photos_html = ""
        for photo_path in photos:
            if os.path.exists(photo_path):
                # Конвертируем локальный путь в URL
                filename = os.path.basename(photo_path)
                photos_html += f'<img src="/uploads/{filename}" style="max-width: 300px; margin: 10px;"><br>'
        
        html = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{note.title}</title>
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 20px auto; padding: 20px; }}
                .note {{ background: #f9f9f9; padding: 20px; border-radius: 10px; }}
                .back-link {{ margin-bottom: 20px; }}
            </style>
        </head>
        <body>
            <div class="back-link">
                <a href="/">← Назад</a>
            </div>
            <div class="note">
                <h1>{note.title}</h1>
                <p>{note.text or 'Нет текста'}</p>
                {photos_html}
                <p><small>Создано: {note.created.strftime('%Y-%m-%d %H:%M')}</small></p>
            </div>
        </body>
        </html>
        """
        return jsonify(note.to_dict()), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/qr')
def qr_generator():
    """Генерация QR-кода в памяти"""
    data = request.args.get('data', '')
    if not data:
        return jsonify({'error': 'Parameter "data" is required'}), 400
    
    qr_image = generate_qr_code(data)
    qr_image.seek(0)  # Возвращаемся в начало BytesIO
    return send_file(qr_image, mimetype='image/png', as_attachment=False, download_name=f'qr_{data[:10]}.png')


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Раздача загруженных файлов"""
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename))


# Обработчики ошибок HTTP
@app.errorhandler(404)
def handle_404(e):
    """Обработчик ошибки 404 - возвращает JSON"""
    return jsonify({'error': 'Not Found', 'status_code': 404}), 404

@app.errorhandler(405)
def handle_405(e):
    """Обработчик ошибки 405 - возвращает JSON"""
    return jsonify({'error': 'Method Not Allowed', 'status_code': 405}), 405

# Регистрация handlers
telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(CommandHandler("qr", qr_command))
telegram_app.add_handler(CommandHandler("note", note_command))
telegram_app.add_handler(CommandHandler("view", view_command))
telegram_app.add_handler(CallbackQueryHandler(button_callback))
telegram_app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))


if __name__ == '__main__':
    # Запускаем Telegram бота в отдельном потоке
    import threading
    
    def run_telegram_bot():
        telegram_app.run_polling()
    
    telegram_thread = threading.Thread(target=run_telegram_bot)
    telegram_thread.daemon = True
    telegram_thread.start()
    
    import time
    time.sleep(2)
    
    print("Flask server starting on http://0.0.0.0:5000")
    # Запускаем Flask сервер
app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
