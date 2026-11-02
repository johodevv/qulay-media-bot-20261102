# Qulay Media Bot

O'zbekcha Telegram bot: video o'lchami va hajmini pasaytirish, rasm → PDF va Word.

## Foydalanish

/start dan keyin pastda doimiy tugmalar chiqadi: Video sifatini pasaytirish,
Rasm → PDF, Rasm → Word, Jarayon holati va Bekor qilish.
Xizmatni tanlang, fayl yuboring va natijani belgilang. Bir nechta rasmni tartib bilan
yuborib, barcha rasmlar qabul qilingach tayyorlash tugmasini bosing.
Word surat yoki OCR orqali tahrirlanadigan matn ko'rinishida chiqadi.

Video uchun faqat pastroq o'lchamlar yoki faqat hajmni kamaytirish tanlanadi.
Manba o'lchami ma'lum bo'lsa yuqori tanlovlar ko'rsatilmaydi; fayl tekshirilganda
har qanday kattalashtirish rad etiladi. Natija asl fayldan katta bo'lsa yuborilmaydi.

Konvertatsiya foizi FFmpeg qayta ishlagan vaqtga asoslanadi. Telegram'dan olish va
yuborishda foiz o'rniga ⏳ va bosqich nomi ko'rsatiladi. Holat har 4 soniyada yangilanadi.
Video va hujjat uchun alohida navbat bor: uzun video boshqa xodimning PDF'ini kutdirmaydi.

## Windows'da ishga tushirish

Python 3.12+, FFmpeg va FFprobe kerak. .env.example dan .env yarating va BOT_TOKEN yozing.
Mavjud .env ustidan yozmang. Buyruqlar:

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv\Scripts\python.exe -m botapp --check
    .\.venv\Scripts\python.exe -m botapp

start.ps1 ham mavjud sozlamalarni saqlab ishga tushiradi. OCR uchun Tesseract va uzb,
uzb_cyrl, rus, eng til modellari kerak. Windows tayyorlash:

    powershell -ExecutionPolicy Bypass -File .\scripts\setup-ocr.ps1

ALLOWED_USER_IDS bilan botni yopiq sinovga cheklash mumkin. Token va API hash .env da
saqlanadi; Git va Docker kontekstidan chiqarilgan. Odatiy loglarda token chiqarilmaydi.
OCR natijasidagi ism, sana, raqamlarni tekshiring; murakkab sahifa aynan tiklanmaydi.

## 20 MB dan katta fayllar

Oddiy Telegram Bot API yuklab olishni 20 MB bilan cheklaydi. Katta fayllar uchun
Docker ichida rasmiy Telegram Bot API serveri manbadan kompilyatsiya qilinadi va
faqat 127.0.0.1:8081 da ochiladi. Shaxsiy kalitlar: https://my.telegram.org/apps.
Hostingning maxfiy environment variables sozlamalari:

    TELEGRAM_API_ID=...
    TELEGRAM_API_HASH=...
    TELEGRAM_EMBEDDED_API=true
    TELEGRAM_LOCAL_MODE=true
    TELEGRAM_API_URL=http://127.0.0.1:8081
    TELEGRAM_API_DIR=/tmp/telegram-bot-api
    MAX_INPUT_MB=100
    MAX_OUTPUT_MB=100

API va bot bir fayl tizimida bo'ladi. Kirish hardlink bilan ishlanadi; katta fayl
Python xotirasiga to'liq yuklanmaydi va qayta nusxalanmaydi. Natija file:// orqali
API'ga beriladi. Ish yakunida topshiriqning media cache fayli ham o'chiriladi;
API bazasi va boshqa fayllar o'chirilmaydi. Chiqishning maksimal sozlama limiti 1900 MB.

Cloud API'dan ko'chishda eski polling nusxasi to'xtatiladi va cloud logOut metodi
bir marta chaqiriladi. Har startupda takrorlanmaydi. Cloud API'ga qaytish uchun
Telegram 10 daqiqa kutishni talab qiladi.
Rasmiy hujjat: https://github.com/tdlib/telegram-bot-api.

Windows'dagi oddiy ishga tushirishda EMBEDDED_API=false va 20/49 MB limit qoladi.
Bot API dasturi ishlamasdan katta limitni shunchaki o'rnatish yetarli emas.

## Bepul hosting

Dockerfile va render.yaml ichki API, FFmpeg va OCR bilan tayyorlangan. Faqat Free
plan ishlatiladi. BOT_MODE=webhook, WEBHOOK_SECRET tasodifiy maxfiy qiymat;
ommaviy HTTPS manzil RENDER_EXTERNAL_URL dan olinadi. Monitoring endpoint: /health.

Render 15 daqiqa tashqi so'rovsiz qolsa uxlaydi. Ichki API ham o'chgani uchun bu
holatda Telegram xabarining o'zi xizmatni uyg'otishiga tayanib bo'lmaydi: tashqi
monitoring yoki URL'ni ochish kerak. Bir workspace'dagi Free xizmatlar 750 soat/oyni
bo'lishadi. Boshqa Free xizmat bo'lsa, ikkalasini oy bo'yi uzluksiz ishlatish kvotaga
sig'masligi mumkin. Trafik, CPU va xotira limitlari ham saqlanadi.
Rasmiy limitlar: https://render.com/docs/free.

Katta fayllarning tezligi video davomiyligi, kodek, tanlov, internet va serverga
bog'liq. Bir xil tezlik yoki barcha videolarga tayyorlanish vaqti kafolatlanmaydi.

## Fayllar va tekshiruvlar

Navbat va tanlovlar RAMda; restartda tugallanmagan ish qayta yuboriladi. Tanlov
30 daqiqada eskiradi. Odatdagi yakun/xatoda vaqtinchalik fayllar o'chiriladi;
majburiy o'chishda qoldiq qolishi mumkin. Telegram nusxasi alohida saqlanadi.

    .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    .\.venv\Scripts\python.exe -m ruff check botapp tests scripts
    .\.venv\Scripts\python.exe -m pytest -q
    .\.venv\Scripts\python.exe scripts\benchmark-video.py
    .\.venv\Scripts\python.exe scripts\make-demo.py

Benchmark shaxsiy ma'lumotsiz 8 soniyalik sun'iy 1080p videoni 360p ga aylantiradi.
Har variant 3 marta tekshiriladi: o'lcham, davomiylik, ovoz, kichikroq hajm.
Bu katta foydalanuvchi videosi yoki Render tezligining o'lchovi emas.
Natija: test-results/benchmark/results.json. Tafsilotlar: TESTING.md.
