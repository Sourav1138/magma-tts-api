import os
import time
import random
import re
import logging
import traceback
import requests
import urllib3
import json
import hashlib
import shutil
from urllib.parse import urlparse
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from datetime import datetime, timedelta
import threading
import tempfile

# --- CONFIGURATION ---
app = Flask(__name__)
CORS(app)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure Logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] MAGMA-API | %(message)s")
logger = logging.getLogger('MagmaTTS-API')

# Upstream API Endpoint
BASE_URL = os.environ.get("UPSTREAM_URL", "https://ai-chat.apisimpacientes.workers.dev/audio")

# Available voices
AVAILABLE_VOICES = [
    {"id": "alloy", "name": "Alloy", "description": "Balanced, neutral voice"},
    {"id": "echo", "name": "Echo", "description": "Deep, resonant voice"},
    {"id": "fable", "name": "Fable", "description": "Storytelling voice"},
    {"id": "onyx", "name": "Onyx", "description": "Authoritative voice"},
    {"id": "nova", "name": "Nova", "description": "Bright, energetic voice"},
    {"id": "shimmer", "name": "Shimmer", "description": "Soft, calming voice"}
]

# File storage setup
TEMP_DIR = os.path.join(tempfile.gettempdir(), "magma_tts_files")
os.makedirs(TEMP_DIR, exist_ok=True)
logger.info(f"Using temp directory: {TEMP_DIR}")

# Metadata storage
FILE_METADATA = {}
METADATA_FILE = os.path.join(TEMP_DIR, "metadata.json")

def load_metadata():
    """Load metadata from file"""
    global FILE_METADATA
    try:
        if os.path.exists(METADATA_FILE):
            with open(METADATA_FILE, 'r') as f:
                FILE_METADATA = json.load(f)
            logger.info(f"Loaded {len(FILE_METADATA)} file metadata entries")
    except Exception as e:
        logger.error(f"Failed to load metadata: {e}")
        FILE_METADATA = {}

def save_metadata():
    """Save metadata to file"""
    try:
        with open(METADATA_FILE, 'w') as f:
            json.dump(FILE_METADATA, f)
    except Exception as e:
        logger.error(f"Failed to save metadata: {e}")

def cleanup_expired_files():
    """Remove expired temporary files"""
    current_time = datetime.now().timestamp()
    expired_files = []
    
    for file_id, metadata in list(FILE_METADATA.items()):
        if metadata['expires'] < current_time:
            expired_files.append(file_id)
    
    for file_id in expired_files:
        try:
            file_path = os.path.join(TEMP_DIR, f"{file_id}.mp3")
            if os.path.exists(file_path):
                os.remove(file_path)
            del FILE_METADATA[file_id]
            logger.info(f"Cleaned up expired file: {file_id}")
        except Exception as e:
            logger.error(f"Error cleaning up file {file_id}: {e}")
    
    if expired_files:
        save_metadata()
    
    # Schedule next cleanup
    threading.Timer(300, cleanup_expired_files).start()

# Initialize
load_metadata()
cleanup_expired_files()

# --- BACKEND LOGIC (Unchanged) ---
def get_rotating_headers():
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ]
    headers = {
        "User-Agent": random.choice(user_agents),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate", 
        "Referer": "https://www.google.com/",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }
    return headers

