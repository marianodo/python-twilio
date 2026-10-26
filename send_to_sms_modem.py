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
from dotenv import load_dotenv
import serial
import unicodedata
import hashlib

# Cargar variables de entorno
load_dotenv()
from threading import Timer
from datetime import datetime
TEST_COUNT = 0
LAST_ACTIVITY = datetime.now()
WATCHDOG_TIMEOUT = 300  # 5 minutos sin actividad (ni siquiera un intento de envío) desencadena reinicio

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
REBOOT_AFTER_ATTEMPS = int(os.getenv("REBOOT_AFTER_ATTEMPS", "10"))

# Anti-duplicados: no reenviar el mismo texto al mismo número dentro de esta ventana (segundos, 0 = desactivado).
DEDUP_WINDOW = int(os.getenv("DEDUP_WINDOW", "120"))
# Se guarda en disco para que sobreviva a un reinicio (por ejemplo, si el watchdog corta a mitad de un lote).
DEDUP_CACHE_FILE = os.getenv(
    "DEDUP_CACHE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "sms_sent_cache.json"),
)

# Variable global para el módem (será un objeto serial.Serial)
modem = None

class SimpleFormatter(logging.Formatter):
    def format(self, record):
        # Usar colores ANSI para hacer el output más legible
        COLORS = {
            'DEBUG': '\033[36m',      # Cyan
            'INFO': '\033[32m',       # Verde
            'WARNING': '\033[33m',    # Amarillo
            'ERROR': '\033[31m',      # Rojo
            'CRITICAL': '\033[35m',   # Magenta
            'RESET': '\033[0m'        # Reset
        }
        
        color = COLORS.get(record.levelname, '')
        reset = COLORS['RESET']
        
        # Formato simple: [TIMESTAMP] LEVEL: mensaje
        timestamp = self.formatTime(record, '%Y-%m-%d %H:%M:%S')
        level = f"{color}{record.levelname:8s}{reset}"
        
        message = record.getMessage()
        
        return f"[{timestamp}] {level}: {message}"

handler = logging.StreamHandler()
handler.setFormatter(SimpleFormatter())
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
        
        #if 'OK' not in response:
        #    raise Exception("El módem no responde a comandos AT")
        
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

def clean_sms_message(message):
    """Limpia el mensaje SMS removiendo acentos, tildes, ñ y caracteres especiales"""
    if not message:
        return message

    # Mapeo manual de caracteres especiales comunes en español
    replacements = {
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
        'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U',
        'ñ': 'n', 'Ñ': 'N',
        'ü': 'u', 'Ü': 'U',
        '¿': '?', '¡': '!',
        'ç': 'c', 'Ç': 'C',
        'à': 'a', 'è': 'e', 'ì': 'i', 'ò': 'o', 'ù': 'u',
        'À': 'A', 'È': 'E', 'Ì': 'I', 'Ò': 'O', 'Ù': 'U',
        ''': "'", ''': "'", '"': '"', '"': '"',
        '–': '-', '—': '-', '…': '...',
    }

    # Aplicar reemplazos
    cleaned = message
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)

    # Normalizar usando unicodedata para casos no cubiertos
    # NFD descompone caracteres acentuados en base + acento
    normalized = unicodedata.normalize('NFD', cleaned)
    # Filtrar solo caracteres ASCII básicos (letras, números, puntuación común)
    ascii_text = ''.join(char for char in normalized if unicodedata.category(char) != 'Mn')

    # Mantener solo caracteres ASCII imprimibles (32-126) más saltos de línea
    final_text = ''.join(char for char in ascii_text if ord(char) < 128)

    return final_text

