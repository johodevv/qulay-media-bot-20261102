# VPS sinovi va ko'chirish

Bot konteyneri `scripts/vps-start.sh` orqali ishga tushadi. Maxfiy muhit fayli
checkout tashqarisida saqlanadi, huquqi 0600. Parol, bot tokeni va API hash
Git'ga kiritilmaydi. SSH host kaliti birinchi ulanishda qayd etiladi va keyingi
ulanishlar o'sha kalit bilan tekshiriladi.

## 2026-11-03 o'lchovlari

Sinov VPS: Ubuntu 22.04, 4 vCPU, 4 GB RAM, 100 GB disk. FFmpeg ikki oqimda,
OCR `OMP_THREAD_LIMIT=1` bilan. Bir xil 8 soniyalik 1080p/24fps sun'iy video,
har bir variant uch marta; bu Telegram orqali uzatish vaqti emas.

| Natija | Median vaqt | Natija hajmi |
| --- | --- | --- |
| 360p / 24fps | 1.284 s | 449785 bayt |
| 720p / 24fps | 2.157 s | 813959 bayt |
| Tez: 360p / 15fps | 1.228 s | 395484 bayt |

Kirish 15173287 bayt. Har bir chiqishda davomiylik, ovoz va kichikroq hajm
tekshirildi. Bir xil sakkiz qatorli OCR sinovida Render 4.290 s, VPS 0.962 s,
matnlar bir xil. Bu hamma hujjat yoki video uchun 4.5x kafolat emas.
Sun'iy hujjatning joylashuvli Word chiqishi 2.230 s, PDF chiqishi 2.185 s.
Xom natijalar mahalliy ignored `data/` katalogida saqlanadi.

## Ko'chirish

1. Yangi hostda Docker image va `python -m botapp --check` tayyor bo'lsin.
2. Eski hostdagi `POST /diagnostics/handoff`'ga `X-Diagnostics-Token` yuboriladi.
   Endpoint faqat xizmat bo'sh bo'lganda o'tadi. Telegram navbati o'chirilmaydi.
   409 — faol ish/navbat bor, 503 — local API close() tayyor emas yoki xato.
3. Eski Render hosti `BOT_MODE=standby` bilan qayta chiqariladi; yangi host
   `BOT_MODE=polling` bilan ishga tushiriladi. Ikki faol qabul qiluvchi bo'lmasin.
4. VPS /health faqat 127.0.0.1:8080'da, SSH orqali tekshiriladi. Local Bot API ham
   tashqi portga chiqarilmaydi. Konteyner qayta yuklanishda avtomatik ishga tushadi,
   Telegram sessiyasi named volume'da, aylantiriladigan fayllar vaqtinchalik.

## Sinovdan qaytish

VPS bo'sh paytda uning localhost diagnostikasidan handoff chaqiriladi. Polling
qabul qiluvchi to'xtatiladi, navbat tugashi kutiladi va local Bot API close qilinadi.
So'ng VPS konteyneri to'xtatilib, Render'da `BOT_MODE=webhook` tiklanadi va deploy
qilinadi. Telegram tokeni o'zgarmaydi. Render qaytishi uchun /health status=ok,
keyin /start va fayl bilan foydalanuvchi sinovi tekshiriladi.

Foydalanuvchi sinov muddatini uch kun deb bildirdi; aniq tugash soati berilmagan.
Sinov VPS doimiy bepul server emas. Muddat tugashidan oldin qaytish yoki to'lovli
davom ettirish qarori kerak; ushbu yo'riqnoma avtomatik qaytishni sozlamaydi.
