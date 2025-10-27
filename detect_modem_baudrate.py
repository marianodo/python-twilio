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
MODEM_PIN = os.getenv("MODEM_PIN", None)

def detect_baudrate():
    """Detecta automáticamente el baudrate del módem"""
    if not MODEM_PORT:
        raise ValueError("MODEM_PORT no configurado en .env")
    
    logger.info(f"Detectando baudrate del módem en puerto {MODEM_PORT}...")
    logger.info("Probando diferentes velocidades...")
    
    # Baudrates comunes para módems GSM
    common_baudrates = [115200, 9600, 19200, 38400, 57600, 230400]
    detected_baudrate = None
    modem = None
    
    for baud in common_baudrates:
        try:
            logger.info(f"Probando {baud} bps...")
            modem = GsmModem(MODEM_PORT, baud)
            
            # Intentar conectar, pero sin esperar respuesta completa
            # Solo verificamos si el módem responde
            modem.connect(MODEM_PIN, timeout=3)
            
            # Si llegamos aquí, el módem respondió
            detected_baudrate = baud
            logger.info(f"✅ Módem detectado a {baud} bps")
            
            # Obtener información del módem
            signal = modem.signalStrength
            network = modem.networkName
            manufacturer = modem.manufacturer
            model = modem.model
            
            logger.info("=" * 50)
            logger.info("DETECCIÓN EXITOSA")
            logger.info("=" * 50)
            logger.info(f"Puerto: {MODEM_PORT}")
            logger.info(f"Baudrate: {baud} bps")
            logger.info(f"Señal: {signal}")
            logger.info(f"Red: {network}")
            logger.info(f"Fabricante: {manufacturer}")
            logger.info(f"Modelo: {model}")
            logger.info("=" * 50)
            
            modem.close()
            break
            
        except Exception as e:
            logger.warning(f"  ❌ No responde a {baud} bps")
            if modem:
                try:
                    modem.close()
                except:
                    pass
            modem = None
            continue
    
    if not detected_baudrate:
        logger.error("No se pudo detectar el baudrate del módem")
        logger.error("Verifica que:")
        logger.error("  1. El módem esté conectado al puerto correcto")
        logger.error("  2. El módem esté encendido")
        logger.error("  3. No haya otro programa usando el puerto")
        return None
    
    return detected_baudrate

if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("DETECTOR DE BAUDRATE PARA MÓDEM GSM")
    logger.info("=" * 50)
    
    if not MODEM_PORT:
        logger.error("ERROR: MODEM_PORT no está configurado en .env")
        logger.info("Agrega MODEM_PORT=COM3 a tu archivo .env")
        exit(1)
    
    detected = detect_baudrate()
    
    if detected:
        logger.info(f"\n✅ Baudrate detectado: {detected} bps")
        logger.info(f"Agrega esto a tu .env: MODEM_BAUDRATE={detected}")
    else:
        logger.error("\n❌ No se pudo detectar el baudrate")
        exit(1)
