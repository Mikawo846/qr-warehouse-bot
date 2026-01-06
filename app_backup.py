import os
import uuid
import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file, render_template_string
from flask_sqlalchemy import SQLAlchemy
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import qrcode
from PIL import Image
import io
from werkzeug.utils import secure_filename

# Загружаем переменные окружения из .env файла
load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///qr_warehouse.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

db = SQLAlchemy(app)

# Создаем папку для загрузок
Path(app.config['UPLOAD_FOLDER']).mkdir(exist_ok=True)

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

# Telegram Bot Application
telegram_app = Application.builder().token(BOT_TOKEN).build()
bot = Bot(token=BOT_TOKEN)

# Хранилище для временных данных пользователей (для загрузки фото)
user_states = {}


def is_authorized(user_id: int) -> bool:
    """Проверка авторизации пользователя"""
    return user_id == ALLOWED_USER_ID


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
    """Обработчик команды /qr <text/link>"""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("❌ Доступ запрещен.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Использование: /qr <текст или ссылка>")
        return
    
    text_or_link = ' '.join(context.args)
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
    qr_data = f"qrapp://note:{note.id}"
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
        qr_data = f"qrapp://note:{note.id}"
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
            
            # Генерируем безопасное имя файла
            file_ext = file.file_path.split('.')[-1] if '.' in file.file_path else 'jpg'
            safe_filename = secure_filename(f"{uuid.uuid4()}.{file_ext}")
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
            
            await file.download_to_drive(file_path)
            state['photos'].append(file_path)
            
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


