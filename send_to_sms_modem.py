import os
import logging
import time
import signal
import sys
from dbSigesmen import Database
import json
import traceback
import smtplib
from email.mime.text import MIMEText
import serial
from threading import Timer
from datetime import datetime
TEST_COUNT = 0
LAST_SUCCESSFUL_RUN = datetime.now()
WATCHDOG_TIMEOUT = 300  # 5 minutos sin actividad exitosa desencadena reinicio

# Configuración de Base de Datos
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_DATABASE = os.getenv("DB_DATABASE")

# Configuración del Módem GSM
MODEM_PORT = os.getenv("MODEM_PORT")
MODEM_BAUDRATE = int(os.getenv("MODEM_BAUDRATE", "115200"))
MODEM_PIN = os.getenv("MODEM_PIN", None)

# Configuración de Email para Alertas
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO")

# Configuración del Servicio
SLEEP = int(os.getenv("SLEEP", "10"))  # 10 segundos por defecto
REBOOT_AFTER_ATTEMPS = int(os.getenv("REBOOT_AFTER_ATTEMPS", "60"))

# Variable global para el módem (será un objeto serial.Serial)
modem = None

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

def send_alert_email(subject, body):
    """Envía email de alerta cuando el módem falla"""
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO]):
        logger.warning("Configuración de email incompleta, no se puede enviar alerta")
        return False
    
    try:
        msg = MIMEText(body)
        msg['Subject'] = f"[ALERTA SMS MODEM] {subject}"
        msg['From'] = SMTP_USER
        msg['To'] = ALERT_EMAIL_TO
        
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"Email de alerta enviado: {subject}")
        return True
    except Exception as e:
        logger.error(f"Error enviando email de alerta: {e}")
        return False

def send_at_command(ser, command, timeout=5):
    """Envía un comando AT y espera respuesta completa"""
    ser.reset_input_buffer()
    
    ser.write((command + '\r\n').encode('utf-8'))
    time.sleep(0.5)
    
    response = b''
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        if ser.in_waiting:
            new_data = ser.read(ser.in_waiting)
            response += new_data
            
            if b'OK' in response or b'ERROR' in response:
                time.sleep(0.3)
                if ser.in_waiting:
                    response += ser.read(ser.in_waiting)
                break
        
        time.sleep(0.1)
    
    return response.decode('utf-8', errors='ignore').strip()

def init_modem():
    """Inicializa y conecta el módem GSM usando pyserial"""
    global modem
    try:
        if not MODEM_PORT:
            raise ValueError("MODEM_PORT no configurado")
        
        logger.info(f"Inicializando módem en puerto {MODEM_PORT}, baudrate {MODEM_BAUDRATE}")
        modem = serial.Serial(MODEM_PORT, MODEM_BAUDRATE, timeout=3)
        time.sleep(2)  # Dar tiempo al módem para iniciar
        logger.info("✅ Puerto abierto")
        
        # Verificar que el módem responda
        logger.info("Verificando módem (AT)...")
        response = send_at_command(modem, 'AT')
        logger.info(f"Respuesta: {response}")
        
        if 'OK' not in response:
            raise Exception("El módem no responde a comandos AT")
        
        # Verificar señal GSM
        logger.info("Verificando señal GSM...")
        response = send_at_command(modem, 'AT+CSQ')
        logger.info(f"Señal: {response}")
        
        # Verificar red
        logger.info("Verificando red...")
        response = send_at_command(modem, 'AT+COPS?')
        logger.info(f"Red: {response}")
        
        logger.info("✅ Módem conectado exitosamente")
        return True
    except Exception as e:
        logger.error(f"Error inicializando módem: {e}")
        if modem:
            try:
                modem.close()
            except:
                pass
        return False

def reconnect_modem(max_attempts=3):
    """Reconecta el módem después de una falla"""
    global modem
    logger.info(f"Intentando reconectar módem ({max_attempts} intentos)")
    
    for attempt in range(max_attempts):
        try:
            if modem:
                try:
                    modem.close()
                except:
                    pass
                time.sleep(2)
            
            if init_modem():
                logger.info("Módem reconectado exitosamente")
                return True
                
        except Exception as e:
            logger.error(f"Intento {attempt+1} de reconexión falló: {e}")
            time.sleep(5)  # Esperar entre intentos
    
    # Si falla después de todos los intentos, enviar email de alerta
    error_msg = f"Módem GSM no responde después de {max_attempts} intentos de reconexión"
    logger.critical(error_msg)
    send_alert_email("Fallo de Módem GSM", error_msg)
    return False

