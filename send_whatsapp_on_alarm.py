import os
import logging
import requests
import time
import signal
import sys
from dbSigesmen import Database
import json
import traceback
import re
from twilio.rest import Client
from flask import Flask, jsonify
from threading import Thread, Timer
from datetime import datetime

app = Flask(__name__)
TEST_COUNT = 0
LAST_SUCCESSFUL_RUN = datetime.now()
WATCHDOG_TIMEOUT = 300  # 5 minutos sin actividad exitosa desencadena reinicio
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_DATABASE = os.getenv("DB_DATABASE")
TOKEN = os.getenv("TOKEN")
SLEEP = int(os.getenv("SLEEP", "60"))
REBOOT_AFTER_ATTEMPS = int(os.getenv("REBOOT_AFTER_ATTEMPS", "60"))
TIME_BETWEEN_MESSAGE = int(os.getenv("TIME_BETWEEN_MESSAGE", "60"))  # Cambiado de TIME_BETWEEN_CALL
ACCOUNT_SID = os.getenv("ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")  # Número de WhatsApp de Twilio
REQUEST_TIMEOUT = 30  # Timeout para peticiones HTTP en segundos
API = f"https://api.telegram.org/bot{TOKEN}"

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_message = {
            "timestamp": self.formatTime(record),
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "logger": record.name,
            "line_number": record.lineno
        }
        if record.exc_info: # Verifica si hay información de excepción
            exc_type, exc_value, exc_traceback = record.exc_info
            log_message["traceback"] = traceback.format_exception(exc_type, exc_value, exc_traceback)

        return json.dumps(log_message)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])

logger = logging.getLogger(__name__)

class ListaTemporal:
    def __init__(self):
        self.lista = []
        self.elementos = {}  # Diccionario para almacenar el tiempo de inserción de cada elemento

    def insert(self, telefono, tiempo_expiracion):
        tiempo_actual = time.time()
        self.lista.append(telefono)
        self.elementos[telefono] = tiempo_actual + tiempo_expiracion

    def clean(self):
        tiempo_actual = time.time()
        elementos_a_eliminar = []
        for telefono, tiempo_expiracion in self.elementos.items():
            if tiempo_actual > tiempo_expiracion:
                elementos_a_eliminar.append(telefono)

        for telefono in elementos_a_eliminar:
            if telefono in self.lista:
                self.lista.remove(telefono)
            if telefono in self.elementos:
                del self.elementos[telefono]

    def get_list(self):
        return self.lista

tmp_list = ListaTemporal()

def is_event_to_call(event: str, msg: str) -> bool:
    """Verifica si el mensaje contiene el evento que requiere notificación"""
    try:
        return bool(re.search(event, msg.lower()))
    except Exception as e:
        logger.error(f"Error al verificar evento '{event}' en mensaje: {e}")
        return False

def remove_non_alphanumeric(input_string):
    """Removes any character from a string that is not a letter, number, or space."""
    try:
        alphanumeric_string = re.sub(r'[^a-zA-Z0-9\s]', '', input_string)
        # Remove extra spaces
        single_space_string = re.sub(r'\s+', ' ', alphanumeric_string).strip()
        return single_space_string
    except Exception as e:
        logger.error(f"Error al limpiar texto: {e}")
        return input_string

def format_phone_for_whatsapp(phone):
    """Formatea el número de teléfono para WhatsApp"""
    try:
        # Remover caracteres no numéricos
        clean_phone = re.sub(r'[^\d]', '', phone)
        
        # Si no tiene código de país, asumir +1 (Estados Unidos)
        if len(clean_phone) == 10:
            clean_phone = '1' + clean_phone
        elif len(clean_phone) == 11 and clean_phone.startswith('1'):
            pass  # Ya tiene código de país
        elif len(clean_phone) < 10:
            raise ValueError(f"Número de teléfono muy corto: {phone}")
        
        return f"whatsapp:+{clean_phone}"
    except Exception as e:
        logger.error(f"Error al formatear teléfono {phone}: {e}")
        return None

