import os
import hashlib
import time
import json
import threading
import schedule
from datetime import datetime
from flask import Flask, request, send_file, jsonify, abort
from werkzeug.utils import secure_filename
import logging

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB

STORAGE_PATH = os.getenv('STORAGE_PATH', '/app/storage')
ADMIN_ID = os.getenv('ADMIN_ID', '')
SECRET_TOKEN = os.getenv('SECRET_TOKEN', '')
MAX_STORAGE_GB = float(os.getenv('MAX_STORAGE_GB', '10'))
FILE_EXPIRY_HOURS = int(os.getenv('FILE_EXPIRY_HOURS', '6'))

os.makedirs(STORAGE_PATH, exist_ok=True)

def check_auth():
    token = request.headers.get('X-Auth-Token', '')
    user_id = request.headers.get('X-User-ID', '')
    return token == SECRET_TOKEN and user_id == ADMIN_ID

def get_storage_info():
    total_size = 0
    file_count = 0
    for filename in os.listdir(STORAGE_PATH):
        if filename.endswith('.json'):
            continue
        filepath = os.path.join(STORAGE_PATH, filename)
        if os.path.isfile(filepath):
            total_size += os.path.getsize(filepath)
            file_count += 1
    return {
        'total_bytes': total_size,
        'total_gb': round(total_size / (1024**3), 2),
        'file_count': file_count,
        'max_gb': MAX_STORAGE_GB
    }

def cleanup_old_files(force=False):
    current_time = time.time()
    expiry_seconds = FILE_EXPIRY_HOURS * 3600
    deleted_files = []
    files_with_time = []
    
    for filename in os.listdir(STORAGE_PATH):
        if filename.endswith('.json'):
            continue
        filepath = os.path.join(STORAGE_PATH, filename)
        if not os.path.isfile(filepath):
            continue
        file_time = os.path.getmtime(filepath)
        file_age = current_time - file_time
        
        if file_age > expiry_seconds:
            try:
                file_id = filename.rsplit('.', 1)[0]
                meta_file = os.path.join(STORAGE_PATH, f"{file_id}.json")
                os.remove(filepath)
                if os.path.exists(meta_file):
                    os.remove(meta_file)
                deleted_files.append({'file': filename, 'age_hours': round(file_age / 3600, 2), 'reason': 'expired'})
            except Exception as e:
                logger.error(f"Cleanup error: {str(e)}")
        else:
            files_with_time.append((filepath, file_time, filename))
    
    if force:
        storage_info = get_storage_info()
        max_bytes = MAX_STORAGE_GB * 1024**3
        if storage_info['total_bytes'] > max_bytes * 0.9:
            files_with_time.sort(key=lambda x: x[1])
            for filepath, file_time, filename in files_with_time[:5]:
                try:
                    file_id = filename.rsplit('.', 1)[0]
                    meta_file = os.path.join(STORAGE_PATH, f"{file_id}.json")
                    os.remove(filepath)
                    if os.path.exists(meta_file):
                        os.remove(meta_file)
                    deleted_files.append({'file': filename, 'reason': 'storage_full'})
                except Exception as e:
                    logger.error(f"Force cleanup error: {str(e)}")
            storage_info = get_storage_info()
            if storage_info['total_bytes'] > max_bytes * 0.9:
                return {'warning': True, 'message': f"Storage critically full: {storage_info['total_gb']}/{MAX_STORAGE_GB}GB", 'deleted': deleted_files}
    return {'deleted': deleted_files, 'warning': False}

def generate_file_hash(filename):
    timestamp = str(time.time_ns())
    data = f"{filename}{timestamp}{SECRET_TOKEN}"
    return hashlib.sha256(data.encode()).hexdigest()[:20]

def save_metadata(file_id, original_name, size, mime_type):
    meta_path = os.path.join(STORAGE_PATH, f"{file_id}.json")
    with open(meta_path, 'w') as f:
        json.dump({'original_name': original_name, 'size': size, 'mime_type': mime_type, 'upload_time': time.time(), 'expires_at': time.time() + (FILE_EXPIRY_HOURS * 3600)}, f)

def get_metadata(file_id):
    meta_path = os.path.join(STORAGE_PATH, f"{file_id}.json")
    if not os.path.exists(meta_path):
        return None
    with open(meta_path, 'r') as f:
        return json.load(f)

@app.route('/')
def home():
    return jsonify({"service": "Personal Cloud Storage", "version": "1.0", "status": "active"})

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

