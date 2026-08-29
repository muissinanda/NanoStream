import os
import psutil
import subprocess
import requests
import json
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

app = FastAPI(title="NanoStreamer")

# Setup templates
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_dir)

SESSION_TOKEN = "nanostreamer_session_token"
VALID_USERNAME = "muis24"
VALID_PASSWORD = "master123"
DB_FILE = "channels.json"

# Global state for stream processes
# mapping path (e.g. 'hbo') to subprocess
processes = {}

def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

def check_auth(request: Request):
    return request.cookies.get("session") == SESSION_TOKEN

import asyncio

@app.on_event("startup")
async def startup_event():
    # Auto-start channels that were marked active
    db = load_db()
    for path, ch in db.items():
        if ch.get("is_active", False):
            start_ffmpeg(path, ch["source"])
            
    # Jalankan background monitor untuk menjaga stream tetap hidup (Auto-Restart)
    asyncio.create_task(monitor_streams())

async def monitor_streams():
    """
    Fungsi ini akan mengecek setiap 3 detik.
    Jika ada stream yang seharusnya aktif (is_active=True) tapi FFmpeg-nya mati/crash
    (karena putus jaringan, client diskonek, dll), maka akan otomatis di-restart!
    """
    while True:
        await asyncio.sleep(3)
        try:
            db = load_db()
            for path, ch in db.items():
                if ch.get("is_active", False):
                    proc = processes.get(path)
                    # Jika proses tidak ada atau sudah mati (poll() is not None)
                    if proc is None or proc.poll() is not None:
                        start_ffmpeg(path, ch["source"])
        except Exception:
            pass

def start_ffmpeg(path, source_url):
    global processes
    if path in processes and processes[path].poll() is None:
        processes[path].terminate()
        processes[path].wait()
    
    # Mengubah audio menjadi AAC (transcode audio) memakan CPU <1% namun menjamin 100% kompatibel dengan HLS/VLC. Video tetap copy.
    cmd = [
        "ffmpeg", "-y", 
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-timeout", "10000000",
        "-user_agent", "IPTVSmartersPro",
        "-i", source_url,
        "-c:v", "copy", "-c:a", "copy",
        "-max_muxing_queue_size", "1024",
        "-f", "mpegts", f"srt://127.0.0.1:8890?streamid=publish:{path}"
    ]
    
    # Menyimpan log untuk proses ini (agar mudah di debug jika gagal)
    log_file = open(f"/opt/nanostreamer/log_{path}.txt", "w")
    processes[path] = subprocess.Popen(cmd, stdout=log_file, stderr=log_file)

def stop_ffmpeg(path):
    global processes
    if path in processes:
        if processes[path].poll() is None:
            processes[path].terminate()
            processes[path].wait()
        del processes[path]

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    if check_auth(request):
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == VALID_USERNAME and password == VALID_PASSWORD:
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(key="session", value=SESSION_TOKEN, max_age=86400)
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "Username atau Password salah"})

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/")
    response.delete_cookie("session")
    return response

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not check_auth(request):
        return RedirectResponse(url="/")
    
    db = load_db()
    
    # Sort channel secara alfabetis berdasarkan nama channel (A-Z)
    db = dict(sorted(db.items(), key=lambda x: x[1].get("name", "").lower()))
    
    # Mengambil IP atau Domain otomatis dari URL yang diakses oleh pengguna
    local_ip = request.url.hostname
    if not local_ip:
        local_ip = "127.0.0.1"

    context = {
        "request": request,
        "channels": db,
        "local_ip": local_ip
    }
    return templates.TemplateResponse("dashboard.html", context)

@app.post("/api/channel/add")
async def add_channel(request: Request, name: str = Form(...), path: str = Form(...), source_url: str = Form(...)):
    if not check_auth(request):
        return RedirectResponse(url="/")
    
    # clean path
    path = path.replace("/", "").replace(" ", "_").lower()
    if not path:
        path = "stream"
        
    db = load_db()
    db[path] = {
        "name": name,
        "path": path,
        "source": source_url,
        "is_active": False
    }
    save_db(db)
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/api/channel/start/{path}")
async def start_channel(request: Request, path: str):
    if not check_auth(request):
        return RedirectResponse(url="/")
    
    db = load_db()
    if path in db:
        start_ffmpeg(path, db[path]["source"])
        db[path]["is_active"] = True
        save_db(db)
        
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/api/channel/stop/{path}")
async def stop_channel(request: Request, path: str):
    if not check_auth(request):
        return RedirectResponse(url="/")
    
    stop_ffmpeg(path)
    db = load_db()
    if path in db:
        db[path]["is_active"] = False
        save_db(db)
        
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/api/channel/delete/{path}")
async def delete_channel(request: Request, path: str):
    if not check_auth(request):
        return RedirectResponse(url="/")
    
    stop_ffmpeg(path)
    db = load_db()
    if path in db:
        del db[path]
        save_db(db)
        
    return RedirectResponse(url="/dashboard", status_code=303)

