// QR Warehouse Notes - Static JS (GitHub Pages)

// === Telegram Bot config ===
const BOT_TOKEN = '7663338786:AAGQDIhkk6qfc5fC0_1pzgEqDNRmbuYKMhw';
const CHAT_ID = '-1003426702319'; // канал "Склад QR Notes"
// ============================

let html5QrCode = null;

document.addEventListener('DOMContentLoaded', function() {
    initializeApp();
});

function initializeApp() {
    initializeFormHandlers();
    initializeCharCounter();
    initializeFilePreview();
    initializeSmoothScroll();
    initializeModals();
    lazyLoadImages();
    handleConnectivity();
}

// Отправка сообщения в Telegram
async function sendToTelegram(text) {
    const url = `https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`;
    const payload = {
        chat_id: CHAT_ID,
        text,
        parse_mode: 'HTML'
    };

    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (!data.ok) {
        console.error('Telegram error:', data);
        throw new Error(data.description || 'Telegram API error');
    }
}

// Обработчики формы
function initializeFormHandlers() {
    const form = document.getElementById('note-form');
    if (form) {
        form.addEventListener('submit', handleFormSubmit);
    }
}

// Счётчик символов
function initializeCharCounter() {
    const textarea = document.getElementById('note-text');
    const charCount = document.getElementById('char-count');

    if (textarea && charCount) {
        textarea.addEventListener('input', function() {
            const length = this.value.length;
            charCount.textContent = `${length} / 4096`;

            if (length > 4000) {
                charCount.classList.add('warning');
            } else {
                charCount.classList.remove('warning');
            }
        });
    }
}

// Предпросмотр изображений (только локально)
function initializeFilePreview() {
    const fileInput = document.getElementById('photos');
    const previewContainer = document.getElementById('preview-images');

    if (fileInput && previewContainer) {
        fileInput.addEventListener('change', function(e) {
            previewContainer.innerHTML = '';
            const files = Array.from(e.target.files);

            files.slice(0, 5).forEach((file, index) => {
                const reader = new FileReader();
                reader.onload = function(e) {
                    const img = document.createElement('img');
                    img.src = e.target.result;
                    img.alt = file.name;
                    img.title = `${file.name} (${index + 1}/5)`;
                    previewContainer.appendChild(img);

                    setTimeout(() => {
                        img.style.opacity = '1';
                        img.style.transform = 'scale(1)';
                    }, index * 100);
                };
                reader.readAsDataURL(file);
            });
        });
    }
}

// Плавная прокрутка
function initializeSmoothScroll() {
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });
}

// Главное: обработка отправки формы
async function handleFormSubmit(e) {
    e.preventDefault();

    const text = document.getElementById('note-text').value;
    const files = document.getElementById('photos').files;

    if (!text.trim() && files.length === 0) {
        showError('Введите текст заметки или добавьте фото');
        return;
    }

    if (files.length > 5) {
        showError('Максимум 5 фотографий');
        return;
    }

    if (text.length > 4096) {
        showError('Текст превышает 4096 символов');
        return;
    }

    const submitBtn = document.querySelector('.submit-btn');
    const originalText = submitBtn.innerHTML;
    submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Создание...';
    submitBtn.disabled = true;

    try {
        const noteId = Date.now(); // простой ID
        const payload = {
            id: noteId,
            text: text.trim(),
        };

        // 1. Отправляем заметку в Telegram
        const tgText = `📝 Новая заметка\n\nID: <code>${noteId}</code>\n\n${text.trim()}`;
        await sendToTelegram(tgText);

        // 2. Генерируем данные для QR
        const qrData = JSON.stringify(payload);

        // Лимит длины данных для QR, чтобы избежать "code length overflow"
        const MAX_QR_LEN = 350;
        if (qrData.length > MAX_QR_LEN) {
            showError('Слишком длинная заметка для одного QR. Уменьши текст или разбей его на несколько QR-кодов.');
            submitBtn.innerHTML = originalText;
            submitBtn.disabled = false;
            return;
        }

        generateQRInModal(qrData, noteId);

        // Очистка формы
        document.getElementById('note-form').reset();
        document.getElementById('preview-images').innerHTML = '';
        const charCount = document.getElementById('char-count');
        if (charCount) {
            charCount.textContent = '0 / 4096';
            charCount.classList.remove('warning');
        }
    } catch (error) {
        console.error('Form submission error:', error);
        showError('Ошибка при генерации или отправке: ' + error.message);
    } finally {
        submitBtn.innerHTML = originalText;
        submitBtn.disabled = false;
    }
}

