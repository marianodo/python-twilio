import os
import time
import logging
from dotenv import load_dotenv
import serial
import unicodedata

# Cargar variables de entorno
load_dotenv()

# Configurar logging
logging.basicConfig(
    level=logging.INFO,  # Cambiar a logging.DEBUG para ver más detalles
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuración desde .env
MODEM_PORT = os.getenv("MODEM_PORT")
MODEM_BAUDRATE = int(os.getenv("MODEM_BAUDRATE", "115200"))

# Datos de prueba
TEST_PHONE = "3517157848"
TEST_MESSAGE = "Mensaje de test desde modem GSMmmmm"

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
    """Formatea el número de teléfono para el módem GSM"""
    clean_phone = ''.join(c for c in phone if c.isdigit() or c == '+')
    
    if not clean_phone.startswith('+'):
        if clean_phone.startswith('54'):
            clean_phone = '+' + clean_phone
        elif clean_phone.startswith('0'):
            clean_phone = '+54' + clean_phone[1:]
        else:
            clean_phone = '+54' + clean_phone
    
    return clean_phone

def send_at_command(ser, command, timeout=5):
    """Envía un comando AT y espera respuesta completa"""
    # Limpiar buffer de entrada antes de enviar
    ser.reset_input_buffer()
    
    logger.debug(f"Enviando: {command}")
    ser.write((command + '\r\n').encode('utf-8'))
    
    # Dar tiempo para que el módem procese
    time.sleep(0.5)
    
    response = b''
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        if ser.in_waiting:
            new_data = ser.read(ser.in_waiting)
            response += new_data
            
            # Log debug de lo que recibimos
            logger.debug(f"Recibido parcial: {new_data}")
            
            # Si recibimos OK o ERROR, esperar un poco más por si hay datos adicionales
            if b'OK' in response or b'ERROR' in response:
                time.sleep(0.3)  # Esperar un poco más por datos adicionales
                if ser.in_waiting:
                    response += ser.read(ser.in_waiting)
                break
        
        time.sleep(0.1)
    
    result = response.decode('utf-8', errors='ignore').strip()
    logger.debug(f"Respuesta completa: {result}")
    
    return result

def test_modem():
    """Inicializa el módem y envía un SMS de prueba usando pyserial"""
    
    if not MODEM_PORT:
        raise ValueError("MODEM_PORT no configurado en .env")
    
    ser = None
    
    try:
        logger.info("=" * 60)
        logger.info("TEST DE ENVÍO SMS CON PY-SERIAL")
        logger.info("=" * 60)
        logger.info(f"Puerto: {MODEM_PORT}")
        logger.info(f"Baudrate: {MODEM_BAUDRATE}")
        logger.info("")
        
        # Abrir puerto serie
        logger.info("Abriendo puerto serie...")
        ser = serial.Serial(MODEM_PORT, MODEM_BAUDRATE, timeout=3)
        time.sleep(2)  # Dar tiempo al módem para iniciar
        logger.info("✅ Puerto abierto")
        
        # Verificar que el módem responda
        logger.info("Verificando módem (AT)...")
        response = send_at_command(ser, 'AT')
        logger.info(f"Respuesta: {response}")
        
        
        # Verificar señal GSM
        logger.info("Verificando señal GSM (AT+CSQ)...")
        response = send_at_command(ser, 'AT+CSQ')
        logger.info(f"Respuesta: {response}")
        
        # Verificar red
        logger.info("Verificando red (AT+COPS?)...")
        response = send_at_command(ser, 'AT+COPS?')
        logger.info(f"Respuesta: {response}")
        
        # Formatear número de teléfono
        clean_phone = format_phone_number(TEST_PHONE)

        # Limpiar mensaje de caracteres especiales
        clean_message = clean_sms_message(TEST_MESSAGE)

        logger.info("")
        logger.info("=" * 60)
        logger.info("PREPARANDO ENVÍO DE SMS")
        logger.info("=" * 60)
        logger.info(f"Destino: {clean_phone}")
        logger.info(f"Mensaje original: {TEST_MESSAGE}")
        logger.info(f"Mensaje limpio: {clean_message}")
        logger.info("")
        
        # Configurar modo texto
        logger.info("Configurando modo texto (AT+CMGF=1)...")
        response = send_at_command(ser, 'AT+CMGF=1')
        logger.info(f"Respuesta: {response}")
        
        if 'OK' not in response:
            raise Exception("No se pudo configurar modo texto")
        
        # Preparar comando AT+CMGS para enviar SMS
        logger.info("Enviando SMS...")
        logger.info("Enviando comando AT+CMGS...")
        ser.write(f'AT+CMGS="{clean_phone}"\r\n'.encode('utf-8'))
        time.sleep(1)
        
        # Esperar prompt '>'
        timeout = 10
        start_time = time.time()
        prompt_received = False
        while time.time() - start_time < timeout:
            if ser.in_waiting:
                data = ser.read(ser.in_waiting)
                logger.debug(f"Recibido: {data}")
                if b'>' in data:
                    prompt_received = True
                    logger.info("✅ Prompt '>' recibido, enviando mensaje...")
                    break
            time.sleep(0.1)
        
        if not prompt_received:
            raise Exception("Timeout esperando prompt '>' del módem")

        # Enviar mensaje limpio y Ctrl+Z (0x1A) usando ser.write directamente
        # porque aquí ya estamos en modo interactivo
        ser.write(clean_message.encode('utf-8'))
        ser.write(b'\x1A')  # Ctrl+Z para finalizar
        
        logger.info("Esperando confirmación del módem...")
        
        # Esperar respuesta final usando el método send_at_command no aplica aquí
        # porque necesitamos leer la respuesta del +CMGS:
        response = b''
        start_time = time.time()
        while time.time() - start_time < 30:
            if ser.in_waiting:
                response += ser.read(ser.in_waiting)
                if b'+CMGS:' in response or b'OK' in response:
                    break
            time.sleep(0.5)
        
        result = response.decode('utf-8', errors='ignore')
        logger.info(f"Respuesta del módem: {result}")
        
        logger.info("")
        logger.info("=" * 60)
        logger.info("SMS ENVIADO")
        logger.info("=" * 60)
        logger.info("El SMS ha sido encolado para envío")
        logger.info("Verifica el estado del mensaje en el móvil")
        logger.info("=" * 60)
        
        return True
        
    except Exception as e:
        logger.error("")
        logger.error("=" * 60)
        logger.error("ERROR AL ENVIAR SMS")
        logger.error("=" * 60)
        logger.error(f"Error: {str(e)}")
        logger.error("")
        logger.error("Posibles causas:")
        logger.error("  1. El módem no está encendido o conectado correctamente")
        logger.error("  2. El puerto COM no es correcto")
        logger.error("  3. El baudrate no coincide con la configuración del módem")
        logger.error("  4. La SIM no tiene cobertura o crédito")
        logger.error("  5. La SIM requiere PIN")
        logger.error("=" * 60)
        return False
    finally:
        if ser:
            logger.info("Cerrando puerto serie...")
            ser.close()
            logger.info("✅ Puerto cerrado")

if __name__ == '__main__':
    success = test_modem()
    
    if success:
        logger.info("")
        logger.info("✅ Test completado exitosamente")
    else:
        logger.info("")
        logger.error("❌ Test falló")
        exit(1)