def with_db_connection(func):
    """
    Decorador para abrir la conexión antes de ejecutar la función
    y cerrarla después. Maneja reintentos si la conexión falla.
    """
    def wrapper(*args, **kwargs):
        retries = 3
        for attempt in range(retries):
            try:
                with Database(DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_DATABASE) as db:
                    return func(db, *args, **kwargs)
            except Exception as e:
                logger.error(f"Error de conexión a la DB (intento {attempt + 1} de {retries}): {e}", exc_info=True)
                time.sleep(5)  # Espera antes de reintentar
        logger.critical("No se pudo conectar a la base de datos después de varios intentos.")
        raise ConnectionError("Fallo la conexión a la base de datos.")
    return wrapper

def send_whatsapp_message(message, phone):
    """Envía un mensaje por WhatsApp usando Twilio con manejo de errores robusto"""
    try:
        # Validar parámetros
        if not phone or len(phone) < 7:
            error = f"Teléfono inválido: {phone}"
            logger.error(error)
            return False, error
        
        if not ACCOUNT_SID or not TWILIO_TOKEN or not TWILIO_WHATSAPP_NUMBER:
            error = "Configuración de Twilio WhatsApp incompleta"
            logger.error(error)
            return False, error

        # Formatear teléfono para WhatsApp
        whatsapp_phone = format_phone_for_whatsapp(phone)
        if not whatsapp_phone:
            error = f"No se pudo formatear el teléfono {phone} para WhatsApp"
            logger.error(error)
            return False, error

        # Limpiar y preparar mensaje
        clean_message = remove_non_alphanumeric(message)
        full_message = f"🚨 ALARMA INTEGRALCOM 🚨\n\n{clean_message}\n\nEste es un mensaje automático de seguridad."

        # Crear cliente Twilio
        client = Client(ACCOUNT_SID, TWILIO_TOKEN)
        
        # Enviar mensaje por WhatsApp
        message_obj = client.messages.create(
            body=full_message,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=whatsapp_phone
        )
        
        logger.info(f"Mensaje WhatsApp enviado exitosamente. SID: {message_obj.sid}, Teléfono: {phone}")
        return True, message_obj.sid
        
    except Exception as e:
        error = f"Error al enviar mensaje WhatsApp a {phone}: {str(e)}"
        logger.error(error, exc_info=True)
        return False, error