@app.route('/storage/info', methods=['GET'])
def storage_info():
    if not check_auth():
        abort(401)
    info = get_storage_info()
    files = []
    for filename in os.listdir(STORAGE_PATH):
        if filename.endswith('.json'):
            continue
        filepath = os.path.join(STORAGE_PATH, filename)
        if os.path.isfile(filepath):
            file_id = filename.rsplit('.', 1)[0]
            meta = get_metadata(file_id)
            if meta:
                files.append({'id': file_id, 'name': meta['original_name'], 'size_mb': round(meta['size'] / (1024**2), 2), 'age_hours': round((time.time() - meta['upload_time']) / 3600, 2), 'expires_in_hours': round((meta['expires_at'] - time.time()) / 3600, 2)})
    return jsonify({'storage': info, 'files': files})

@app.route('/upload', methods=['POST'])
def upload():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Empty filename"}), 400
    
    cleanup_result = cleanup_old_files(force=True)
    file_content = file.read()
    file_size = len(file_content)
    
    if file_size == 0:
        return jsonify({"error": "Empty file"}), 400
    if file_size > 2 * 1024 * 1024 * 1024:
        return jsonify({"error": "File too large (max 2GB)"}), 413
    
    storage = get_storage_info()
    if storage['total_bytes'] + file_size > MAX_STORAGE_GB * 1024**3:
        return jsonify({"error": "Storage full", "current_gb": storage['total_gb'], "max_gb": MAX_STORAGE_GB, "cleanup_performed": cleanup_result}), 507
    
    original_name = secure_filename(file.filename)
    file_id = generate_file_hash(original_name)
    extension = ''
    if '.' in original_name:
        extension = '.' + original_name.rsplit('.', 1)[1].lower()
    filename = f"{file_id}{extension}"
    filepath = os.path.join(STORAGE_PATH, filename)
    
    with open(filepath, 'wb') as f:
        f.write(file_content)
    
    mime_type = file.content_type or 'application/octet-stream'
    save_metadata(file_id, original_name, file_size, mime_type)
    
    base_url = request.host_url.rstrip('/')
    download_url = f"{base_url}/f/{file_id}"
    
    response_data = {"success": True, "file_id": file_id, "url": download_url, "direct_url": f"{base_url}/d/{file_id}", "size_mb": round(file_size / (1024**2), 2), "expires_in_hours": FILE_EXPIRY_HOURS, "storage": {"used_gb": round((storage['total_bytes'] + file_size) / (1024**3), 2), "max_gb": MAX_STORAGE_GB}}
    
    if cleanup_result.get('warning'):
        response_data['warning'] = cleanup_result['message']
        response_data['deleted_files'] = cleanup_result['deleted']
    
    return jsonify(response_data), 201

@app.route('/f/<file_id>')
def view_file(file_id):
    if not file_id.isalnum():
        abort(400)
    meta = get_metadata(file_id)
    if not meta:
        abort(404)
    if time.time() > meta['expires_at']:
        abort(410)
    extension = ''
    if '.' in meta['original_name']:
        extension = '.' + meta['original_name'].rsplit('.', 1)[1].lower()
    filepath = os.path.join(STORAGE_PATH, f"{file_id}{extension}")
    if not os.path.exists(filepath):
        abort(404)
    return send_file(filepath, mimetype=meta['mime_type'], as_attachment=False, download_name=meta['original_name'])

@app.route('/d/<file_id>')
def download_file(file_id):
    if not file_id.isalnum():
        abort(400)
    meta = get_metadata(file_id)
    if not meta:
        abort(404)
    if time.time() > meta['expires_at']:
        abort(410)
    extension = ''
    if '.' in meta['original_name']:
        extension = '.' + meta['original_name'].rsplit('.', 1)[1].lower()
    filepath = os.path.join(STORAGE_PATH, f"{file_id}{extension}")
    if not os.path.exists(filepath):
        abort(404)
    return send_file(filepath, mimetype=meta['mime_type'], as_attachment=True, download_name=meta['original_name'])

@app.route('/cleanup', methods=['POST'])
def manual_cleanup():
    if not check_auth():
        abort(401)
    result = cleanup_old_files(force=True)
    return jsonify(result)

def auto_cleanup():
    schedule.every(1).hours.do(lambda: cleanup_old_files(force=False))
    while True:
        schedule.run_pending()
        time.sleep(3600)

threading.Thread(target=auto_cleanup, daemon=True).start()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