def get_index_html(qr_url=None, note_id=None, message=None, error=None):
    """Генерация HTML для главной страницы"""
    qr_section = ""
    if qr_url and note_id:
        qr_section = f"""
            <div id="qr-result" style="display: block; margin-top: 30px; padding: 20px; background-color: #f8f9fa; border-radius: 5px;">
                <h3 style="color: #28a745; margin-bottom: 15px;">{message or 'Заметка сохранена'}</h3>
                <div style="margin: 20px 0;">
                    <img src="{qr_url}" alt="QR Code" style="max-width: 300px; height: auto;">
                </div>
                <a href="{qr_url}" download="qr-note-{note_id}.png" class="btn-success" style="display: inline-block; padding: 12px 24px; background-color: #28a745; color: white; text-decoration: none; border-radius: 5px; font-weight: bold;">Скачать QR</a>
            </div>
        """
    
    error_section = ""
    if error:
        error_section = f"""
            <div class="error" style="display: block; color: #dc3545; margin-top: 10px; padding: 10px; background-color: #f8d7da; border-radius: 5px;">
                {error}
            </div>
        """
    
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QR Warehouse Notes</title>
        <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
        <style>
            * {{
                box-sizing: border-box;
            }}
            body {{
                font-family: Arial, sans-serif;
                max-width: 900px;
                margin: 20px auto;
                padding: 20px;
                background-color: #f5f5f5;
            }}
            .container {{
                background-color: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }}
            h1 {{
                color: #333;
                margin-bottom: 30px;
            }}
            .form-group {{
                margin-bottom: 20px;
            }}
            label {{
                display: block;
                margin-bottom: 8px;
                color: #333;
                font-weight: bold;
            }}
            textarea {{
                width: 100%;
                min-height: 150px;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
                font-family: Arial, sans-serif;
                font-size: 14px;
                resize: vertical;
            }}
            input[type="file"] {{
                width: 100%;
                padding: 8px;
                border: 1px solid #ddd;
                border-radius: 5px;
                background-color: #f9f9f9;
            }}
            .file-count {{
                margin-top: 5px;
                font-size: 12px;
                color: #666;
            }}
            .button-group {{
                display: flex;
                gap: 10px;
                margin-top: 20px;
            }}
            button {{
                padding: 12px 24px;
                border: none;
                border-radius: 5px;
                font-size: 16px;
                cursor: pointer;
                font-weight: bold;
            }}
            .btn-primary {{
                background-color: #007bff;
                color: white;
            }}
            .btn-primary:hover {{
                background-color: #0056b3;
            }}
            .btn-secondary {{
                background-color: #6c757d;
                color: white;
            }}
            .btn-secondary:hover {{
                background-color: #545b62;
            }}
            .btn-success {{
                background-color: #28a745;
                color: white;
            }}
            .btn-success:hover {{
                background-color: #218838;
            }}
            .error {{
                color: #dc3545;
                margin-top: 10px;
                padding: 10px;
                background-color: #f8d7da;
                border-radius: 5px;
            }}
            .preview-images {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin-top: 10px;
            }}
            .preview-images img {{
                max-width: 100px;
                max-height: 100px;
                object-fit: cover;
                border-radius: 5px;
                border: 1px solid #ddd;
            }}
            a {{
                color: #007bff;
                text-decoration: none;
            }}
            a:hover {{
                text-decoration: underline;
            }}
            .modal {{
                display: none;
                position: fixed;
                z-index: 1000;
                left: 0;
                top: 0;
                width: 100%;
                height: 100%;
                background-color: rgba(0,0,0,0.5);
            }}
            .modal-content {{
                background-color: white;
                margin: 5% auto;
                padding: 20px;
                border-radius: 10px;
                width: 90%;
                max-width: 500px;
                position: relative;
            }}
            .close {{
                color: #aaa;
                float: right;
                font-size: 28px;
                font-weight: bold;
                cursor: pointer;
            }}
            .close:hover {{
                color: #000;
            }}
            #qr-reader {{
                width: 100%;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>QR Warehouse Notes</h1>
            
            <form id="note-form" enctype="multipart/form-data" method="POST" action="/create_note">
                <div class="form-group">
                    <label for="note-text">Заметка</label>
                    <textarea id="note-text" name="text" maxlength="4096" placeholder="Введите текст заметки..."></textarea>
                    <div id="char-count" style="text-align: right; color: #666; font-size: 12px; margin-top: 5px;">0 / 4096</div>
                </div>
                
                <div class="form-group">
                    <label for="photos">Фото (до 5 файлов)</label>
                    <input type="file" id="photos" name="photos" accept="image/*" multiple>
                    <div class="file-count" id="file-count"></div>
                    <div class="preview-images" id="preview-images"></div>
                </div>
                
                {error_section}
                
                <div class="button-group">
                    <button type="submit" class="btn-primary">Создать QR-заметку</button>
                    <button type="button" class="btn-secondary" onclick="openQRScanner()">Сканировать QR-код</button>
                </div>
            </form>
            
            {qr_section}
            
            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd;">
                <a href="/status">Проверить статус</a>
            </div>
        </div>
        
        <!-- QR Scanner Modal -->
        <div id="qr-modal" class="modal">
            <div class="modal-content">
                <span class="close" onclick="closeQRScanner()">&times;</span>
                <h2>Сканирование QR-кода</h2>
                <div id="qr-reader"></div>
                <div id="scanner-result" style="margin-top: 20px;"></div>
            </div>
        </div>
        
        <script>
            let html5QrCode = null;
            
            const textarea = document.getElementById('note-text');
            const charCount = document.getElementById('char-count');
            textarea.addEventListener('input', function() {{
                const length = this.value.length;
                charCount.textContent = length + ' / 4096';
            }});
            
            const fileInput = document.getElementById('photos');
            const fileCount = document.getElementById('file-count');
            const previewImages = document.getElementById('preview-images');
            
            fileInput.addEventListener('change', function() {{
                const files = Array.from(this.files);
                fileCount.textContent = `Выбрано файлов: ${{files.length}} / 5`;
                
                previewImages.innerHTML = '';
                files.slice(0, 5).forEach(file => {{
                    const reader = new FileReader();
                    reader.onload = function(e) {{
                        const img = document.createElement('img');
                        img.src = e.target.result;
                        previewImages.appendChild(img);
                    }};
                    reader.readAsDataURL(file);
                }});
            }});
            
            function openQRScanner() {{
                document.getElementById('qr-modal').style.display = 'block';
                const scannerDiv = document.getElementById('qr-reader');
                
                html5QrCode = new Html5Qrcode("qr-reader");
                html5QrCode.start(
                    {{ facingMode: "environment" }},
                    {{
                        fps: 10,
                        qrbox: {{ width: 250, height: 250 }}
                    }},
                    onScanSuccess,
                    onScanFailure
                ).catch(err => {{
                    console.error("Unable to start scanning", err);
                    document.getElementById('scanner-result').innerHTML = 
                        '<p style="color: red;">Не удалось запустить камеру.</p>';
                }});
            }}
            
            function closeQRScanner() {{
                if (html5QrCode) {{
                    html5QrCode.stop().then(() => {{
                        html5QrCode.clear();
                        html5QrCode = null;
                    }}).catch(err => {{
                        console.error("Error stopping scanner", err);
                    }});
                }}
                document.getElementById('qr-modal').style.display = 'none';
                document.getElementById('scanner-result').innerHTML = '';
            }}
            
            function onScanSuccess(decodedText, decodedResult) {{
                document.getElementById('scanner-result').innerHTML = 
                    `<p style="color: green;"><strong>Найден QR-код:</strong><br>${{decodedText}}</p>`;
                
                // Отправляем данные на сервер через fetch
                fetch('/open_qr', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{ data: decodedText }})
                }})
                .then(response => {{
                    if (response.ok) {{
                        return response.text();
                    }} else {{
                        return response.json().then(err => Promise.reject(err));
                    }}
                }})
                .then(html => {{
                    // Закрываем модальное окно
                    closeQRScanner();
                    // Заменяем содержимое страницы заметкой
                    document.open();
                    document.write(html);
                    document.close();
                }})
                .catch(error => {{
                    console.error('Error:', error);
                    document.getElementById('scanner-result').innerHTML = 
                        `<p style="color: red;"><strong>Ошибка:</strong><br>${{error.error || error.message || 'Не удалось загрузить заметку'}}</p>`;
                }});
            }}
            
            function onScanFailure(error) {{
                // Ignore scanning errors
            }}
            
            window.onclick = function(event) {{
                const modal = document.getElementById('qr-modal');
                if (event.target == modal) {{
                    closeQRScanner();
                }}
            }}
        </script>
    </body>
    </html>
    """


# Flask Routes
@app.route('/')
def index():
    """Главная страница с формой создания заметок"""
    return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QR Warehouse Notes</title>
        <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
        <style>
            * {
                box-sizing: border-box;
            }
            body {
                font-family: Arial, sans-serif;
                max-width: 900px;
                margin: 20px auto;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                background-color: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 {
                color: #333;
                margin-bottom: 30px;
            }
            .form-group {
                margin-bottom: 20px;
            }
            label {
                display: block;
                margin-bottom: 8px;
                color: #333;
                font-weight: bold;
            }
            textarea {
                width: 100%;
                min-height: 150px;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
                font-family: Arial, sans-serif;
                font-size: 14px;
                resize: vertical;
            }
            input[type="file"] {
                width: 100%;
                padding: 8px;
                border: 1px solid #ddd;
                border-radius: 5px;
                background-color: #f9f9f9;
            }
            .file-count {
                margin-top: 5px;
                font-size: 12px;
                color: #666;
            }
            .button-group {
                display: flex;
                gap: 10px;
                margin-top: 20px;
            }
            button {
                padding: 12px 24px;
                border: none;
                border-radius: 5px;
                font-size: 16px;
                cursor: pointer;
                font-weight: bold;
            }
            .btn-primary {
                background-color: #007bff;
                color: white;
            }
            .btn-primary:hover {
                background-color: #0056b3;
            }
            .btn-secondary {
                background-color: #6c757d;
                color: white;
            }
            .btn-secondary:hover {
                background-color: #545b62;
            }
            .btn-success {
                background-color: #28a745;
                color: white;
            }
            .btn-success:hover {
                background-color: #218838;
            }
            .error {
                color: #dc3545;
                margin-top: 10px;
                padding: 10px;
                background-color: #f8d7da;
                border-radius: 5px;
            }
            .preview-images {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin-top: 10px;
            }
            .preview-images img {
                max-width: 100px;
                max-height: 100px;
                object-fit: cover;
                border-radius: 5px;
                border: 1px solid #ddd;
            }
            a {
                color: #007bff;
                text-decoration: none;
            }
            a:hover {
                text-decoration: underline;
            }
            .modal {
                display: none;
                position: fixed;
                z-index: 1000;
                left: 0;
                top: 0;
                width: 100%;
                height: 100%;
                background-color: rgba(0,0,0,0.5);
            }
            .modal-content {
                background-color: white;
                margin: 5% auto;
                padding: 20px;
                border-radius: 10px;
                width: 90%;
                max-width: 500px;
                position: relative;
            }
            .close {
                color: #aaa;
                float: right;
                font-size: 28px;
                font-weight: bold;
                cursor: pointer;
            }
            .close:hover {
                color: #000;
            }
            #qr-reader {
                width: 100%;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>QR Warehouse Notes</h1>
            
            <form id="note-form" enctype="multipart/form-data" method="POST" action="/create_note">
                <div class="form-group">
                    <label for="note-text">Заметка</label>
                    <textarea id="note-text" name="text" maxlength="4096" placeholder="Введите текст заметки..."></textarea>
                    <div id="char-count" style="text-align: right; color: #666; font-size: 12px; margin-top: 5px;">0 / 4096</div>
                </div>
                
                <div class="form-group">
                    <label for="photos">Фото (до 5 файлов)</label>
                    <input type="file" id="photos" name="photos" accept="image/*" multiple>
                    <div class="file-count" id="file-count"></div>
                    <div class="preview-images" id="preview-images"></div>
                </div>
                
                <div class="button-group">
                    <button type="submit" class="btn-primary">Создать QR-заметку</button>
                    <button type="button" class="btn-secondary" onclick="openQRScanner()">Сканировать QR-код</button>
                </div>
            </form>
            
            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd;">
                <a href="/status">Проверить статус</a>
            </div>
        </div>
        
        <!-- QR Scanner Modal -->
        <div id="qr-modal" class="modal">
            <div class="modal-content">
                <span class="close" onclick="closeQRScanner()">&times;</span>
                <h2>Сканирование QR-кода</h2>
                <div id="qr-reader"></div>
                <div id="scanner-result" style="margin-top: 20px;"></div>
            </div>
        </div>
        
        <script>
            let html5QrCode = null;
            
            const textarea = document.getElementById('note-text');
            const charCount = document.getElementById('char-count');
            textarea.addEventListener('input', function() {
                const length = this.value.length;
                charCount.textContent = length + ' / 4096';
            });
            
            const fileInput = document.getElementById('photos');
            const fileCount = document.getElementById('file-count');
            const previewImages = document.getElementById('preview-images');
            
            fileInput.addEventListener('change', function() {
                const files = Array.from(this.files);
                fileCount.textContent = `Выбрано файлов: ${files.length} / 5`;
                
                previewImages.innerHTML = '';
                files.slice(0, 5).forEach(file => {
                    const reader = new FileReader();
                    reader.onload = function(e) {
                        const img = document.createElement('img');
                        img.src = e.target.result;
                        previewImages.appendChild(img);
                    };
                    reader.readAsDataURL(file);
                });
            });
            
            function openQRScanner() {
                document.getElementById('qr-modal').style.display = 'block';
                const scannerDiv = document.getElementById('qr-reader');
                
                html5QrCode = new Html5Qrcode("qr-reader");
                html5QrCode.start(
                    { facingMode: "environment" },
                    {
                        fps: 10,
                        qrbox: { width: 250, height: 250 }
                    },
                    onScanSuccess,
                    onScanFailure
                ).catch(err => {
                    console.error("Unable to start scanning", err);
                    document.getElementById('scanner-result').innerHTML = 
                        '<p style="color: red;">Не удалось запустить камеру.</p>';
                });
            }
            
            function closeQRScanner() {
                if (html5QrCode) {
                    html5QrCode.stop().then(() => {
                        html5QrCode.clear();
                        html5QrCode = null;
                    }).catch(err => {
                        console.error("Error stopping scanner", err);
                    });
                }
                document.getElementById('qr-modal').style.display = 'none';
                document.getElementById('scanner-result').innerHTML = '';
            }
            
            function onScanSuccess(decodedText, decodedResult) {
                document.getElementById('scanner-result').innerHTML = 
                    `<p style="color: green;"><strong>Найден QR-код:</strong><br>${decodedText}</p>`;
                
                // Отправляем данные на сервер через fetch
                fetch('/open_qr', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ data: decodedText })
                })
                .then(response => {
                    if (response.ok) {
                        return response.text();
                    } else {
                        return response.json().then(err => Promise.reject(err));
                    }
                })
                .then(html => {
                    // Закрываем модальное окно
                    closeQRScanner();
                    // Заменяем содержимое страницы заметкой
                    document.open();
                    document.write(html);
                    document.close();
                })
                .catch(error => {
                    console.error('Error:', error);
                    document.getElementById('scanner-result').innerHTML = 
                        `<p style="color: red;"><strong>Ошибка:</strong><br>${error.error || error.message || 'Не удалось загрузить заметку'}</p>`;
                });
            }
            
            function onScanFailure(error) {
                // Ignore scanning errors
            }
            
            window.onclick = function(event) {
                const modal = document.getElementById('qr-modal');
                if (event.target == modal) {
                    closeQRScanner();
                }
            }
        </script>
    </body>
    </html>
    """


