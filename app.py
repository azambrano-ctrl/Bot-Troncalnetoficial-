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
from flask import Flask, request
from dotenv import load_dotenv
from utils_sheets import registrar_pago, obtener_hashes_existentes
import fitz
from waitress import serve

# --- 👇 NUEVAS IMPORTACIONES DESDE LOS MÓDulos CREADOS 👇 ---
from bot.state_manager import guardar_estado, cargar_estado, borrar_estado
from bot.client_service import (
    buscar_nombre_por_id,
    buscar_id_por_nombre,
    get_client_phrases,
    parse_client_line
)
from services.meta_api import (
    enviar_mensaje_whatsapp,
    enviar_accion_escritura,
    transcribe_audio,
    obtener_contenido_imagen,
    obtener_contenido_documento
)

load_dotenv()
app = Flask(__name__)

# --- CONFIGURACIÓN PARA LA API DE META ---
# (Las variables se cargan desde .env, no es necesario definirlas aquí)
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "TRONCALNET_BOT_2025")
GRUPO_SOPORTE_ID = os.getenv("GRUPO_SOPORTE_ID")

# --- FUNCIONES DE NOTIFICACIÓN ---
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

# --- COMANDOS Y MENSAJES DE ERROR ---
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

# --- FUNCIONES DE VALIDACIÓN Y EXTRACCIÓN (Se pueden mover a un utils.py) ---

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
    texto_normalizado = texto_lower
    for char_in, char_out in [('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u')]:
        texto_normalizado = texto_normalizado.replace(char_in, char_out)

    palabras_transaccion = {'transferencia', 'pago exitoso', 'comprobante', 'transaccion', 'deposito', 'transferido'}
    tiene_palabra_transaccion = any(palabra in texto_normalizado for palabra in palabras_transaccion)
    tiene_monto = bool(re.search(r'[\d,]+\.\d{2}', texto_normalizado))
    bancos = {'pichincha', 'guayaquil', 'produbanco', 'jep', 'jardin azuayo', 'bolivariano', 'pacifico', 'internacional', 'cb'}
    tiene_banco = any(banco in texto_normalizado for banco in bancos)
    palabras_financieras = {'cuenta', 'monto', 'valor', 'fecha', 'total', 'efectivo', 'documento', 'nombre', 'destino'}
    tiene_palabra_financiera = any(palabra in palabras_financieras for palabra in texto_normalizado)
    condiciones_cumplidas = sum([tiene_palabra_transaccion, tiene_monto, tiene_banco, tiene_palabra_financiera])
    return condiciones_cumplidas >= 3

def es_recaudacion_directa(texto_completo):
    if not texto_completo: return False
    texto_normalizado = texto_completo.lower()
    for char_in, char_out in [('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u')]:
        texto_normalizado = texto_normalizado.replace(char_in, char_out)
    if not re.search(r'troncalnet', texto_normalizado): return False
    frases_clave = ["de recaudacion", "recaudaciones", "pago en efectivo", "empresa o servicio", "pago de servicio", "pago de servicios", "cuenta o contrato"]
    if any(frase in texto_normalizado for frase in frases_clave):
        return True
    return False

def analizar_intencion(texto):
    if not texto: return None
    texto_normalizado = texto.lower()
    for char_in, char_out in [('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u')]:
        texto_normalizado = texto_normalizado.replace(char_in, char_out)

    intenciones = {
        "SIN_INTERNET": ["sin internet", "no tengo internet", "internet lento", "falla el internet", "inestable", "no puedo navegar", "se me va el internet", "no hay servicio"],
        "SIN_TV": ["sin señal", "no tengo canales", "falla la tele", "problema con el tvcable", "canales no se ven", "falla el cable"],
        "PROBLEMA_PAGO": ["problema con mi pago", "no se registra mi pago", "pago no aplicado", "error en la factura", "cobro indebido", "inconveniente con el pago", "pague y no se refleja", "mi pago no aparece", "duda sobre mi pago", "error en el pago", "ya pague", "ya pagué", "tengo un problema con un pago"],
        "INFO_PLANES": ["informacion de planes", "quiero un plan", "que planes tienen", "aumentar megas", "cambiar de plan"]
    }
    scores = {intent: 0 for intent in intenciones}
    for intent, keywords in intenciones.items():
        for keyword in keywords:
            if keyword in texto_normalizado:
                scores[intent] += 1
    max_score = max(scores.values())
    return max(scores, key=scores.get) if max_score > 0 else None

