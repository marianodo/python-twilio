import os
import logging
import requests
import time
from dbSigesmen import Database
import json
import traceback
import re
from twilio.rest import Client
import requests

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_DATABASE = os.getenv("DB_DATABASE")
TOKEN = os.getenv("TOKEN")
SLEEP = int(os.getenv("SLEEP", "60"))
REBOOT_AFTER_ATTEMPS = int(os.getenv("REBOOT_AFTER_ATTEMPS", "60"))
TIME_BETWEEN_CALL = int(os.getenv("TIME_BETWEEN_CALL", "60"))
ACCOUNT_SID = os.getenv("ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
TWILIO_IVR = os.getenv("TWILIO_IVR")
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
            self.lista.remove(telefono)
            del self.elementos[telefono]

    def get_list(self):
        return self.lista

tmp_list = ListaTemporal()

def is_event_to_call(event: str, msg: str) -> bool:
    return bool(re.search(event, msg.lower()))


def remove_non_alphanumeric(input_string):
  """Removes any character from a string that is not a letter, number, or space.

  Args:
    input_string: The string to process.

  Returns:
    The string with only letters, numbers, and spaces.
  """
  alphanumeric_string = re.sub(r'[^a-zA-Z0-9\s]', '', input_string)

  # Remove extra spaces
  single_space_string = re.sub(r'\s+', ' ', alphanumeric_string).strip()

  return single_space_string


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

def call_to_phone(message, phone):
    message = remove_non_alphanumeric(message)
    message = f"Este es un mensaje de integralcom. {message}"
    message = requests.utils.quote(message)

    client = Client(ACCOUNT_SID, TWILIO_TOKEN)
    call = client.calls.create(
        to=phone,
        from_=TWILIO_NUMBER,
        url=f'{TWILIO_IVR}{message}' #Ejemplo: https://ejemplo.com/voice
    )
    print(f"Llamada iniciada. SID: {call.sid}")

@with_db_connection
def routine(db):
    tmp_list.clean()
    query = "SELECT * FROM mensaje_llamada_por_robo WHERE men_status = 0"
    unsent_messages = db.get_unsent(query)
    logger.info(f"Total mensajes sin enviar: {len(unsent_messages)}")
    
    for msg in unsent_messages:
        msg_id, message, _, code_cli, _, _, _ = msg
        query = f"SELECT * FROM clientes_llamada WHERE abonado = {code_cli}"

        row = db.get_one_row(query)
        if row:
            id, client, name, phone, event = row
            print(message, name)
            
            if client not in tmp_list.get_list() and is_event_to_call(event, message):
                tmp_list.insert(client, TIME_BETWEEN_CALL)
                call_to_phone(message, phone)
                logger.info(f"ALARMA: LLAMAR AL TELEFONO {phone} por el evento {event}. Mensaje: {remove_non_alphanumeric(message)}")
        
        db.mark_as_process("mensaje_llamada_por_robo", msg_id)
    
    tmp_list.clean()

        

def send_message_to_phone(db, phone, message):
    """
    Envía un mensaje a un teléfono específico usando la API de Telegram.
    """
    last_num_phone = phone[-7:]
    chat_id = db.get_chat_id(last_num_phone)

    if chat_id:
        url = f"{API}/sendMessage?chat_id={chat_id}&text={message}"
        response = requests.get(url)

        if response.status_code == 200:
            return True, ""
        else:
            error = f"Error al enviar mensaje a {phone}: Código {response.status_code}, Respuesta: {response.text}"
            return False, error
    else:
        error = f"Teléfono {phone} no está registrado en la DB."
        return False, error


if __name__ == '__main__':
    i = 0
    logger.info(f"Starting")
    while i < REBOOT_AFTER_ATTEMPS :
        try:
            routine()
        except Exception as e:
            logger.exception(f"Error en la ejecución principal: {e}")

        time.sleep(SLEEP)
        i += 1