@app.post("/api/channel/edit")
async def edit_channel(request: Request, old_path: str = Form(...), name: str = Form(...), path: str = Form(...), source_url: str = Form(...)):
    if not check_auth(request):
        return RedirectResponse(url="/")
        
    db = load_db()
    if old_path in db:
        # Jika path/ID berubah, kita harus menghapus stream lama dan memindahkannya
        was_active = db[old_path]["is_active"]
        if was_active:
            stop_ffmpeg(old_path)
            
        del db[old_path]
        
        # Bersihkan path baru
        path = path.replace("/", "").replace(" ", "_").lower()
        if not path:
            path = "stream"
            
        db[path] = {
            "name": name,
            "path": path,
            "source": source_url,
            "is_active": False
        }
        
        # Jika sebelumnya nyala, otomatis nyalakan lagi dengan URL baru
        if was_active:
            start_ffmpeg(path, source_url)
            db[path]["is_active"] = True
            
        save_db(db)
        
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/api/metrics")
async def metrics(request: Request):
    if not check_auth(request):
        return {"error": "Unauthorized"}
    
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    
    # Fetch MediaMTX API for Viewers per path
    viewers_per_path = {}
    total_viewers = 0
    try:
        r = requests.get("http://127.0.0.1:9997/v3/paths/list", timeout=1)
        if r.status_code == 200:
            data = r.json()
            for item in data.get("items", []):
                p_name = item.get("name")
                # MediaMTX 'readers' adalah sebuah list/array of objects.
                # Kita harus menghitung panjang list tersebut untuk mendapat jumlah penonton.
                readers_list = item.get("readers", [])
                readers_count = len(readers_list) if isinstance(readers_list, list) else 0
                
                viewers_per_path[p_name] = readers_count
                total_viewers += readers_count
    except:
        pass

    global processes
    channel_status = {}
    for path, proc in processes.items():
        is_running = proc.poll() is None
        channel_status[path] = {
            "is_running": is_running,
            "viewers": viewers_per_path.get(path, 0)
        }

    return {
        "cpu": cpu,
        "ram": ram,
        "total_viewers": total_viewers,
        "channels": channel_status
    }

# --- HLS REVERSE PROXY ---
# Berfungsi untuk mem-bypass blokir port Cloudflare Tunnel
from fastapi.responses import StreamingResponse, Response

@app.get("/hls/{path}/{filename}")
def proxy_hls(path: str, filename: str, request: Request):
    # [OBFUSCATION] Jika client meminta .bin, kita rubah kembali ke .ts untuk MediaMTX
    real_filename = filename
    if filename.endswith(".bin"):
        real_filename = filename.replace(".bin", ".ts")
        
    url = f"http://127.0.0.1:8888/{path}/{real_filename}"
    query = request.url.query
    if query:
        url += f"?{query}"
        
    try:
        r = requests.get(url, stream=True, timeout=30)
        
        # [OBFUSCATION] Manipulasi Playlist .m3u8 agar Cloudflare mengira ini file text/json biasa
        if real_filename.endswith(".m3u8"):
            content = r.text
            # Ubah semua akhiran .ts di dalam playlist menjadi .bin
            content = content.replace(".ts", ".bin")
            return Response(
                content=content, 
                status_code=r.status_code,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                    "Content-Type": "text/plain" # Menipu Cloudflare (Bukan video)
                }
            )
        
        # Streaming untuk file video (sekarang berekstensi .bin)
        def iterfile():
            try:
                # Chunk size dibesarkan ke 128KB agar transfer rate maksimal
                # Mencegah bottleneck dan micro-buffering di sisi Python/FastAPI
                for chunk in r.iter_content(chunk_size=131072):
                    if chunk:
                        yield chunk
            finally:
                r.close()
                
        # [OBFUSCATION] Manipulasi Header Video agar dianggap sebagai file sistem acak, bukan video
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Content-Type": "application/octet-stream" # Menipu Cloudflare (Bukan video)
        }
            
        return StreamingResponse(iterfile(), headers=headers, status_code=r.status_code)
    except Exception as e:
        return Response(content=f"Upstream Timeout/Error: {str(e)}", status_code=504)

@app.get("/api/logs/{path}")
def get_logs(path: str):
    import os
    log_path = f"/opt/nanostreamer/log_{path}.txt"
    if os.path.exists(log_path):
        with open(log_path, "r") as f:
            lines = f.readlines()
            return Response(content="".join(lines[-50:]), media_type="text/plain")
    return Response(content="Log not found.", media_type="text/plain")

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
