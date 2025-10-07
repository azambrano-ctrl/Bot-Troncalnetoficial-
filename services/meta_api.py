# Al inicio de services/meta_api.py
import os
import requests
import json
import traceback
from io import BytesIO
from pydub import AudioSegment
from google.cloud import speech
from .utils import validate_image_quality, generate_temp_filename, save_temp_image # Asumiremos que moverás estas a un utils.py más tarde

# --- Variables de Configuración ---
# Por ahora, las definimos aquí. Luego las moveremos a un config.py
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "660511147155188")
WHATSAPP_API_VERSION = "v19.0"

# --- FUNCIONES PARA COMUNICARSE CON META ---
def enviar_accion_escritura(recipient_id, action='typing_on'):
    """
    Envía el indicador de escritura a un usuario.
    action puede ser 'typing_on' para activarlo o 'typing_off' para desactivarlo.
    """
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_id,
        "type": "sender_action",
        "sender_action": {
            "action": action
        }
    }
    try:
        requests.post(url, json=payload, headers=headers, timeout=5)
    except requests.exceptions.RequestException as e:
        print(f"Error al enviar acción de escritura: {e}")

def enviar_mensaje_whatsapp(recipient_id, message_text, buttons=None):
    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": recipient_id}
    if buttons:
        payload["type"] = "interactive"
        payload["interactive"] = {"type": "button", "body": {"text": message_text}, "action": {"buttons": [{"type": "reply", "reply": {"id": btn["id"], "title": btn["title"]}} for btn in buttons]}}
    else:
        payload["type"] = "text"
        payload["text"] = {"body": message_text}
    
    print(f"Enviando payload a WhatsApp: {json.dumps(payload, indent=2)}")

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error al enviar mensaje: {e}")
        if hasattr(e, 'response') and e.response: 
            print(f"Respuesta de Meta: {e.response.text}")
        return False

def transcribe_audio(media_id):
    try:
        url_media = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{media_id}/"
        headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}"}
        response_media = requests.get(url_media, headers=headers, timeout=30)
        response_media.raise_for_status()
        media_url = response_media.json().get("url")

        if not media_url:
            return None, "No se pudo obtener la URL del audio."

        response_audio = requests.get(media_url, headers=headers, timeout=30)
        response_audio.raise_for_status()
        audio_content_ogg = response_audio.content

        audio_ogg = AudioSegment.from_ogg(BytesIO(audio_content_ogg))
        audio_flac = audio_ogg.set_channels(1).set_frame_rate(16000)
        
        buffer = BytesIO()
        audio_flac.export(buffer, format="flac")
        audio_content_flac = buffer.getvalue()
        
        client = speech.SpeechClient.from_service_account_json("credentials.json")
        audio = speech.RecognitionAudio(content=audio_content_flac)
        
        client_phrases = get_client_phrases()
        speech_contexts = []
        if client_phrases:
            speech_contexts.append(speech.SpeechContext(phrases=client_phrases, boost=15.0))

        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.FLAC,
            sample_rate_hertz=16000,
            language_code="es-EC",
            speech_contexts=speech_contexts
        )

        response = client.recognize(config=config, audio=audio)

        if response.results and response.results[0].alternatives:
            transcript = response.results[0].alternatives[0].transcript
            print(f"Texto transcrito: '{transcript}'")
            return transcript, "Transcripción exitosa."
        else:
            return None, "No se pudo transcribir el audio."

    except requests.exceptions.RequestException as e:
        print(f"Error de red al procesar audio: {e}")
        return None, "Error de red al procesar el audio."
    except Exception as e:
        print("--- INICIO DE REPORTE DE ERROR DETALLADO (AUDIO) ---")
        print(f"Error inesperado al transcribir audio: {e}")
        traceback.print_exc()
        print("--- FIN DE REPORTE DE ERROR DETALLADO (AUDIO) ---")
        return None, "Ocurrió un error al procesar el audio."