@app.route('/create_note', methods=['POST'])
def create_note():
    """Создание заметки через веб-интерфейс"""
    try:
        text = request.form.get('text', '')
        photos = request.files.getlist('photos')
        
        # Валидация
        if len(text) > 4096:
            return get_index_html(error='Текст заметки превышает 4096 символов'), 400
        
        if not text.strip() and not photos:
            return get_index_html(error='Необходимо указать текст или добавить фото'), 400
        
        if len(photos) > 5:
            return get_index_html(error='Максимум 5 фотографий'), 400
        
        # Получаем title из первой строки текста
        title = 'Без названия'
        if text.strip():
            first_line = text.strip().split('\n')[0].strip()
            title = first_line[:500] if first_line else 'Без названия'
        
        # Сохраняем фото
        photo_paths = []
        for photo in photos:
            if photo.filename:
                safe_filename = secure_filename(f"{uuid.uuid4()}_{photo.filename}")
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
                photo.save(file_path)
                photo_paths.append(file_path)
        
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
        try:
            asyncio.run(send_to_channel(text, photo_paths))
        except Exception as e:
            app.logger.error(f"Error sending to channel: {e}")
            # Продолжаем даже если не удалось отправить в канал
        
        # Генерируем QR-код с форматом "note:<id>"
        qr_data = f"note:{note.id}"
        qr_image = generate_qr_code(qr_data)
        qr_filename = f"qr_{note.id}.png"
        qr_path = os.path.join(app.config['UPLOAD_FOLDER'], qr_filename)
        qr_image.seek(0)
        with open(qr_path, 'wb') as f:
            f.write(qr_image.read())
        
        qr_url = f'/uploads/{qr_filename}'
        
        # Возвращаем HTML страницу с результатом
        return get_index_html(qr_url=qr_url, note_id=note.id, message="Заметка сохранена")
        
    except Exception as e:
        app.logger.error(f"Error creating note: {e}")
        import traceback
        traceback.print_exc()
        return get_index_html(error=f'Ошибка при создании заметки: {str(e)}'), 500


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


