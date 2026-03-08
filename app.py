import os
import zipfile
import tempfile
import shutil
import time
import re
from flask import Flask, request, send_file, render_template_string
from flask_socketio import SocketIO, emit
from yt_dlp import YoutubeDL
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
app.config['SECRET_KEY'] = 'progress_tracker_key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

TEMP_STAGING = {}

def update_status(msg, status_type="info"):
    socketio.emit('status_update', {'msg': msg, 'type': status_type})

def update_progress(current, total):
    socketio.emit('progress_count', {'current': current, 'total': total})

def safe_filename(title):
    clean = re.sub(r'[\\/*?:"<>|]', "", title)
    return clean[:50].strip()

def download_item(name, download_dir, video_format, index_info):
    try:
        update_status(f"🔍 Searching: {name}...")
        
        search_opts = {'quiet': True, 'default_search': 'ytsearch', 'noplaylist': True, 'skip_download': True}
        with YoutubeDL(search_opts) as ydl:
            info = ydl.extract_info(name, download=False)
            if not info or 'entries' not in info or not info['entries']:
                update_status(f"❌ Not found: {name}", "error")
                return None
            
            video = info['entries'][0]
            url = video['webpage_url']
            raw_title = video.get('title', 'Unknown')
            artist = video.get('uploader', 'Streamloader')
            filename = safe_filename(raw_title)

        update_status(f"📥 Downloading: {filename}...")

        ydl_opts = {
            'outtmpl': os.path.join(download_dir, f'{filename}.%(ext)s'),
            'quiet': True, 'no_warnings': True,
        }

        if video_format == "mp3":
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [
                    {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'},
                    {'key': 'FFmpegMetadata', 'add_metadata': True}
                ],
                'postprocessor_args': [
                    '-ar', '44100', '-ac', '2', '-b:a', '192k', '-id3v2_version', '3',
                    '-metadata', f'title={raw_title}',
                    '-metadata', f'artist={artist}',
                    '-metadata', f'album=Streamloader'
                ],
            })
        else:
            ydl_opts['format'] = 'bestvideo+bestaudio/best'

        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # After successful download, increment counter on frontend
        index_info['completed'] += 1
        update_progress(index_info['completed'], index_info['total'])
        update_status(f"✅ Finished ({index_info['completed']}/{index_info['total']}): {filename}", "success")
        return filename

    except Exception as e:
        update_status(f"⚠️ Error: {str(e)}", "error")
        # Still increment counter even on error to keep total consistent
        index_info['completed'] += 1
        update_progress(index_info['completed'], index_info['total'])
        return None

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@socketio.on('start_download')
def handle_download(data):
    video_names = [v.strip() for v in data['video_list'].split('\n') if v.strip()]
    video_format = data['format']
    threads = int(data['threads'])
    
    total_count = len(video_names)
    # Using a dictionary to pass by reference to threads
    index_info = {'completed': 0, 'total': total_count}

    base_temp = tempfile.mkdtemp()
    download_dir = os.path.join(base_temp, "downloads")
    os.makedirs(download_dir, exist_ok=True)

    update_progress(0, total_count)
    update_status(f"🚀 Batch started: {total_count} items.")

    with ThreadPoolExecutor(max_workers=threads) as executor:
        # Pass the index_info dict to each worker
        list(executor.map(lambda name: download_item(name, download_dir, video_format, index_info), video_names))

    update_status("📦 Finalizing ZIP...")
    zip_path = os.path.join(base_temp, f"batch_{int(time.time())}.zip")
    with zipfile.ZipFile(zip_path, "w") as zipf:
        for root, _, files in os.walk(download_dir):
            for file in files:
                zipf.write(os.path.join(root, file), file)

    shutil.rmtree(download_dir)
    sid = request.sid
    TEMP_STAGING[sid] = zip_path
    
    update_status("✨ Process Complete!", "success")
    emit('download_ready', {'url': f'/fetch/{sid}'})