@with_db_connection
def routine(db):
    """Rutina principal que procesa mensajes de alarma y envía notificaciones por WhatsApp"""
    try:
        logger.info("Iniciando rutina de procesamiento de alarmas WhatsApp")
        start_time = time.time()
        
        # Limpiar lista temporal
        tmp_list.clean()
        
        # Recuperar mensajes no procesados con límite para evitar sobrecarga
        query = "SELECT * FROM mensaje_a_whatsapp WHERE men_status = 0 LIMIT 100"
        unsent_messages = db.get_unsent(query)
        
        message_count = len(unsent_messages)
        if message_count == 0:
            logger.info("No hay mensajes de alarma pendientes para procesar")
            return
            
        logger.info(f"Procesando {message_count} mensajes de alarma pendientes")
        
        messages_processed = 0
        whatsapp_sent = 0
        whatsapp_failed = 0
        
        for msg in unsent_messages:
            try:
                msg_id, message, _, code_cli, _, _, _, _ = msg
                logger.info(f"Procesando mensaje de alarma ID {msg_id} para cliente {code_cli}")
                
                # Obtener información del cliente
                query = f"SELECT * FROM clientes_whatsapp WHERE abonado = {code_cli}"
                row = db.get_one_row(query)
                if not row:
                    logger.warning(f"No se encontró información de notificación para el cliente {code_cli}")
                    db.mark_as_process("mensaje_a_whatsapp", msg_id)
                    messages_processed += 1
                    continue
                id, client, name, phone, event = row

                logger.info(f"Cliente encontrado: {name} ({phone}), Evento: {event}")
                
                # Verificar si el cliente ya está en la lista temporal
                if client in tmp_list.get_list():
                    logger.info(f"Cliente {client} ya fue notificado recientemente, saltando...")
                    db.mark_as_process("mensaje_a_whatsapp", msg_id)
                    messages_processed += 1
                    continue
                
                # Verificar si el mensaje contiene el evento que requiere notificación
                if is_event_to_call(event, message):
                    logger.info(f"Evento '{event}' detectado en mensaje, enviando WhatsApp...")
                    
                    # Agregar cliente a lista temporal
                    tmp_list.insert(client, TIME_BETWEEN_MESSAGE)
                    
                    # Enviar mensaje por WhatsApp
                    success, result = send_whatsapp_message(message, phone)
                    
                    if success:
                        whatsapp_sent += 1
                        logger.info(f"ALARMA: WhatsApp enviado exitosamente al teléfono {phone} por evento {event}. SID: {result}")
                    else:
                        whatsapp_failed += 1
                        logger.error(f"ALARMA: Error en WhatsApp al teléfono {phone} por evento {event}. Error: {result}")
                        # Guardar observación de error
                        db.insert_obs(f"Error en WhatsApp: {result[:500]}")
                else:
                    logger.info(f"Evento '{event}' no detectado en mensaje, saltando notificación...")
                
                # Marcar mensaje como procesado
                db.mark_as_process("mensaje_a_whatsapp", msg_id)
                messages_processed += 1
                
            except Exception as msg_error:
                logger.exception(f"Error al procesar mensaje de alarma {msg}: {str(msg_error)}")
                # Intentar marcar como procesado para evitar reprocesamiento infinito
                try:
                    db.mark_as_process("mensaje_a_whatsapp", msg_id)
                except Exception:
                    pass
                whatsapp_failed += 1
        
        # Limpiar lista temporal al final
        tmp_list.clean()
        
        # Resumen de la ejecución
        execution_time = time.time() - start_time
        logger.info(f"Rutina de alarmas WhatsApp completada en {execution_time:.2f} segundos. Procesados: {messages_processed}, WhatsApp: {whatsapp_sent}, Fallidos: {whatsapp_failed}")
    
    except Exception as routine_error:
        logger.exception(f"Error general en la rutina de alarmas WhatsApp: {str(routine_error)}")
        raise  # Re-lanzamos la excepción para que se maneje en el bucle principal

def send_message_to_phone(db, phone, message):
    """
    Envía un mensaje a un teléfono específico usando la API de Telegram.
    """
    try:
        # Validar que el teléfono tenga al menos 7 dígitos
        if not phone or len(phone) < 7:
            error = f"Teléfono inválido: {phone}"
            logger.error(error)
            return False, error

        last_num_phone = phone[-7:]
        chat_id = db.get_chat_id(last_num_phone)
        logger.info(f"Buscando chat_id para teléfono terminado en {last_num_phone}: {chat_id}")
        
        if chat_id:
            chat_id = chat_id[0]
            url = f"{API}/sendMessage?chat_id={chat_id}&text={message}"
            # Agregar timeout para evitar que las peticiones se queden colgadas
            response = requests.get(url, timeout=REQUEST_TIMEOUT)

            if response.status_code == 200:
                logger.info(f"Mensaje enviado exitosamente a {phone}")
                return True, ""
            else:
                error = f"Error al enviar mensaje a {phone}: Código {response.status_code}, Respuesta: {response.text}"
                logger.error(error)
                return False, error
        else:
            error = f"Teléfono {phone} no está registrado en la DB."
            logger.warning(error)
            return False, error
    except requests.Timeout:
        error = f"Timeout al enviar mensaje a {phone}"
        logger.error(error)
        return False, error
    except Exception as e:
        error = f"Error inesperado al enviar mensaje a {phone}: {str(e)}"
        logger.error(error, exc_info=True)
        return False, error

