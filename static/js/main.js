// QR Warehouse Notes - Main JavaScript
const API_BASE = 'http://192.168.1.178:5000'; // адрес твоего Flask-сервера

let html5QrCode = null;

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', function() {
    initializeApp();
});

function initializeApp() {
    initializeFormHandlers();
    initializeCharCounter();
    initializeFilePreview();
    initializeSmoothScroll();
}

// Обработчики формы
function initializeFormHandlers() {
    const form = document.getElementById('note-form');
    if (form) {
        form.addEventListener('submit', handleFormSubmit);
    }
}

// Счетчик символов
function initializeCharCounter() {
    const textarea = document.getElementById('note-text');
    const charCount = document.getElementById('char-count');

    if (textarea && charCount) {
        textarea.addEventListener('input', function() {
            const length = this.value.length;
            charCount.textContent = `${length} / 4096`;

            // Изменяем цвет при приближении к лимиту
            if (length > 4000) {
                charCount.classList.add('warning');
            } else {
                charCount.classList.remove('warning');
            }
        });
    }
}

// Предпросмотр изображений
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

                    // Анимация появления
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
    // Добавляем плавную прокрутку для всех якорных ссылок
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    });
}

// Обработка отправки формы
async function handleFormSubmit(e) {
    e.preventDefault();

    const formData = new FormData();
    const text = document.getElementById('note-text').value;
    const files = document.getElementById('photos').files;

    // Валидация
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

    formData.append('text', text);
    for (let i = 0; i < Math.min(files.length, 5); i++) {
        formData.append('photos', files[i]);
    }

    // Показываем индикатор загрузки
    const submitBtn = document.querySelector('.submit-btn');
    const originalText = submitBtn.innerHTML;
    submitBtn.innerHTML = ' Создание...';
    submitBtn.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/create_note`, {
            method: 'POST',
            body: formData
        });

        if (response.ok) {
            const data = await response.json();
            showQRResult(data.qr_url, data.note_id);

            // Очищаем форму
            document.getElementById('note-form').reset();
            document.getElementById('preview-images').innerHTML = '';
            document.getElementById('char-count').textContent = '0 / 4096';
            document.getElementById('char-count').classList.remove('warning');

            // Прокручиваем к результату
            setTimeout(() => {
                document.getElementById('qr-result-modal').scrollIntoView({
                    behavior: 'smooth'
                });
            }, 100);
        } else {
            const errorData = await response.json();
            showError(errorData.error || 'Ошибка при создании заметки');
        }
    } catch (error) {
        console.error('Form submission error:', error);
        showError('Ошибка сети: ' + error.message);
    } finally {
        // Восстанавливаем кнопку
        submitBtn.innerHTML = originalText;
        submitBtn.disabled = false;
    }
}

// Прокрутка к форме
function scrollToForm() {
    document.getElementById('create-form').scrollIntoView({
        behavior: 'smooth',
        block: 'start'
    });
}

// QR Сканер
function openQRScanner() {
    const modal = document.getElementById('qr-modal');
    modal.style.display = 'block';

    // Фокус на модальное окно
    setTimeout(() => {
        document.getElementById('qr-modal').focus();
    }, 100);

    const scannerDiv = document.getElementById('qr-reader');
    scannerDiv.innerHTML = ''; // Очищаем предыдущий сканер

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

function onScanSuccess(decodedText, decodedResult) {
    showScannerSuccess(
        `QR-код распознан: ${decodedText.substring(0, 50)}${decodedText.length > 50 ? '...' : ''}`
    );

    // Вибрация при успехе (если поддерживается)
    if (navigator.vibrate) {
        navigator.vibrate(200);
    }

    // Отправляем запрос на открытие заметки
    fetch(`${API_BASE}/open_qr`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: decodedText })
    })
        .then(response => {
            if (response.ok) {
                return response.text(); // /open_qr возвращает HTML
            } else {
                return response.json().then(err => Promise.reject(err));
            }
        })
        .then(html => {
            closeQRScanner();

            // Открываем в новом окне для лучшего UX
            const newWindow = window.open('', '_blank');
            newWindow.document.write(html);
            newWindow.document.close();
        })
        .catch(error => {
            console.error('QR scan error:', error);
            showScannerError(error.error || 'Не удалось загрузить заметку');
        });
}

function onScanFailure(error) {
    // Игнорируем ошибки сканирования для лучшего UX
    // console.error('QR scan failure:', error);
}

// Результат QR-кода
function showQRResult(qrUrl, noteId) {
    const modal = document.getElementById('qr-result-modal');
    const imageContainer = document.getElementById('qr-image-container');
    const downloadBtn = document.getElementById('download-btn');

    // Предзагрузка изображения для лучшего UX
    const img = new Image();
    img.onload = function() {
        imageContainer.innerHTML = `<img src="${qrUrl}" alt="QR Code">`;
        modal.style.display = 'block';
    };
    img.onerror = function() {
        imageContainer.innerHTML = 'Ошибка загрузки QR-кода';
        modal.style.display = 'block';
    };
    img.src = qrUrl;

    // Кнопка скачивания
    if (downloadBtn) {
        downloadBtn.onclick = function() {
            const link = document.createElement('a');
            link.href = qrUrl;
            link.download = `note-${noteId}.png`;
            link.click();
        };
    }
}
