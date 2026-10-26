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
import re
import csv
from logging.handlers import RotatingFileHandler

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
# Solo módems Quectel: puerto por donde mandan los avisos espontáneos (+CDS, RING...).
# "uart1" si el módem está conectado por su UART (por ejemplo con un adaptador CP210x);
# "usbat" si se usa su puerto USB AT. Vacío = no tocar la configuración del módem.
MODEM_URC_PORT = os.getenv("MODEM_URC_PORT", "uart1")

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

# Avisos de entrega: los SMS enviados que esperan el aviso de la operadora (+CDS) se guardan en
# disco para no perderlos si el proceso se reinicia. El resultado va a mensaje_a_sms.men_entregado.
DELIVERY_CACHE_FILE = os.getenv(
    "DELIVERY_CACHE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "sms_delivery_pending.json"),
)
# Si en este tiempo no llegó el aviso, el mensaje queda sin dato (NULL). El SMS tiene validez de 24 h.
DELIVERY_REPORT_TIMEOUT = int(os.getenv("DELIVERY_REPORT_TIMEOUT", str(72 * 3600)))

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

# Además de la consola, un archivo (hasta 4 x 5 MB) para poder revisar qué pasó después.
LOG_FILE = os.getenv("LOG_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "sms_modem.log"))
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-8s: %(message)s", "%Y-%m-%d %H:%M:%S"))

logging.basicConfig(level=logging.INFO, handlers=[handler, file_handler])

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

# Avisos de entrega (+CDS) leídos del puerto que todavía no se pasaron a la base: (referencia, teléfono, estado)
_pending_reports = []
# Final de la última lectura que todavía no completó una línea (un aviso puede llegar en dos lecturas)
_rx_partial = b''

def parse_delivery_report(line):
    """Interpreta un aviso de entrega en modo texto:
    +CDS: <fo>,<mr>,<teléfono>,<tipo>,<hora envío>,<hora entrega>,<estado>
    (algunos módems omiten teléfono y tipo: +CDS: <fo>,<mr>,<hora envío>,<hora entrega>,<estado>)
    Devuelve (referencia, teléfono, estado) o None si no tiene ese formato."""
    try:
        text = line[line.index('+CDS:') + 5:].strip()
        fields = next(csv.reader([text], skipinitialspace=True))
        if len(fields) < 5:
            return None  # por ejemplo, en modo PDU llega "+CDS: 25" y el contenido en otra línea
        address = fields[2].strip() if len(fields) >= 7 else ""
        return int(fields[1]), address, int(fields[-1])
    except (ValueError, StopIteration):
        return None

def _read_modem(ser):
    """Lee lo que haya en el puerto. Los avisos de entrega que lleguen mezclados con las
    respuestas a los comandos se separan en _pending_reports."""
    global _rx_partial
    data = ser.read(ser.in_waiting)
    lines = (_rx_partial + data).split(b'\n')
    _rx_partial = lines.pop()[-512:]
    for raw in lines:
        line = raw.decode('utf-8', errors='ignore').strip()
        if '+CDS:' in line:
            report = parse_delivery_report(line)
            if report:
                _pending_reports.append(report)
            else:
                logger.warning(f"Aviso de entrega con formato desconocido, se ignora: {line!r}")
    return data

def _drain(ser):
    """Descarta lo que quedó en el puerto antes de un comando (como reset_input_buffer),
    pero sin perder los avisos de entrega que estén ahí."""
    for _ in range(50):
        if not ser.in_waiting:
            break
        _read_modem(ser)
        time.sleep(0.05)

def send_at_command(ser, command, timeout=5):
    """Envía un comando AT y espera respuesta completa"""
    _drain(ser)

    ser.write((command + '\r\n').encode('utf-8'))
    time.sleep(0.5)

    response = b''
    start_time = time.time()

    while time.time() - start_time < timeout:
        if ser.in_waiting:
            new_data = _read_modem(ser)
            response += new_data

            if b'OK' in response or b'ERROR' in response:
                time.sleep(0.3)
                if ser.in_waiting:
                    response += _read_modem(ser)
                break
        
        time.sleep(0.1)
    
    return response.decode('utf-8', errors='ignore').strip()

def init_modem():
    """Inicializa y conecta el módem GSM usando pyserial"""
    global modem, _rx_partial
    try:
        if not MODEM_PORT:
            raise ValueError("MODEM_PORT no configurado")

        logger.info(f"Inicializando módem en puerto {MODEM_PORT}, baudrate {MODEM_BAUDRATE}")
        modem = serial.Serial(MODEM_PORT, MODEM_BAUDRATE, timeout=3)
        _rx_partial = b''
        time.sleep(2)  # Dar tiempo al módem para iniciar
        logger.info("✅ Puerto abierto")
        
        # Verificar que el módem responda. Se prueba varias veces porque el primer AT
        # después de abrir el puerto a veces se pierde.
        logger.info("Verificando módem (AT)...")
        for _ in range(3):
            response = send_at_command(modem, 'AT')
            if 'OK' in response:
                break
            time.sleep(1)
        logger.info(f"Respuesta: {response}")
        if 'OK' not in response:
            raise Exception("El módem no responde a comandos AT")

        # Sin eco: así la respuesta a AT+CMGS no repite el texto del mensaje
        # (un "OK" o "ERROR" dentro del texto confundiría la lectura de la respuesta).
        send_at_command(modem, 'ATE0')

        # Verificar que la SIM no esté bloqueada. No se ingresa el PIN automáticamente:
        # con un PIN equivocado y los reinicios automáticos, la SIM se bloquearía con PUK.
        response = send_at_command(modem, 'AT+CPIN?')
        logger.info(f"SIM: {response}")
        if re.search(r'\+CPIN:\s*SIM (PIN|PUK)\b', response):
            raise Exception("La SIM pide PIN/PUK. Hay que desbloquearla (o quitarle el PIN) desde un celular.")

        # Modo texto y avisos de entrega:
        # - AT+CSMS=0: el módem no exige confirmar cada aviso con AT+CNMA.
        # - AT+CSMP=49,...: cada SMS pide a la operadora un aviso de entrega (validez 24 h).
        # - AT+CNMI=2,1,0,1,0: el módem pasa los avisos por el puerto como "+CDS: ...".
        manufacturer = send_at_command(modem, 'AT+CGMI')
        logger.info(f"Modelo: {manufacturer} / {send_at_command(modem, 'AT+CGMM')}")

        # Los Quectel mandan los avisos espontáneos por un solo puerto (por defecto el USB AT).
        # Si están conectados por UART y no se cambia, los avisos de entrega nunca llegan al script.
        if 'QUECTEL' in manufacturer.upper() and MODEM_URC_PORT:
            current = send_at_command(modem, 'AT+QURCCFG="urcport"')
            logger.info(f"Puerto de avisos del módem: {current}")
            response = send_at_command(modem, f'AT+QURCCFG="urcport","{MODEM_URC_PORT}"')
            if 'OK' not in response:
                logger.warning(f"El módem no aceptó el puerto de avisos {MODEM_URC_PORT} (respuesta: {response})")

        send_at_command(modem, 'AT+CMGF=1')
        for command in ('AT+CSMS=0', 'AT+CSMP=49,167,0,0', 'AT+CNMI=2,1,0,1,0'):
            response = send_at_command(modem, command)
            if 'OK' not in response:
                logger.warning(f"El módem no aceptó {command} (respuesta: {response}). Puede que no lleguen avisos de entrega.")
        # Dejar en el log cómo quedó configurado, para diagnosticar si no llegan avisos
        for command in ('AT+CSMS?', 'AT+CSMP?', 'AT+CNMI?'):
            logger.info(f"{command} {send_at_command(modem, command)}")

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

# Para mandar un solo email por caída del módem (y no uno por ciclo mientras siga caído)
_modem_down_alerted = False

def reconnect_modem(max_attempts=3):
    """Reconecta el módem después de una falla"""
    global modem, _modem_down_alerted
    logger.info(f"Intentando reconectar módem ({max_attempts} intentos)")

    for attempt in range(1, max_attempts + 1):
        if modem:
            try:
                modem.close()
            except Exception:
                pass
        time.sleep(2)

        if init_modem():
            logger.info("Módem reconectado exitosamente")
            if _modem_down_alerted:
                send_alert_email("Módem GSM recuperado", "El módem volvió a responder. Los mensajes pendientes se envían a partir de ahora.")
                _modem_down_alerted = False
            return True

        logger.error(f"Intento {attempt}/{max_attempts} de reconexión falló")
        if attempt < max_attempts:
            time.sleep(5)  # Esperar entre intentos

    # Si falla después de todos los intentos, enviar email de alerta (una vez por caída)
    error_msg = f"Módem GSM no responde después de {max_attempts} intentos de reconexión. Los mensajes quedan pendientes hasta que vuelva."
    logger.critical(error_msg)
    if not _modem_down_alerted:
        send_alert_email("Fallo de Módem GSM", error_msg)
        _modem_down_alerted = True
    return False

def ensure_modem():
    """True si el módem responde. Si no (puerto cerrado o sin respuesta), intenta reconectarlo."""
    modem_status = check_modem_status()
    if modem_status["status"] == "ok":
        return True
    logger.warning(f"Módem no disponible ({modem_status.get('error')}), intentando reconectar...")
    return reconnect_modem()

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
        '\r\n': '\n', '\r': '\n', '\t': ' ',
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
        'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U',
        'ñ': 'n', 'Ñ': 'N',
        'ü': 'u', 'Ü': 'U',
        '¿': '?', '¡': '!',
        'ç': 'c', 'Ç': 'C',
        'à': 'a', 'è': 'e', 'ì': 'i', 'ò': 'o', 'ù': 'u',
        'À': 'A', 'È': 'E', 'Ì': 'I', 'Ò': 'O', 'Ù': 'U',
        # Comillas tipográficas, acento suelto (D´Amico), guiones largos y puntos suspensivos.
        # Van como códigos \uXXXX para que un editor no los vuelva a convertir en comillas rectas.
        '‘': "'", '’': "'", '´': "'",
        '“': '"', '”': '"', '«': '"', '»': '"',
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

    # Mantener solo caracteres ASCII imprimibles (32-126) más saltos de línea.
    # Cualquier otro carácter de control (Ctrl+Z, ESC) cortaría o cancelaría el SMS en el módem.
    final_text = ''.join(char for char in ascii_text if 32 <= ord(char) <= 126 or char == '\n')

    return final_text

# Largo de un SMS en modo texto (alfabeto GSM de 7 bits). Estos caracteres ocupan 2 lugares.
SMS_MAX_LEN = 160
_GSM_DOUBLE_CHARS = set('^{}\\[]~|')
# Texto por parte cuando hay que dividir: deja lugar para el prefijo "(1/3) " (hasta "(99/99) ").
SMS_PART_LEN = 150

def _gsm_len(text):
    return len(text) + sum(1 for char in text if char in _GSM_DOUBLE_CHARS)

def split_sms(text):
    """Divide un texto que no entra en un SMS en partes numeradas "(1/3) ...", cortando en un espacio cuando se puede."""
    if _gsm_len(text) <= SMS_MAX_LEN:
        return [text]

    chunks = []
    rest = text
    while rest:
        # Cuántos caracteres de 'rest' entran en una parte
        size = 0
        cut = 0
        for char in rest:
            size += 2 if char in _GSM_DOUBLE_CHARS else 1
            if size > SMS_PART_LEN:
                break
            cut += 1
        # No cortar una palabra al medio si hay un espacio razonablemente cerca
        if cut < len(rest):
            space = max(rest.rfind(' ', 0, cut + 1), rest.rfind('\n', 0, cut + 1))
            if space > SMS_PART_LEN // 2:
                cut = space
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()

    chunks = [chunk for chunk in chunks if chunk]
    return [f"({number}/{len(chunks)}) {chunk}" for number, chunk in enumerate(chunks, 1)]

def format_phone_number(phone):
    """Formatea el número de teléfono para el módem GSM (Argentina): +549 + característica + número,
    sin el 0 ni el 15. Ejemplos que quedan como +5493515123456:
    0351 15 5123456 / 54 0351 5123456 / +54 9 351 5123456 / 005493515123456 / 3515123456"""
    digits = ''.join(c for c in phone if c.isdigit())

    # Quitar prefijos: país (54), troncal (0), celular (9) y el 00 internacional, en cualquier
    # combinación mal cargada (5405493516483831, 054351..., 540351...). Las características
    # argentinas empiezan con 1, 2 o 3, así que todo lo que venga antes es prefijo.
    while digits and digits[0] not in '123':
        if digits.startswith('54'):
            digits = digits[2:]
        elif digits[0] in '09':
            digits = digits[1:]
        else:
            break

    # El número nacional tiene 10 dígitos. Si tiene 12, trae el "15" del celular después de la
    # característica, que tiene 2, 3 o 4 dígitos (11 15..., 351 15..., 3543 15...).
    if len(digits) == 12:
        for pos in (2, 3, 4):
            if digits[pos:pos + 2] == '15':
                digits = digits[:pos] + digits[pos + 2:]
                break

    return '+549' + digits

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

def _abort_sms_input():
    """Manda ESC para cancelar un AT+CMGS a medio escribir, así el módem no queda esperando texto."""
    try:
        modem.write(b'\x1b')
        time.sleep(0.5)
        _drain(modem)
    except Exception:
        pass

def _wait_cmgs_result(timeout=60):
    """Lee la respuesta del módem después de mandar el texto + Ctrl+Z.
    Devuelve (True, referencia) solo si el módem confirmó con +CMGS, o (False, detalle)
    si respondió ERROR / +CMS ERROR o no confirmó a tiempo."""
    response = b''
    reference = None
    start_time = time.time()
    while time.time() - start_time < timeout:
        if modem.in_waiting:
            response += _read_modem(modem)
            lines = [line.strip() for line in response.decode('utf-8', errors='ignore').splitlines()]
            for line in lines:
                if line == 'ERROR' or line.startswith(('+CMS ERROR', '+CME ERROR')):
                    return False, f"El módem rechazó el SMS ({line})"
                if line.startswith('+CMGS:'):
                    reference = line.split(':', 1)[1].strip()
            if reference is not None and 'OK' in lines:
                return True, reference
        time.sleep(0.2)

    if reference is not None:
        return True, reference  # Confirmó con +CMGS aunque no llegó el OK final
    detail = response.decode('utf-8', errors='ignore').strip()
    return False, f"Sin confirmación del módem en {timeout} s (respuesta: {detail or 'ninguna'})"

def send_sms_via_modem(phone, message):
    """Envía un SMS (una sola parte, hasta 160 caracteres) usando el módem GSM con pyserial.
    Devuelve (True, referencia) solo si el módem confirmó el envío."""
    global modem
    try:
        if not modem or not modem.is_open:
            raise Exception("Módem no inicializado")

        # Formatear número de teléfono
        clean_phone = format_phone_number(phone)

        # Limpiar mensaje de caracteres especiales, acentos, ñ, etc.
        clean_message = clean_sms_message(message)
        logger.info(f"Enviando SMS a {clean_phone}")
        logger.debug(f"Mensaje original: {message}")
        logger.debug(f"Mensaje limpio: {clean_message}")

        # Limpiar buffer antes de enviar para evitar datos residuales (sin perder avisos de entrega)
        _drain(modem)
        modem.reset_output_buffer()

        # Configurar modo texto
        response = send_at_command(modem, 'AT+CMGF=1')
        if 'OK' not in response:
            raise Exception("No se pudo configurar modo texto")

        # Enviar SMS
        logger.debug(f"Enviando comando AT+CMGS a {clean_phone}")
        modem.write(f'AT+CMGS="{clean_phone}"\r\n'.encode('utf-8'))
        time.sleep(1)
        
        # Esperar prompt '>' (o un ERROR, por ejemplo si el número no le gusta al módem)
        prompt_data = b''
        prompt_received = False
        start_time = time.time()
        while time.time() - start_time < 10:
            if modem.in_waiting:
                prompt_data += _read_modem(modem)
                if b'>' in prompt_data:
                    prompt_received = True
                    logger.debug("Prompt '>' recibido")
                    break
                if b'ERROR' in prompt_data:
                    break
            time.sleep(0.1)

        if not prompt_received:
            _abort_sms_input()
            detail = prompt_data.decode('utf-8', errors='ignore').strip()
            raise Exception(f"El módem no dio el prompt '>' (respuesta: {detail or 'ninguna en 10 s'})")

        # Enviar mensaje limpio y Ctrl+Z (0x1A)
        modem.write(clean_message.encode('utf-8'))
        modem.write(b'\x1A')  # Ctrl+Z

        # Esperar la confirmación (+CMGS) o el error del módem
        sent, info = _wait_cmgs_result()
        if not sent:
            _abort_sms_input()
            raise Exception(info)

        logger.info(f"SMS enviado exitosamente (referencia {info})")
        return True, info

    except Exception as e:
        error = f"Error enviando SMS a {phone}: {str(e)}"
        logger.error(error, exc_info=True)
        return False, error

class ModemUnavailable(Exception):
    """El módem dejó de responder y no se pudo reconectar."""

SMS_MAX_RETRIES = 3

def send_message_to_phone(phone, message):
    """Envía un mensaje a un teléfono (en varias partes si es largo), con hasta SMS_MAX_RETRIES intentos por parte.
    Devuelve (True, [referencia de cada parte]) si salieron todas las partes, o (False, último error).
    Si el módem se cae y no se puede reconectar lanza ModemUnavailable, para que el mensaje
    quede pendiente en vez de marcarse como procesado sin haberse enviado."""
    # Validar que el teléfono tenga al menos 7 dígitos
    if not phone or len(phone) < 7:
        return False, f"Teléfono inválido: {phone}"

    formatted = format_phone_number(phone)
    if len(formatted) != len('+549') + 10:
        logger.warning(f"El teléfono {phone!r} quedó como {formatted}: no son 10 dígitos después de +549, revisar en cli_clientes")

    parts = split_sms(clean_sms_message(message))
    total = len(parts)
    if total > 1:
        logger.info(f"El mensaje no entra en un SMS: se envía en {total} partes")

    references = []
    for number, part in enumerate(parts, 1):
        label = f" (parte {number}/{total})" if total > 1 else ""
        sent = False
        last_error = None

        for attempt in range(1, SMS_MAX_RETRIES + 1):
            touch_heartbeat()  # cada intento cuenta como actividad para el watchdog
            logger.info(f"Intento {attempt}/{SMS_MAX_RETRIES} de envío a {phone}{label}")
            sent, result = send_sms_via_modem(phone, part)
            if sent:
                references.append(result)
                logger.info(f"✅ SMS enviado a {phone}{label} en intento {attempt}")
                break

            last_error = result
            logger.warning(f"Intento {attempt}/{SMS_MAX_RETRIES} falló para {phone}{label}: {result}")

            # Si la falla es porque se cayó el módem, cortar acá: si no, se gastarían los
            # intentos de todos los mensajes que siguen y se marcarían como procesados sin enviarse.
            if not ensure_modem():
                raise ModemUnavailable("El módem no responde y no se pudo reconectar")

            if attempt < SMS_MAX_RETRIES:
                logger.info(f"Esperando 2 segundos antes del reintento {attempt + 1}...")
                time.sleep(2)

        if not sent:
            return False, f"{last_error}{label}"

    return True, references

# ---------------------------------------------------------------------------
# Seguimiento de entregas (columna mensaje_a_sms.men_entregado)
#   NULL = todavía sin dato (o la operadora nunca mandó el aviso)
#   1    = llegó completo (todas sus partes) a al menos uno de los teléfonos
#   0    = no llegó a ninguno (falló el envío, no tenía teléfonos, o la operadora avisó que no lo entregó)
# ---------------------------------------------------------------------------

# msg_id -> {"created": epoch, "complete": bool, "result": None/0/1,
#            "phones": {teléfono: {"number": últimos 10 dígitos, "sent_at": epoch,
#                                  "pending": [referencias sin aviso], "failed": bool}}}
_deliveries = {}

def _save_deliveries():
    tmp_path = DELIVERY_CACHE_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(_deliveries, f)
        os.replace(tmp_path, DELIVERY_CACHE_FILE)
    except Exception as e:
        logger.warning(f"No se pudo guardar el seguimiento de entregas: {e}")

def load_deliveries():
    """Carga desde disco los envíos que esperan aviso de entrega (por si el proceso se reinició)."""
    global _deliveries
    try:
        with open(DELIVERY_CACHE_FILE, "r", encoding="utf-8") as f:
            _deliveries = json.load(f)
    except FileNotFoundError:
        _deliveries = {}
    except Exception as e:
        logger.warning(f"No se pudo leer el seguimiento de entregas ({e}). Se empieza vacío.")
        _deliveries = {}
    logger.info(f"Seguimiento de entregas: {len(_deliveries)} mensajes esperando aviso")

def _new_delivery_entry():
    return {"created": time.time(), "complete": False, "result": None, "phones": {}}

def track_sent(msg_id, phone, references):
    """Registra un envío confirmado, para asociarle después los avisos de entrega de cada parte."""
    pending = []
    for reference in references:
        match = re.match(r'\d+', str(reference))
        if not match:
            logger.warning(f"Mensaje SMS {msg_id}: referencia {reference!r} no numérica, no se puede seguir la entrega a {phone}")
            return
        pending.append(int(match.group()))

    entry = _deliveries.setdefault(str(msg_id), _new_delivery_entry())
    entry["phones"][phone] = {
        "number": format_phone_number(phone)[-10:],
        "sent_at": time.time(),
        "pending": pending,
        "failed": False,
    }
    _save_deliveries()

def _evaluate_delivery(entry):
    """Decide el resultado del mensaje cuando ya se sabe: 1 si llegó completo a algún teléfono, 0 si falló en todos."""
    if not entry["complete"] or entry["result"] is not None:
        return
    phones = list(entry["phones"].values())
    if any(not p["pending"] and not p["failed"] for p in phones):
        entry["result"] = 1
    elif phones and all(p["failed"] for p in phones):
        entry["result"] = 0

def finish_tracking(msg_id, phones_sent, phones_skipped):
    """Se llama cuando el mensaje ya quedó procesado. Si no salió a ningún teléfono queda como
    no entregado (0); si salió, el resultado lo van a decidir los avisos de entrega."""
    key = str(msg_id)
    if phones_sent == 0 and phones_skipped == 0:
        entry = _new_delivery_entry()
        entry.update(complete=True, result=0)
        _deliveries[key] = entry
    elif key in _deliveries:
        _deliveries[key]["complete"] = True
        _evaluate_delivery(_deliveries[key])
    else:
        return  # solo omitidos por duplicado: queda sin dato
    _save_deliveries()

def _apply_delivery_report(reference, address, status):
    """Asocia un aviso de entrega a su envío y actualiza el estado. Devuelve False si no encontró a qué envío corresponde."""
    # La referencia se repite cada 256 SMS: además se compara el teléfono que trae el aviso.
    number = ''.join(c for c in address if c.isdigit())[-10:]
    candidates = [
        (entry, info)
        for entry in _deliveries.values() if entry["result"] is None
        for info in entry["phones"].values() if reference in info["pending"]
    ]
    same_phone = [c for c in candidates if c[1]["number"] == number]
    if same_phone:
        candidates = same_phone
    elif len(candidates) != 1:
        return False
    entry, info = max(candidates, key=lambda c: c[1]["sent_at"])

    if status < 32:
        # 0-31: entregado al teléfono
        info["pending"].remove(reference)
    elif status < 64:
        # 32-63: la operadora sigue intentando (teléfono apagado, sin señal...). Llega otro aviso después.
        return True
    else:
        # 64-127: la operadora dejó de intentar, no se entregó
        info["pending"].remove(reference)
        info["failed"] = True
    _evaluate_delivery(entry)
    return True

def _delivery_status_text(status):
    if status < 32:
        return "entregado"
    if status < 64:
        return "la operadora sigue intentando"
    return "no entregado"

def process_delivery_reports(db):
    """Aplica los avisos de entrega leídos del módem y guarda en men_entregado los resultados ya decididos.
    Nunca corta la rutina: si la base falla, se reintenta en el próximo ciclo."""
    changed = False
    try:
        while _pending_reports:
            reference, address, status = _pending_reports.pop(0)
            changed = True
            if _apply_delivery_report(reference, address, status):
                logger.info(f"Aviso de entrega: referencia {reference}, teléfono {address}: {_delivery_status_text(status)} (estado {status})")
            else:
                logger.info(f"Aviso de entrega sin envío pendiente asociado (referencia {reference}, teléfono {address}, estado {status})")

        now = time.time()
        for key, entry in list(_deliveries.items()):
            if entry["result"] is None and now - entry["created"] > DELIVERY_REPORT_TIMEOUT:
                logger.info(f"Mensaje SMS {key}: no se supo si se entregó en {DELIVERY_REPORT_TIMEOUT // 3600} h, queda sin dato")
                del _deliveries[key]
                changed = True

        for key, entry in list(_deliveries.items()):
            if entry["result"] is not None:
                db.set_delivered("mensaje_a_sms", key, entry["result"])
                logger.info(f"Mensaje SMS {key}: men_entregado = {entry['result']}")
                del _deliveries[key]
                changed = True
    except Exception as e:
        logger.warning(f"No se pudo guardar el estado de entrega en la base (se reintenta en el próximo ciclo): {e}")
    finally:
        if changed:
            _save_deliveries()

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
        
        # Verificar estado del módem antes de procesar (reconecta si está caído o con el puerto cerrado)
        if not ensure_modem():
            logger.error("No se pudo reconectar el módem, saltando esta ejecución. Los mensajes quedan pendientes.")
            return

        # Avisos de entrega que llegaron desde el ciclo anterior
        process_delivery_reports(db)

        # Recuperar mensajes no enviados con un límite para evitar sobrecarga.
        # Columnas por nombre (no SELECT *) para que agregar columnas a la tabla no rompa el script.
        query = "SELECT id, mensaje, cliente FROM mensaje_a_sms WHERE men_status = 0 LIMIT 100"
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

        for index, msg in enumerate(unsent_messages):
            touch_heartbeat()
            msg_id = msg[0]  # antes del try, para que el except marque este mensaje y no el anterior
            try:
                msg_id, message, code_cli = msg
                logger.info(f"Procesando mensaje SMS ID {msg_id} para cliente {code_cli}")
                
                # Obtener teléfonos del cliente
                phones_result = db.get_phone_from_code(code_cli)
                
                if not phones_result or not phones_result[0]:
                    logger.warning(f"No hay teléfonos registrados para el cliente {code_cli}. Marcando mensaje como enviado.")
                    db.mark_as_process("mensaje_a_sms", msg_id)
                    finish_tracking(msg_id, phones_sent=0, phones_skipped=0)  # no entregado
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

                    # Hasta SMS_MAX_RETRIES intentos (por parte, si el mensaje es largo).
                    # Si el módem se cae sin poder reconectar, lanza ModemUnavailable.
                    success, result = send_message_to_phone(phone, message)

                    if success:
                        phones_sent += 1
                        remember_sent(phone, message)
                        track_sent(msg_id, phone, result)  # para asociarle el aviso de entrega
                    else:
                        last_error = result
                        phones_failed += 1
                        all_sent = False
                        logger.error(f"❌ No se pudo enviar a {phone}: {last_error}")
                        # Guardar observación del último error. La tabla es compartida con Telegram
                        # y las llamadas, por eso el prefijo "SMS".
                        obs = f"SMS - {SMS_MAX_RETRIES} intentos fallidos para {phone}: {str(last_error)[:500]}"
                        # Escapar barras y comillas simples para evitar errores SQL
                        db.insert_obs(obs.replace("\\", "\\\\").replace("'", "''"))

                # Marcar como procesado después de intentar todos los teléfonos
                # (ya sea exitoso o fallido, después de 3 reintentos por teléfono)
                db.mark_as_process("mensaje_a_sms", msg_id)
                finish_tracking(msg_id, phones_sent, phones_skipped)
                messages_processed += 1

                skipped_note =f" ({phones_skipped} omitidos por duplicado)" if phones_skipped else ""
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
            
            except ModemUnavailable as modem_error:
                # No marcar este mensaje ni los siguientes: quedan pendientes para cuando vuelva el módem
                logger.error(f"{modem_error}. Se corta el lote: {message_count - index} mensajes (desde el ID {msg_id}) quedan pendientes para el próximo ciclo.")
                break

            except Exception as msg_error:
                logger.exception(f"Error al procesar mensaje SMS {msg}: {str(msg_error)}")
                # Marcar como procesado para evitar reprocesamiento infinito en caso de error estructural
                try:
                    db.mark_as_process("mensaje_a_sms", msg_id)
                    logger.warning(f"Mensaje SMS {msg_id} marcado como procesado debido a error de procesamiento")
                except Exception as mark_error:
                    logger.error(f"No se pudo marcar mensaje {msg_id} como procesado: {mark_error}")
                messages_failed += 1

        # Avisos de entrega que llegaron durante el lote, y resultados de los mensajes que no salieron
        process_delivery_reports(db)

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
    load_deliveries()

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
