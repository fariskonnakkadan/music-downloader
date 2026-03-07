import os
import zipfile
import tempfile
import shutil
import time
from flask import Flask, request, send_file, render_template_string
from flask_socketio import SocketIO, emit
from yt_dlp import YoutubeDL
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev_key_123'
# Using 'threading' mode to avoid the NotImplementedError on macOS/LibreSSL
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Store paths to clean up later
TEMP_STAGING = {}

def update_status(msg, status_type="info"):
    """Sends real-time updates to the frontend."""
    socketio.emit('status_update', {'msg': msg, 'type': status_type})

def download_item(name, download_dir, video_format):
    try:
        update_status(f"🔍 Searching: {name}...")
        
        # 1. Search Logic
        search_opts = {
            'quiet': True, 
            'default_search': 'ytsearch', 
            'noplaylist': True, 
            'skip_download': True
        }
        with YoutubeDL(search_opts) as ydl:
            info = ydl.extract_info(name, download=False)
            if not info or 'entries' not in info or not info['entries']:
                update_status(f"⚠️ Could not find: {name}", "error")
                return None
            video = info['entries'][0]
            url = video['webpage_url']
            title = video.get('title', 'Unknown Video')

        update_status(f"📥 Downloading: {title[:50]}...")

        # 2. Download Logic
        ydl_opts = {
            'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }

        if video_format == "mp3":
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192'
                }],
            })
        else:
            ydl_opts['format'] = 'bestvideo+bestaudio/best'

        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        update_status(f"✅ Completed: {title[:40]}...", "success")
        return title

    except Exception as e:
        update_status(f"❌ Error processing '{name}': {str(e)}", "error")
        return None

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@socketio.on('start_download')
def handle_download(data):
    video_names = [v.strip() for v in data['video_list'].split('\n') if v.strip()]
    video_format = data['format']
    threads = int(data['threads'])

    # Create isolated temp environment
    base_temp = tempfile.mkdtemp()
    download_dir = os.path.join(base_temp, "downloads")
    os.makedirs(download_dir, exist_ok=True)

    update_status(f"🚀 Batch started ({len(video_names)} items, {threads} threads)")

    with ThreadPoolExecutor(max_workers=threads) as executor:
        # We use list() to block until all threads in this batch are done
        list(executor.map(lambda name: download_item(name, download_dir, video_format), video_names))

    update_status("📦 Creating ZIP archive...")
    zip_filename = f"batch_{int(time.time())}.zip"
    zip_path = os.path.join(base_temp, zip_filename)
    
    with zipfile.ZipFile(zip_path, "w") as zipf:
        for root, _, files in os.walk(download_dir):
            for file in files:
                zipf.write(os.path.join(root, file), file)

    # Clean up the raw downloads folder to save space, keep the ZIP
    shutil.rmtree(download_dir)
    
    sid = request.sid
    TEMP_STAGING[sid] = zip_path
    
    update_status("✨ All tasks finished!", "success")
    emit('download_ready', {'url': f'/fetch/{sid}'})

@app.route("/fetch/<sid>")
def fetch_zip(sid):
    file_path = TEMP_STAGING.get(sid)
    if file_path and os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return "Link expired or file not found.", 404

# --- MODERN UI TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Streamloader UI</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <style>
        body { background: #020617; color: #f8fafc; font-family: 'Inter', sans-serif; }
        .glass { background: rgba(15, 23, 42, 0.8); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.1); }
        .custom-scrollbar::-webkit-scrollbar { width: 4px; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: #334155; border-radius: 10px; }
    </style>
</head>
<body class="min-h-screen flex items-center justify-center p-6">
    <div class="max-w-3xl w-full glass rounded-3xl p-8 shadow-2xl border border-white/5">
        <header class="mb-8">
            <h1 class="text-4xl font-black tracking-tight bg-gradient-to-br from-blue-400 to-indigo-600 bg-clip-text text-transparent">
                Streamloader <span class="text-white/20 font-light">v2.0</span>
            </h1>
            <p class="text-slate-400 mt-2">Professional YouTube batch downloader with real-time logging.</p>
        </header>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="md:col-span-2 space-y-5">
                <div>
                    <label class="block text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">Video Queue</label>
                    <textarea id="video_list" rows="6" 
                        class="w-full bg-slate-950/50 border border-slate-800 rounded-2xl p-4 focus:ring-2 focus:ring-blue-500/50 outline-none transition-all placeholder:text-slate-700" 
                        placeholder="Paste URLs or video titles here..."></textarea>
                </div>
                
                <div class="flex gap-4">
                    <div class="flex-1">
                        <label class="block text-xs font-bold text-slate-500 mb-2">Format</label>
                        <select id="format" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 outline-none focus:border-blue-500">
                            <option value="mp3">Audio (MP3)</option>
                            <option value="mp4">Video (MP4)</option>
                        </select>
                    </div>
                    <div class="flex-1">
                        <label class="block text-xs font-bold text-slate-500 mb-2">Threads</label>
                        <select id="threads" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 outline-none focus:border-blue-500">
                            <option value="1">Single</option>
                            <option value="4" selected>4 Threads</option>
                            <option value="8">8 Threads</option>
                        </select>
                    </div>
                </div>

                <button onclick="startDownload()" id="main_btn" class="w-full bg-blue-600 hover:bg-blue-500 py-4 rounded-2xl font-bold text-lg shadow-lg shadow-blue-500/20 transition-all active:scale-[0.98]">
                    Start Processing
                </button>
            </div>

            <div class="space-y-4">
                <label class="block text-xs font-bold uppercase tracking-wider text-slate-500">Live Status</label>
                <div id="log_container" class="h-[280px] bg-black/40 border border-slate-800 rounded-2xl p-4 overflow-y-auto custom-scrollbar text-[11px] font-mono space-y-2">
                    <div class="text-slate-600 italic">Waiting for input...</div>
                </div>
                
                <div id="finish_zone" class="hidden animate-bounce">
                    <a id="dl_link" href="#" class="block w-full text-center bg-emerald-600 hover:bg-emerald-500 py-3 rounded-xl font-bold shadow-xl shadow-emerald-500/20">
                        Download ZIP
                    </a>
                </div>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        const log = document.getElementById('log_container');
        const btn = document.getElementById('main_btn');

        function startDownload() {
            const list = document.getElementById('video_list').value;
            if(!list.trim()) return;

            log.innerHTML = '';
            btn.disabled = true;
            btn.innerText = 'Processing...';
            btn.classList.add('opacity-50');
            document.getElementById('finish_zone').classList.add('hidden');

            socket.emit('start_download', {
                video_list: list,
                format: document.getElementById('format').value,
                threads: document.getElementById('threads').value
            });
        }

        socket.on('status_update', (data) => {
            const entry = document.createElement('div');
            entry.className = data.type === 'error' ? 'text-red-400' : (data.type === 'success' ? 'text-emerald-400' : 'text-blue-300');
            entry.innerText = `> ${data.msg}`;
            log.appendChild(entry);
            log.scrollTop = log.scrollHeight;
        });

        socket.on('download_ready', (data) => {
            btn.disabled = false;
            btn.innerText = 'Start Processing';
            btn.classList.remove('opacity-50');
            document.getElementById('finish_zone').classList.remove('hidden');
            document.getElementById('dl_link').href = data.url;
        });
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    # Flask-SocketIO runs its own server wrapper
    socketio.run(app, debug=True, port=5001)
