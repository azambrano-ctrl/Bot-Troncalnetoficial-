import os
import time
import hashlib
from io import BytesIO
from PIL import Image

BOT_CONFIG = {
    'max_messages_per_minute': 10,
    'session_timeout_minutes': 30,
    'supported_formats': ['JPEG', 'PNG', 'WEBP'],
    'max_retries': 3,
    'temp_images_dir': 'temp_images',
    'cleanup_interval_hours': 24
}

if not os.getenv("META_ACCESS_TOKEN"):
    raise ValueError("META_ACCESS_TOKEN no está configurado en las variables de entorno")

if not GRUPO_SOPORTE_ID:
    print("ADVERTENCIA: GRUPO_SOPORTE_ID no está configurado en las variables de entorno.")

# --- SISTEMA DE GESTIÓN DE ARCHIVOS TEMPORALES (Estas funciones se pueden mover a un utils.py más adelante) ---
def ensure_temp_directory():
    if not os.path.exists(BOT_CONFIG['temp_images_dir']):
        os.makedirs(BOT_CONFIG['temp_images_dir'])
        print(f"Directorio creado: {BOT_CONFIG['temp_images_dir']}")

def cleanup_temp_files():
    try:
        ensure_temp_directory()
        current_time = time.time()
        cleanup_threshold = current_time - (BOT_CONFIG['cleanup_interval_hours'] * 3600)
        for filename in os.listdir(BOT_CONFIG['temp_images_dir']):
            if filename.startswith('temp_'):
                filepath = os.path.join(BOT_CONFIG['temp_images_dir'], filename)
                try:
                    file_time = os.path.getctime(filepath)
                    if file_time < cleanup_threshold:
                        os.remove(filepath)
                        print(f"Archivo temporal eliminado: {filename}")
                except Exception as e:
                    print(f"Error eliminando {filename}: {e}")
    except Exception as e:
        print(f"Error en limpieza de archivos temporales: {e}")

def create_image_url_alternative(filepath, user_id):
    try:
        if not os.path.exists(filepath):
            return "Imagen no disponible"
        with Image.open(filepath) as img:
            img.thumbnail((800, 600), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            img.convert('RGB').save(buffer, format='JPEG', quality=60)
            encoded = base64.b64encode(buffer.getvalue())
            reference = f"temp_ref_{user_id}_{encoded[:20].decode()}"
            return reference
    except Exception as e:
        print(f"Error creando referencia de imagen: {e}")
        return "Error de referencia"

