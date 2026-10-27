# LoadAll — โหลดวิดีโอทุกแพลตฟอร์ม

เว็บสำหรับโหลดวิดีโอและเสียงจาก **YouTube, TikTok, Instagram, Facebook, X, Vimeo** และเว็บอื่นกว่า 1,000 เว็บ
ใช้ [yt-dlp](https://github.com/yt-dlp/yt-dlp) เป็นตัวโหลด และ ffmpeg เป็นตัวแปลงไฟล์

- เสียง: MP3, M4A, AAC, FLAC, OPUS, OGG, WAV
- วิดีโอ: MP4 ตั้งแต่ 144p ถึง 4K (ถ้าคลิปไม่มีความละเอียดที่เลือก ระบบจะเลือกค่าที่ใกล้ที่สุดให้)
- แถบความคืบหน้า แสดงชื่อคลิปและภาพปก
- ลบไฟล์ออกจากเซิร์ฟเวอร์อัตโนมัติหลัง 30 นาที

> ⚠️ **ทำไมใช้ GitHub Pages อย่างเดียวไม่ได้:** การดึงวิดีโอจากแพลตฟอร์มเหล่านี้ต้องทำบนเซิร์ฟเวอร์
> (เบราว์เซอร์ติด CORS และระบบป้องกันของแต่ละเจ้า) ให้เก็บโค้ดไว้บน GitHub แล้ว deploy ไปที่โฮสต์ที่รัน Docker ได้

## โครงสร้าง
```
app.py              # API (FastAPI + yt-dlp)
static/index.html   # หน้าเว็บ
Dockerfile          # ติดตั้ง Python, ffmpeg, Deno
render.yaml         # ตั้งค่าสำหรับ Render
```

## Deploy ฟรีบน Render
1. push โปรเจกต์นี้ขึ้น GitHub
2. ไปที่ [render.com](https://render.com) → **New → Blueprint** → เลือก repo นี้ (Render อ่าน `render.yaml` ให้เอง)
   หรือใช้ **New → Web Service** → เลือก repo → Runtime: **Docker**
3. รอ build เสร็จ จะได้ลิงก์ เช่น `https://loadall.onrender.com`

แพ็กเกจฟรีของ Render จะพักเครื่องเมื่อไม่มีคนใช้ เปิดครั้งแรกหลังพักต้องรอประมาณ 1 นาที
ถ้าต้องการให้เร็วและเสถียรกว่านี้ ใช้ VPS (DigitalOcean, Vultr ฯลฯ) แล้วรัน Docker คำสั่งเดียวกับข้างล่าง

## รันบนเครื่องตัวเอง
**แบบ Docker**
```bash
docker build -t loadall .
docker run -p 8000:8000 loadall
```
**แบบ Python** (ต้องติดตั้ง ffmpeg และ [Deno](https://deno.com) ก่อน)
```bash
pip install -r requirements.txt
uvicorn app:app --reload
```
แล้วเปิด http://localhost:8000

## (ทางเลือก) หน้าเว็บบน GitHub Pages + API บน Render
1. deploy API บน Render ตามข้างบน
2. ใน `static/index.html` แก้บรรทัด `const API_BASE = '';` เป็น URL ของ Render
3. เปิด GitHub Pages ให้โฟลเดอร์ `static` (หรือคัดลอก `index.html` ไปไว้ใน repo ของ Pages)
4. ใน Render ตั้ง env `ALLOWED_ORIGINS=https://<username>.github.io`

## ตั้งค่าผ่าน Environment Variables
| ชื่อ | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `MAX_WORKERS` | 2 | จำนวนงานที่โหลดพร้อมกัน |
| `MAX_QUEUE` | 20 | จำนวนงานในคิวสูงสุด |
| `MAX_DURATION` | 10800 | ความยาวคลิปสูงสุด (วินาที) |
| `JOB_TTL` | 1800 | ลบไฟล์หลังกี่วินาที |
| `COOKIES_FILE` | – | path ของไฟล์ cookies.txt |
| `ALLOWED_ORIGINS` | `*` | โดเมนที่เรียก API ได้ (คั่นด้วย `,`) |

## ปัญหาที่พบบ่อย
**YouTube ขึ้นว่า “Sign in to confirm you’re not a bot”**
YouTube มักบล็อก IP ของ data center (รวมถึง Render) วิธีแก้คือ export cookies จากเบราว์เซอร์ที่ล็อกอิน YouTube
(ใช้ส่วนขยาย “Get cookies.txt LOCALLY”) แล้วใช้ **Secret File** ของ Render ชื่อ `cookies.txt` และตั้ง
`COOKIES_FILE=/etc/secrets/cookies.txt` — ควรใช้บัญชีสำรอง ไม่ใช่บัญชีหลัก และ**ห้าม commit cookies.txt ขึ้น GitHub**

**จู่ ๆ บางเว็บโหลดไม่ได้**
แพลตฟอร์มเปลี่ยนระบบบ่อย ให้ redeploy เพื่อดึง yt-dlp เวอร์ชันล่าสุด (บน Render กด **Manual Deploy → Clear build cache & deploy**)

## ข้อควรระวัง
โปรดใช้โหลดเฉพาะคอนเทนต์ของคุณเองหรือที่ได้รับอนุญาต การโหลดคอนเทนต์ของผู้อื่นอาจขัดกับเงื่อนไขการใช้งานของแพลตฟอร์มและกฎหมายลิขสิทธิ์
