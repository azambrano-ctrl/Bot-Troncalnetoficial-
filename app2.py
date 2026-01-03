# app.py
import os
import requests
import re
import json
import time
import hashlib
import base64
import unicodedata
import traceback
import random
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image
import imagehash
from google.cloud import vision
from google.cloud import speech
from pydub import AudioSegment
from flask import Flask, request
from dotenv import load_dotenv
from utils_sheets import registrar_pago, obtener_hashes_existentes
import fitz
from waitress import serve

load_dotenv()
app = Flask(__name__)

# --- CONFIGURACIÓN PARA LA API DE META ---
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "660511147155188")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "TRONCALNET_BOT_2025")
WHATSAPP_API_VERSION = "v19.0"
GRUPO_SOPORTE_ID = os.getenv("GRUPO_SOPORTE_ID")

# Configuración del bot
BOT_CONFIG = {
    'max_messages_per_minute': 10,
    'session_timeout_minutes': 30,
    'supported_formats': ['JPEG', 'PNG', 'WEBP'],
    'max_retries': 3,
    'temp_images_dir': 'temp_images',
    'cleanup_interval_hours': 24
}

if not META_ACCESS_TOKEN:
    raise ValueError("META_ACCESS_TOKEN no está configurado en las variables de entorno")

if not GRUPO_SOPORTE_ID:
    print("ADVERTENCIA: GRUPO_SOPORTE_ID no está configurado en las variables de entorno.")

# --- SISTEMA DE GESTIÓN DE ARCHIVOS TEMPORALES ---
def ensure_temp_directory():
    if not os.path.exists(BOT_CONFIG['temp_images_dir']):
        os.makedirs(BOT_CONFIG['temp_images_dir'])
        print(f"Directorio creado: {BOT_CONFIG['temp_images_dir']}")

def generate_temp_filename(user_id, media_id, extension="jpg"):
    timestamp = int(time.time())
    hash_input = f"{user_id}_{media_id}_{timestamp}"
    file_hash = hashlib.md5(hash_input.encode()).hexdigest()[:12]
    return f"temp_{file_hash}_{timestamp}.{extension}"

def save_temp_image(image_content, filename):
    try:
        ensure_temp_directory()
        filepath = os.path.join(BOT_CONFIG['temp_images_dir'], filename)
        with open(filepath, 'wb') as f:
            f.write(image_content)
        print(f"Imagen temporal guardada: {filepath}")
        return filepath
    except Exception as e:
        print(f"Error guardando imagen temporal: {e}")
        return None

def load_temp_image(filepath):
    try:
        if os.path.exists(filepath):
            with open(filepath, 'rb') as f:
                return f.read()
        return None
    except Exception as e:
        print(f"Error cargando imagen temporal: {e}")
        return None

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

# --- FUNCIÓN PARA NOTIFICAR PAGOS AL SOPORTE ---
def notificar_pago_a_soporte(cliente_id, nombre_cliente, cedula_cliente, monto, banco, fecha, documento):
    if not GRUPO_SOPORTE_ID:
        return

    try:
        mensaje = f"✅ *NUEVO PAGO REGISTRADO (BOT)*\n\n"
        mensaje += f"👤 *Cliente:* {nombre_cliente.title()}\n"
        mensaje += f"🆔 *C.I./RUC:* {cedula_cliente}\n"
        mensaje += f"💰 *Monto:* ${monto}\n"
        mensaje += f"🏦 *Banco:* {banco}\n"
        mensaje += f"📅 *Fecha del Pago:* {fecha}\n"
        mensaje += f"📄 *Ref/Doc:* {documento}\n\n"
        mensaje += "El pago ha sido añadido a la hoja de cálculo para su posterior verificación."
        
        enviar_mensaje_whatsapp(GRUPO_SOPORTE_ID, mensaje)
    except Exception as e:
        print(f"Error notificando pago a soporte: {e}")


# --- FUNCIÓN PARA NOTIFICAR AL GRUPO DE SOPORTE ---
def notificar_grupo_soporte(cliente_id, nombre_cliente, tipo_problema, telefono_contacto=None, mensaje_cliente=None, cedula_cliente=None):
    if not GRUPO_SOPORTE_ID:
        print("No se puede enviar notificación: GRUPO_SOPORTE_ID no configurado")
        return False
    try:
        ahora_ajustado = datetime.now() - timedelta(hours=5)
        hora_actual = ahora_ajustado.strftime("%H:%M")
        fecha_actual = ahora_ajustado.strftime("%d/%m/%Y")
        
        mensaje_soporte = f"🚨 *NUEVA SOLICITUD DE SOPORTE*\n\n"
        mensaje_soporte += f"⏰ *Hora:* {hora_actual} - {fecha_actual}\n"
        mensaje_soporte += f"👤 *Cliente:* {nombre_cliente.title() if nombre_cliente else 'No identificado'}\n"
        
        if cedula_cliente:
            mensaje_soporte += f"🆔 *C.I./RUC:* {cedula_cliente}\n"
        
        mensaje_soporte += f"💬 *N° de WhatsApp (Cliente):* {cliente_id}\n"
        
        if telefono_contacto:
            mensaje_soporte += f"📱 *N° de Contacto (Indicado):* {telefono_contacto}\n"
            
        mensaje_soporte += f"🏷️ *Tipo:* {tipo_problema}\n"
        
        if mensaje_cliente:
            mensaje_soporte += f"📝 *Descripción del problema:*\n{mensaje_cliente}\n"
            
        mensaje_soporte += f"\n📲 *Responder directamente al cliente:* wa.me/{cliente_id}"
        
        return enviar_mensaje_whatsapp(GRUPO_SOPORTE_ID, mensaje_soporte)
    except Exception as e:
        print(f"Error enviando notificación al grupo de soporte: {e}")
        return False

# --- SISTEMA DE RATE LIMITING ---
RATE_LIMIT_FILE = "rate_limits.json"

