import os
import psutil
import subprocess
import requests
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

app = FastAPI(title="NanoStreamer")

# Setup templates
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_dir)

# Dummy simple authentication for muis24 / master123
SESSION_TOKEN = "nanostreamer_session_token"
VALID_USERNAME = "muis24"
VALID_PASSWORD = "master123"

# Global state for stream
stream_process = None
active_input_url = ""

def check_auth(request: Request):
    if request.cookies.get("session") != SESSION_TOKEN:
        return False
    return True

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
    
    # Check if stream is currently running
    global stream_process, active_input_url
    is_running = stream_process is not None and stream_process.poll() is None

    # Get local IP for playback URLs
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = '127.0.0.1'
    finally:
        s.close()

    context = {
        "request": request,
        "is_running": is_running,
        "active_input": active_input_url,
        "hls_url": f"http://{local_ip}:8888/live/stream/index.m3u8",
        "rtmp_url": f"rtmp://{local_ip}:1935/live/stream",
        "rtsp_url": f"rtsp://{local_ip}:8554/live/stream"
    }
    return templates.TemplateResponse("dashboard.html", context)

@app.post("/api/stream/start")
async def start_stream(request: Request, input_url: str = Form(...)):
    if not check_auth(request):
        return RedirectResponse(url="/")
    
    global stream_process, active_input_url
    if stream_process is not None and stream_process.poll() is None:
        stream_process.terminate()
        stream_process.wait()

    # Route external stream to local MediaMTX (using copy to prevent transcoding)
    # Pushes to rtmp://127.0.0.1:1935/live/stream
    cmd = [
        "ffmpeg", "-y", "-fflags", "+genpts", "-i", input_url,
        "-c:v", "copy", "-c:a", "copy", "-f", "flv", "rtmp://127.0.0.1:1935/live/stream"
    ]
    
    stream_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    active_input_url = input_url
    
    return RedirectResponse(url="/dashboard", status_code=303)

@app.post("/api/stream/stop")
async def stop_stream(request: Request):
    if not check_auth(request):
        return RedirectResponse(url="/")
    
    global stream_process, active_input_url
    if stream_process is not None:
        stream_process.terminate()
        stream_process = None
        active_input_url = ""
        
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/api/metrics")
async def metrics(request: Request):
    if not check_auth(request):
        return {"error": "Unauthorized"}
    
    # CPU & RAM
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    
    # Network Bandwidth (simple diff)
    net_io = psutil.net_io_counters()
    
    # Get MediaMTX Viewers via its API
    viewers = 0
    try:
        r = requests.get("http://127.0.0.1:9997/v3/paths/list", timeout=1)
        if r.status_code == 200:
            data = r.json()
            items = data.get("items", [])
            for item in items:
                if item.get("name") == "live/stream":
                    viewers = item.get("readers", 0)
    except Exception:
        pass

    global stream_process
    is_running = stream_process is not None and stream_process.poll() is None

    return {
        "cpu": cpu,
        "ram": ram,
        "viewers": viewers,
        "is_running": is_running,
        "net_bytes_sent": net_io.bytes_sent,
        "net_bytes_recv": net_io.bytes_recv
    }

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