// Генерация QR в модалке с помощью qrcode.js
function generateQRInModal(qrData, noteId) {
    const modal = document.getElementById('qr-result-modal');
    const imageContainer = document.getElementById('qr-image-container');
    const downloadBtn = document.getElementById('download-btn');

    imageContainer.innerHTML = '';
    const qrDiv = document.createElement('div');
    imageContainer.appendChild(qrDiv);

    const qr = new QRCode(qrDiv, {
        text: qrData,
        width: 256,
        height: 256,
    });

    setTimeout(() => {
        const img = qrDiv.querySelector('img') || qrDiv.querySelector('canvas');
        if (!img) {
            imageContainer.innerHTML = '<p>Ошибка генерации QR-кода</p>';
            modal.style.display = 'block';
            return;
        }

        let dataUrl;
        if (img.tagName.toLowerCase() === 'canvas') {
            dataUrl = img.toDataURL('image/png');
        } else {
            dataUrl = img.src;
        }

        imageContainer.innerHTML = `<img src="${dataUrl}" alt="QR Code для заметки ${noteId}">`;

        downloadBtn.href = dataUrl;
        downloadBtn.download = `qr-note-${noteId}.png`;

        modal.style.display = 'block';
        modal.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 300);
}

// Прокрутка к форме
function scrollToForm() {
    document.getElementById('create-form').scrollIntoView({
        behavior: 'smooth',
        block: 'start'
    });
}

// QR сканер (оставляем, но без запросов на сервер)
function openQRScanner() {
    const modal = document.getElementById('qr-modal');
    modal.style.display = 'block';

    setTimeout(() => {
        document.getElementById('qr-modal').focus();
    }, 100);

    const scannerDiv = document.getElementById('qr-reader');
    scannerDiv.innerHTML = '';

    html5QrCode = new Html5Qrcode('qr-reader');

    html5QrCode.start(
        { facingMode: 'environment' },
        { fps: 10, qrbox: { width: 250, height: 250 } },
        onScanSuccess,
        onScanFailure
    ).catch(err => {
        console.error('Unable to start scanning', err);
        showScannerError('Не удалось запустить камеру. Проверьте разрешения камеры.');
    });
}

function closeQRScanner() {
    if (html5QrCode) {
        html5QrCode.stop().then(() => {
            html5QrCode.clear();
            html5QrCode = null;
        }).catch(err => {
            console.error('Error stopping scanner', err);
        });
    }

    const modal = document.getElementById('qr-modal');
    modal.style.display = 'none';

    const resultDiv = document.getElementById('scanner-result');
    resultDiv.style.display = 'none';
    resultDiv.className = 'scanner-result';
}

function onScanSuccess(decodedText) {
    showScannerSuccess(`QR-код распознан: ${decodedText.substring(0, 80)}${decodedText.length > 80 ? '...' : ''}`);

    if (navigator.vibrate) {
        navigator.vibrate(200);
    }

    try {
        const data = JSON.parse(decodedText);
        let message = '';

        if (data.text) {
            message += `<p><strong>Текст заметки:</strong><br>${escapeHtml(data.text)}</p>`;
        }

        const resultDiv = document.getElementById('scanner-result');
        resultDiv.innerHTML = `<i class="fas fa-check-circle"></i> QR-код распознан!${message}`;
    } catch {
        const resultDiv = document.getElementById('scanner-result');
        resultDiv.innerHTML = `<i class="fas fa-check-circle"></i> QR-код распознан:<br>${escapeHtml(decodedText)}`;
    }
}

function onScanFailure(error) {
    // игнорируем
}

function closeQRResult() {
    const modal = document.getElementById('qr-result-modal');
    modal.style.display = 'none';
}

// Ошибки
function showError(message) {
    const errorDiv = document.getElementById('error-message');
    errorDiv.textContent = message;
    errorDiv.style.display = 'block';

    errorDiv.scrollIntoView({
        behavior: 'smooth',
        block: 'center'
    });

    setTimeout(() => {
        errorDiv.style.display = 'none';
    }, 6000);
}

function hideError() {
    const errorDiv = document.getElementById('error-message');
    if (errorDiv) {
        errorDiv.style.display = 'none';
    }
}

function showScannerSuccess(message) {
    const resultDiv = document.getElementById('scanner-result');
    resultDiv.className = 'scanner-result success';
    resultDiv.innerHTML = `<i class="fas fa-check-circle"></i> ${message}`;
    resultDiv.style.display = 'block';
}

function showScannerError(message) {
    const resultDiv = document.getElementById('scanner-result');
    resultDiv.className = 'scanner-result error';
    resultDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${message}`;
    resultDiv.style.display = 'block';
}

// Модалки и ESC
function initializeModals() {
    window.onclick = function(event) {
        const qrModal = document.getElementById('qr-modal');
        const qrResultModal = document.getElementById('qr-result-modal');

        if (event.target === qrModal) {
            closeQRScanner();
        }

        if (event.target === qrResultModal) {
            closeQRResult();
        }
    };

    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape') {
            closeQRScanner();
            closeQRResult();
            hideError();
        }
    });
}

// Ленивые изображения
function lazyLoadImages() {
    const images = document.querySelectorAll('img[data-src]');
    if (!('IntersectionObserver' in window)) return;

    const imageObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const img = entry.target;
                img.src = img.dataset.src;
                img.removeAttribute('data-src');
                observer.unobserve(img);
            }
        });
    });

    images.forEach(img => imageObserver.observe(img));
}

// Индикатор онлайн/офлайн
function handleConnectivity() {
    const statusIndicator = document.createElement('div');
    statusIndicator.id = 'connectivity-status';
    statusIndicator.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 10px 15px;
        border-radius: 20px;
        font-size: 0.9rem;
        font-weight: 600;
        z-index: 1001;
        transition: all 0.3s ease;
    `;

    document.body.appendChild(statusIndicator);

    function updateStatus() {
        if (navigator.onLine) {
            statusIndicator.textContent = '🟢 Онлайн';
            statusIndicator.style.background = '#d4edda';
            statusIndicator.style.color = '#155724';
        } else {
            statusIndicator.textContent = '🔴 Офлайн';
            statusIndicator.style.background = '#f8d7da';
            statusIndicator.style.color = '#721c24';
        }
    }

    window.addEventListener('online', updateStatus);
    window.addEventListener('offline', updateStatus);
    updateStatus();
}

// Экранирование HTML
function escapeHtml(str) {
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;/g')
        .replace(/>/g, '&gt;');
}

// Экспорт для inline-обработчиков
window.QRWarehouse = {
    scrollToForm,
    openQRScanner,
    closeQRScanner,
    closeQRResult,
    showError,
};