def format_phone_number(phone):
    """Formatea el número de teléfono para el módem GSM (Argentina)"""
    # Limpiar número de teléfono (remover caracteres no numéricos excepto +)
    clean_phone = ''.join(c for c in phone if c.isdigit() or c == '+')

    # Si ya tiene +, removerlo temporalmente para procesar
    has_plus = clean_phone.startswith('+')
    if has_plus:
        clean_phone = clean_phone[1:]

    # Paso 1: Corregir errores comunes de formato
    # Caso: 005493516483831 → debe ser 5493516483831
    if clean_phone.startswith('00'):
        clean_phone = clean_phone[2:]  # Remover el '00' del inicio

    # Caso: 5405493516483831 → debe ser 5493516483831
    # Detectar si tiene '54054' o '5405' seguido de '49'
    if clean_phone.startswith('540549'):
        clean_phone = clean_phone[2:]  # Remover '54', dejar '05493516483831' → procesará abajo
    elif clean_phone.startswith('54054'):
        # Caso raro: 54054351... → debe ser 549351...
        clean_phone = '549' + clean_phone[5:]  # Remover '54054', agregar '549'

    # Caso: 05493516483831 o 0549351... → debe ser 5493516483831
    if clean_phone.startswith('0549'):
        clean_phone = clean_phone[1:]  # Remover solo el '0', dejar '5493516483831'
    elif clean_phone.startswith('054'):
        # Caso: 054351XXXXXX → debe ser 549351XXXXXX
        clean_phone = '549' + clean_phone[3:]  # Remover '054', agregar '549'

    # Paso 2: Normalizar según diferentes formatos
    if clean_phone.startswith('549'):
        # Ya tiene formato correcto: 549351XXXXXXX
        clean_phone = '+' + clean_phone
    elif clean_phone.startswith('54'):
        # Formato: 54351XXXXXXX (falta el 9)
        # Insertar el 9 después del 54
        clean_phone = '+549' + clean_phone[2:]
    elif clean_phone.startswith('0'):
        # Formato: 0351XXXXXXX (número local con 0)
        clean_phone = '+549' + clean_phone[1:]
    elif clean_phone.startswith('351') or clean_phone.startswith('11'):
        # Formato: 351XXXXXXX o 11XXXXXXXX (directo sin código de país)
        clean_phone = '+549' + clean_phone
    else:
        # Otros casos: asumir que falta todo el prefijo
        clean_phone = '+549' + clean_phone

    return clean_phone

# Cache anti-duplicados: hash(número + texto) -> momento (epoch) del último envío exitoso.
_sent_cache = {}

def _cache_key(phone, message):
    """Número normalizado + texto tal como se envía. Se hashea para no guardar el contenido en disco."""
    raw = f"{format_phone_number(phone)}\n{clean_sms_message(message)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _is_fresh(sent_at, now):
    # El "0 <=" evita bloquear envíos si el reloj del sistema se atrasa
    return 0 <= now - sent_at < DEDUP_WINDOW

def _purge_sent_cache(now):
    for key in [k for k, sent_at in _sent_cache.items() if not _is_fresh(sent_at, now)]:
        del _sent_cache[key]

def _save_sent_cache():
    tmp_path = DEDUP_CACHE_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(_sent_cache, f)
        os.replace(tmp_path, DEDUP_CACHE_FILE)
    except Exception as e:
        logger.warning(f"No se pudo guardar el cache anti-duplicados: {e}")

def load_sent_cache():
    """Carga desde disco los envíos recientes (por si el proceso se reinició hace poco)."""
    global _sent_cache
    try:
        with open(DEDUP_CACHE_FILE, "r", encoding="utf-8") as f:
            _sent_cache = {k: float(v) for k, v in json.load(f).items()}
    except FileNotFoundError:
        _sent_cache = {}
    except Exception as e:
        logger.warning(f"No se pudo leer el cache anti-duplicados ({e}). Se empieza vacío.")
        _sent_cache = {}
    _purge_sent_cache(time.time())
    logger.info(f"Cache anti-duplicados: {len(_sent_cache)} envíos recientes cargados (ventana de {DEDUP_WINDOW} s)")

def was_sent_recently(phone, message):
    """True si este mismo texto ya se envió con éxito a este número dentro de DEDUP_WINDOW."""
    sent_at = _sent_cache.get(_cache_key(phone, message))
    return sent_at is not None and _is_fresh(sent_at, time.time())

