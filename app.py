import os
import subprocess
import uuid
import sys
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS 
from werkzeug.utils import secure_filename
import logging
import shutil 
import time

# Configure logging to console (stdout/stderr)
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

app = Flask(__name__)
# Enable CORS for all domains to allow access from the Cloudflare Pages frontend.
CORS(app) 

# Directory to temporarily store uploaded and converted files inside the container
# This path must match the host volume mount: -v /yourdsgnpro/file-converter/data:/tmp/uploads
UPLOAD_FOLDER = '/tmp/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Define file extensions 
DOCUMENT_TYPES = ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'pdf', 'csv', 'txt', 'html', 'rtf']
MEDIA_TYPES = ['mp3', 'mp4', 'webm', 'wav', 'flac', 'avi', 'mov', 'mkv', 'gif', 'jpg', 'png', 'mpeg', '3gp', 'aac', 'wma', 'aiff', 'ogg', 'webp', 'tiff', 'ico', 'bmp', 'psd', 'raw', 'heic']

def convert_document_libreoffice(input_path, output_dir, target_format):
    # Use unique ID for per-process LibreOffice configuration to prevent conflicts
    lo_instance_id = str(uuid.uuid4())
    lo_user_dir = f"/tmp/lo-user/{lo_instance_id}"
    os.makedirs(lo_user_dir, exist_ok=True)
    
    sys.stderr.write(f"LibreOffice START: Converting {input_path} to {target_format}. LO User Dir: {lo_user_dir}\n")
    
    # Command uses the explicit user profile and --outdir
    command = [
        "libreoffice",
        "--headless",
        f"-env:UserInstallation=file://{lo_user_dir}", 
        "--convert-to", target_format,
        input_path, 
        "--outdir", output_dir
    ]
    
    sys.stderr.write(f"Executing command: {' '.join(command)}\n")
    
    start_time = time.time()
    
    try:
        # Increased timeout to 4 minutes (240s) for large document conversions
        result = subprocess.run(command, capture_output=True, text=True, timeout=240) 
    except subprocess.TimeoutExpired as e:
        sys.stderr.write(f"LibreOffice TIMEOUT after {time.time() - start_time:.2f}s\n")
        raise
    finally:
        # CRITICAL: Clean up the dedicated LibreOffice user directory
        try:
            shutil.rmtree(lo_user_dir, ignore_errors=True)
        except Exception as e:
            sys.stderr.write(f"Cleanup warning: Failed to remove LO user dir {lo_user_dir}. {e}\n")
    
    if result.returncode != 0:
        error_output = result.stderr + result.stdout
        sys.stderr.write(f"LibreOffice FAILED (Code {result.returncode}): Output: {error_output}\n")
        last_error_line = error_output.strip().splitlines()[-1] if error_output else 'Unknown LibreOffice Error'
        raise Exception(f"LibreOffice conversion failed. Detail: {last_error_line}")
    
    sys.stderr.write(f"LibreOffice SUCCESS in {time.time() - start_time:.2f}s.\n")
    
    # LibreOffice outputs to a predictable filename based on the input name
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    output_filename = f"{base_name}.{target_format}"
    return os.path.join(output_dir, output_filename)

def convert_media_ffmpeg(input_path, output_dir, target_format):
    sys.stderr.write(f"FFmpeg START: Converting {input_path} to {target_format}\n")
    
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    output_filename = f"{base_name}.{target_format}"
    output_path = os.path.join(output_dir, output_filename)
    
    command = [
        "ffmpeg",
        "-i", input_path,
        "-y", 
        output_path
    ]
    
    sys.stderr.write(f"Executing command: {' '.join(command)}\n")
    
    start_time = time.time()
    
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300) 
    except subprocess.TimeoutExpired as e:
        sys.stderr.write(f"FFmpeg TIMEOUT: {e}\n")
        raise

    if result.returncode != 0:
        error_output = result.stderr + result.stdout
        sys.stderr.write(f"FFmpeg FAILED (Code {result.returncode}): Stderr: {result.stderr}\n")
        last_error_line = error_output.strip().splitlines()[-1] if error_output else 'Unknown FFmpeg Error'
        raise Exception(f"FFmpeg conversion failed. Error: {last_error_line}")
    
    sys.stderr.write(f"FFmpeg SUCCESS in {time.time() - start_time:.2f}s.\n")
    return output_path


@app.route('/convert', methods=['POST'])
def convert_file():
    # 1. Setup unique working directory (per request)
    temp_id = str(uuid.uuid4())
    temp_work_dir = os.path.join(app.config['UPLOAD_FOLDER'], temp_id)
    os.makedirs(temp_work_dir, exist_ok=True)
    
    def cleanup():
        # This cleanup handles the temporary working directory (uploads/outputs)
        try:
            if os.path.exists(temp_work_dir):
                shutil.rmtree(temp_work_dir)
                app.logger.info(f"Cleaned up directory: {temp_work_dir}")
        except Exception as e:
            app.logger.error(f"Failed to cleanup working directory {temp_work_dir}: {e}")

    try:
        # Check for required parts (standard checks)
        if 'file' not in request.files or 'target_format' not in request.form:
            return jsonify({'error': 'Missing file or target format'}), 400
        
        file = request.files['file']
        target_format = request.form.get('target_format').lower()
        
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400

        # 1. Save the uploaded file
        original_ext = file.filename.rsplit('.', 1)[-1].lower()
        temp_filename = secure_filename(file.filename) 
        temp_input_path = os.path.join(temp_work_dir, temp_filename)
        file.save(temp_input_path)
        app.logger.info(f"Received file: {temp_input_path}. Target format: {target_format}")

        # 2. Determine conversion tool
        converted_file_path = None
        
        is_document = original_ext in DOCUMENT_TYPES or target_format in DOCUMENT_TYPES
        is_media = original_ext in MEDIA_TYPES or target_format in MEDIA_TYPES

        if is_document:
            converted_file_path = convert_document_libreoffice(temp_input_path, temp_work_dir, target_format)
        elif is_media:
            converted_file_path = convert_media_ffmpeg(temp_input_path, temp_work_dir, target_format)
        else:
            return jsonify({'error': f'Unsupported conversion pair: .{original_ext} to .{target_format}.'}), 400

        # 3. Handle result
        if converted_file_path and os.path.exists(converted_file_path):
            app.logger.info(f"Conversion successful. Sending: {converted_file_path}")
            
            original_name_base = os.path.splitext(file.filename)[0]
            final_filename = f"{original_name_base}.{target_format}"
            
            response = send_file(
                converted_file_path,
                mimetype='application/octet-stream',
                as_attachment=True,
                download_name=final_filename
            )
            return response
        
        else:
            app.logger.error(f"Output file missing after conversion attempt: {converted_file_path}")
            return jsonify({'error': 'Conversion failed or output file not found. Check server logs for exact LibreOffice error.'}), 500

    except subprocess.TimeoutExpired:
        app.logger.error("Conversion timed out.")
        return jsonify({'error': 'Conversion timed out on the server. Try a smaller file.'}), 504
    except Exception as e:
        app.logger.error(f"Critical error during request processing: {e}")
        return jsonify({'error': f'Conversion failed: {str(e)}'}), 500
    finally:
        cleanup()

if __name__ == '__main__':
    # This block is only for local testing, production uses Gunicorn (via CMD in Dockerfile)
    app.run(debug=True, host='0.0.0.0', port=8080)