def get_index_html(qr_url=None, note_id=None, message=None, error=None):
    """Генерация HTML для главной страницы"""
    qr_section = ""
    if qr_url and note_id:
        qr_section = f"""
            <div id="qr-result" style="display: block; margin-top: 30px; padding: 20px; background-color: #f8f9fa; border-radius: 5px;">
                <h3 style="color: #28a745; margin-bottom: 15px;">{message or 'Заметка сохранена'}</h3>
                <div style="margin: 20px 0;">
                    <img src="{qr_url}" alt="QR Code" style="max-width: 300px; height: auto;">
                </div>
                <a href="{qr_url}" download="qr-note-{note_id}.png" class="btn-success" style="display: inline-block; padding: 12px 24px; background-color: #28a745; color: white; text-decoration: none; border-radius: 5px; font-weight: bold;">Скачать QR</a>
            </div>
        """
    
    error_section = ""
    if error:
        error_section = f"""
            <div class="error" style="display: block; color: #dc3545; margin-top: 10px; padding: 10px; background-color: #f8d7da; border-radius: 5px;">
                {error}
            </div>
        """
    
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QR Warehouse Notes</title>
        <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
        <style>
            * {{
                box-sizing: border-box;
            }}
            body {{
                font-family: Arial, sans-serif;
                max-width: 900px;
                margin: 20px auto;
                padding: 20px;
                background-color: #f5f5f5;
            }}
            .container {{
                background-color: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }}
            h1 {{
                color: #333;
                margin-bottom: 30px;
            }}
            .form-group {{
                margin-bottom: 20px;
            }}
            label {{
                display: block;
                margin-bottom: 8px;
                color: #333;
                font-weight: bold;
            }}
            textarea {{
                width: 100%;
                min-height: 150px;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
                font-family: Arial, sans-serif;
                font-size: 14px;
                resize: vertical;
            }}
            input[type="file"] {{
                width: 100%;
                padding: 8px;
                border: 1px solid #ddd;
                border-radius: 5px;
                background-color: #f9f9f9;
            }}
            .file-count {{
                margin-top: 5px;
                font-size: 12px;
                color: #666;
            }}
            .button-group {{
                display: flex;
                gap: 10px;
                margin-top: 20px;
            }}
            button {{
                padding: 12px 24px;
                border: none;
                border-radius: 5px;
                font-size: 16px;
                cursor: pointer;
                font-weight: bold;
            }}
            .btn-primary {{
                background-color: #007bff;
                color: white;
            }}
            .btn-primary:hover {{
                background-color: #0056b3;
            }}
            .btn-secondary {{
                background-color: #6c757d;
                color: white;
            }}
            .btn-secondary:hover {{
                background-color: #545b62;
            }}
            .btn-success {{
                background-color: #28a745;
                color: white;
            }}
            .btn-success:hover {{
                background-color: #218838;
            }}
            .error {{
                color: #dc3545;
                margin-top: 10px;
                padding: 10px;
                background-color: #f8d7da;
                border-radius: 5px;
            }}
            .preview-images {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin-top: 10px;
            }}
            .preview-images img {{
                max-width: 100px;
                max-height: 100px;
                object-fit: cover;
                border-radius: 5px;
                border: 1px solid #ddd;
            }}
            a {{
                color: #007bff;
                text-decoration: none;
            }}
            a:hover {{
                text-decoration: underline;
            }}
            .modal {{
                display: none;
                position: fixed;
                z-index: 1000;
                left: 0;
                top: 0;
                width: 100%;
                height: 100%;
                background-color: rgba(0,0,0,0.5);
            }}
            .modal-content {{
                background-color: white;
                margin: 5% auto;
                padding: 20px;
                border-radius: 10px;
                width: 90%;
                max-width: 500px;
                position: relative;
            }}
            .close {{
                color: #aaa;
                float: right;
                font-size: 28px;
                font-weight: bold;
                cursor: pointer;
            }}
            .close:hover {{
                color: #000;
            }}
            #qr-reader {{
                width: 100%;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>QR Warehouse Notes</h1>
            
            <form id="note-form" enctype="multipart/form-data" method="POST" action="/create_note">
                <div class="form-group">
                    <label for="note-text">Заметка</label>
                    <textarea id="note-text" name="text" maxlength="4096" placeholder="Введите текст заметки..."></textarea>
                    <div id="char-count" style="text-align: right; color: #666; font-size: 12px; margin-top: 5px;">0 / 4096</div>
                </div>
                
                <div class="form-group">
                    <label for="photos">Фото (до 5 файлов)</label>
                    <input type="file" id="photos" name="photos" accept="image/*" multiple>
                    <div class="file-count" id="file-count"></div>
                    <div class="preview-images" id="preview-images"></div>
                </div>
                
                {error_section}
                
                <div class="button-group">
                    <button type="submit" class="btn-primary">Создать QR-заметку</button>
                    <button type="button" class="btn-secondary" onclick="openQRScanner()">Сканировать QR-код</button>
                </div>
            </form>
            
            {qr_section}
            
            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd;">
                <a href="/status">Проверить статус</a>
            </div>
        </div>
        
        <!-- QR Scanner Modal -->
        <div id="qr-modal" class="modal">
            <div class="modal-content">
                <span class="close" onclick="closeQRScanner()">&times;</span>
                <h2>Сканирование QR-кода</h2>
                <div id="qr-reader"></div>
                <div id="scanner-result" style="margin-top: 20px;"></div>
            </div>
        </div>
        
        <script>
            let html5QrCode = null;
            
            const textarea = document.getElementById('note-text');
            const charCount = document.getElementById('char-count');
            textarea.addEventListener('input', function() {{
                const length = this.value.length;
                charCount.textContent = length + ' / 4096';
            }});
            
            const fileInput = document.getElementById('photos');
            const fileCount = document.getElementById('file-count');
            const previewImages = document.getElementById('preview-images');
            
            fileInput.addEventListener('change', function() {{
                const files = Array.from(this.files);
                fileCount.textContent = `Выбрано файлов: ${{files.length}} / 5`;
                
                previewImages.innerHTML = '';
                files.slice(0, 5).forEach(file => {{
                    const reader = new FileReader();
                    reader.onload = function(e) {{
                        const img = document.createElement('img');
                        img.src = e.target.result;
                        previewImages.appendChild(img);
                    }};
                    reader.readAsDataURL(file);
                }});
            }});
            
            function openQRScanner() {{
                document.getElementById('qr-modal').style.display = 'block';
                const scannerDiv = document.getElementById('qr-reader');
                
                html5QrCode = new Html5Qrcode("qr-reader");
                html5QrCode.start(
                    {{ facingMode: "environment" }},
                    {{
                        fps: 10,
                        qrbox: {{ width: 250, height: 250 }}
                    }},
                    onScanSuccess,
                    onScanFailure
                ).catch(err => {{
                    console.error("Unable to start scanning", err);
                    document.getElementById('scanner-result').innerHTML = 
                        '<p style="color: red;">Не удалось запустить камеру.</p>';
                }});
            }}
            
            function closeQRScanner() {{
                if (html5QrCode) {{
                    html5QrCode.stop().then(() => {{
                        html5QrCode.clear();
                        html5QrCode = null;
                    }}).catch(err => {{
                        console.error("Error stopping scanner", err);
                    }});
                }}
                document.getElementById('qr-modal').style.display = 'none';
                document.getElementById('scanner-result').innerHTML = '';
            }}
            
            function onScanSuccess(decodedText, decodedResult) {{
                document.getElementById('scanner-result').innerHTML = 
                    `<p style="color: green;"><strong>Найден QR-код:</strong><br>${{decodedText}}</p>`;
                
                // Отправляем данные на сервер через fetch
                fetch('/open_qr', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{ data: decodedText }})
                }})
                .then(response => {{
                    if (response.ok) {{
                        return response.text();
                    }} else {{
                        return response.json().then(err => Promise.reject(err));
                    }}
                }})
                .then(html => {{
                    // Закрываем модальное окно
                    closeQRScanner();
                    // Заменяем содержимое страницы заметкой
                    document.open();
                    document.write(html);
                    document.close();
                }})
                .catch(error => {{
                    console.error('Error:', error);
                    document.getElementById('scanner-result').innerHTML = 
                        `<p style="color: red;"><strong>Ошибка:</strong><br>${{error.error || error.message || 'Не удалось загрузить заметку'}}</p>`;
                }});
            }}
            
            function onScanFailure(error) {{
                // Ignore scanning errors
            }}
            
            window.onclick = function(event) {{
                const modal = document.getElementById('qr-modal');
                if (event.target == modal) {{
                    closeQRScanner();
                }}
            }}
        </script>
    </body>
    </html>
    """


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
        
        # Извлекаем ID из формата "qrapp://note:<id>" или "note:<id>"
        note_id = None
        if qr_data.startswith('qrapp://note:'):
            note_id = qr_data.replace('qrapp://note:', '')
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
        return html
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Раздача загруженных файлов"""
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename))


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
    app.run(host='0.0.0.0', port=5000, debug=True)
        html = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{note.title}</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    max-width: 900px;
                    margin: 20px auto;
                    padding: 20px;
                    background-color: #f5f5f5;
                }}
                .container {{
                    background-color: white;
                    padding: 30px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                h1 {{
                    color: #333;
                    margin-bottom: 20px;
                }}
                .note-text {{
                    margin: 20px 0;
                    line-height: 1.6;
                    white-space: pre-wrap;
                    color: #666;
                }}
                .photos {{
                    margin: 20px 0;
                }}
                .photos img {{
                    max-width: 100%;
                    height: auto;
                    margin: 10px 0;
                    border-radius: 8px;
                    box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                }}
                .meta {{
                    color: #666;
                    font-size: 0.9em;
                    margin-top: 20px;
                    padding-top: 20px;
                    border-top: 1px solid #ddd;
                }}
                a {{
                    color: #007bff;
                    text-decoration: none;
                }}
                a:hover {{
                    text-decoration: underline;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>{note.title}</h1>
                {f'<div class="note-text">{note.text}</div>' if note.text else ''}
                <div class="photos">
                    {''.join([f'<img src="/uploads/{photo_filename}" alt="Photo">' for photo_filename in photo_filenames])}
                </div>
                <div class="meta">
                    ID: {note.id}<br>
                    Created: {note.created.strftime('%Y-%m-%d %H:%M:%S') if note.created else 'N/A'}
                </div>
                <div style="margin-top: 20px;">
                    <a href="/">← Назад на главную</a>
                </div>
            </div>
        </body>
        </html>
        """
        return html
        
    except Exception as e:
        app.logger.error(f"Error opening QR note: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/status')
def status():
    """Страница статуса сервиса"""
    with app.app_context():
        notes_count = Note.query.count()
    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Статус - QR Warehouse Notes</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 50px auto;
                padding: 20px;
                background-color: #f5f5f5;
            }}
            .container {{
                background-color: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }}
            h1 {{
                color: #333;
                margin-bottom: 20px;
            }}
            .status-item {{
                margin: 15px 0;
                padding: 10px;
                background-color: #f8f9fa;
                border-left: 4px solid #007bff;
            }}
            .status-label {{
                font-weight: bold;
                color: #333;
            }}
            .status-value {{
                color: #666;
                margin-left: 10px;
            }}
            .status-ok {{
                color: #28a745;
            }}
            a {{
                color: #007bff;
                text-decoration: none;
                display: inline-block;
                margin-top: 20px;
            }}
            a:hover {{
                text-decoration: underline;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Статус сервиса</h1>
            <div class="status-item">
                <span class="status-label">Статус:</span>
                <span class="status-value status-ok">OK</span>
            </div>
            <div class="status-item">
                <span class="status-label">Сервис:</span>
                <span class="status-value">QR Warehouse Notes Bot</span>
            </div>
            <div class="status-item">
                <span class="status-label">База данных:</span>
                <span class="status-value status-ok">Подключена</span>
            </div>
            <div class="status-item">
                <span class="status-label">Количество заметок:</span>
                <span class="status-value">{notes_count}</span>
            </div>
            <a href="/">← Назад на главную</a>
        </div>
    </body>
    </html>
    """
    return html


@app.route('/webhook/<token>', methods=['POST'])
def webhook(token):
    """Webhook endpoint для Telegram Bot API"""
    if token != BOT_TOKEN:
        return jsonify({'error': 'Invalid token'}), 403
    
    if request.is_json:
        try:
            update = Update.de_json(request.get_json(), telegram_app.bot)
            # Добавляем update в очередь обработки
            telegram_app.update_queue.put_nowait(update)
        except Exception as e:
            app.logger.error(f"Error processing update: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': str(e)}), 500
    
    return jsonify({'status': 'ok'})


@app.route('/qr')
def qr_route():
    """Генерация QR-кода через веб-интерфейс"""
    data = request.args.get('data', '')
    if not data:
        return jsonify({'error': 'Parameter "data" is required'}), 400
    
    qr_image = generate_qr_code(data)
    return send_file(qr_image, mimetype='image/png')


@app.route('/note/<note_id>')
def view_note_web(note_id):
    """Просмотр заметки через веб"""
    note = Note.query.get(note_id)
    if not note:
        return jsonify({'error': 'Note not found'}), 404
    
    # Парсим фото из JSON
    photos = json.loads(note.photos_json) if note.photos_json else []
    photo_filenames = [os.path.basename(photo) for photo in photos]
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>{{ note.title }}</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
            h1 { color: #333; }
            .note-text { margin: 20px 0; line-height: 1.6; white-space: pre-wrap; }
            .photos { margin: 20px 0; }
            .photos img { max-width: 100%; margin: 10px 0; border-radius: 8px; }
            .meta { color: #666; font-size: 0.9em; }
        </style>
    </head>
    <body>
        <h1>{{ note.title }}</h1>
        <div class="note-text">{{ note.text or '' }}</div>
        <div class="photos">
            {% for photo_filename in photo_filenames %}
                <img src="/uploads/{{ photo_filename }}" alt="Photo">
            {% endfor %}
        </div>
        <div class="meta">ID: {{ note.id }}<br>Created: {{ note.created }}</div>
    </body>
    </html>
    """
    
    return render_template_string(template, note=note, photo_filenames=photo_filenames)


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Отдача загруженных файлов"""
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename))


# Регистрация handlers
telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(CommandHandler("qr", qr_command))
telegram_app.add_handler(CommandHandler("note", note_command))
telegram_app.add_handler(CommandHandler("view", view_command))
telegram_app.add_handler(CallbackQueryHandler(button_callback))
telegram_app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))


if __name__ == '__main__':
    # Создаем БД если нет
    with app.app_context():
        db.create_all()
    
    # Инициализируем и запускаем Telegram Application в фоне
    async def run_telegram():
        await telegram_app.initialize()
        await telegram_app.start()
        print("Telegram bot application started")
        # Держим приложение живым для обработки очереди
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            await telegram_app.stop()
            await telegram_app.shutdown()
    
    # Запускаем Telegram бота в отдельном потоке
    import threading
    def run_bot():
        try:
            asyncio.run(run_telegram())
        except Exception as e:
            print(f"Error running telegram app: {e}")
            import traceback
            traceback.print_exc()
    
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Небольшая задержка для инициализации бота
    import time
    time.sleep(2)
    
    print("Flask server starting on http://0.0.0.0:5000")
    # Запускаем Flask сервер
    app.run(host='0.0.0.0', port=5000, debug=True)