def remember_sent(phone, message):
    """Registra un envío exitoso y lo persiste en disco."""
    now = time.time()
    _purge_sent_cache(now)
    _sent_cache[_cache_key(phone, message)] = now
    _save_sent_cache()

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

        # Limpiar mensaje de caracteres especiales, acentos, ñ, etc.
        clean_message = clean_sms_message(message)
        logger.info(f"Enviando SMS a {clean_phone}")
        logger.debug(f"Mensaje original: {message}")
        logger.debug(f"Mensaje limpio: {clean_message}")

        # Limpiar buffer antes de enviar para evitar datos residuales
        modem.reset_input_buffer()
        modem.reset_output_buffer()

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

        # Enviar mensaje limpio y Ctrl+Z (0x1A)
        modem.write(clean_message.encode('utf-8'))
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

def touch_heartbeat():
    """Avisa al watchdog que el servicio sigue avanzando."""
    global LAST_ACTIVITY
    LAST_ACTIVITY = datetime.now()

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
        messages_skipped = 0

        for msg in unsent_messages:
            touch_heartbeat()
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
                phones_skipped = 0

                for phone in phones_list:
                    # No reenviar el mismo texto al mismo número dentro de la ventana anti-duplicados
                    if was_sent_recently(phone, message):
                        phones_skipped += 1
                        logger.info(f"⏭️ Omitido {phone}: este mismo mensaje ya se le envió hace menos de {DEDUP_WINDOW} s")
                        continue

                    max_retries = 3
                    success = False
                    last_error = None

                    # Reintentar hasta 3 veces por teléfono
                    for attempt in range(1, max_retries + 1):
                        touch_heartbeat()  # cada intento cuenta como actividad para el watchdog
                        try:
                            logger.info(f"Intento {attempt}/{max_retries} de envío a {phone}")
                            success, obs = send_sms_via_modem(phone, message)

                            if success:
                                phones_sent += 1
                                remember_sent(phone, message)
                                logger.info(f"✅ SMS enviado exitosamente a {phone} en intento {attempt}")
                                break  # Salir del loop de reintentos si fue exitoso
                            else:
                                last_error = obs
                                logger.warning(f"Intento {attempt}/{max_retries} falló para {phone}: {obs}")

                                # Si falla y no es el último intento, verificar si necesita reconexión
                                if attempt < max_retries:
                                    # Verificar si el error es de módem (no de red/señal)
                                    error_str = str(obs).lower()
                                    if any(keyword in error_str for keyword in ['módem', 'modem', 'puerto', 'port', 'timeout', 'no responde']):
                                        logger.warning(f"Error de módem detectado, verificando conexión antes del reintento...")
                                        # Verificar estado del módem
                                        modem_status = check_modem_status()
                                        if modem_status["status"] == "error":
                                            logger.warning("Módem no disponible, intentando reconectar...")
                                            reconnect_modem()

                                    logger.info(f"Esperando 2 segundos antes del reintento {attempt + 1}...")
                                    time.sleep(2)
                        except Exception as phone_error:
                            last_error = str(phone_error)
                            logger.exception(f"Excepción en intento {attempt}/{max_retries} para {phone}: {str(phone_error)}")

                            # Si es excepción y no es el último intento, verificar módem
                            if attempt < max_retries:
                                logger.warning(f"Excepción detectada, verificando estado del módem...")
                                modem_status = check_modem_status()
                                if modem_status["status"] == "error":
                                    logger.warning("Módem no disponible, intentando reconectar...")
                                    reconnect_modem()

                                logger.info(f"Esperando 2 segundos antes del reintento {attempt + 1}...")
                                time.sleep(2)

                    # Si después de todos los intentos no fue exitoso
                    if not success:
                        phones_failed += 1
                        all_sent = False
                        logger.error(f"❌ TODOS los intentos ({max_retries}) fallaron para {phone}")
                        # Guardar observación del último error
                        if last_error:
                            if isinstance(last_error, Exception):
                                obs_text = str(last_error)
                            else:
                                obs_text = last_error
                            # Escapar comillas simples para evitar errores SQL
                            obs_text_escaped = obs_text.replace("'", "''")[:500]
                            db.insert_obs(f"3 intentos fallidos para {phone}: {obs_text_escaped}")

                # Marcar como procesado después de intentar todos los teléfonos
                # (ya sea exitoso o fallido, después de 3 reintentos por teléfono)
                db.mark_as_process("mensaje_a_sms", msg_id)
                messages_processed += 1

                skipped_note = f" ({phones_skipped} omitidos por duplicado)" if phones_skipped else ""
                if phones_skipped > 0 and phones_sent == 0 and phones_failed == 0:
                    messages_skipped += 1
                    logger.info(f"Mensaje SMS {msg_id} omitido: el mismo texto ya se envió a todos sus teléfonos hace menos de {DEDUP_WINDOW} s")
                elif phones_sent > 0:
                    if all_sent:
                        messages_sent += 1
                        logger.info(f"Mensaje SMS {msg_id} enviado correctamente a {phones_sent} teléfonos via módem{skipped_note}")
                    else:
                        messages_failed += 1
                        logger.warning(f"Mensaje SMS {msg_id}: {phones_sent} enviados, {phones_failed} fallidos via módem{skipped_note}")
                else:
                    # Si TODOS los teléfonos fallaron después de 3 reintentos, marcar como procesado de todas formas
                    messages_failed += 1
                    logger.error(f"Mensaje SMS {msg_id}: TODOS los envíos fallaron ({phones_failed} teléfonos) después de 3 reintentos. Se marca como procesado para evitar bucle infinito.")
            
            except Exception as msg_error:
                logger.exception(f"Error al procesar mensaje SMS {msg}: {str(msg_error)}")
                # Marcar como procesado para evitar reprocesamiento infinito en caso de error estructural
                try:
                    db.mark_as_process("mensaje_a_sms", msg_id)
                    logger.warning(f"Mensaje SMS {msg_id} marcado como procesado debido a error de procesamiento")
                except Exception as mark_error:
                    logger.error(f"No se pudo marcar mensaje {msg_id} como procesado: {mark_error}")
                messages_failed += 1
        
        # Resumen de la ejecución
        execution_time = time.time() - start_time
        logger.info(f"Rutina SMS módem completada en {execution_time:.2f} segundos. Procesados: {messages_processed}, Exitosos: {messages_sent}, Omitidos por duplicado: {messages_skipped}, Fallidos: {messages_failed}")
    
    except Exception as routine_error:
        logger.exception(f"Error general en la rutina SMS módem: {str(routine_error)}")
        raise  # Re-lanzamos la excepción para que se maneje en el bucle principal