def obtener_contenido_imagen(media_id, user_id):
    try:
        url1 = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{media_id}/"
        headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}"}
        r1 = requests.get(url1, headers=headers, timeout=30)
        r1.raise_for_status()
        media_data = r1.json()
        media_url = media_data.get("url")
        if not media_url:
            return None, None, "❌ No se pudo obtener la imagen desde WhatsApp."
        r2 = requests.get(media_url, headers=headers, timeout=30)
        r2.raise_for_status()
        image_content = r2.content
        is_valid, message = validate_image_quality(image_content)
        if not is_valid: return None, None, message
        temp_filename = generate_temp_filename(user_id, media_id)
        temp_filepath = save_temp_image(image_content, temp_filename)
        if not temp_filepath: return None, None, BotError.storage_error()
        return image_content, temp_filepath, "✅ Imagen descargada y guardada correctamente"
    except requests.exceptions.Timeout:
        return None, None, BotError.network_error() + "\n\n🔄 **Sugerencia:** Intenta enviar la imagen nuevamente."
    except requests.exceptions.RequestException as e:
        print(f"Error al descargar imagen: {e}")
        return None, None, BotError.network_error()
    except Exception as e:
        print(f"Error inesperado descargando imagen: {e}")
        return None, None, BotError.system_error()

def obtener_contenido_documento(media_id):
    try:
        url1 = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{media_id}/"
        headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}"}
        r1 = requests.get(url1, headers=headers, timeout=30)
        r1.raise_for_status()
        media_data = r1.json()
        media_url = media_data.get("url")
        if not media_url:
            return None, "❌ No se pudo obtener el documento desde WhatsApp."
        r2 = requests.get(media_url, headers=headers, timeout=30)
        r2.raise_for_status()
        return r2.content, "✅ Documento descargado correctamente."
    except requests.exceptions.RequestException as e:
        print(f"Error al descargar documento: {e}")
        return None, BotError.network_error()
    except Exception as e:
        print(f"Error inesperado descargando documento: {e}")
        return None, BotError.system_error()

def process_payment_document(from_number, media_id, state):
    enviar_mensaje_whatsapp(from_number, "📄 Procesando comprobante, por favor espera...")
    pdf_content, message = obtener_contenido_documento(media_id)
    if not pdf_content:
        enviar_mensaje_whatsapp(from_number, message)
        return
    try:
        pdf_document = fitz.open(stream=pdf_content, filetype="pdf")
        if not len(pdf_document):
            enviar_mensaje_whatsapp(from_number, "📄 El PDF está vacío o corrupto. Por favor, envía un archivo válido.")
            return
        page = pdf_document[0]
        zoom = 2.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        image_bytes = pix.tobytes("png")
        temp_filename = generate_temp_filename(from_number, media_id, extension="png")
        temp_filepath = save_temp_image(image_bytes, temp_filename)
        if not temp_filepath:
            enviar_mensaje_whatsapp(from_number, BotError.storage_error())
            return
        print(f"PDF convertido a imagen temporal: {temp_filepath}. Pasando al procesador de imágenes.")
        process_payment_image(from_number, temp_filepath, state, use_stored_image=True)
    except Exception as e:
        print(f"Error al procesar el documento PDF: {e}")
        enviar_mensaje_whatsapp(from_number, "❌ No pude procesar el archivo PDF. Asegúrate de que no esté protegido o dañado e intenta de nuevo.")