class AudioGenerator:
    def __init__(self):
        self.session_cache = {}
        self.dns_cache = {}
    
    def get_session(self):
        session_key = int(time.time() // 60)
        if session_key not in self.session_cache:
            session = requests.Session()
            session.headers.update(get_rotating_headers())
            session.cookies.update({'session_id': str(int(time.time())), 'consent': 'true'})
            session.mount('https://', requests.adapters.HTTPAdapter(max_retries=3))
            self.session_cache[session_key] = session
            valid_keys = [k for k in self.session_cache.keys() if k >= session_key - 2]
            self.session_cache = {k: self.session_cache[k] for k in valid_keys}
        return self.session_cache[session_key]
    
    def resolve_real_ip(self, hostname):
        if hostname in self.dns_cache: return self.dns_cache[hostname]
        try:
            doh = f"https://dns.google/resolve?name={hostname}&type=A"
            resp = requests.get(doh, timeout=3).json()
            if 'Answer' in resp:
                for ans in resp['Answer']:
                    if ans['type'] == 1:
                        ip = ans['data']
                        self.dns_cache[hostname] = ip
                        return ip
        except: pass
        return None

    def make_safe_request(self, url, params=None, method='GET'):
        session = self.get_session()
        time.sleep(random.uniform(0.1, 0.4))
        try:
            if method == 'GET':
                return session.get(url, params=params, timeout=45)
            else:
                return session.post(url, json=params, timeout=45)
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return None

audio_engine = AudioGenerator()

def download_audio_safe(url):
    try:
        parsed = urlparse(url)
        hostname = parsed.netloc
        path = parsed.path
        scheme = parsed.scheme
        real_ip = audio_engine.resolve_real_ip(hostname)
        headers = {"User-Agent": get_rotating_headers()["User-Agent"], "Host": hostname}
        target_url = f"{scheme}://{real_ip}{path}" if real_ip else url
        resp = requests.get(target_url, headers=headers, stream=True, timeout=60, verify=False)
        if resp.status_code == 200: return resp.content
        if resp.status_code in [301,302,307]: return download_audio_safe(resp.headers['Location'])
    except Exception as e:
        logger.error(f"Download Error: {e}")
    return None

def chunk_text(text, max_chars=2800):
    text = text.replace('\r\n', '\n').replace('**', '')
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = ""
    for para in paragraphs:
        para = para.strip()
        if not para: continue
        if len(current_chunk) + len(para) + 2 <= max_chars:
            current_chunk = f"{current_chunk}\n\n{para}" if current_chunk else para
        else:
            if current_chunk: chunks.append(current_chunk); current_chunk = ""
            if len(para) > max_chars:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sent in sentences:
                    if len(current_chunk) + len(sent) + 1 <= max_chars:
                        current_chunk += (" " if current_chunk else "") + sent
                    else:
                        if current_chunk: chunks.append(current_chunk)
                        current_chunk = sent
            else: current_chunk = para
    if current_chunk: chunks.append(current_chunk)
    return chunks

def generate_full_audio(text, voice='onyx', speed=1.0):
    if not text: raise ValueError("Text is empty")
    if len(text) > 10000: text = text[:10000]
    chunks = chunk_text(text)
    segments = []
    for i, chunk in enumerate(chunks):
        logger.info(f"Chunk {i+1}/{len(chunks)}")
        params = {'model': 'openai-tts', 'voice': voice, 'text': chunk, 'speed': speed}
        success = False
        resp = audio_engine.make_safe_request(BASE_URL, params)
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                if data.get('audio_url'):
                    c = download_audio_safe(data['audio_url'])
                    if c: segments.append(c); success = True
            except: pass
        if not success:
            time.sleep(1)
            params['speed'] = 1.0
            resp = audio_engine.make_safe_request(BASE_URL, params)
            if resp and resp.status_code == 200:
                try:
                    data = resp.json()
                    if data.get('audio_url'):
                        c = download_audio_safe(data['audio_url'])
                        if c: segments.append(c); success = True
                except: pass
    if not segments: raise Exception("Generation failed.")
    return b"".join(segments)

# --- API ROUTES ---
@app.route('/')
def api_root():
    """API root endpoint"""
    return jsonify({
        "service": "MagmaTTS API",
        "version": "1.0",
        "endpoints": {
            "voices": "/api/voices",
            "generate": "/api/generate (POST)",
            "download": "/api/download/<file_id>",
            "status": "/api/status/<file_id>"
        },
        "status": "operational",
        "temp_dir": TEMP_DIR
    })

@app.route('/api/voices', methods=['GET'])
def get_voices():
    """Get all available voices"""
    return jsonify({
        "count": len(AVAILABLE_VOICES),
        "voices": AVAILABLE_VOICES,
        "default": "onyx"
    })

@app.route('/api/generate', methods=['POST'])
def generate_tts():
    """Generate TTS audio and return temporary download link"""
    try:
        data = request.json
        if not data:
            return jsonify({
                "error": True,
                "message": "No JSON data provided"
            }), 400
        
        text = data.get('text', '')
        voice = data.get('voice', 'onyx')
        speed = float(data.get('speed', 1.0))
        
        # Validate voice
        valid_voices = [v['id'] for v in AVAILABLE_VOICES]
        if voice not in valid_voices:
            return jsonify({
                "error": True,
                "message": f"Invalid voice. Available voices: {', '.join(valid_voices)}"
            }), 400
        
        # Validate text
        if not text or len(text.strip()) == 0:
            return jsonify({
                "error": True,
                "message": "Text is required"
            }), 400
        
        if len(text) > 10000:
            return jsonify({
                "error": True,
                "message": "Text too long (max 10000 characters)"
            }), 400
        
        logger.info(f"Generating TTS: voice={voice}, speed={speed}, chars={len(text)}")
        
        # Generate audio
        audio_data = generate_full_audio(text, voice, speed)
        
        # Generate unique file ID
        file_id = hashlib.md5(f"{text}{voice}{speed}{time.time()}".encode()).hexdigest()[:16]
        file_path = os.path.join(TEMP_DIR, f"{file_id}.mp3")
        
        # Save audio file
        with open(file_path, 'wb') as f:
            f.write(audio_data)
        
        # Store metadata
        expires_at = time.time() + 3600  # 1 hour from now
        FILE_METADATA[file_id] = {
            'filename': f"tts_{voice}_{int(time.time())}.mp3",
            'created': time.time(),
            'expires': expires_at,
            'voice': voice,
            'speed': speed,
            'text_length': len(text),
            'file_path': file_path,
            'size_bytes': len(audio_data)
        }
        save_metadata()
        
        # Generate download URL - handle HTTPS correctly
        if request.headers.get('X-Forwarded-Proto') == 'https':
            scheme = 'https://'
        else:
            scheme = 'http://'
        
        host = request.host
        download_url = f"{scheme}{host}/api/download/{file_id}"
        
        return jsonify({
            "error": False,
            "message": "Audio generated successfully",
            "data": {
                "file_id": file_id,
                "download_url": download_url,
                "expires_at": datetime.fromtimestamp(expires_at).isoformat(),
                "voice": voice,
                "speed": speed,
                "size_bytes": len(audio_data),
                "duration_hours": 1
            }
        })
        
    except Exception as e:
        logger.error(f"Generation error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({
            "error": True,
            "message": f"Generation failed: {str(e)}"
        }), 500

