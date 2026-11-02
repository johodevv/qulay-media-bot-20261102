# Tekshiruv holati — 2026-11-02

## 0.4.3: yonboshlagan hujjat va tez video rejimi

Telefon EXIF belgilamagan 90/180/270 gradusli matn OSD orqali to'g'rilanadi.
Neytral qog'ozdagi yoritish farqi tekislanadi va fon oqartiriladi; rangli muhr
va tasvir ranglari saqlanadi. Yetarli matn tanilmagan holat muvaffaqiyat deb
qaytarilmaydi. Jadval chizig'i bilan qo'shilib ketgan baland OCR bloklari ulkan
harflarga aylantirilmaydi. Word menyusida oddiy, oq sahifadagi tahrirlanadigan
matn alohida ko'rinadi. Joylashuvli usulda tanilmagan matn rasm bo'lib qoladi;
bu usul barcha kataklar native Word jadvali bo'lishini kafolatlamaydi.

Mahalliy tekshiruv: 8 s, 1080p/24fps sun'iy video, 3 takror.
`python scripts/benchmark-decode.py --confirm-fast`: odatiy 720p/24fps median
5.905 s, 812879 bayt; tez 360p/15fps median 1.816 s, 394404 bayt. Ovoz, davomiylik,
o'lcham va kirishdan kichikroq hajm tekshirildi. Tez rejim kamroq piksel/kadr beradi.
Bu Render va katta Telegram fayli uchun vaqt kafolati emas. Ultrafast/superfast
variantlari hajmni kattalashtirgani uchun standart sozlamaga o'tkazilmadi.
Xom o'lchovlar ignored test-results katalogida.

To'liq sinovdagi 64 test o'tdi; bitta test double yangi fast argumentini qabul
qilmagani uchun xato berdi. Test double tuzatilgach hujjat/video/UI/worker bo'yicha
tegishli qayta tekshiruvlar o'tdi. Ruff va diff tekshiruvi toza. Rasmlar, foydalanuvchi
PDFi va natija fayllari Git'ga qo'shilmaydi.

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

## 0.4.1 tezlik tekshiruvi

2026-11-02 server loglari: pdf-edit 58.75 s hisoblash / 0.23 s yuborish;
word-layout 50.20 s / 0.12 s; video-144 200.91 s / 0.61 s;
video-720 488.20 s / 0.97 s. CPU limiti 0.15, RAM 512 MB.
Bu turli fayllar bo'lib, ular o'zaro benchmark sifatida solishtirilmaydi.

scripts/benchmark-speed.py: 4 video variant x 3 takror. Hozirgi veryfast/2 oqim
mediani 1.336 s / 448705 bayt; superfast/1 1.688 s / 1003151 bayt;
ultrafast/1 1.360 s / 1777616 bayt. Tez presetlar MB maqsadini yomonlashtirgani
va ishonchli tezlik yutug'i bermagani sabab video encoder o'zgartirilmadi.

Yozuv rangini aniqlash: 400 so'z kesmasi x 3 takror median 3.860 s → 0.764 s,
rangli/oq-qora namunalarda maksimal kanal farqi 0. Butun OCR → Word pipeline
3.445 s → 3.384 s: farq shovqin oralig'ida; umumiy 5x tezlashish da'vo qilinmaydi.
Docker OMP_THREAD_LIMIT=1 bilan ko'p oqimli OCR ortiqcha xarajatini cheklaydi.
Serverning aynan 0.15 CPU sharoitida sobit rasm bilan taqqoslash uchun maxfiy
diagnostics benchmarki bor; foydalanuvchi topshirig'i paydo bo'lsa to'xtaydi.

Tayyor Telegram natijasi faqat o'sha foydalanuvchi, aynan bir xil fayl ID'lari
va xizmat uchun 30 daqiqa xotirada qayta ishlatiladi. Kirish fayli olinmagani va
qayta konvertatsiya bo'lmagani, boshqa foydalanuvchiga o'tmasligi, muddati o'tishi,
Telegram ID bekor bo'lsa qayta konvertatsiya, tarmoq timeoutida CPU sarflamaslik
tekshirildi. Diagnostics autentifikatsiyasiz 403 qaytaradi; foydalanuvchi/fayl ID
chiqarmaydi. Maxfiy kalit va shaxsiy hujjatlar Git'ga kiritilmadi.

0.4.1 live: health HTTP 200 / version=0.4.1. Serverdagi bir xil 8 qatorli sun'iy
rasmda 4 oqim → 1 oqim: 28.912 s → 4.517 s va ikkinchi takrorda 35.000 s →
4.991 s. Har ikki holatda tanilgan matn aynan bir xil. Mediana 31.956 s → 4.754 s,
ya'ni ushbu OCR namunasi 6.72x tezroq. Bu video yoki Telegram almashinuvini
6.72x tezlatish da'vosi emas. Oldingi barcha 57 test va so'nggi cache-timeout
tuzatishiga tegishli 20 test o'tdi. Server rejasi Free, CPU chegarasi o'zgarmadi.

Monitoring: Chrome'da Domain Monitor tablari kuzatildi; UptimeRobot monitori
tasdiqlanmadi. Domain Monitor shartlari 14 kundan keyin monitorni to'xtatishini
aytadi. UptimeRobot hisobiga kirish foydalanuvchini kutmoqda.

## UptimeRobot va yig'iladigan menyu

UptimeRobot monitor 804520050 yaratildi: HTTPS /health, har 300 soniya,
standart bepul sozlamalar. UI'da Current status=Up va Checked every 5m tasdiqlandi.
Dashboard: https://dashboard.uptimerobot.com/monitors/804520050.
Boshqa uchta monitor o'zgartirilmadi. Render Free soat va trafik kvotalari qoladi.

0.4.2 kodi: ReplyKeyboardMarkup is_persistent=false, one_time_keyboard=false.
Telegram klienti klaviatura belgisi bilan menyuni yashirib-ochishi mumkin.
Belgining dizayni Telegram tomonidan belgilanadi. /menu va /menyu tanlov/rasmlarni
tozalamasdan menyuni qaytaradi. Tegishli 16 test, keyingi 6 UI testi o'tdi.
Kod d7b2ccb GitHub'ga yuborilgan. Render Chrome'da login talab qildi;
ushbu menyu versiyasi hali serverga chiqarilmadi. Joriy runtime 0.4.1 qolmoqda.

Keyingi tasdiq: foydalanuvchi Render'ga kirgach, e9b3854 versiyasi mavjud Free
xizmatga Dashboard orqali joylandi. Deploy dep-dbkbvkon74is73c1bnf0 Live;
Office smoke o'tdi, webhook OCR=True ishga tushdi; /health HTTP 200 version=0.4.2.
Yashirib-ochish sozlamasi va /menu buyrug'i shu versiyada mavjud. UptimeRobot
804520050 monitori saqlangan. Belgining ko'rinishi foydalanuvchi Telegram klientiga bog'liq.

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
