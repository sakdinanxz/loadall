"""LoadAll — โหลดวิดีโอ/เสียงจาก YouTube, TikTok, Instagram, Facebook ฯลฯ ด้วย yt-dlp"""
import ipaddress
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from yt_dlp.utils import match_filter_func

# ---------- ตั้งค่าผ่าน environment variables ----------
BASE_DIR = Path(__file__).parent
WORK_DIR = Path(os.getenv("WORK_DIR", tempfile.gettempdir())) / "loadall"
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "2"))          # จำนวนงานที่โหลดพร้อมกัน
MAX_QUEUE = int(os.getenv("MAX_QUEUE", "20"))             # งานรอคิวสูงสุด
MAX_DURATION = int(os.getenv("MAX_DURATION", "10800"))    # ความยาวคลิปสูงสุด (วินาที) ค่าเริ่มต้น 3 ชม.
JOB_TTL = int(os.getenv("JOB_TTL", "1800"))               # ลบไฟล์หลังกี่วินาที
COOKIES_FILE = os.getenv("COOKIES_FILE")                  # ไฟล์ cookies.txt (ถ้ามี)
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

WORK_DIR.mkdir(parents=True, exist_ok=True)

AUDIO_FORMATS = {
    "mp3": "mp3", "m4a": "m4a", "aac": "aac", "flac": "flac",
    "opus": "opus", "ogg": "vorbis", "wav": "wav",
}
VIDEO_FORMATS = {"144": 144, "240": 240, "360": 360, "480": 480,
                 "720": 720, "1080": 1080, "1440": 1440, "2160": 2160}

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS)

app = FastAPI(title="LoadAll")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_methods=["GET", "POST"], allow_headers=["*"])


class JobRequest(BaseModel):
    url: str
    format: str = "mp3"


# ---------- ตัวช่วย ----------
def check_url(url: str) -> str:
    url = url.strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise HTTPException(400, "ลิงก์ไม่ถูกต้อง ต้องขึ้นต้นด้วย http:// หรือ https://")
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror:
        raise HTTPException(400, "ไม่พบโดเมนนี้")
    # กันไม่ให้ใช้เซิร์ฟเวอร์ไปยิงเครือข่ายภายใน (SSRF)
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise HTTPException(400, "ไม่อนุญาตลิงก์นี้")
    return url


def friendly_error(msg: str) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg).replace("ERROR: ", "").strip()
    low = msg.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return "YouTube บล็อกเซิร์ฟเวอร์ชั่วคราว (ต้องตั้งค่า cookies ดู README)"
    if "unsupported url" in low:
        return "ยังไม่รองรับลิงก์นี้"
    if "private" in low or "login" in low or "log in" in low:
        return "คลิปนี้เป็นแบบส่วนตัวหรือต้องล็อกอินก่อน"
    if "unavailable" in low or "removed" in low:
        return "คลิปนี้ถูกลบหรือไม่สามารถเข้าถึงได้"
    if "geo" in low and "restrict" in low:
        return "คลิปนี้ถูกจำกัดตามประเทศ"
    return msg[:300] or "เกิดข้อผิดพลาด"


