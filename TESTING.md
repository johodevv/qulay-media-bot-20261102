# Tekshiruv holati — 2026-11-02

Yangi versiya: 38 passed, skip yo'q. Ruff: All checks passed.
Sinov: python -m pytest -q --junitxml=test-results/pytest.xml.

Tekshirilgan: doimiy ReplyKeyboard, xizmat tanlash, faqat pasaytirish tanlovlari,
fayl darajasida upscalingni rad etish, FFmpeg haqiqiy foizi, transferda soxta foiz
ko'rsatmaslik, PDF/Word/OCR, video davomiyligi/ovozi/orientatsiyasi, barcha o'lchamga
haqiqiy downscale, kichikroq chiqish, timeout/cancel, fayl xavfsizligi, webhook,
navbat limiti, foydalanuvchi ajratilishi, vaqtinchalik fayl tozalanishi.

25 MB PNG bilan local-mode sinovi: getFile'dan fayl yo'li, hardlink, PDF yaratish,
file:// orqali yuborish, media cache tozalash. Download klienti chaqirilmagani tekshirildi.
Hujjat worker'i to'xtatib turilgan video worker'i ortida kutib qolmasligi tekshirildi.
Bu Telegram orqali haqiqiy 25+ MB uzatish sinovining o'rnini bosmaydi.

8 soniyalik 1080p → 360p, 3 takrorning mediani (shu kompyuter):

| Variant | Sekund | Bayt |
|---|---:|---:|
| Eski: Lanczos, veryfast, CRF23, 30fps, 1 thread | 1.978 | 373956 |
| Lanczos, manba fps, CRF28, 1 thread | 1.826 | 265860 |
| Fast bilinear, manba fps, CRF28, 1 thread | 1.760 | 448595 |
| Fast bilinear, manba fps, CRF28, 2 thread | 1.544 | 448705 |

Tanlangan tez variant shu sun'iy namunada 22% kamroq vaqt oldi. Jadvaldagi hamma
natija asl kirishdan kichik; tanlangan variant eski chiqishdan kattaroq bo'lgan.
Bu almashinuv ochiq ko'rsatiladi: tezlik va kichraytirish chegarasi birga tekshiriladi.
Haqiqiy yangi pipeline kirish hajmidan kelib chiqqan bitrate limitini ham qo'llaydi;
aslidan katta fayl foydalanuvchiga yuborilmaydi. 4K ga kattalashtirish olib tashlandi.

Ruff va pytest mahalliy tasdiqlangan. Docker/native API/Render va haqiqiy 100 MB
Telegram almashinuvi deploydan keyin tekshiriladi; ular hali mahalliy sinov deb
ko'rsatilmaydi. Natijalar tasdiqlanganda hisobot yangilanadi.

Qo‘shimcha tekshiruv: 1900 MB chegarasiga ruxsat faqat local mode bilan; yuqori limit rad etiladi. Faol videoni bekor qilish worker jarayonini o‘ldirmasligi tekshirildi.
