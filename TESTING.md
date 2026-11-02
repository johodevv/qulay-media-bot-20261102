# Tekshiruv holati — 2026-11-02

Yangi versiya: 49 passed, skip yo'q. Ruff: All checks passed.
Sinov: python -m pytest -q --junitxml=test-results/pytest.xml.

## 0.4.0 Word va PDF xizmatlari

Menyu asosiy tanlovni bitta tayyorlash tugmasiga qisqartiradi. «Boshqa usullar»
va «Orqaga» rasmlarni yo'qotmasdan ishlaydi. Eski video xizmat tugmasi ham taniladi.
Word (.docx) yoki PDF yuborilganda tanlangan aylantirish avtomatik navbatga o'tadi.

Word → PDF LibreOffice orqali; PDF → Word PyMuPDF matn koordinatalari va Word
matn bloklari orqali ishlaydi. Rasm va chiziqlar fon qatlamida saqlanadi.
Skanerlangan PDF matni bu yo'lda OCR qilinmaydi. Hujjat limiti 30 MB / 50 PDF sahifa;
katta video limiti 1900 MB qoladi. Har konvertatsiyada LibreOffice dasturining
birinchi ishga tushish sozlamalarini takrorlamaslik uchun faqat profil qayta ishlatiladi;
kirish va chiqish fayllari topshiriq vaqtinchalik katalogida saqlanadi.

Yangi sinovlar: haqiqiy DOCX → PDF → DOCX, ikki sahifa va raqamlar, rangli katakdagi
yozuvlar, buzilgan Word, tashqi obyekt, parolli PDF, sahifa limiti, to'g'ri/noto'g'ri
yo'nalish, qo'shimcha tugmasiz avtomatik navbat. Docker build ichida kichik
Word → PDF → Word smoke sinovi ham bor. PDF → Word namunasi va hujjat maketlari
render_docx.py orqali render qilinib ko'rildi. Dependency audit: ma'lum zaiflik topilmadi.
Bu serverdagi haqiqiy Telegram fayli uchun tezlik kafolati emas.

Chek va to'lov topshirig'i foydalanuvchi so'roviga ko'ra ushbu versiyaga qo'shilmadi.

## Joylashuvni saqlaydigan OCR eksporti

0.3.0: PDF ichida ko'rinadigan matn obyektlari, Word ichida tahrirlanadigan matn
bloklari va sahifa koordinatalari bor. Ustunlar oddiy matn ro'yxatiga aylantirilmaydi.
Asl rasmning tanilgan yozuvlari fon tasviridan olib tashlanadi; logo va rangli
bezaklar tasvirligicha qoladi. Ishonchi past yoki mos bo'lmagan geometriyali qismlar
asl tasvir holida saqlanadi. OCR xatosi ehtimoli va shrift farqi qoladi.

Yangi testlar sun'iy hujjat asosida: PDF'da ko'rinadigan matn va ustun pozitsiyasi,
Word'da matn bloklari va ikki sahifa, bo'sh rasmda aniq xato, menyu variantlari.
Shaxsiy hujjatlar test fixtures yoki Git omboriga kiritilmadi.

Haqiqiy namuna bilan mahalliy PDF 2.36 s, Word 1.76 s (bir martalik kuzatuv,
Telegram transferisiz; server tezligi yoki median natija sifatida talqin qilinmaydi).
PDF Poppler orqali, Word paketlangan render_docx.py va loyiha ichiga chiqarilgan
LibreOffice orqali PNG'ga aylantirildi. Bir sahifali namuna va turli o'lchamdagi
ikki sahifali sun'iy hujjat vizual tekshirildi. Natija OCR aniqligini kafolatlamaydi.

Runtime dependency audit: no known vulnerabilities found. ReportLab bilan PDF
yaratiladi; og'ir AI/ONNX paketlari runtime requirements'ga kiritilmagan.
Olish, aylantirish va yuborish vaqtlari alohida loglanadi. Server rejasi Free qoladi.

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

Ruff va pytest mahalliy tasdiqlangan. Haqiqiy katta faylning Telegram orqali to‘liq almashinuvi foydalanuvchi sinovini kutadi.

Qo‘shimcha tekshiruv: 1900 MB chegarasiga ruxsat faqat local mode bilan; yuqori limit rad etiladi. Faol videoni bekor qilish worker jarayonini o‘ldirmasligi tekshirildi.


## Joylashtirish tasdig'i

Render Free, Frankfurt: https://universal-yordamchi-bot.onrender.com.
Kod: fcb1af9cb85bf8547ff3845036f5b6eb65a93ce7, deploy dep-dbk97vm7bikc739i8ocg — live.
/health: HTTP 200, status=ok, max_input_mb=1900, local_api=true, version=0.2.0.
Rasmiy Bot API binari manbadan muvaffaqiyatli qurildi; ishga tushish logi webhook va
OCR=True ni tasdiqladi. Bu foydalanuvchining 1900 MB fayli bilan sinov bo'lganini anglatmaydi.

Eski cloud Bot API logOut bilan ko'chirildi; kompyuterda shu botning polling jarayoni qolmagan.
Render muhiti: MAX_INPUT_MB=1900, MAX_OUTPUT_MB=1900, JOB_TIMEOUT_SECONDS=1800.
Boshqa Render xizmatlari o'zgartirilmadi.

Foydalanuvchi yuborgan skrinshotda Domain Monitor 5 daqiqalik HTTP monitor va Up
holatini ko'rsatdi. URL skrinshotda ochiq ko'rinmagan; aynan /health ekanini mustaqil
tekshirish imkoni bo'lmadi. Ushbu servisning forma sahifasi bepul monitoring 14 kundan
keyin to'xtashini ko'rsatgan. Doimiy UptimeRobot monitori hali tasdiqlanmagan.
Brauzer boshqaruvi URL xavfsizlik tekshiruvi tomonidan to'xtatildi; undan keyin
brauzerga avtomatik input berilmadi.

## Yakuniy o'zini baholash

agent-self-evaluation: aniqlik 4/5 (health va 38 test; to'liq 1900 MB trafik sinalmagan),
to'liqlik 4/5 (asosiy imkoniyatlar va hosting tayyor; uzoq muddatli monitoring kerak),
tushunarlilik 4/5 (o'zbekcha menyu; haqiqiy foydalanuvchi qulaylik sinovi qolgan),
amalga tayyorlik 4/5 (bot live; free kvota va tashqi monitor cheklovlari),
ixchamlik 4/5 (asosiy oqim uch xizmat; texnik qo'llanma batafsil). O'rtacha 4.0/5.
Keyingi tekshiruv: haqiqiy katta video va rasmni bot orqali yuborish; doimiy bepul
monitoringni yakunlash. Foydalanuvchi bahoga qo'shilishi kutiladi: tayyor natija va
uning amaliy chegaralari dalillar bilan ajratilgan.
