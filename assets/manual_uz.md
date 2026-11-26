# FFmpeg qobig'i: qo'llanma

## Konvertatsiya
1. Video yoki audio fayllarni yuqoridagi maydonga sudrab tashlang (yoki **Fayl qo'shish**).
2. O'ng tomonda **Andoza**ni tanlang: siqish, Telegram, Reels, YouTube, WhatsApp, format, ovoz, GIF, kesish, o'lcham, burish, ovozsiz, kadrlar.
3. Kerak bo'lsa sifat, balandlik, format va boshqa sozlamalarni o'zgartiring. Jadvalda har bir faylning taxminiy hajmi ko'rinadi.
4. **Boshlanish / Tugash** maydonlari ixtiyoriy: `90`, `1:30` yoki `00:01:30.5` ko'rinishida yozing.
5. **Boshlash** tugmasini bosing. Har bir fayl uchun jarayon foizi ko'rsatiladi, **Bekor qilish** ishni to'xtatadi.
6. Natija manba fayl yonida (yoki tanlangan papkada) `nom_compressed.mp4` kabi nom bilan saqlanadi. Mavjud fayllar ustidan yozilmaydi.

Bir nechta qator tanlansa, faqat ular bajariladi.

## Kodlovchi
**Avto** rejimi kompyuterdagi eng tez H.264 kodlovchini tanlaydi (NVIDIA NVENC, AMD AMF, Intel QSV, Windows Media Foundation yoki OpenH264). Apparat kodlovchi xato bersa, dastur avtomatik ravishda OpenH264 bilan qayta urinadi.

## Kengaytirilgan buyruq
Navbatdan faylni tanlab **Buyruq (kengaytirilgan)** ni bosing. ffmpeg buyrug'ini ko'rish, nusxalash yoki tahrirlab shu faylga qo'llash mumkin.

## Birlashtirish
Fayllarni qo'shing, tartibni **Yuqoriga / Pastga** bilan o'zgartiring. Fayllar bir xil formatda bo'lsa, **Tez** rejimda qayta kodlanmasdan birlashtiriladi.

## Media ma'lumoti
Faylni tashlang: davomiylik, o'lcham, kodeklar, bitreyt va barcha oqimlar ko'rsatiladi.
