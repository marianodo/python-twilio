import os
import logging
from dotenv import load_dotenv
from gsmmodem.modem import GsmModem
from gsmmodem.exceptions import TimeoutException, CommandError

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
        
        logger.info(f"Iniciando módem en puerto {MODEM_PORT}")
        
        # Usar baudrate configurado o autodetección
        if MODEM_BAUDRATE:
            logger.info(f"Usando baudrate configurado: {MODEM_BAUDRATE}")
            modem = GsmModem(MODEM_PORT, MODEM_BAUDRATE)
        else:
            logger.info("Baudrate no configurado, intentando detección automática...")
            common_baudrates = [115200, 9600, 19200, 38400, 57600]
            modem = None
            for baud in common_baudrates:
                try:
                    logger.info(f"Probando {baud} bps...")
                    modem = GsmModem(MODEM_PORT, baud)
                    modem.connect(MODEM_PIN)
                    logger.info(f"✅ Módem conectado a {baud} bps")
                    break
                except Exception as e:
                    logger.debug(f"No responde a {baud} bps: {e}")
                    if modem:
                        try:
                            modem.close()
                        except:
                            pass
                    modem = None
                    continue
        
        if not modem:
            raise Exception("No se pudo detectar el baudrate del módem")
        
        # Conectar solo si no se conectó en la autodetección
        try:
            modem.connect(MODEM_PIN)
        except:
            pass  # Ya está conectado
        
        # Verificar nivel de señal
        signal_strength = modem.signalStrength
        network_name = modem.networkName
        
        logger.info(f"Módem conectado exitosamente!")
        logger.info(f"Señal: {signal_strength}")
        logger.info(f"Red: {network_name}")
        
        if signal_strength < 10:
            logger.warning(f"Señal débil detectada: {signal_strength}")
        
        # Formatear número de teléfono
        clean_phone = format_phone_number(TEST_PHONE)
        logger.info(f"Enviando SMS de prueba a {clean_phone}")
        logger.info(f"Mensaje: {TEST_MESSAGE}")
        
        # Enviar SMS con delivery report
        logger.info("Enviando SMS...")
        sms = modem.sendSms(clean_phone, TEST_MESSAGE, waitForDeliveryReport=True)
        
        logger.info("=" * 50)
        logger.info("SMS ENVIADO EXITOSAMENTE")
        logger.info(f"Status: {sms.status}")
        logger.info(f"Reference: {sms.reference}")
        logger.info("=" * 50)
        
        return True
        
    except TimeoutException:
        logger.error("Timeout esperando respuesta del módem")
        return False
    except CommandError as e:
        logger.error(f"Error de comando AT: {e}")
        return False
    except Exception as e:
        logger.error(f"Error inesperado: {e}", exc_info=True)
        return False
    finally:
        if modem:
            try:
                logger.info("Cerrando conexión con el módem...")
                modem.close()
                logger.info("Módem desconectado correctamente")
            except Exception as e:
                logger.error(f"Error cerrando módem: {e}")

if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("TEST DE ENVÍO SMS CON MÓDEM GSM")
    logger.info("=" * 50)
    logger.info(f"Puerto: {MODEM_PORT}")
    logger.info(f"Baudrate: {MODEM_BAUDRATE}")
    logger.info(f"PIN: {MODEM_PIN if MODEM_PIN else 'No configurado'}")
    logger.info(f"Destino: {TEST_PHONE}")
    logger.info(f"Mensaje: {TEST_MESSAGE}")
    logger.info("=" * 50)
    
    success = test_modem()
    
    if success:
        logger.info("\n✅ Test completado exitosamente")
    else:
        logger.error("\n❌ Test falló")
        exit(1)