def process_payment_image(from_number, media_id_or_filepath, state, use_stored_image=False):
    temp_filepath = None
    if use_stored_image:
        temp_filepath = media_id_or_filepath
        image_content = load_temp_image(temp_filepath)
        if not image_content:
            enviar_mensaje_whatsapp(from_number, BotError.storage_error())
            return
    else:
        enviar_mensaje_whatsapp(from_number, "📄 Procesando comprobante, por favor espera...")
        image_content, temp_filepath, message = obtener_contenido_imagen(media_id_or_filepath, from_number)
        if not image_content or not temp_filepath:
            enviar_mensaje_whatsapp(from_number, message)
            return

    try:
        client = vision.ImageAnnotatorClient.from_service_account_json("credentials.json")
        image = vision.Image(content=image_content)
        response = client.text_detection(image=image)
        if response.error.message:
            print(f"Error en OCR: {response.error.message}")
            enviar_mensaje_whatsapp(from_number, BotError.ocr_error())
            return

        texts = response.text_annotations
        texto_completo_ocr = texts[0].description if texts else ""

        if es_recaudacion_directa(texto_completo_ocr):
            mensaje_recaudacion = (
                "✅ **¡Gracias por tu pago!**\n\n"
                "Hemos detectado que realizaste un pago directo a través de nuestros puntos de recaudación autorizados (Bancos, Tiendas, etc.).\n\n"
                "Este tipo de pago **se registra automáticamente** en nuestro sistema y no necesita validación adicional por este medio.\n\n"
                "Si tienes alguna duda, escribe /soporte."
            )
            botones_menu = [{"id": "reset", "title": "⬅️ Volver al Menú"}]
            enviar_mensaje_whatsapp(from_number, mensaje_recaudacion, botones_menu)
            borrar_estado(from_number)
            if temp_filepath and os.path.exists(temp_filepath):
                try: os.remove(temp_filepath)
                except Exception as e: print(f"Error eliminando archivo temporal de recaudación: {e}")
            return

        if not texto_completo_ocr.strip():
            enviar_mensaje_whatsapp(from_number, "📝 **No se detectó texto**\n\nNo pude leer texto en la imagen. Por favor:\n• Asegúrate de que la imagen sea clara\n• Verifica que el comprobante tenga texto visible\n• Intenta con mejor iluminación")
            return

        if not es_comprobante_valido(texto_completo_ocr):
            enviar_mensaje_whatsapp(from_number, BotError.invalid_receipt())
            return

        if not contiene_nombre_empresa(texto_completo_ocr) and not validar_destino_pago(texto_completo_ocr):
            enviar_mensaje_whatsapp(from_number, BotError.wrong_recipient())
            return

        new_hash = imagehash.phash(Image.open(BytesIO(image_content)))
        if str(new_hash) in obtener_hashes_existentes():
            enviar_mensaje_whatsapp(from_number, BotError.duplicate_receipt())
            return

        monto_pago = buscar_monto(texto_completo_ocr)
        fecha_deposito = buscar_fecha(texto_completo_ocr)
        num_documento = buscar_numero_documento(texto_completo_ocr)
        banco_identificado = identificar_banco(texto_completo_ocr)

        nombre_cliente = state.get("apellidos_y_nombres", "")
        cedula_cliente = state.get("cedula", "")
        image_reference = create_image_url_alternative(temp_filepath, from_number)

        success = registrar_pago(nombre_cliente, cedula_cliente, monto_pago, fecha_deposito, num_documento, banco_identificado, image_reference, str(new_hash))

        if success:
            mensaje_exito = (f"🎉 **¡Pago registrado exitosamente!**\n\n"
                             f"👤 **Cliente:** {nombre_cliente.title()}\n"
                             f"🆔 **C.I./RUC:** {cedula_cliente}\n"
                             f"💰 **Monto:** ${monto_pago}\n"
                             f"🏦 **Banco:** {banco_identificado}\n"
                             f"📅 **Fecha:** {fecha_deposito}\n\n"
                             "✅ Nuestro equipo verificará tu pago en las próximas horas.\n"
                             "📧 Te notificaremos cuando esté confirmado.")
            enviar_mensaje_whatsapp(from_number, mensaje_exito)
            
            notificar_pago_a_soporte(
                cliente_id=from_number,
                nombre_cliente=nombre_cliente,
                cedula_cliente=cedula_cliente,
                monto=monto_pago,
                banco=banco_identificado,
                fecha=fecha_deposito,
                documento=num_documento
            )

        else:
            enviar_mensaje_whatsapp(from_number, "❌ **Error al registrar**\n\nHubo un problema técnico al guardar tu pago. Por favor:\n• Intenta de nuevo en unos momentos\n• Si persiste, usa `/soporte`\n• Conserva tu comprobante como respaldo")

        if temp_filepath and os.path.exists(temp_filepath):
            try: os.remove(temp_filepath)
            except Exception as e: print(f"Error eliminando archivo temporal: {e}")

        mensaje_siguiente_paso = "¿Necesitas algo más?"
        botones_siguiente_paso = [{"id": "opcion_1", "title": "Registrar otro pago"}, {"id": "opcion_2", "title": "Ver planes"}, {"id": "opcion_3", "title": "Soporte técnico"}]
        enviar_mensaje_whatsapp(from_number, mensaje_siguiente_paso, botones_siguiente_paso)
        guardar_estado(from_number, {"paso": "awaiting_initial_action"})

    except FileNotFoundError:
        enviar_mensaje_whatsapp(from_number, "⚙️ **Error de configuración**\n\nHay un problema con la configuración del sistema. Por favor, contacta soporte técnico con `/soporte`.")
    except Exception as e:
        print("--- INICIO DE REPORTE DE ERROR DETALLADO (IMAGEN) ---")
        print(f"Error procesando la imagen para el usuario {from_number}: {e}")
        traceback.print_exc()
        print("--- FIN DE REPORTE DE ERROR DETALLADO (IMAGEN) ---")
        enviar_mensaje_whatsapp(from_number, BotError.system_error())
