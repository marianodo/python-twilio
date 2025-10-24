import os
import logging
import requests
import time
import signal
import sys
from dbSigesmen import Database
import json
import traceback
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
ACCOUNT_SID = os.getenv("ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
SLEEP = int(os.getenv("SLEEP", "10"))  # 10 segundos por defecto
REQUEST_TIMEOUT = 30  # Timeout para peticiones HTTP en segundos
REBOOT_AFTER_ATTEMPS = int(os.getenv("REBOOT_AFTER_ATTEMPS", "60"))

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


@with_db_connection
def routine(db):
    """Rutina principal que lee mensajes no enviados y los envía por SMS"""
    try:
        logger.info("Iniciando rutina de procesamiento de mensajes SMS")
        start_time = time.time()
        
        # Recuperar mensajes no enviados con un límite para evitar sobrecarga
        query = "SELECT * FROM mensaje_a_sms WHERE men_status = 0 LIMIT 100"
        unsent_messages = db.get_unsent(query)
        
        message_count = len(unsent_messages)
        if message_count == 0:
            logger.info("No hay mensajes SMS pendientes para enviar")
            return
            
        logger.info(f"Procesando {message_count} mensajes SMS pendientes")
        
        messages_processed = 0
        messages_sent = 0
        messages_failed = 0
        
        for msg in unsent_messages:
            try:
                msg_id, message, _, code_cli, _, _, _, _ = msg
                logger.info(f"Procesando mensaje SMS ID {msg_id} para cliente {code_cli}")
                
                # Obtener teléfonos del cliente
                phones_result = db.get_phone_from_code(code_cli)
                
                if not phones_result or not phones_result[0]:
                    logger.warning(f"No hay teléfonos registrados para el cliente {code_cli}. Marcando mensaje como enviado.")
                    db.mark_as_process("mensaje_a_sms", msg_id)
                    messages_processed += 1
                    continue
                
                phones = phones_result[0]
                phones_list = [phone.strip() for phone in phones.split(";") if phone.strip()]
                logger.info(f"Encontrados {len(phones_list)} teléfonos para el cliente {code_cli}")
                
                all_sent = True
                phones_sent = 0
                phones_failed = 0

                for phone in phones_list:
                    try:
                        success, obs = send_sms_to_phone(db, phone, message)
                        
                        if success:
                            phones_sent += 1
                        else:
                            phones_failed += 1
                            all_sent = False
                            # Guardar observación de error
                            if isinstance(obs, Exception):
                                obs_text = str(obs)
                            else:
                                obs_text = obs
                            # Escapar comillas simples para evitar errores SQL
                            obs_text_escaped = obs_text.replace("'", "''")[:500]
                            db.insert_obs(obs_text_escaped)
                    except Exception as phone_error:
                        logger.exception(f"Error al procesar teléfono {phone}: {str(phone_error)}")
                        phones_failed += 1
                        all_sent = False

                # Marcar el mensaje como enviado independientemente de los resultados
                db.mark_as_process("mensaje_a_sms", msg_id)
                messages_processed += 1
                
                if all_sent:
                    messages_sent += 1
                    logger.info(f"Mensaje SMS {msg_id} enviado correctamente a {phones_sent} teléfonos")
                else:
                    messages_failed += 1
                    logger.warning(f"Mensaje SMS {msg_id}: {phones_sent} enviados, {phones_failed} fallidos")
            
            except Exception as msg_error:
                logger.exception(f"Error al procesar mensaje SMS {msg}: {str(msg_error)}")
                # Intentar marcar como enviado para evitar reprocesamiento infinito
                try:
                    db.mark_as_process("mensaje_a_sms", msg_id)
                except Exception:
                    pass
                messages_failed += 1
        
        # Resumen de la ejecución
        execution_time = time.time() - start_time
        logger.info(f"Rutina SMS completada en {execution_time:.2f} segundos. Procesados: {messages_processed}, Exitosos: {messages_sent}, Fallidos: {messages_failed}")
    
    except Exception as routine_error:
        logger.exception(f"Error general en la rutina SMS: {str(routine_error)}")
        raise  # Re-lanzamos la excepción para que se maneje en el bucle principal


def send_sms_to_phone(db, phone, message):
    """
    Envía un SMS a un teléfono específico usando la API de Twilio.
    """
    try:
        # Validar que el teléfono tenga al menos 7 dígitos
        if not phone or len(phone) < 7:
            error = f"Teléfono inválido: {phone}"
            logger.error(error)
            return False, error

        # Validar configuración de Twilio
        if not ACCOUNT_SID or not TWILIO_TOKEN or not TWILIO_NUMBER:
            error = "Configuración de Twilio incompleta"
            logger.error(error)
            return False, error

        # Limpiar número de teléfono (remover caracteres no numéricos excepto +)
        clean_phone = ''.join(c for c in phone if c.isdigit() or c == '+')
        
        # Si no tiene código de país, asumir que es argentino (+54)
        if not clean_phone.startswith('+'):
            if clean_phone.startswith('54'):
                clean_phone = '+' + clean_phone
            elif clean_phone.startswith('0'):
                clean_phone = '+54' + clean_phone[1:]
            else:
                clean_phone = '+54' + clean_phone

        logger.info(f"Enviando SMS a {clean_phone}")
        
        # Crear cliente Twilio
        client = Client(ACCOUNT_SID, TWILIO_TOKEN)
        
        # Enviar SMS
        message_obj = client.messages.create(
            body=message,
            from_=TWILIO_NUMBER,
            to=clean_phone
        )
        
        logger.info(f"SMS enviado exitosamente a {clean_phone}. SID: {message_obj.sid}")
        return True, message_obj.sid
        
    except Exception as e:
        error = f"Error al enviar SMS a {phone}: {str(e)}"
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
            logger.warning(f"Health check: El servicio SMS lleva {time_since_last_success} segundos sin una ejecución exitosa")
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
    
    logger.info(f"Iniciando bucle principal SMS con intervalo de {SLEEP} segundos")
    while True:
        try:
            start_time = time.time()
            logger.info(f"Ejecutando rutina de verificación de mensajes SMS...")
            routine()
            
            # Actualiza el timestamp de última ejecución exitosa
            LAST_SUCCESSFUL_RUN = datetime.now()
            consecutive_errors = 0  # Reinicia el contador de errores
            
            # Calcula el tiempo que tomó la ejecución
            execution_time = time.time() - start_time
            logger.info(f"Rutina SMS completada en {execution_time:.2f} segundos")
            
            # Asegura un intervalo constante ajustando el tiempo de espera
            sleep_time = max(0.1, SLEEP - execution_time)  # Al menos 0.1 segundos
            time.sleep(sleep_time)
            
        except Exception as e:
            consecutive_errors += 1
            logger.exception(f"Error en la ejecución principal SMS ({consecutive_errors}/{max_consecutive_errors}): {e}")
            
            if consecutive_errors >= max_consecutive_errors:
                logger.critical(f"Demasiados errores consecutivos ({consecutive_errors}). Reiniciando el servicio SMS...")
                os._exit(1)  # Fuerza reinicio
                
            # Espera antes de reintentar tras un error
            time.sleep(SLEEP)