def build_opts(fmt: str, outdir: Path, job: dict) -> dict:
    def progress_hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            job["stage"] = "downloading"
            if total:
                job["progress"] = round(d.get("downloaded_bytes", 0) / total * 100, 1)
        elif d["status"] == "finished":
            job["progress"] = 100

    def pp_hook(d):
        if d["status"] == "started":
            job["stage"] = "processing"

    opts = {
        "outtmpl": str(outdir / "%(title).80B [%(id)s].%(ext)s"),
        "windowsfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [pp_hook],
        "match_filter": match_filter_func(f"duration <=? {MAX_DURATION}"),
    }
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE

    if fmt in AUDIO_FORMATS:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": AUDIO_FORMATS[fmt],
            "preferredquality": "192",
        }]
    else:
        h = VIDEO_FORMATS[fmt]
        opts["format"] = "bv*+ba/b"
        # เลือกความละเอียดไม่เกินที่ขอ และเน้น H.264/AAC ให้เปิดได้ทุกเครื่อง
        opts["format_sort"] = [f"res:{h}", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"]
        opts["merge_output_format"] = "mp4"
        opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
    return opts


def run_job(job_id: str):
    job = JOBS[job_id]
    outdir = WORK_DIR / job_id
    outdir.mkdir(parents=True, exist_ok=True)
    job["status"] = "running"
    job["stage"] = "starting"
    try:
        with yt_dlp.YoutubeDL(build_opts(job["format"], outdir, job)) as ydl:
            info = ydl.extract_info(job["url"], download=True)
        if info and info.get("entries"):
            info = next((e for e in info["entries"] if e), info)
        if info:
            job["title"] = info.get("title")
            job["thumbnail"] = info.get("thumbnail")
            job["duration"] = info.get("duration")
            job["source"] = info.get("extractor_key")

        files = [f for f in outdir.iterdir()
                 if f.is_file() and not f.name.endswith((".part", ".ytdl", ".temp"))]
        if not files:
            raise RuntimeError(f"ไม่พบไฟล์ — คลิปอาจยาวเกิน {MAX_DURATION // 60} นาที หรือไม่มีสื่อให้โหลด")
        f = max(files, key=lambda x: x.stat().st_size)
        job.update(status="done", stage="done", progress=100,
                   file=str(f), filename=f.name, size=f.stat().st_size)
    except yt_dlp.utils.DownloadError as e:
        job.update(status="error", error=friendly_error(str(e)))
    except Exception as e:  # noqa: BLE001
        job.update(status="error", error=friendly_error(str(e)))
    finally:
        job["finished_at"] = time.time()


def cleanup_loop():
    while True:
        time.sleep(300)
        now = time.time()
        with JOBS_LOCK:
            old = [jid for jid, j in JOBS.items()
                   if now - j.get("finished_at", j["created_at"]) > JOB_TTL
                   and j["status"] in ("done", "error")]
            for jid in old:
                shutil.rmtree(WORK_DIR / jid, ignore_errors=True)
                JOBS.pop(jid, None)


threading.Thread(target=cleanup_loop, daemon=True).start()


# ---------- API ----------
@app.get("/api/health")
def health():
    return {"ok": True, "yt_dlp": yt_dlp.version.__version__}


@app.post("/api/jobs")
def create_job(req: JobRequest):
    fmt = req.format.lower()
    if fmt not in AUDIO_FORMATS and fmt not in VIDEO_FORMATS:
        raise HTTPException(400, "รูปแบบไฟล์ไม่ถูกต้อง")
    url = check_url(req.url)
    with JOBS_LOCK:
        active = sum(1 for j in JOBS.values() if j["status"] in ("queued", "running"))
        if active >= MAX_QUEUE:
            raise HTTPException(429, "คิวเต็ม ลองใหม่อีกครั้งในอีกสักครู่")
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"id": job_id, "url": url, "format": fmt, "status": "queued",
                        "stage": "queued", "progress": 0, "created_at": time.time()}
    EXECUTOR.submit(run_job, job_id)
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "ไม่พบงานนี้ (อาจหมดอายุแล้ว)")
    keys = ("id", "status", "stage", "progress", "title", "thumbnail",
            "duration", "source", "filename", "size", "error", "format")
    return {k: job.get(k) for k in keys}


@app.get("/api/jobs/{job_id}/file")
def get_file(job_id: str):
    job = JOBS.get(job_id)
    if not job or job["status"] != "done" or not os.path.exists(job.get("file", "")):
        raise HTTPException(404, "ไม่พบไฟล์ (อาจหมดอายุแล้ว)")
    return FileResponse(job["file"], filename=job["filename"],
                        media_type="application/octet-stream")


# หน้าเว็บ (ต้องอยู่ท้ายสุด)
app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
