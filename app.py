import os
import zipfile
import tempfile
from flask import Flask, request, send_file, render_template_string
from yt_dlp import YoutubeDL
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
app.secret_key = "your_secret_key"

# Search YouTube using yt-dlp
def search_youtube(video_name):
    try:
        ydl_opts = {
            'quiet': True,
            'default_search': 'ytsearch',
            'noplaylist': True,
            'skip_download': True,
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_name, download=False)
            if 'entries' in info and info['entries']:
                first = info['entries'][0]
                return first['webpage_url'], first['title']
        return None, None
    except Exception as e:
        print(f"Error searching for {video_name}: {e}")
        return None, None

# Download video/audio using yt-dlp
def download_video(url, output_path, format="mp3"):
    if format == "mp3":
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': os.path.join(output_path, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }
    else:
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': os.path.join(output_path, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        print(f"Error downloading {url}: {e}")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        video_names = request.form.get("video_list").strip().split("\n")
        format = request.form.get("format")
        threads = int(request.form.get("threads", 1))

        # Temporary directory for downloads
        temp_dir = tempfile.mkdtemp()
        download_dir = os.path.join(temp_dir, "downloads")
        os.makedirs(download_dir, exist_ok=True)

        # Download each video
        def process_video(name):
            url, title = search_youtube(name)
            if url:
                download_video(url, download_dir, format=format)
                return title
            return None

        with ThreadPoolExecutor(max_workers=threads) as executor:
            executor.map(process_video, video_names)

        # Create a ZIP file
        zip_path = os.path.join(temp_dir, "downloads.zip")
        with zipfile.ZipFile(zip_path, "w") as zipf:
            for root, _, files in os.walk(download_dir):
                for file in files:
                    zipf.write(os.path.join(root, file), file)

        return send_file(zip_path, as_attachment=True, download_name="downloads.zip")

    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>YouTube Downloader</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.0/css/bootstrap.min.css">
    </head>
    <body>
    <div class="container my-5">
        <h1 class="text-center mb-4">YouTube Downloader</h1>
        <form method="POST">
            <div class="mb-3">
                <label for="video_list" class="form-label">Video List</label>
                <textarea class="form-control" id="video_list" name="video_list" rows="5" placeholder="Enter one video name per line" required></textarea>
            </div>
            <div class="mb-3">
                <label class="form-label">Download Format</label>
                <div>
                    <div class="form-check form-check-inline">
                        <input class="form-check-input" type="radio" name="format" id="mp3" value="mp3" checked>
                        <label class="form-check-label" for="mp3">MP3</label>
                    </div>
                    <div class="form-check form-check-inline">
                        <input class="form-check-input" type="radio" name="format" id="mp4" value="mp4">
                        <label class="form-check-label" for="mp4">MP4</label>
                    </div>
                </div>
            </div>
            <div class="mb-3">
                <label for="threads" class="form-label">Threads</label>
                <select class="form-select" id="threads" name="threads">
                    <option value="1">1</option>
                    <option value="2">2</option>
                    <option value="4">4</option>
                    <option value="8">8</option>
                </select>
            </div>
            <div class="d-grid">
                <button type="submit" class="btn btn-primary">Download</button>
            </div>
        </form>
    </div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.0/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """)

if __name__ == "__main__":
    app.run(debug=True)
