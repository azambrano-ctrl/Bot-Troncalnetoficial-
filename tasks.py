# tasks.py
import os
from celery import Celery
from PIL import Image
from io import BytesIO
import imagehash
from google.cloud import vision

# Importamos las funciones que necesitamos de nuestro archivo original
# ¡OJO! Puede que necesites mover algunas funciones a un archivo `utils.py`
# para evitar importaciones circulares si se complica.
from app import (
    enviar_mensaje_whatsapp,
    borrar_estado,
    guardar_estado,
    obtener_hashes_existentes,
    create_image_url_alternative,
    BotError,
    # Funciones de extracción de datos
    es_recaudacion_directa,
    es_comprobante_valido,
    contiene_nombre_empresa,
    validar_destino_pago,
    buscar_monto,
    buscar_fecha,
    buscar_numero_documento,
    identificar_banco
)
from utils_sheets import registrar_pago

# Render proveerá la variable de entorno 'REDIS_URL' automáticamente.
redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
celery_app = Celery('tasks', broker=redis_url)


# 2. Creamos la Tarea Asíncrona
# Usamos el decorador `@celery_app.task` para convertir una función normal en una tarea.
@celery_app.task
def process_image_task(from_number, image_content_bytes, state, temp_filepath):
    """
    Esta es la versión asíncrona de nuestra lógica de procesamiento de imagen.
    Recibe los bytes de la imagen en lugar del media_id para no hacer llamadas a la API aquí.
    """
    try:
        # Aquí va la MISMA lógica que tenías en `process_payment_image`
        # pero adaptada para recibir el contenido de la imagen directamente.

        client = vision.ImageAnnotatorClient.from_service_account_json("credentials.json")
        image = vision.Image(content=image_content_bytes)
        response = client.text_detection(image=image)
        if response.error.message:
            enviar_mensaje_whatsapp(from_number, BotError.ocr_error())
            return

        texts = response.text_annotations
        texto_completo_ocr = texts[0].description if texts else ""

        if not texto_completo_ocr.strip():
            enviar_mensaje_whatsapp(from_number, "📝 No se detectó texto en la imagen.")
            return

        if es_recaudacion_directa(texto_completo_ocr):
            # ... lógica para recaudación directa
            enviar_mensaje_whatsapp(from_number, "✅ Este pago se registra automáticamente...")
            borrar_estado(from_number)
            return

        if not es_comprobante_valido(texto_completo_ocr):
            enviar_mensaje_whatsapp(from_number, BotError.invalid_receipt())
            return

        if not contiene_nombre_empresa(texto_completo_ocr) and not validar_destino_pago(texto_completo_ocr):
            enviar_mensaje_whatsapp(from_number, BotError.wrong_recipient())
            return

        new_hash = imagehash.phash(Image.open(BytesIO(image_content_bytes)))
        if str(new_hash) in obtener_hashes_existentes():
            enviar_mensaje_whatsapp(from_number, BotError.duplicate_receipt())
            return
        
        # Extracción de datos...
        monto_pago = buscar_monto(texto_completo_ocr)
        fecha_deposito = buscar_fecha(texto_completo_ocr)
        num_documento = buscar_numero_documento(texto_completo_ocr)
        banco_identificado = identificar_banco(texto_completo_ocr)

        nombre_cliente = state.get("apellidos_y_nombres", "")
        cedula_cliente = state.get("cedula", "")
        image_reference = create_image_url_alternative(temp_filepath, from_number)

        # Registro en Google Sheets
        success = registrar_pago(nombre_cliente, cedula_cliente, monto_pago, fecha_deposito, num_documento, banco_identificado, image_reference, str(new_hash))

        if success:
            mensaje_exito = (f"🎉 ¡Pago registrado exitosamente!**\n\n"
                             f"👤 **Cliente:** {nombre_cliente.title()}\n"
                             f"💰 **Monto:** ${monto_pago}\n"
                             f"📅 **Fecha:** {fecha_deposito}\n\n"
                             "✅ Nuestro equipo lo verificará pronto.")
            enviar_mensaje_whatsapp(from_number, mensaje_exito)
        else:
            enviar_mensaje_whatsapp(from_number, BotError.system_error())

        # Limpiamos el estado y el archivo temporal
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
        
        mensaje_siguiente_paso = "¿Necesitas algo más?"
        botones_siguiente_paso = [{"id": "opcion_1", "title": "Registrar otro pago"}, {"id": "opcion_3", "title": "Soporte técnico"}]
        enviar_mensaje_whatsapp(from_number, mensaje_siguiente_paso, botones_siguiente_paso)
        guardar_estado(from_number, {"paso": "awaiting_initial_action"})

    except Exception as e:
        print(f"Error en la tarea de Celery: {e}")
        enviar_mensaje_whatsapp(from_number, BotError.system_error())