def check_modem_status():
    """Verifica el estado actual del módem usando pyserial"""
    global modem
    try:
        if not modem or not modem.is_open:
            return {"status": "disconnected", "error": "Módem no inicializado o puerto cerrado"}
        
        # Intentar enviar comando AT
        response = send_at_command(modem, 'AT', timeout=2)
        
        if 'OK' not in response:
            return {"status": "error", "error": "Módem no responde"}
        
        # Verificar señal
        signal_response = send_at_command(modem, 'AT+CSQ', timeout=2)
        
        return {
            "signal_info": signal_response,
            "network_info": "Verificado",
            "status": "ok"
        }
    except Exception as e:
        logger.error(f"Error verificando estado del módem: {e}")
        return {"status": "error", "error": str(e)}

def format_phone_number(phone):
    """Formatea el número de teléfono para el módem GSM"""
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
    
    return clean_phone

def send_sms_via_modem(phone, message):
    """Envía un SMS usando el módem GSM con pyserial"""
    global modem
    try:
        if not modem or not modem.is_open:
            raise Exception("Módem no inicializado")
        
        # Validar que el teléfono tenga al menos 7 dígitos
        if not phone or len(phone) < 7:
            error = f"Teléfono inválido: {phone}"
            logger.error(error)
            return False, error

        # Formatear número de teléfono
        clean_phone = format_phone_number(phone)
        logger.info(f"Enviando SMS a {clean_phone}")
        
        # Configurar modo texto
        response = send_at_command(modem, 'AT+CMGF=1')
        if 'OK' not in response:
            raise Exception("No se pudo configurar modo texto")
        
        # Enviar SMS
        logger.debug(f"Enviando comando AT+CMGS a {clean_phone}")
        modem.write(f'AT+CMGS="{clean_phone}"\r\n'.encode('utf-8'))
        time.sleep(1)
        
        # Esperar prompt '>'
        timeout = 10
        start_time = time.time()
        prompt_received = False
        while time.time() - start_time < timeout:
            if modem.in_waiting:
                data = modem.read(modem.in_waiting)
                if b'>' in data:
                    prompt_received = True
                    logger.debug("Prompt '>' recibido")
                    break
            time.sleep(0.1)
        
        if not prompt_received:
            raise Exception("Timeout esperando prompt '>' del módem")
        
        # Enviar mensaje y Ctrl+Z (0x1A)
        modem.write(message.encode('utf-8'))
        modem.write(b'\x1A')  # Ctrl+Z
        
        # Esperar respuesta final
        response = b''
        start_time = time.time()
        while time.time() - start_time < 30:
            if modem.in_waiting:
                response += modem.read(modem.in_waiting)
                if b'+CMGS:' in response or b'OK' in response:
                    break
            time.sleep(0.5)
        
        result = response.decode('utf-8', errors='ignore')
        logger.info(f"SMS enviado exitosamente. Respuesta: {result}")
        
        # Extraer referencia si existe
        reference = "unknown"
        if '+CMGS:' in result:
            try:
                reference = result.split('+CMGS:')[1].split('\n')[0].strip()
            except:
                pass
        
        return True, reference
        
    except Exception as e:
        error = f"Error enviando SMS a {phone}: {str(e)}"
        logger.error(error, exc_info=True)
        return False, error

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
    """Rutina principal que lee mensajes no enviados y los envía por SMS via módem"""
    global modem
    try:
        logger.info("Iniciando rutina de procesamiento de mensajes SMS via módem")
        start_time = time.time()
        
        # Verificar estado del módem antes de procesar
        modem_status = check_modem_status()
        if modem_status["status"] == "error":
            logger.error("Módem no disponible, intentando reconectar...")
            if not reconnect_modem():
                logger.error("No se pudo reconectar el módem, saltando esta ejecución")
                return
        
        # Recuperar mensajes no enviados con un límite para evitar sobrecarga
        query = "SELECT * FROM mensaje_a_sms WHERE men_status = 0 LIMIT 100"
        unsent_messages = db.get_unsent(query)
        
        message_count = len(unsent_messages)
        if message_count == 0:
            logger.info("No hay mensajes SMS pendientes para enviar")
            return
            
        logger.info(f"Procesando {message_count} mensajes SMS pendientes via módem")
        
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
                        success, obs = send_sms_via_modem(phone, message)
                        
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
                    logger.info(f"Mensaje SMS {msg_id} enviado correctamente a {phones_sent} teléfonos via módem")
                else:
                    messages_failed += 1
                    logger.warning(f"Mensaje SMS {msg_id}: {phones_sent} enviados, {phones_failed} fallidos via módem")
            
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
        logger.info(f"Rutina SMS módem completada en {execution_time:.2f} segundos. Procesados: {messages_processed}, Exitosos: {messages_sent}, Fallidos: {messages_failed}")
    
    except Exception as routine_error:
        logger.exception(f"Error general en la rutina SMS módem: {str(routine_error)}")
        raise  # Re-lanzamos la excepción para que se maneje en el bucle principal

