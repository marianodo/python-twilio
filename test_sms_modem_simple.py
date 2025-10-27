import os
import logging
from dotenv import load_dotenv
from gsmmodem.modem import GsmModem

# Cargar variables de entorno desde .env
load_dotenv()

# Configurar logging básico
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuración desde .env
MODEM_PORT = os.getenv("MODEM_PORT")
MODEM_BAUDRATE = int(os.getenv("MODEM_BAUDRATE", "115200"))
MODEM_PIN = os.getenv("MODEM_PIN", None)

# Datos de prueba
TEST_PHONE = "3517157848"
TEST_MESSAGE = "Mensaje de test desde módem GSM"

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

def test_modem():
    """Inicializa el módem y envía un SMS de prueba"""
    modem = None
    
    try:
        if not MODEM_PORT:
            raise ValueError("MODEM_PORT no configurado en .env")
        
        logger.info("=" * 60)
        logger.info("CONECTANDO CON EL MÓDEM GSM")
        logger.info("=" * 60)
        logger.info(f"Puerto: {MODEM_PORT}")
        logger.info(f"Baudrate: {MODEM_BAUDRATE}")
        logger.info(f"PIN: {'Configurado' if MODEM_PIN else 'No requerido'}")
        logger.info("")
        
        # Crear instancia del módem
        logger.info("Creando instancia del módem...")
        modem = GsmModem(MODEM_PORT, MODEM_BAUDRATE)
        logger.info("✅ Instancia creada")
        
        # Conectar al módem
        logger.info("Conectando al módem...")
        modem.connect(MODEM_PIN)
        logger.info("✅ Conectado exitosamente")
        
        # Obtener información del módem
        logger.info("Obteniendo información del módem...")
        signal_strength = modem.signalStrength
        network_name = modem.networkName
        try:
            manufacturer = modem.manufacturer
            model = modem.model
        except:
            manufacturer = "Desconocido"
            model = "Desconocido"
        
        logger.info("")
        logger.info("=" * 60)
        logger.info("INFORMACIÓN DEL MÓDEM")
        logger.info("=" * 60)
        logger.info(f"Señal GSM: {signal_strength}")
        logger.info(f"Red: {network_name}")
        logger.info(f"Fabricante: {manufacturer}")
        logger.info(f"Modelo: {model}")
        logger.info("")
        
        if signal_strength < 10:
            logger.warning("⚠️  Advertencia: Señal débil detectada")
            logger.info("")
        
        # Formatear número de teléfono
        clean_phone = format_phone_number(TEST_PHONE)
        
        logger.info("=" * 60)
        logger.info("PREPARANDO ENVÍO DE SMS")
        logger.info("=" * 60)
        logger.info(f"Destino: {clean_phone}")
        logger.info(f"Mensaje: {TEST_MESSAGE}")
        logger.info("")
        
        # Enviar SMS con delivery report
        logger.info("Enviando SMS...")
        sms = modem.sendSms(clean_phone, TEST_MESSAGE, waitForDeliveryReport=True)
        
        logger.info("")
        logger.info("=" * 60)
        logger.info("SMS ENVIADO EXITOSAMENTE")
        logger.info("=" * 60)
        logger.info(f"Status: {sms.status}")
        logger.info(f"Reference: {sms.reference}")
        logger.info("=" * 60)
        logger.info("")
        
        return True
        
    except Exception as e:
        logger.error("")
        logger.error("=" * 60)
        logger.error("ERROR AL ENVIAR SMS")
        logger.error("=" * 60)
        logger.error(f"Tipo de error: {type(e).__name__}")
        logger.error(f"Mensaje: {str(e)}")
        logger.error("")
        logger.error("Posibles causas:")
        logger.error("  1. El módem no está conectado o apagado")
        logger.error("  2. El puerto COM no es correcto")
        logger.error("  3. El baudrate no es correcto")
        logger.error("  4. La SIM no tiene cobertura o crédito")
        logger.error("  5. El número de teléfono es inválido")
        logger.error("=" * 60)
        return False
    finally:
        if modem:
            try:
                logger.info("Cerrando conexión con el módem...")
                modem.close()
                logger.info("✅ Módem desconectado correctamente")
            except Exception as e:
                logger.error(f"Error cerrando módem: {e}")

if __name__ == '__main__':
    logger.info("")
    logger.info("=" * 60)
    logger.info("TEST DE ENVÍO SMS CON MÓDEM GSM")
    logger.info("=" * 60)
    logger.info("")
    
    if not MODEM_PORT:
        logger.error("ERROR: MODEM_PORT no está configurado en .env")
        logger.error("Agrega MODEM_PORT=COM3 a tu archivo .env")
        exit(1)
    
    success = test_modem()
    
    logger.info("")
    if success:
        logger.info("✅ Test completado exitosamente")
    else:
        logger.error("❌ Test falló")
        logger.info("")
        logger.info("Para más ayuda:")
        logger.info("  1. Verifica que el módem esté encendido y conectado")
        logger.info("  2. Verifica que no haya otro programa usando el puerto")
        logger.info("  3. Intenta ejecutar: python detect_modem_baudrate.py")
        exit(1)