@app.route("/fetch/<sid>")
def fetch_zip(sid):
    file_path = TEMP_STAGING.get(sid)
    if file_path and os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return "Expired.", 404

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Streamloader Pro</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <style>
        body { background: #020617; color: #f1f5f9; font-family: 'Inter', sans-serif; overflow: hidden; }
        .glass { background: rgba(15, 23, 42, 0.8); backdrop-filter: blur(14px); border: 1px solid rgba(255,255,255,0.05); }
        #log::-webkit-scrollbar { height: 6px; width: 4px; }
        #log::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 10px; }
    </style>
</head>
<body class="h-screen w-screen flex flex-col items-center justify-center p-6">
    <div class="w-full max-w-5xl h-full flex flex-col space-y-4">
        
        <div class="glass rounded-3xl p-6 flex-shrink-0 flex justify-between items-center">
            <div>
                <h1 class="text-2xl font-black text-blue-400">Streamloader <span class="text-white opacity-20 italic font-light">Pro</span></h1>
                <p class="text-[10px] text-slate-500 uppercase tracking-widest font-bold">Metadata + Nokia Legacy Patch v3</p>
            </div>
            <div id="finish_zone" class="hidden">
                <a id="dl_link" href="#" class="bg-blue-600 hover:bg-blue-500 px-6 py-2 rounded-xl text-sm font-bold transition-all shadow-lg shadow-blue-500/20 animate-pulse">
                    Download ZIP
                </a>
            </div>
        </div>

        <div class="glass rounded-3xl p-6 flex-1 flex flex-col min-h-0">
            <div class="flex gap-4 mb-4">
                <select id="format" class="bg-slate-900 border border-slate-800 rounded-xl px-4 py-2 text-xs font-bold text-slate-400 outline-none">
                    <option value="mp3">MP3 (Universal)</option>
                    <option value="mp4">MP4 (Video)</option>
                </select>
                <select id="threads" class="bg-slate-900 border border-slate-800 rounded-xl px-4 py-2 text-xs font-bold text-slate-400 outline-none">
                    <option value="4">4 Threads</option>
                    <option value="8">8 Threads</option>
                </select>
            </div>
            <textarea id="video_list" class="flex-1 w-full bg-black/30 border border-slate-800/50 rounded-2xl p-5 font-mono text-sm outline-none resize-none transition-all focus:border-blue-500/30" placeholder="One name or link per line..."></textarea>
            <button onclick="startDownload()" id="main_btn" class="mt-4 w-full bg-blue-600 hover:bg-blue-500 py-4 rounded-2xl font-bold transition-all active:scale-[0.99]">Start Process</button>
        </div>

        <div class="glass rounded-3xl p-5 flex-shrink-0">
            <div class="flex justify-between items-end mb-3 px-1">
                <div class="flex flex-col">
                    <span class="text-[10px] uppercase font-bold text-slate-500 tracking-widest">Live Output</span>
                    <span id="counter_text" class="text-xs font-mono text-blue-400">Idle</span>
                </div>
                <div class="w-48 bg-slate-900 h-1 rounded-full overflow-hidden">
                    <div id="progress_bar" class="bg-blue-500 h-full w-0 transition-all duration-500"></div>
                </div>
            </div>
            <div id="log" class="h-28 bg-black/60 border border-slate-900 rounded-xl p-4 font-mono text-[11px] overflow-y-auto overflow-x-auto whitespace-nowrap space-y-1">
                <div class="text-slate-700 italic">// Terminal ready for input...</div>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        const log = document.getElementById('log');
        const btn = document.getElementById('main_btn');
        const counterText = document.getElementById('counter_text');
        const progressBar = document.getElementById('progress_bar');

        function startDownload() {
            const list = document.getElementById('video_list').value;
            if(!list.trim()) return;
            
            log.innerHTML = '';
            btn.disabled = true;
            btn.classList.add('opacity-50');
            counterText.innerText = "Initializing...";
            progressBar.style.width = "0%";
            document.getElementById('finish_zone').classList.add('hidden');

            socket.emit('start_download', {
                video_list: list,
                format: document.getElementById('format').value,
                threads: document.getElementById('threads').value
            });
        }

        socket.on('progress_count', (data) => {
            const percent = (data.current / data.total) * 100;
            counterText.innerText = `PROCESSED: ${data.current} / ${data.total} ITEMS`;
            progressBar.style.width = `${percent}%`;
        });

        socket.on('status_update', (data) => {
            const entry = document.createElement('div');
            entry.className = data.type === 'error' ? 'text-red-400' : (data.type === 'success' ? 'text-emerald-400' : 'text-slate-500');
            entry.innerHTML = `<span class="opacity-30 mr-2">>>></span>${data.msg}`;
            log.appendChild(entry);
            log.scrollTop = log.scrollHeight;
        });

        socket.on('download_ready', (data) => {
            btn.disabled = false;
            btn.classList.remove('opacity-50');
            document.getElementById('finish_zone').classList.remove('hidden');
            document.getElementById('dl_link').href = data.url;
        });
    </script>
</body>
</html>"""

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5001, debug=True)