@app.route("/health", methods=['GET'])
def health_check():
    try:
        # Comprueba si el programa está funcionando correctamente
        # Calcula tiempo desde la última ejecución exitosa
        time_since_last_success = (datetime.now() - LAST_SUCCESSFUL_RUN).total_seconds()
        
        # Si han pasado más de 5 minutos desde la última ejecución exitosa, considera que hay un problema
        if time_since_last_success > WATCHDOG_TIMEOUT:
            logger.warning(f"Health check: El servicio lleva {time_since_last_success} segundos sin una ejecución exitosa")
            return jsonify({
                "status": "warning",
                "last_success": LAST_SUCCESSFUL_RUN.isoformat(),
                "seconds_since_success": time_since_last_success
            }), 200  # Seguimos devolviendo 200 para no reiniciar el servicio automáticamente
        
        # Para pruebas, mantenemos el contador
        global TEST_COUNT
        TEST_COUNT += 1
        
        return jsonify({
            "status": "ok",
            "last_success": LAST_SUCCESSFUL_RUN.isoformat(),
            "seconds_since_success": time_since_last_success
        }), 200
    except Exception as e:
        logger.error(f"Error en health check: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500

def watchdog_check():
    """Comprueba si el programa está funcionando correctamente y lo reinicia si es necesario"""
    global LAST_SUCCESSFUL_RUN
    time_since_last_success = (datetime.now() - LAST_SUCCESSFUL_RUN).total_seconds()
    
    if time_since_last_success > WATCHDOG_TIMEOUT:
        logger.critical(f"¡WATCHDOG ACTIVADO! Han pasado {time_since_last_success} segundos desde la última ejecución exitosa. Reiniciando...")
        # En un entorno real, este es el punto donde reiniciaríamos el proceso
        # En Railway, podemos salir con un código de error para que el sistema nos reinicie
        os._exit(1)  # Fuerza la salida del proceso
    
    # Programa la próxima verificación
    timer = Timer(60, watchdog_check)  # Comprobar cada minuto
    timer.daemon = True
    timer.start()

# Maneja señales de terminación para limpieza
def signal_handler(sig, frame):
    logger.info("Señal de terminación recibida. Limpiando recursos...")
    # Aquí podríamos realizar tareas de limpieza si fuera necesario
    sys.exit(0)

if __name__ == '__main__':
    # Configura manejadores de señales
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Inicia el servidor Flask en un hilo separado
    flask_thread = Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': 8080, 'debug': False, 'use_reloader': False})
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("Servidor Flask iniciado")
    
    # Inicia el watchdog
    watchdog_thread = Thread(target=watchdog_check)
    watchdog_thread.daemon = True
    watchdog_thread.start()
    logger.info("Watchdog iniciado")
    
    # Bucle principal mejorado
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    logger.info(f"Iniciando bucle principal con intervalo de {SLEEP} segundos")
    while True:
        try:
            start_time = time.time()
            logger.info(f"Ejecutando rutina de verificación de alarmas WhatsApp...")
            routine()
            
            # Actualiza el timestamp de última ejecución exitosa
            LAST_SUCCESSFUL_RUN = datetime.now()
            consecutive_errors = 0  # Reinicia el contador de errores
            
            # Calcula el tiempo que tomó la ejecución
            execution_time = time.time() - start_time
            logger.info(f"Rutina completada en {execution_time:.2f} segundos")
            
            # Asegura un intervalo constante ajustando el tiempo de espera
            sleep_time = max(0.1, SLEEP - execution_time)  # Al menos 0.1 segundos
            time.sleep(sleep_time)
            
        except Exception as e:
            consecutive_errors += 1
            logger.exception(f"Error en la ejecución principal ({consecutive_errors}/{max_consecutive_errors}): {e}")
            
            if consecutive_errors >= max_consecutive_errors:
                logger.critical(f"Demasiados errores consecutivos ({consecutive_errors}). Reiniciando el servicio...")
                os._exit(1)  # Fuerza reinicio
                
            # Espera antes de reintentar tras un error
            time.sleep(SLEEP)