@app.route('/api/download/<file_id>', methods=['GET'])
def download_audio(file_id):
    """Download generated audio file"""
    try:
        if file_id not in FILE_METADATA:
            return jsonify({
                "error": True,
                "message": "File not found"
            }), 404
        
        metadata = FILE_METADATA[file_id]
        file_path = metadata['file_path']
        
        # Check if expired
        if time.time() > metadata['expires']:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                del FILE_METADATA[file_id]
                save_metadata()
            except:
                pass
            return jsonify({
                "error": True,
                "message": "File has expired"
            }), 410  # Gone
        
        # Check if file exists
        if not os.path.exists(file_path):
            del FILE_METADATA[file_id]
            save_metadata()
            return jsonify({
                "error": True,
                "message": "File not found on disk"
            }), 404
        
        # Read and return file
        with open(file_path, 'rb') as f:
            audio_data = f.read()
        
        response = Response(
            audio_data,
            mimetype="audio/mpeg",
            headers={
                "Content-Disposition": f"attachment; filename={metadata['filename']}",
                "X-Expires-At": datetime.fromtimestamp(metadata['expires']).isoformat(),
                "X-Voice": metadata['voice'],
                "X-Speed": str(metadata['speed'])
            }
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Download error for {file_id}: {e}")
        return jsonify({
            "error": True,
            "message": f"Download failed: {str(e)}"
        }), 500

@app.route('/api/status/<file_id>', methods=['GET'])
def check_status(file_id):
    """Check if a file exists and its expiration status"""
    try:
        if file_id not in FILE_METADATA:
            return jsonify({
                "exists": False,
                "message": "File not found"
            })
        
        metadata = FILE_METADATA[file_id]
        current_time = time.time()
        expires_at = metadata['expires']
        
        # Check if file actually exists on disk
        file_exists = os.path.exists(metadata['file_path'])
        
        return jsonify({
            "exists": file_exists,
            "expired": current_time > expires_at,
            "file_on_disk": file_exists,
            "expires_at": datetime.fromtimestamp(expires_at).isoformat(),
            "seconds_remaining": max(0, int(expires_at - current_time)),
            "voice": metadata['voice'],
            "speed": metadata['speed'],
            "created": datetime.fromtimestamp(metadata['created']).isoformat(),
            "size_bytes": metadata['size_bytes']
        })
    except Exception as e:
        return jsonify({
            "exists": False,
            "message": f"Error checking status: {str(e)}"
        })

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "temp_files": len(FILE_METADATA),
            "temp_dir": TEMP_DIR,
            "disk_usage": f"{sum(os.path.getsize(f) for f in os.listdir(TEMP_DIR) if f.endswith('.mp3')) / (1024*1024):.2f} MB",
            "service": "MagmaTTS API"
        })
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500

@app.route('/cleanup', methods=['POST'])
def manual_cleanup():
    """Manually trigger cleanup (admin only)"""
    cleanup_expired_files()
    return jsonify({
        "message": "Cleanup triggered",
        "remaining_files": len(FILE_METADATA)
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '0.0.0.0')
    
    logger.info(f"Starting MagmaTTS API on {host}:{port}")
    logger.info(f"Available voices: {[v['id'] for v in AVAILABLE_VOICES]}")
    logger.info(f"Temp directory: {TEMP_DIR}")
    logger.info(f"Loaded {len(FILE_METADATA)} existing files")
    
    app.run(host=host, port=port, threaded=True)