def get_rate_limit_data():
    try:
        with open(RATE_LIMIT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_rate_limit_data(data):
    try:
        with open(RATE_LIMIT_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error guardando rate limits: {e}")

def check_rate_limit(user_id):
    try:
        data = get_rate_limit_data()
        now = datetime.now()
        cutoff = now - timedelta(hours=1)
        for uid in list(data.keys()):
            user_data = data[uid]
            user_data['timestamps'] = [
                ts for ts in user_data.get('timestamps', [])
                if datetime.fromisoformat(ts) > cutoff
            ]
            if not user_data['timestamps']:
                del data[uid]
        if user_id not in data:
            data[user_id] = {'timestamps': []}
        user_data = data[user_id]
        minute_ago = now - timedelta(minutes=1)
        recent_timestamps = [
            ts for ts in user_data['timestamps']
            if datetime.fromisoformat(ts) > minute_ago
        ]
        if len(recent_timestamps) >= BOT_CONFIG['max_messages_per_minute']:
            return False, f"Has alcanzado el límite de {BOT_CONFIG['max_messages_per_minute']} mensajes por minuto. Por favor, espera un momento."
        user_data['timestamps'] = recent_timestamps + [now.isoformat()]
        data[user_id] = user_data
        save_rate_limit_data(data)
        return True, ""
    except Exception as e:
        print(f"Error en rate limiting: {e}")
        return True, ""

# --- VALIDACIÓN DE CALIDAD DE IMAGEN ---
def validate_image_quality(image_content):
    try:
        if not image_content:
            return False, "❌ No se pudo obtener el contenido de la imagen."
        try:
            image = Image.open(BytesIO(image_content))
        except Exception:
            return False, "❌ El archivo no es una imagen válida. Por favor, envía un archivo JPG, PNG o WebP."
        if image.format not in BOT_CONFIG['supported_formats']:
            return False, f"❌ Formato no soportado ({image.format}). Por favor, envía una imagen en formato JPG, PNG o WebP."
        extremes = image.convert('L').getextrema()
        if extremes[0] == extremes[1]:
            return False, "❌ La imagen parece estar en blanco o muy oscura. Por favor, envía una imagen más clara."
        return True, "✅ Imagen válida"
    except Exception as e:
        print(f"Error validando imagen: {e}")
        return False, "❌ Error al validar la imagen. Por favor, intenta con otra imagen."

# --- COMANDOS DE CANCELACIÓN Y AYDA ---
QUICK_COMMANDS = {
    '/cancelar': 'Cancelar proceso actual',
    '/ayuda': 'Mostrar comandos disponibles',
    '/estado': 'Ver estado actual',
    '/reset': 'Reiniciar conversación',
    '/soporte': 'Transferir a soporte humano',
    '/limpieza': 'Limpiar archivos temporales (solo admin)'
}

def handle_quick_command(command, user_id):
    command = command.lower().strip()
    if command in ['/cancelar', '/reset']:
        borrar_estado(user_id)
        return "🔄 Proceso cancelado. Escribe 'hola' para empezar de nuevo."
    elif command == '/ayuda':
        help_text = "🤖 **Comandos disponibles:**\n\n"
        for cmd, desc in QUICK_COMMANDS.items():
            if cmd != '/limpieza':
                help_text += f"• `{cmd}` - {desc}\n"
        help_text += "\n💡 **Consejos:**\n"
        help_text += "• Para registrar un pago, envía la imagen del comprobante con el nombre del titular\n"
        help_text += "• Asegúrate de que la imagen sea clara y legible\n"
        help_text += "• Si tienes problemas, usa `/soporte` para hablar con un humano"
        return help_text
    elif command == '/estado':
        state = cargar_estado(user_id)
        if not state:
            return "📊 No tienes ningún proceso activo. Escribe 'hola' para comenzar."
        paso = state.get('paso', 'Desconocido')
        estado_msgs = {
            'awaiting_initial_action': 'Esperando que elijas una opción del menú principal',
            'awaiting_id_or_name': 'Esperando que proporciones cédula/RUC o nombre para un pago',
            'awaiting_receipt': 'Esperando que envíes el comprobante de pago',
            'awaiting_clarification': 'Esperando que elijas entre las opciones de clientes para un pago',
            'awaiting_support_name': 'Esperando nombres y apellidos del titular para reporte de soporte',
            'awaiting_support_clarification': 'Esperando que elijas entre las opciones de clientes para un reporte',
            'awaiting_support_phone': 'Esperando número de teléfono de contacto',
            'awaiting_support_description': 'Esperando descripción del problema',
            'human_takeover': 'Transferido a soporte humano'
        }
        estado_desc = estado_msgs.get(paso, f'Estado: {paso}')
        return f"📊 **Estado actual:** {estado_desc}\n\nUsa `/cancelar` si quieres empezar de nuevo."
    elif command == '/soporte':
        state = cargar_estado(user_id)
        nombre_cliente = state.get('apellidos_y_nombres', '')
        notificar_grupo_soporte(
            cliente_id=user_id,
            nombre_cliente=nombre_cliente,
            tipo_problema="Solicitud de soporte general",
            mensaje_cliente="Cliente solicitó soporte usando el comando /soporte"
        )
        borrar_estado(user_id)
        return "👨‍💻 Transfiriendo a soporte humano. En un momento, uno de nuestros agentes se pondrá en contacto contigo.\n\n*Para volver al bot automático, escribe `/reset`.*"
    elif command == '/limpieza' and user_id in ['admin_user_id']: # Reemplazar con el ID de admin real
        cleanup_temp_files()
        return "🧹 Limpieza de archivos temporales completada."
    return None

# --- MEJORES MENSAJES DE ERROR ---
class BotError:
    @staticmethod
    def network_error(): return "🌐 **Error de conexión**\n\nHay problemas de conectividad. Por favor, intenta de nuevo en unos momentos."
    @staticmethod
    def ocr_error(): return "👁️ **Error de lectura**\n\nNo pude leer el texto de la imagen. Por favor:\n• Asegúrate de que la imagen esté clara\n• Verifica que tenga buena iluminación\n• Evita imágenes borrosas o muy pequeñas"
    @staticmethod
    def invalid_receipt(): return "📄 **Comprobante no válido**\n\nLa imagen no parece ser un comprobante de pago válido. Asegúrate de que contenga:\n• Información del banco o entidad\n• Monto de la transacción\n• Fecha del pago\n• Datos del destinatario"
    @staticmethod
    def wrong_recipient(): return "🎯 **Destinatario incorrecto**\n\nEl comprobante no parece ser para TRONCALNET o nuestras cuentas autorizadas. Verifica que el pago sea hacia:\n• Cuentas de TRONCALNET\n• Rodriguez Quinteros\n• Números de cuenta autorizados"
    @staticmethod
    def duplicate_receipt(): return "🔄 **Comprobante duplicado**\n\nEste comprobante ya fue registrado anteriormente. Cada comprobante solo puede ser usado una vez.\n\nSi crees que es un error, contacta soporte con `/soporte`."
    @staticmethod
    def client_not_found(name): return f"👤 **Cliente no encontrado**\n\nNo encontré a '{name}' en nuestra base de datos.\n\n**Sugerencias:**\n• Verifica que el nombre esté completo\n• Intenta con la cédula/RUC\n• Usa `/soporte` si necesitas ayuda"
    @staticmethod
    def system_error(): return "⚠️ **Error del sistema**\n\nOcurrió un error técnico. Por favor:\n• Intenta de nuevo en unos momentos\n• Si persiste, usa `/soporte`\n• Como alternativa, escribe `/reset` para empezar de nuevo"
    @staticmethod
    def rate_limit_exceeded(): return "⏳ **Muchos mensajes**\n\nHas enviado muchos mensajes muy rápido. Por favor, espera un momento antes de continuar.\n\n💡 Tip: Puedes usar `/ayuda` para ver todos los comandos disponibles."
    @staticmethod
    def storage_error(): return "💾 **Error de almacenamiento**\n\nHay un problema temporal con el almacenamiento de archivos. Por favor, intenta de nuevo en unos momentos."

def handle_client_search(from_number, input_text, state, success_step, clarification_step):
    matches_with_scores = []
    if re.match(r'^\d{10,13}$', input_text):
        nombre_encontrado = buscar_nombre_por_id(input_text)
        if nombre_encontrado:
            matches_with_scores.append(((input_text, nombre_encontrado), 1000))
    else:
        matches_with_scores = buscar_id_por_nombre(input_text)

    if not matches_with_scores:
        enviar_mensaje_whatsapp(from_number, BotError.client_not_found(input_text))
        return None

    is_unique_match = False
    if len(matches_with_scores) == 1:
        is_unique_match = True
    elif len(matches_with_scores) > 1:
        top_score = matches_with_scores[0][1]
        second_score = matches_with_scores[1][1]
        if top_score > (second_score * 4):
            is_unique_match = True

    if is_unique_match:
        (cedula, nombre), _ = matches_with_scores[0]
        guardar_estado(from_number, {**state, "paso": success_step, "cedula": cedula, "apellidos_y_nombres": nombre})
        return cedula, nombre
    else:
        matches_to_show = [match[0] for match in matches_with_scores]
        mensaje_opciones = "Encontré varios clientes con ese nombre. ¿A cuál te refieres?"
        botones_clientes = []
        for i, (cedula, nombre) in enumerate(matches_to_show[:3]):
            btn_title = f"{nombre.split()[0]} {nombre.split()[-1] if len(nombre.split()) > 1 else ''} - {cedula[-4:]}"
            botones_clientes.append({"id": f"cliente_{i}", "title": btn_title[:20]})
        
        enviar_mensaje_whatsapp(from_number, mensaje_opciones, botones_clientes)
        guardar_estado(from_number, {**state, "paso": clarification_step, "matches": matches_to_show})
        return None

def contiene_nombre_empresa(texto_completo):
    if not texto_completo: return False
    return "troncalnet" in texto_completo.lower()

def validar_destino_pago(texto_completo):
    if not texto_completo:
        return False
    texto_lower = texto_completo.lower()
    nombres_validos = ["rodriguez", "quinteros", "ismael"]
    conteo_nombres = sum(1 for nombre in nombres_validos if nombre in texto_lower)
    return conteo_nombres >= 1

def es_comprobante_valido(texto_completo):
    if not texto_completo: return False
    texto_lower = texto_completo.lower()

    # Normalizar acentos
    texto_normalizado = texto_lower
    for char_in, char_out in [('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u')]:
        texto_normalizado = texto_normalizado.replace(char_in, char_out)

    palabras_transaccion = {'transferencia', 'pago exitoso', 'comprobante', 'transaccion', 'deposito', 'transferido'}
    tiene_palabra_transaccion = any(palabra in texto_normalizado for palabra in palabras_transaccion)
    
    tiene_monto = bool(re.search(r'[\d,]+\.\d{2}', texto_normalizado))

    bancos = {'pichincha', 'guayaquil', 'produbanco', 'jep', 'jardin azuayo', 'bolivariano', 'pacifico', 'internacional', 'cb'}
    tiene_banco = any(banco in texto_normalizado for banco in bancos)
    
    # Lista de palabras clave ampliada
    palabras_financieras = {'cuenta', 'monto', 'valor', 'fecha', 'total', 'efectivo', 'documento', 'nombre', 'destino'}
    
    tiene_palabra_financiera = any(palabra in palabras_financieras for palabra in texto_normalizado)
    
    condiciones_cumplidas = sum([tiene_palabra_transaccion, tiene_monto, tiene_banco, tiene_palabra_financiera])
    print(f"DEBUG: Condiciones cumplidas para ser comprobante: {condiciones_cumplidas}/4")
    return condiciones_cumplidas >= 3

def es_recaudacion_directa(texto_completo):
    if not texto_completo: return False
    texto_normalizado = texto_completo.lower()
    for char_in, char_out in [('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u')]:
        texto_normalizado = texto_normalizado.replace(char_in, char_out)
    if not re.search(r'troncalnet', texto_normalizado): return False
    frases_clave = ["de recaudacion", "recaudaciones", "pago en efectivo", "empresa o servicio", "pago de servicio", "pago de servicios", "cuenta o contrato"]
    if any(frase in texto_normalizado for frase in frases_clave):
        print("DEBUG: Detectado como recaudación directa.")
        return True
    return False

def analizar_intencion(texto):
    if not texto:
        return None

    texto_normalizado = texto.lower()
    for char_in, char_out in [('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u')]:
        texto_normalizado = texto_normalizado.replace(char_in, char_out)

    intenciones = {
        "SIN_INTERNET": [
            "sin internet", "no tengo internet", "internet lento", "falla el internet",
            "inestable", "no puedo navegar", "se me va el internet", "no hay servicio"
        ],
        "SIN_TV": [
            "sin señal", "no tengo canales", "falla la tele", "problema con el tvcable",
            "canales no se ven", "falla el cable"
        ],
        "PROBLEMA_PAGO": [
            "problema con mi pago", "no se registra mi pago", "pago no aplicado",
            "error en la factura", "cobro indebido",
            "inconveniente con el pago", "pague y no se refleja", "mi pago no aparece",
            "duda sobre mi pago", "error en el pago", "ya pague", "ya pagué",
            "tengo un problema con un pago"
        ],
        "INFO_PLANES": [
            "informacion de planes", "quiero un plan", "que planes tienen",
            "aumentar megas", "cambiar de plan"
        ]
    }

    scores = {intent: 0 for intent in intenciones}
    for intent, keywords in intenciones.items():
        for keyword in keywords:
            if keyword in texto_normalizado:
                scores[intent] += 1
    
    max_score = 0
    detected_intent = None
    for intent, score in scores.items():
        if score > max_score:
            max_score = score
            detected_intent = intent
    
    if detected_intent:
        print(f"Intención detectada: {detected_intent} con puntuación {max_score}")
        return detected_intent
    
    return None

def detectar_intencion_de_soporte(texto_completo):
    intencion = analizar_intencion(texto_completo)
    if intencion in ["SIN_INTERNET", "SIN_TV"]:
        return True
    return False

def buscar_monto(texto_completo):
    if not texto_completo: return "0.00"
    
    patrones_monto = [
        r'(?:monto|valor|total|pago)\s*:?\s*(?:usd|\$)?\s*([\d,]+\.\d{2})',
        r'(?:usd|\$)\s*([\d,]+\.\d{2})'
    ]
    
    montos_encontrados = []
    for patron in patrones_monto:
        matches = re.findall(patron, texto_completo, re.IGNORECASE)
        for match in matches:
            monto_limpio = match.replace(',', '')
            try:
                montos_encontrados.append(float(monto_limpio))
            except ValueError:
                continue
    
    if montos_encontrados:
        return f"{max(montos_encontrados):.2f}"
        
    matches_generales = re.findall(r'([\d,]+\.\d{2})', texto_completo)
    for match in matches_generales:
        monto_limpio = match.replace(',', '')
        try:
            if float(monto_limpio) > 0:
                montos_encontrados.append(float(monto_limpio))
        except ValueError:
            continue
            
    if montos_encontrados:
        return f"{max(montos_encontrados):.2f}"

    return "0.00"

def buscar_fecha(texto_completo):
    if not texto_completo:
        return datetime.now().strftime("%d/%m/%Y")

    texto_lower = texto_completo.lower()
    meses_es = {
        'ene': '01', 'feb': '02', 'mar': '03', 'abr': '04', 'may': '05', 'jun': '06',
        'jul': '07', 'ago': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dic': '12'
    }

    match = re.search(r'(\d{1,2})[/\s-]([a-zA-Z]{3})[/\s-](\d{2,4})', texto_lower)
    if match:
        d, M, y = match.groups()
        m = meses_es.get(M, '00')
        if len(y) == 2: y = '20' + y
        return f"{d.zfill(2)}/{m}/{y}"

    match = re.search(r'(\d{4})[/\s-]([a-zA-Z]{3})[/\s-](\d{1,2})', texto_lower)
    if match:
        y, M, d = match.groups()
        m = meses_es.get(M, '00')
        return f"{d.zfill(2)}/{m}/{y}"

    match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', texto_lower)
    if match:
        d, m, y = match.groups()
        if len(y) == 2: y = '20' + y
        return f"{d.zfill(2)}/{m.zfill(2)}/{y}"

    match = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', texto_lower)
    if match:
        y, m, d = match.groups()
        return f"{d.zfill(2)}/{m.zfill(2)}/{y}"

    return datetime.now().strftime("%d/%m/%Y")


def identificar_banco(texto_completo):
    if not texto_completo: return "Entidad no identificada"
    texto_lower = texto_completo.lower()

    # MEJORA: Normalizar texto para ignorar acentos
    texto_normalizado = texto_lower
    for char_in, char_out in [('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u')]:
        texto_normalizado = texto_normalizado.replace(char_in, char_out)

    BANCOS_ECUADOR = {
        "Banco del Pacífico": ["pacifico", "bancodelpacifico", "banco del pacifico", "bdp", "del pacifico"],
        "Banco Pichincha": ["pichincha", "banco pichincha"],
        "Banco Guayaquil": ["guayaquil", "bancoguayaquil", "banco guayaquil"],
        "Produbanco": ["produbanco", "prodomatico"],
        "Banco Bolivariano": ["bolivariano", "banco bolivariano"],
        "Banco Internacional": ["internacional", "banco internacional"],
        "Banco Austro": ["austro", "banco austro"],
        "Cooperativa JEP": ["jep", "coop. jep", "cooperativa jep"],
        "Cooperativa Jardín Azuayo": ["jardin azuayo", "cooperativa jardin azuayo"],
        "Cooperativa Lucha Campesina": ["lucha campesina", "cooperativa lucha campesina"],
        "Cooperativa CB": ["cooperativa cb", "cb en linea", "coop. cb", "coop cb", "cb cooperativa", "cb movil", "biblian"]
    }

    for banco, keywords in BANCOS_ECUADOR.items():
        keywords_sorted = sorted(keywords, key=len, reverse=True)
        for keyword in keywords_sorted:
            if keyword in texto_normalizado:
                return banco

    return "Entidad no identificada"

def buscar_numero_documento(texto_completo):
    if not texto_completo:
        return "No encontrado"

    texto_normalizado = re.sub(r'[^\w\s.]', ' ', texto_completo).lower()

    nombres_bancos = [
        'pichincha', 'guayaquil', 'produbanco', 'jep',
        'jardin azuayo', 'bolivariano', 'pacifico', 'internacional'
    ]

    patrones_combinados = [
        r'\bno\.(jm\d{4}[a-z]{3}\d+)\b',
        r'\bno\.\s*([a-zA-Z0-9]{10,})\b',
        r'(?:No\.|Nro\.|Número\s+de)?\s*Transacci[oó]n\s*:?:?\s*#?\s*([a-zA-Z0-9-]{6,25})\b',
        r'Cod\.\s*Movimiento\s*:?:?\s*([a-zA-Z0-9]{6,25})\b',
        r'(?:Comprobante|No\.|Referencia|Ref|Secuencial|Documento|Movimiento|Cod|Doc)\.?\s*:?:?\s*([a-zA-Z0-9-]{6,25})\b',
        r'\b([a-zA-Z0-9]{7,25})\b(?=.*\d)',
        r'\b(\d{9,25})\b'
    ]

    found_ids = []

    for patron in patrones_combinados:
        for match in re.finditer(patron, texto_normalizado, re.IGNORECASE):
            document_id = match.group(1).strip() if match.group(1) else match.group(0).strip()

            if document_id.lower() in nombres_bancos or document_id.lower() in ['numero', 'codigo', 'comprobante', 'referencia']:
                continue

            if re.fullmatch(r'\d{1,3}(?:,\d{3})*\.\d{2}', document_id): continue
            if re.fullmatch(r'\d{1,2}/\d{1,2}/\d{2,4}', document_id): continue

            if len(document_id) >= 6 and (re.search(r'\d', document_id) or len(document_id) > 8):
                found_ids.append(document_id)

    if found_ids:
        return found_ids[0].upper()

    return "No encontrado"

# --- INICIO DE LA SECCIÓN REFACTORIZADA ---
def handle_client_search(from_number, input_text, state, success_step, clarification_step):
    matches_with_scores = []
    if re.match(r'^\d{10,13}$', input_text):
        nombre_encontrado = buscar_nombre_por_id(input_text)
        if nombre_encontrado:
            matches_with_scores.append(((input_text, nombre_encontrado), 1000))
    else:
        matches_with_scores = buscar_id_por_nombre(input_text)

    if not matches_with_scores:
        enviar_mensaje_whatsapp(from_number, BotError.client_not_found(input_text))
        return None

    is_unique_match = False
    if len(matches_with_scores) == 1:
        is_unique_match = True
    elif len(matches_with_scores) > 1:
        top_score = matches_with_scores[0][1]
        second_score = matches_with_scores[1][1]
        if top_score > (second_score * 4):
            is_unique_match = True

    if is_unique_match:
        (cedula, nombre), _ = matches_with_scores[0]
        guardar_estado(from_number, {**state, "paso": success_step, "cedula": cedula, "apellidos_y_nombres": nombre})
        return cedula, nombre
    else:
        matches_to_show = [match[0] for match in matches_with_scores]
        mensaje_opciones = "Encontré varios clientes con ese nombre. ¿A cuál te refieres?"
        botones_clientes = []
        for i, (cedula, nombre) in enumerate(matches_to_show[:3]):
            btn_title = f"{nombre.split()[0]} {nombre.split()[-1] if len(nombre.split()) > 1 else ''} - {cedula[-4:]}"
            botones_clientes.append({"id": f"cliente_{i}", "title": btn_title[:20]})
        
        enviar_mensaje_whatsapp(from_number, mensaje_opciones, botones_clientes)
        guardar_estado(from_number, {**state, "paso": clarification_step, "matches": matches_to_show})
        return None

# --- WEBHOOK PRINCIPAL ---
@app.route("/whatsapp", methods=["GET", "POST"])
def whatsapp_webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == META_VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Error, token de verificación incorrecto", 403

    try:
        data = request.get_json()
        if not (data and data.get("entry") and data["entry"][0].get("changes") and data["entry"][0]["changes"][0].get("value", {}).get("messages")):
            return "OK", 200

        message_data = data["entry"][0]["changes"][0]["value"]["messages"][0]
        from_number = message_data["from"]

        allowed, _ = check_rate_limit(from_number)
        if not allowed:
            enviar_mensaje_whatsapp(from_number, BotError.rate_limit_exceeded())
            return "OK", 200

        enviar_accion_escritura(from_number, 'typing_on')

        msg_type = message_data.get("type", "")
        msg_body = ""
        caption = ""

        if msg_type == "text":
            msg_body = message_data["text"]["body"].strip()
        elif msg_type == "audio":
            audio_id = message_data["audio"]["id"]
            enviar_mensaje_whatsapp(from_number, "🎙️ Recibí tu audio, lo estoy procesando...")
            transcribed_text, message = transcribe_audio(audio_id)
            if transcribed_text:
                print(f"DEBUG: Texto transcrito del audio: '{transcribed_text}'")
                msg_body = transcribed_text
            else:
                enviar_mensaje_whatsapp(from_number, f"No pude entender el audio. Por favor, intenta de nuevo o escribe tu consulta. (Error: {message})")
                return "OK", 200
        elif msg_type == 'image' and message_data.get('image', {}).get('caption'):
            caption = message_data['image']['caption'].strip()
        elif msg_type == 'document' and message_data.get('document', {}).get('caption'):
            caption = message_data['document']['caption'].strip()
        elif msg_type == "interactive" and message_data.get("interactive", {}).get("type") == "button_reply":
            msg_body = message_data["interactive"]["button_reply"].get("id", "")

        command_text = msg_body or caption
        if command_text.startswith('/'):
            command_response = handle_quick_command(command_text, from_number)
            if command_response:
                if command_text == '/soporte':
                    guardar_estado(from_number, {"paso": "human_takeover"})
                enviar_mensaje_whatsapp(from_number, command_response)
                return "OK", 200

        state = cargar_estado(from_number)
        paso = state.get("paso")

        RESTART_KEYWORDS = {'reset', 'hola', 'menú', 'menu', 'inicio'}
        text_for_check = (msg_body or caption or "").lower().strip()
        
        if text_for_check == "finalizar":
            borrar_estado(from_number)
            mensaje_despedida = "¡Gracias por contactar a TRONCALNET! 😊\n\nSi necesitas algo más, aquí estoy para ayudarte."
            botones_finales = [{"id": "reset", "title": "Menú principal"}]
            enviar_mensaje_whatsapp(from_number, mensaje_despedida, botones_finales)
            return "OK", 200
        
        if any(text_for_check.startswith(keyword) for keyword in RESTART_KEYWORDS):
            borrar_estado(from_number)
            state = {}
            paso = None

        if paso == "human_takeover":
            return "OK", 200
        
        if (msg_type == 'image' and not caption) or (msg_type == 'document' and message_data.get('document', {}).get('filename', '').lower().endswith('.pdf')):
            if paso not in ['awaiting_receipt', 'awaiting_id_or_name', 'awaiting_clarification', 'awaiting_support_clarification'] and not state.get("cedula"):
                enviar_mensaje_whatsapp(from_number, "Recibí tu comprobante. 📄\n\nPor favor, escribe el nombre completo o la cédula del titular para registrarlo.")
                guardar_estado(from_number, {
                    'paso': 'awaiting_id_for_file',
                    'media_id': message_data[msg_type]['id'],
                    'is_pdf': msg_type == 'document'
                })
                return "OK", 200
        
        # --- LÓGICA REESTRUCTURADA ---
        
        if not paso:
            mensaje_bienvenida = "¡Hola! 👋 Soy el asistente virtual de TRONCALNET.\n\n¿Cómo puedo ayudarte hoy?"
            botones_menu = [{"id": "opcion_1", "title": "Registrar un pago"}, {"id": "opcion_2", "title": "Consultar planes"}, {"id": "opcion_3", "title": "Reportar un problema"}]
            enviar_mensaje_whatsapp(from_number, mensaje_bienvenida, botones_menu)
            guardar_estado(from_number, {"paso": "awaiting_initial_action"})
        
        elif paso == "awaiting_initial_action":
            # Primero manejamos los botones que son una respuesta exacta
            if msg_body == 'opcion_1':
                mensaje_solicitud_datos = "Para registrar tu pago, por favor, envía los nombres y apellidos o su numero de cedula del titular del contrato."
                enviar_mensaje_whatsapp(from_number, mensaje_solicitud_datos)
                guardar_estado(from_number, {"paso": "awaiting_id_or_name"})
            elif msg_body == 'opcion_2':
                nombre_cliente = state.get('apellidos_y_nombres', '')
                notificar_grupo_soporte(
                    cliente_id=from_number, nombre_cliente=nombre_cliente,
                    tipo_problema="Consulta de planes", mensaje_cliente="Cliente seleccionó la opción para consultar planes."
                )
                mensaje = "¡Perfecto! 📋\n\nEn un momento, uno de nuestros asesores se pondrá en contacto contigo."
                botones_reset = [{"id": "reset", "title": "⬅️ Volver al menú"}]
                enviar_mensaje_whatsapp(from_number, mensaje, botones_reset)
                guardar_estado(from_number, {"paso": "human_takeover"})
            elif msg_body == 'opcion_3':
                mensaje_tipo_problema = "Entendido. Para dirigirte al área correcta, por favor, selecciona el tipo de problema que deseas reportar:"
                botones_problema = [
                    {"id": "report_tecnico", "title": "Internet o TVCable"},
                    {"id": "report_pago", "title": "Problemas con Pagos"}
                ]
                enviar_mensaje_whatsapp(from_number, mensaje_tipo_problema, botones_problema)
                guardar_estado(from_number, {"paso": "awaiting_problem_type"})
            
            # Si no es un botón, analizamos la intención del texto
            else:
                intencion_detectada = analizar_intencion(msg_body or caption)
                if intencion_detectada in ["SIN_INTERNET", "SIN_TV", "PROBLEMA_PAGO"]:
                    tipo_problema_str = "un problema con tu servicio."
                    if intencion_detectada == "SIN_INTERNET":
                        tipo_problema_str = "un problema con tu servicio de internet."
                    elif intencion_detectada == "SIN_TV":
                        tipo_problema_str = "una falla en tu servicio de TV Cable."
                    elif intencion_detectada == "PROBLEMA_PAGO":
                        tipo_problema_str = "un inconveniente con un pago."

                    mensaje_inicio_flujo = (
                        f"¡Entendido! 🛠️ Detecté que podrías tener {tipo_problema_str}\n\n"
                        "Para ayudarte mejor, necesito verificar al titular. Por favor, escríbeme los *nombres y apellidos* o la *cédula/RUC* del titular del contrato."
                    )
                    enviar_mensaje_whatsapp(from_number, mensaje_inicio_flujo)
                    guardar_estado(from_number, {"paso": "awaiting_support_name"})
                
                elif intencion_detectada == "INFO_PLANES":
                    notificar_grupo_soporte(
                        cliente_id=from_number, nombre_cliente=state.get('apellidos_y_nombres', ''),
                        tipo_problema="Consulta de planes", mensaje_cliente=f"Cliente consultó por planes con el mensaje: '{msg_body}'"
                    )
                    mensaje = "¡Perfecto! 📋 Veo que necesitas información sobre nuestros planes.\n\nEn un momento, uno de nuestros asesores se pondrá en contacto contigo para darte la mejor oferta."
                    botones_reset = [{"id": "reset", "title": "⬅️ Volver al menú"}]
                    enviar_mensaje_whatsapp(from_number, mensaje, botones_reset)
                    guardar_estado(from_number, {"paso": "human_takeover"})
                else:
                    enviar_mensaje_whatsapp(from_number, "Por favor, selecciona una de las opciones disponibles presionando los botones.")
        
        elif paso == "awaiting_problem_type":
            if msg_body == 'report_pago':
                problem_type = "Problema con Pago"
                state = cargar_estado(from_number)
                state['problem_type'] = problem_type
                state['paso'] = 'awaiting_support_name'
                mensaje_solicitud_nombre = "Perfecto. Para continuar con tu reporte, por favor, escríbeme los *nombres y apellidos* o la *cédula/RUC* del titular del contrato."
                enviar_mensaje_whatsapp(from_number, mensaje_solicitud_nombre)
                guardar_estado(from_number, state)
            elif msg_body == 'report_tecnico':
                state = cargar_estado(from_number)
                state['problem_type'] = "Falla de Internet/TV"
                mensaje_paso_previo = "Entendido. Antes de crear un reporte, un paso simple suele solucionar muchos problemas de internet o TV.\n\n¿Ya intentaste apagar y encender tu router/decodificador durante 30 segundos?"
                botones = [
                    {"id": "restart_yes", "title": "Sí, ya lo intenté"},
                    {"id": "restart_no", "title": "No, déjame intentar"}
                ]
                enviar_mensaje_whatsapp(from_number, mensaje_paso_previo, botones)
                state['paso'] = 'awaiting_router_restart_confirm'
                guardar_estado(from_number, state)
            else:
                enviar_mensaje_whatsapp(from_number, "Por favor, selecciona una de las dos opciones usando los botones.")

        elif paso == "awaiting_router_restart_confirm":
            if msg_body == 'restart_yes':
                mensaje_solicitud_nombre = "De acuerdo. Para continuar y crear tu ticket de soporte, por favor, escríbeme los *nombres y apellidos* o la *cédula/RUC* del titular del contrato."
                enviar_mensaje_whatsapp(from_number, mensaje_solicitud_nombre)
                guardar_estado(from_number, {**state, "paso": "awaiting_support_name"})
            elif msg_body == 'restart_no':
                mensaje_instruccion = ("Ok. Por favor, desconecta el equipo (router o decodificador) de la corriente, espera 30 segundos y vuelve a conectarlo. "
                                       "Luego, espera unos 5 minutos a que se estabilicen las luces.\n\n"
                                       "¿Hacer esto solucionó el problema?")
                botones = [
                    {"id": "restart_solved", "title": "Sí, se solucionó"},
                    {"id": "restart_not_solved", "title": "No, sigue igual"}
                ]
                enviar_mensaje_whatsapp(from_number, mensaje_instruccion, botones)
                guardar_estado(from_number, {**state, "paso": "awaiting_restart_result"})
            else:
                enviar_mensaje_whatsapp(from_number, "Por favor, selecciona una de las opciones con los botones.")

        elif paso == "awaiting_restart_result":
            if msg_body == 'restart_solved':
                mensaje_final = "¡Excelente! Me alegra que se haya solucionado. Si necesitas algo más, no dudes en escribir 'hola' para volver al menú principal."
                enviar_mensaje_whatsapp(from_number, mensaje_final)
                borrar_estado(from_number)
            elif msg_body == 'restart_not_solved':
                mensaje_solicitud_nombre = "Lamento escuchar eso. Vamos a crear tu ticket de soporte. Por favor, escríbeme los *nombres y apellidos* o la *cédula/RUC* del titular del contrato."
                enviar_mensaje_whatsapp(from_number, mensaje_solicitud_nombre)
                guardar_estado(from_number, {**state, "paso": "awaiting_support_name"})
            else:
                enviar_mensaje_whatsapp(from_number, "Por favor, responde usando los botones para saber si el problema se solucionó.")

        elif paso == "awaiting_support_name":
            if (msg_body or caption):
                input_text = (msg_body or caption).strip()
                if input_text:
                    cliente_encontrado = handle_client_search(from_number, input_text, state, "awaiting_support_phone", "awaiting_support_clarification")
                    if cliente_encontrado:
                        nombre = cliente_encontrado[1]
                        mensaje_segundo_paso = (f"✅ **Titular verificado:** {nombre.title()}\n\n"
                                              "Ahora, por favor, compárteme un *número de teléfono de contacto*.")
                        enviar_mensaje_whatsapp(from_number, mensaje_segundo_paso)
            else:
                enviar_mensaje_whatsapp(from_number, "Por favor, escribe los nombres y apellidos o la cédula del titular del contrato.")

        elif paso in ["awaiting_clarification", "awaiting_support_clarification"]:
            if msg_body.startswith("cliente_"):
                try:
                    index = int(msg_body.split("_")[1])
                    matches = state.get("matches", [])
                    if 0 <= index < len(matches):
                        cedula, nombre = matches[index]
                        
                        if state.get('paso') == 'awaiting_support_clarification':
                            mensaje = (f"✅ **Titular seleccionado:** {nombre.title()}\n\n"
                                       "Ahora, por favor, compárteme un *número de teléfono de contacto*.")
                            next_step = 'awaiting_support_phone'
                        else:
                            mensaje = f"✅ Cliente seleccionado: *{nombre.title()}*\n\nAhora, por favor, envía la imagen o el PDF del comprobante."
                            next_step = 'awaiting_receipt'

                        enviar_mensaje_whatsapp(from_number, mensaje)
                        guardar_estado(from_number, {"paso": next_step, "cedula": cedula, "apellidos_y_nombres": nombre})
                    else:
                        enviar_mensaje_whatsapp(from_number, "Selección inválida. Por favor, elige una opción.")
                except (ValueError, IndexError):
                    enviar_mensaje_whatsapp(from_number, "Error en la selección. Por favor, usa los botones.")
            else:
                enviar_mensaje_whatsapp(from_number, "Por favor, selecciona uno de los clientes usando los botones.")

        elif paso == "awaiting_support_phone":
            if (msg_body or caption):
                input_text = (msg_body or caption).strip()
                telefono_contacto = None
                if input_text.lower() in ["este numero", "este número", "este", "mismo numero", "mismo número", "este mismo"]:
                    telefono_contacto = from_number
                else:
                    cleaned_number = re.sub(r'\D', '', input_text)
                    if cleaned_number.startswith('09') and len(cleaned_number) == 10:
                        telefono_contacto = cleaned_number
                    elif cleaned_number.startswith('5939') and len(cleaned_number) == 12:
                        telefono_contacto = '0' + cleaned_number[3:]
                    elif cleaned_number.startswith('9') and len(cleaned_number) == 9:
                        telefono_contacto = '0' + cleaned_number
                
                if telefono_contacto:
                    state["support_phone"] = telefono_contacto
                    
                    problem_type = state.get("problem_type", "Reporte General")

                    if problem_type == "Problema con Pago":
                        mensaje_tercer_paso = (f"✅ **Teléfono:** {telefono_contacto}\n\n"
                                             "Entendido. Ahora, por favor, describe detalladamente el *inconveniente con tu pago*.\n\n"
                                             "📝 Por ejemplo: 'Pagué el día X pero aún no se refleja', 'Tengo un cobro doble', o 'No estoy seguro de cuánto debo pagar'.")
                    else: # Por defecto, se usa el mensaje técnico
                        mensaje_tercer_paso = (f"✅ **Teléfono:** {telefono_contacto}\n\n"
                                             "Ahora, por favor, describe detalladamente el *problema que estás experimentando*.\n\n"
                                             "📝 Incluye toda la información que consideres relevante (presenta luz roja, cables rotos, no aparece el nombre de la red).")

                    enviar_mensaje_whatsapp(from_number, mensaje_tercer_paso)
                    guardar_estado(from_number, {**state, "paso": "awaiting_support_description"})
                else:
                    mensaje_error_telefono = "❌ Número no válido.\n\nPor favor, ingresa un número de celular de 10 dígitos (ej: 0987654321) o escribe \"este número\"."
                    enviar_mensaje_whatsapp(from_number, mensaje_error_telefono)
            else:
                enviar_mensaje_whatsapp(from_number, "Por favor, escribe tu número de contacto o la frase \"este número\".")

        elif paso == "awaiting_support_description":
            if (msg_body or caption):
                descripcion_problema = (msg_body or caption).strip()

                if len(descripcion_problema) < 10 or descripcion_problema.isdigit():
                    mensaje_error = "📝 Por favor, describe el problema con más detalle usando solo texto. Tu descripción es muy corta o parece ser un número."
                    enviar_mensaje_whatsapp(from_number, mensaje_error)
                    return "OK", 200
                
                # ✅ NUEVO: Verificar si ya se envió un ticket para esta conversación
                if state.get("ticket_enviado"):
                    mensaje_ya_enviado = "✅ Tu reporte ya fue registrado anteriormente. Nuestro equipo se pondrá en contacto contigo pronto.\n\n¿Necesitas reportar algo diferente? Escribe 'menú' para volver al inicio."
                    enviar_mensaje_whatsapp(from_number, mensaje_ya_enviado)
                    return "OK", 200
                
                nombre_titular = state.get("apellidos_y_nombres", "No proporcionado")
                cedula_titular = state.get("cedula", "No proporcionado")
                telefono_contacto = state.get("support_phone", from_number)
                problem_type_from_state = state.get("problem_type", "Reporte General")
                
                notificar_grupo_soporte(cliente_id=from_number, nombre_cliente=nombre_titular, tipo_problema=problem_type_from_state, telefono_contacto=telefono_contacto, mensaje_cliente=descripcion_problema, cedula_cliente=cedula_titular)

                # ✅ NUEVO: Marcar que el ticket ya fue enviado
                state["ticket_enviado"] = True
                guardar_estado(from_number, state)

                mensaje_confirmacion = (f"✅ **¡Reporte registrado exitosamente!**\n\n"
                                        f"👤 **Titular:** {nombre_titular.title()}\n"
                                        f"🆔 **C.I./RUC:** {cedula_titular}\n"
                                        f"📱 **Contacto:** {telefono_contacto}\n\n"
                                        "🚀 Nuestro equipo técnico revisará tu caso y se pondrá en contacto contigo lo antes posible.")
                enviar_mensaje_whatsapp(from_number, mensaje_confirmacion)
                borrar_estado(from_number) 
                
                time.sleep(1) 

                mensaje_siguiente_paso = "¿Puedo ayudarte en algo más?"
                botones_menu = [
                    {"id": "opcion_1", "title": "Registrar un pago"},
                    {"id": "opcion_2", "title": "Consultar planes"},
                    {"id": "finalizar", "title": "No, gracias"}
                ]
                enviar_mensaje_whatsapp(from_number, mensaje_siguiente_paso, botones_menu)
                
                guardar_estado(from_number, {"paso": "awaiting_initial_action"})
            else:
                enviar_mensaje_whatsapp(from_number, "Por favor, describe el problema que estás experimentando.")

        elif paso == "awaiting_id_or_name":
            if (msg_body or caption):
                input_text = (msg_body or caption).strip()
                if input_text:
                    cliente_encontrado = handle_client_search(from_number, input_text, state, "awaiting_receipt", "awaiting_clarification")
                    if cliente_encontrado:
                        nombre = cliente_encontrado[1]
                        mensaje = f"✅ Cliente encontrado: *{nombre.title()}*\n\nAhora, por favor, envía la imagen o el PDF del comprobante."
                        enviar_mensaje_whatsapp(from_number, mensaje)
            else:
                enviar_mensaje_whatsapp(from_number, "Por favor, envía el nombre o la cédula como texto.")
                
        elif paso == 'awaiting_id_for_file':
            if (msg_body or caption):
                input_text = (msg_body or caption).strip()
                if input_text:
                    media_id = state.get('media_id')
                    is_pdf = state.get('is_pdf', False)
                    
                    matches_with_scores = []
                    if re.match(r'^\d{10,13}$', input_text):
                        nombre = buscar_nombre_por_id(input_text)
                        if nombre: matches_with_scores.append(((input_text, nombre), 1000))
                    else:
                        matches_with_scores = buscar_id_por_nombre(input_text)

                    if not matches_with_scores:
                        enviar_mensaje_whatsapp(from_number, BotError.client_not_found(input_text))
                        return "OK", 200

                    is_unique_match = False
                    if len(matches_with_scores) == 1:
                        is_unique_match = True
                    elif len(matches_with_scores) > 1:
                        top_score = matches_with_scores[0][1]
                        second_score = matches_with_scores[1][1]
                        if top_score > (second_score * 4):
                            is_unique_match = True
                    
                    if is_unique_match:
                        (id_cliente, nombre_cliente), _ = matches_with_scores[0]
                        new_state = {"cedula": id_cliente, "apellidos_y_nombres": nombre_cliente}
                        if is_pdf:
                            process_payment_document(from_number, media_id, new_state)
                        else:
                            process_payment_image(from_number, media_id, new_state)
                    else:
                        matches_to_show = [match[0] for match in matches_with_scores]
                        mensaje_opciones = "Encontré varios clientes con ese nombre. ¿A cuál te refieres?"
                        botones_clientes = []
                        for i, (cedula, nombre) in enumerate(matches_to_show[:3]):
                            btn_title = f"{nombre.split()[0]} {nombre.split()[-1] if len(nombre.split()) > 1 else ''} - {cedula[-4:]}"
                            botones_clientes.append({"id": f"cliente_{i}", "title": btn_title[:20]})
                        
                        enviar_mensaje_whatsapp(from_number, mensaje_opciones, botones_clientes)
                        guardar_estado(from_number, {
                            "paso": "awaiting_clarification_for_file", 
                            "matches": matches_to_show,
                            "media_id": media_id,
                            "is_pdf": is_pdf
                        })
            else:
                enviar_mensaje_whatsapp(from_number, "Por favor, envía el nombre o la cédula como texto.")

        elif paso == "awaiting_receipt":
            if msg_type == "image":
                process_payment_image(from_number, message_data["image"]["id"], state)
            elif msg_type == "document" and message_data["document"].get("filename", "").lower().endswith('.pdf'):
                process_payment_document(from_number, message_data["document"]["id"], state)
            else:
                enviar_mensaje_whatsapp(from_number, "📷 **Esperando comprobante**\n\nPor favor, envía una imagen o un archivo PDF del comprobante.")

        elif paso == "awaiting_clarification_for_file":
            if msg_body.startswith("cliente_"):
                try:
                    index = int(msg_body.split("_")[1])
                    matches = state.get("matches", [])
                    if 0 <= index < len(matches):
                        cedula, nombre = matches[index]
                        
                        media_id = state.get('media_id')
                        is_pdf = state.get('is_pdf', False)
                        new_state = {"cedula": cedula, "apellidos_y_nombres": nombre}
                        
                        if is_pdf:
                            process_payment_document(from_number, media_id, new_state)
                        else:
                            process_payment_image(from_number, media_id, new_state)
                    else:
                        enviar_mensaje_whatsapp(from_number, "Selección inválida. Por favor, elige una de las opciones.")
                except (ValueError, IndexError):
                    enviar_mensaje_whatsapp(from_number, "Error en la selección. Por favor, usa los botones.")
            else:
                enviar_mensaje_whatsapp(from_number, "Por favor, selecciona uno de los clientes usando los botones.")

        return "OK", 200

    except Exception as e:
        print(f"Error en webhook: {e}")
        traceback.print_exc()
        return "Error interno", 500
        
if __name__ == "__main__":
    init_db()
    cleanup_temp_files()
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Servidor iniciado en el puerto {port}...")
    serve(app, host="0.0.0.0", port=port)