def buscar_monto(texto_completo):
    if not texto_completo: return "0.00"
    patrones_monto = [r'(?:monto|valor|total|pago)\s*:?\s*(?:usd|\$)?\s*([\d,]+\.\d{2})', r'(?:usd|\$)\s*([\d,]+\.\d{2})']
    montos_encontrados = []
    for patron in patrones_monto:
        matches = re.findall(patron, texto_completo, re.IGNORECASE)
        for match in matches:
            montos_encontrados.append(float(match.replace(',', '')))
    if montos_encontrados: return f"{max(montos_encontrados):.2f}"
    matches_generales = re.findall(r'([\d,]+\.\d{2})', texto_completo)
    for match in matches_generales:
        try:
            if float(match.replace(',', '')) > 0: montos_encontrados.append(float(match.replace(',', '')))
        except ValueError: continue
    return f"{max(montos_encontrados):.2f}" if montos_encontrados else "0.00"

def buscar_fecha(texto_completo):
    if not texto_completo: return datetime.now().strftime("%d/%m/%Y")
    texto_lower = texto_completo.lower()
    meses_es = {'ene': '01', 'feb': '02', 'mar': '03', 'abr': '04', 'may': '05', 'jun': '06', 'jul': '07', 'ago': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dic': '12'}
    match = re.search(r'(\d{1,2})[/\s-]([a-zA-Z]{3})[/\s-](\d{2,4})', texto_lower)
    if match: d, M, y = match.groups(); return f"{d.zfill(2)}/{meses_es.get(M, '00')}/{'20' + y if len(y) == 2 else y}"
    match = re.search(r'(\d{4})[/\s-]([a-zA-Z]{3})[/\s-](\d{1,2})', texto_lower)
    if match: y, M, d = match.groups(); return f"{d.zfill(2)}/{meses_es.get(M, '00')}/{y}"
    match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', texto_lower)
    if match: d, m, y = match.groups(); return f"{d.zfill(2)}/{m.zfill(2)}/{'20' + y if len(y) == 2 else y}"
    match = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', texto_lower)
    if match: y, m, d = match.groups(); return f"{d.zfill(2)}/{m.zfill(2)}/{y}"
    return datetime.now().strftime("%d/%m/%Y")

def identificar_banco(texto_completo):
    if not texto_completo: return "Entidad no identificada"
    texto_normalizado = unicodedata.normalize('NFKD', texto_completo.lower()).encode('ascii', 'ignore').decode('utf-8')
    BANCOS_ECUADOR = {
        "Banco del Pacífico": ["pacifico", "bancodelpacifico", "banco del pacifico", "bdp"],
        "Banco Pichincha": ["pichincha", "banco pichincha"],
        "Banco Guayaquil": ["guayaquil", "bancoguayaquil", "banco guayaquil"],
        "Produbanco": ["produbanco", "prodomatico"],
        "Banco Bolivariano": ["bolivariano"], "Banco Internacional": ["internacional"], "Banco Austro": ["austro"],
        "Cooperativa JEP": ["jep"], "Cooperativa Jardín Azuayo": ["jardin azuayo"],
        "Cooperativa CB": ["cooperativa cb", "cb en linea", "cb movil", "biblian"]
    }
    for banco, keywords in BANCOS_ECUADOR.items():
        if any(keyword in texto_normalizado for keyword in keywords): return banco
    return "Entidad no identificada"

def buscar_numero_documento(texto_completo):
    if not texto_completo: return "No encontrado"
    texto_normalizado = re.sub(r'[^\w\s.]', ' ', texto_completo).lower()
    nombres_bancos = ['pichincha', 'guayaquil', 'produbanco', 'jep', 'jardin azuayo', 'bolivariano', 'pacifico', 'internacional']
    patrones = [r'\bno\.(jm\d{4}[a-z]{3}\d+)\b', r'\bno\.\s*([a-zA-Z0-9]{10,})\b', r'(?:No\.|Nro\.)?\s*Transacci[oó]n\s*:?#?\s*([a-zA-Z0-9-]{6,25})\b', r'Cod\.\s*Movimiento\s*:?\s*([a-zA-Z0-9]{6,25})\b', r'(?:Comprobante|Ref|Secuencial|Documento)\.?\s*:?\s*([a-zA-Z0-9-]{6,25})\b', r'\b([a-zA-Z0-9]{7,25})\b(?=.*\d)', r'\b(\d{9,25})\b']
    found_ids = []
    for patron in patrones:
        for match in re.finditer(patron, texto_normalizado, re.IGNORECASE):
            doc_id = match.group(1) or match.group(0)
            if doc_id.lower() in nombres_bancos or doc_id.lower() in ['numero', 'codigo', 'comprobante', 'referencia']: continue
            if re.fullmatch(r'\d{1,3}(?:,\d{3})*\.\d{2}', doc_id) or re.fullmatch(r'\d{1,2}/\d{1,2}/\d{2,4}', doc_id): continue
            if len(doc_id) >= 6 and (re.search(r'\d', doc_id) or len(doc_id) > 8): found_ids.append(doc_id)
    return found_ids[0].upper() if found_ids else "No encontrado"

# --- PROCESADORES DE PAGOS ---

def process_payment_document(from_number, media_id, state):
    enviar_mensaje_whatsapp(from_number, "📄 Procesando comprobante PDF, por favor espera...")
    pdf_content, message = obtener_contenido_documento(media_id)
    if not pdf_content:
        enviar_mensaje_whatsapp(from_number, message)
        return
    try:
        pdf_document = fitz.open(stream=pdf_content, filetype="pdf")
        if not len(pdf_document):
            enviar_mensaje_whatsapp(from_number, "📄 El PDF está vacío o corrupto.")
            return
        pix = pdf_document[0].get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        image_bytes = pix.tobytes("png")
        temp_filename = generate_temp_filename(from_number, media_id, extension="png")
        temp_filepath = save_temp_image(image_bytes, temp_filename)
        if not temp_filepath:
            enviar_mensaje_whatsapp(from_number, BotError.storage_error())
            return
        process_payment_image(from_number, temp_filepath, state, use_stored_image=True)
    except Exception as e:
        print(f"Error al procesar el documento PDF: {e}")
        enviar_mensaje_whatsapp(from_number, "❌ No pude procesar el archivo PDF.")

def process_payment_image(from_number, media_id_or_filepath, state, use_stored_image=False):
    if use_stored_image:
        temp_filepath = media_id_or_filepath
        with open(temp_filepath, 'rb') as f: image_content = f.read()
        if not image_content:
            enviar_mensaje_whatsapp(from_number, BotError.storage_error())
            return
    else:
        enviar_mensaje_whatsapp(from_number, "📄 Procesando imagen del comprobante, por favor espera...")
        image_content, temp_filepath, message = obtener_contenido_imagen(media_id_or_filepath, from_number)
        if not image_content or not temp_filepath:
            enviar_mensaje_whatsapp(from_number, message)
            return

    try:
        client = vision.ImageAnnotatorClient.from_service_account_json("credentials.json")
        response = client.text_detection(image=vision.Image(content=image_content))
        if response.error.message:
            enviar_mensaje_whatsapp(from_number, BotError.ocr_error())
            return

        texto_completo_ocr = response.text_annotations[0].description if response.text_annotations else ""
        if es_recaudacion_directa(texto_completo_ocr):
            mensaje = "✅ **¡Gracias por tu pago!**\n\nDetectamos que es un pago de recaudación directa (Bancos, Tiendas, etc.). Este tipo de pago se registra automáticamente y no necesita validación por este medio."
            enviar_mensaje_whatsapp(from_number, mensaje, [{"id": "reset", "title": "⬅️ Volver al Menú"}])
            borrar_estado(from_number)
            if temp_filepath and os.path.exists(temp_filepath): os.remove(temp_filepath)
            return
        
        if not texto_completo_ocr.strip() or not es_comprobante_valido(texto_completo_ocr):
            enviar_mensaje_whatsapp(from_number, BotError.invalid_receipt())
            return

        if not contiene_nombre_empresa(texto_completo_ocr) and not validar_destino_pago(texto_completo_ocr):
            enviar_mensaje_whatsapp(from_number, BotError.wrong_recipient())
            return

        new_hash = imagehash.phash(Image.open(BytesIO(image_content)))
        if str(new_hash) in obtener_hashes_existentes():
            enviar_mensaje_whatsapp(from_number, BotError.duplicate_receipt())
            return

        monto = buscar_monto(texto_completo_ocr)
        fecha = buscar_fecha(texto_completo_ocr)
        documento = buscar_numero_documento(texto_completo_ocr)
        banco = identificar_banco(texto_completo_ocr)
        nombre_cliente, cedula_cliente = state.get("apellidos_y_nombres", ""), state.get("cedula", "")
        
        success = registrar_pago(nombre_cliente, cedula_cliente, monto, fecha, documento, banco, create_image_url_alternative(temp_filepath, from_number), str(new_hash))

        if success:
            mensaje_exito = (f"🎉 **¡Pago registrado exitosamente!**\n\n"
                             f"👤 **Cliente:** {nombre_cliente.title()}\n🆔 **C.I./RUC:** {cedula_cliente}\n"
                             f"💰 **Monto:** ${monto}\n🏦 **Banco:** {banco}\n📅 **Fecha:** {fecha}\n\n"
                             "✅ Nuestro equipo verificará tu pago en las próximas horas.")
            enviar_mensaje_whatsapp(from_number, mensaje_exito)
            notificar_pago_a_soporte(from_number, nombre_cliente, cedula_cliente, monto, banco, fecha, documento)
        else:
            enviar_mensaje_whatsapp(from_number, "❌ **Error al registrar**\n\nHubo un problema técnico al guardar tu pago. Por favor, intenta de nuevo o usa `/soporte`.")

        if temp_filepath and os.path.exists(temp_filepath): os.remove(temp_filepath)
        
        botones = [{"id": "opcion_1", "title": "Registrar otro pago"}, {"id": "opcion_3", "title": "Soporte técnico"}]
        enviar_mensaje_whatsapp(from_number, "¿Necesitas algo más?", botones)
        guardar_estado(from_number, {"paso": "awaiting_initial_action"})

    except Exception as e:
        traceback.print_exc()
        enviar_mensaje_whatsapp(from_number, BotError.system_error())

# --- MANEJADOR DE BÚSQUEDA DE CLIENTES ---
def handle_client_search(from_number, input_text, state, success_step, clarification_step):
    matches_with_scores = []
    if re.match(r'^\d{10,13}$', input_text):
        nombre = buscar_nombre_por_id(input_text)
        if nombre: matches_with_scores.append(((input_text, nombre), 1000))
    else:
        matches_with_scores = buscar_id_por_nombre(input_text)

    if not matches_with_scores:
        enviar_mensaje_whatsapp(from_number, BotError.client_not_found(input_text))
        return None

    is_unique = len(matches_with_scores) == 1 or (len(matches_with_scores) > 1 and matches_with_scores[0][1] > matches_with_scores[1][1] * 4)
    if is_unique:
        cedula, nombre = matches_with_scores[0][0]
        guardar_estado(from_number, {**state, "paso": success_step, "cedula": cedula, "apellidos_y_nombres": nombre})
        return cedula, nombre
    else:
        matches = [match[0] for match in matches_with_scores][:3]
        botones = [{"id": f"cliente_{i}", "title": f"{nombre.split()[0]} {nombre.split()[-1] if ' ' in nombre else ''} - {cedula[-4:]}"[:20]} for i, (cedula, nombre) in enumerate(matches)]
        enviar_mensaje_whatsapp(from_number, "Encontré varios clientes. ¿A cuál te refieres?", botones)
        guardar_estado(from_number, {**state, "paso": clarification_step, "matches": matches})
        return None

# --- WEBHOOK PRINCIPAL ---
@app.route("/whatsapp", methods=["GET", "POST"])
def whatsapp_webhook():
    if request.method == "GET":
        return request.args.get("hub.challenge") if request.args.get("hub.verify_token") == META_VERIFY_TOKEN else ("Error", 403)

    try:
        data = request.get_json()
        if not (data and data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages")):
            return "OK", 200

        message_data = data["entry"][0]["changes"][0]["value"]["messages"][0]
        from_number = message_data["from"]

        if not check_rate_limit(from_number)[0]:
            enviar_mensaje_whatsapp(from_number, BotError.rate_limit_exceeded())
            return "OK", 200

        enviar_accion_escritura(from_number, 'typing_on')

        msg_type = message_data.get("type", "")
        msg_body = ""
        caption = ""

        if msg_type == "text": msg_body = message_data["text"]["body"].strip()
        elif msg_type == "audio":
            transcribed_text, _ = transcribe_audio(message_data["audio"]["id"])
            if transcribed_text: msg_body = transcribed_text
            else: enviar_mensaje_whatsapp(from_number, "No pude entender el audio. Por favor, intenta de nuevo o escribe."); return "OK", 200
        elif msg_type == 'image': caption = message_data.get('image', {}).get('caption', '').strip()
        elif msg_type == 'document': caption = message_data.get('document', {}).get('caption', '').strip()
        elif msg_type == "interactive": msg_body = message_data.get("interactive", {}).get("button_reply", {}).get("id", "")

        command_text = (msg_body or caption).lower().strip()
        if command_text.startswith('/'):
            response = handle_quick_command(command_text, from_number)
            if response:
                if command_text == '/soporte': guardar_estado(from_number, {"paso": "human_takeover"})
                enviar_mensaje_whatsapp(from_number, response)
                return "OK", 200

        state = cargar_estado(from_number)
        paso = state.get("paso")

        if command_text in {'reset', 'hola', 'menú', 'menu', 'inicio', 'finalizar'}:
            borrar_estado(from_number)
            if command_text == 'finalizar':
                enviar_mensaje_whatsapp(from_number, "¡Gracias por contactarnos! 😊", [{"id": "reset", "title": "Menú principal"}])
                return "OK", 200
            state, paso = {}, None

        if paso == "human_takeover": return "OK", 200

        if (msg_type in ['image', 'document'] and not caption) and (paso not in ['awaiting_receipt', 'awaiting_id_or_name'] and not state.get("cedula")):
            enviar_mensaje_whatsapp(from_number, "Recibí tu comprobante. 📄 Por favor, escribe el nombre o la cédula del titular.")
            guardar_estado(from_number, {'paso': 'awaiting_id_for_file', 'media_id': message_data[msg_type]['id'], 'is_pdf': msg_type == 'document'})
            return "OK", 200
        
        # --- LÓGICA DE ESTADOS ---
        if not paso:
            botones = [{"id": "opcion_1", "title": "Registrar un pago"}, {"id": "opcion_3", "title": "Reportar un problema"}]
            enviar_mensaje_whatsapp(from_number, "¡Hola! 👋 Soy el asistente virtual de TRONCALNET. ¿Cómo puedo ayudarte?", botones)
            guardar_estado(from_number, {"paso": "awaiting_initial_action"})

        elif paso == "awaiting_initial_action":
            if msg_body == 'opcion_1':
                enviar_mensaje_whatsapp(from_number, "Para registrar tu pago, por favor, envía el nombre completo o la cédula del titular.")
                guardar_estado(from_number, {"paso": "awaiting_id_or_name"})
            elif msg_body == 'opcion_3':
                botones = [{"id": "report_tecnico", "title": "Internet o TV"}, {"id": "report_pago", "title": "Problemas con Pagos"}]
                enviar_mensaje_whatsapp(from_number, "Entendido. ¿Qué tipo de problema deseas reportar?", botones)
                guardar_estado(from_number, {"paso": "awaiting_problem_type"})
            else:
                intencion = analizar_intencion(command_text)
                if intencion in ["SIN_INTERNET", "SIN_TV", "PROBLEMA_PAGO"]:
                    enviar_mensaje_whatsapp(from_number, f"¡Entendido! 🛠️ Para ayudarte, necesito verificar al titular. Por favor, escribe los nombres y apellidos o la cédula/RUC.")
                    guardar_estado(from_number, {"paso": "awaiting_support_name"})
                else:
                    enviar_mensaje_whatsapp(from_number, "Por favor, selecciona una de las opciones disponibles.")
        
        elif paso == "awaiting_problem_type":
            if msg_body in ['report_pago', 'report_tecnico']:
                state['problem_type'] = "Problema con Pago" if msg_body == 'report_pago' else "Falla de Internet/TV"
                state['paso'] = 'awaiting_support_name'
                enviar_mensaje_whatsapp(from_number, "Perfecto. Para continuar, por favor, escríbeme los nombres y apellidos o la cédula/RUC del titular.")
                guardar_estado(from_number, state)
            else:
                enviar_mensaje_whatsapp(from_number, "Por favor, selecciona una de las dos opciones.")

        elif paso == "awaiting_support_name":
            if command_text:
                cliente = handle_client_search(from_number, command_text, state, "awaiting_support_phone", "awaiting_support_clarification")
                if cliente:
                    enviar_mensaje_whatsapp(from_number, f"✅ **Titular verificado:** {cliente[1].title()}\n\nAhora, compárteme un *número de teléfono de contacto*.")
        
        elif paso in ["awaiting_clarification", "awaiting_support_clarification", "awaiting_clarification_for_file"]:
            if msg_body.startswith("cliente_"):
                try:
                    index = int(msg_body.split("_")[1])
                    cedula, nombre = state["matches"][index]
                    
                    if paso == "awaiting_clarification_for_file":
                        new_state = {"cedula": cedula, "apellidos_y_nombres": nombre}
                        if state.get('is_pdf', False):
                            process_payment_document(from_number, state['media_id'], new_state)
                        else:
                            process_payment_image(from_number, state['media_id'], new_state)
                    elif paso == 'awaiting_support_clarification':
                        enviar_mensaje_whatsapp(from_number, f"✅ **Titular:** {nombre.title()}\n\nAhora, compárteme un *número de teléfono de contacto*.")
                        guardar_estado(from_number, {"paso": 'awaiting_support_phone', "cedula": cedula, "apellidos_y_nombres": nombre})
                    else: # awaiting_clarification
                        enviar_mensaje_whatsapp(from_number, f"✅ Cliente: *{nombre.title()}*\n\nAhora, por favor, envía la imagen o PDF del comprobante.")
                        guardar_estado(from_number, {"paso": 'awaiting_receipt', "cedula": cedula, "apellidos_y_nombres": nombre})
                except (ValueError, IndexError, KeyError):
                    enviar_mensaje_whatsapp(from_number, "Error en la selección. Por favor, usa los botones.")
            else:
                enviar_mensaje_whatsapp(from_number, "Por favor, selecciona uno de los clientes usando los botones.")
        
        elif paso == "awaiting_support_phone":
            if command_text:
                telefono = from_number if command_text in ["este numero", "este número", "este"] else ''.join(filter(str.isdigit, command_text))
                if len(telefono) >= 9:
                    state["support_phone"] = telefono
                    enviar_mensaje_whatsapp(from_number, f"✅ **Teléfono:** {telefono}\n\nAhora, por favor, describe detalladamente el problema que estás experimentando.")
                    guardar_estado(from_number, {**state, "paso": "awaiting_support_description"})
                else:
                    enviar_mensaje_whatsapp(from_number, "❌ Número no válido. Ingresa un número de 10 dígitos o escribe \"este número\".")

        elif paso == "awaiting_support_description":
            if command_text and len(command_text) > 10:
                # ✅ NUEVO: Verificar si ya se envió un ticket para esta conversación
                if state.get("ticket_enviado"):
                    mensaje_ya_enviado = "✅ Tu reporte ya fue registrado anteriormente. Nuestro equipo se pondrá en contacto contigo pronto.\n\n¿Necesitas reportar algo diferente? Escribe 'menú' para volver al inicio."
                    enviar_mensaje_whatsapp(from_number, mensaje_ya_enviado)
                    return "OK", 200
                
                notificar_grupo_soporte(cliente_id=from_number, nombre_cliente=state.get("apellidos_y_nombres"), tipo_problema=state.get("problem_type"), telefono_contacto=state.get("support_phone"), mensaje_cliente=command_text, cedula_cliente=state.get("cedula"))
                
                # ✅ NUEVO: Marcar que el ticket ya fue enviado
                state["ticket_enviado"] = True
                guardar_estado(from_number, state)
                
                mensaje_confirmacion = (f"✅ **¡Reporte registrado exitosamente!**\n\n"
                                        f"👤 **Titular:** {state.get('apellidos_y_nombres', '').title()}\n"
                                        f"🚀 Nuestro equipo técnico revisará tu caso y se pondrá en contacto contigo.")
                enviar_mensaje_whatsapp(from_number, mensaje_confirmacion)
                borrar_estado(from_number) 
                time.sleep(1)
                enviar_mensaje_whatsapp(from_number, "¿Puedo ayudarte en algo más?", [{"id": "opcion_1", "title": "Registrar un pago"}, {"id": "finalizar", "title": "No, gracias"}])
                guardar_estado(from_number, {"paso": "awaiting_initial_action"})
            else:
                enviar_mensaje_whatsapp(from_number, "📝 Por favor, describe el problema con más detalle.")

        elif paso == "awaiting_id_or_name":
            if command_text:
                cliente = handle_client_search(from_number, command_text, state, "awaiting_receipt", "awaiting_clarification")
                if cliente:
                    enviar_mensaje_whatsapp(from_number, f"✅ Cliente: *{cliente[1].title()}*\n\nAhora, por favor, envía la imagen o el PDF del comprobante.")

        elif paso == 'awaiting_id_for_file':
            if command_text:
                handle_client_search(from_number, command_text, state, None, "awaiting_clarification_for_file")
                # El siguiente paso se maneja dentro de handle_client_search o en el estado de clarificación
        
        elif paso == "awaiting_receipt":
            if msg_type == "image": process_payment_image(from_number, message_data["image"]["id"], state)
            elif msg_type == "document" and message_data["document"].get("filename", "").lower().endswith('.pdf'):
                process_payment_document(from_number, message_data["document"]["id"], state)
            else:
                enviar_mensaje_whatsapp(from_number, "📷 Por favor, envía una imagen o un archivo PDF del comprobante.")

        return "OK", 200

    except Exception as e:
        traceback.print_exc()
        return "Error interno", 500
        
if __name__ == "__main__":
    # init_db() # Si usas una base de datos, la inicializas aquí
    cleanup_temp_files()
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Servidor iniciado en el puerto {port}...")

    serve(app, host="0.0.0.0", port=port)