def health_check():
    """Verifica el estado del servicio y módem"""
    try:
        # Calcula tiempo desde la última actividad
        time_since_last_activity = (datetime.now() - LAST_ACTIVITY).total_seconds()

        # Verificar estado del módem
        modem_status = check_modem_status()

        # Si pasó más del timeout del watchdog sin actividad, considera que hay un problema
        if time_since_last_activity > WATCHDOG_TIMEOUT:
            logger.warning(f"Health check: El servicio SMS módem lleva {time_since_last_activity:.0f} segundos sin actividad")
            return {
                "status": "warning",
                "last_activity": LAST_ACTIVITY.isoformat(),
                "seconds_since_activity": time_since_last_activity,
                "modem_status": modem_status
            }

        return {
            "status": "ok",
            "last_activity": LAST_ACTIVITY.isoformat(),
            "seconds_since_activity": time_since_last_activity,
            "modem_status": modem_status
        }
    except Exception as e:
        logger.error(f"Error en health check: {str(e)}", exc_info=True)
        return {"status": "error", "error": str(e)}

def watchdog_check():
    """Corta el proceso si se quedó colgado (sin ninguna actividad durante WATCHDOG_TIMEOUT).
    Un lote largo NO lo dispara: cada intento de envío refresca el heartbeat."""
    time_since_last_activity = (datetime.now() - LAST_ACTIVITY).total_seconds()

    if time_since_last_activity > WATCHDOG_TIMEOUT:
        logger.critical(f"¡WATCHDOG ACTIVADO! Pasaron {time_since_last_activity:.0f} segundos sin actividad. Reiniciando...")
        # Sale con código 1: run_sms_modem.bat (o Railway) vuelve a lanzar el script.
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

    # Recuperar envíos recientes (por si venimos de un reinicio a mitad de un lote)
    load_sent_cache()

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
            
            touch_heartbeat()
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