def health_check():
    """Verifica el estado del servicio y módem"""
    try:
        # Calcula tiempo desde la última ejecución exitosa
        time_since_last_success = (datetime.now() - LAST_SUCCESSFUL_RUN).total_seconds()
        
        # Verificar estado del módem
        modem_status = check_modem_status()
        
        # Si han pasado más de 5 minutos desde la última ejecución exitosa, considera que hay un problema
        if time_since_last_success > WATCHDOG_TIMEOUT:
            logger.warning(f"Health check: El servicio SMS módem lleva {time_since_last_success} segundos sin una ejecución exitosa")
            return {
                "status": "warning",
                "last_success": LAST_SUCCESSFUL_RUN.isoformat(),
                "seconds_since_success": time_since_last_success,
                "modem_status": modem_status
            }
        
        return {
            "status": "ok",
            "last_success": LAST_SUCCESSFUL_RUN.isoformat(),
            "seconds_since_success": time_since_last_success,
            "modem_status": modem_status
        }
    except Exception as e:
        logger.error(f"Error en health check: {str(e)}", exc_info=True)
        return {"status": "error", "error": str(e)}

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
    global modem
    logger.info("Señal de terminación recibida. Limpiando recursos...")
    if modem:
        try:
            modem.close()
            logger.info("Puerto serie cerrado correctamente")
        except Exception as e:
            logger.error(f"Error cerrando puerto serie: {e}")
    sys.exit(0)

if __name__ == '__main__':
    # Configura manejadores de señales
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Inicializar módem
    if not init_modem():
        logger.critical("No se pudo inicializar el módem. Saliendo...")
        sys.exit(1)
    
    # Inicia el watchdog
    watchdog_thread = Timer(60, watchdog_check)
    watchdog_thread.daemon = True
    watchdog_thread.start()
    logger.info("Watchdog iniciado")
    
    # Bucle principal mejorado
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    logger.info(f"Iniciando bucle principal SMS módem con intervalo de {SLEEP} segundos")
    logger.info("El script chequeará la tabla mensaje_a_sms cada {} segundos".format(SLEEP))
    
    while True:
        try:
            start_time = time.time()
            logger.info(f"Chequeando tabla mensaje_a_sms para mensajes nuevos...")
            routine()
            
            # Actualiza el timestamp de última ejecución exitosa
            LAST_SUCCESSFUL_RUN = datetime.now()
            consecutive_errors = 0  # Reinicia el contador de errores
            
            # Calcula el tiempo que tomó la ejecución
            execution_time = time.time() - start_time
            logger.info(f"Chequeo completado en {execution_time:.2f} segundos. Esperando {SLEEP} segundos...")
            
            # Asegura un intervalo constante ajustando el tiempo de espera
            sleep_time = max(0.1, SLEEP - execution_time)  # Al menos 0.1 segundos
            time.sleep(sleep_time)
            
        except Exception as e:
            consecutive_errors += 1
            logger.exception(f"Error en la ejecución principal SMS módem ({consecutive_errors}/{max_consecutive_errors}): {e}")
            
            if consecutive_errors >= max_consecutive_errors:
                logger.critical(f"Demasiados errores consecutivos ({consecutive_errors}). Reiniciando el servicio SMS módem...")
                os._exit(1)  # Fuerza reinicio
                
            # Espera antes de reintentar tras un error
            time.sleep(SLEEP)
