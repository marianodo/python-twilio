import os
import logging
import requests
import time
from dbSigesmen import Database
import json

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_DATABASE = os.getenv("DB_DATABASE")
TOKEN = os.getenv("TOKEN")

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
                logger.error(f"Error de conexión a la DB (intento {attempt + 1} de {retries}): {e}")
                time.sleep(5)  # Espera antes de reintentar
        logger.critical("No se pudo conectar a la base de datos después de varios intentos.")
        raise ConnectionError("Fallo la conexión a la base de datos.")
    return wrapper


@with_db_connection
def routine(db):
    unsent_messages = db.get_unsent()
    logger.info(f"Total mensajes sin enviar: {len(unsent_messages)}")

    for msg in unsent_messages:
        msg_id, message, _, code_cli = msg

        phones = db.get_phone_from_code(code_cli)
        if not phones:
            logger.warning(f"No hay teléfonos registrados para el cliente {code_cli}. Marcando como enviado.")
            db.mark_as_sent(msg_id)
            continue

        phones_list = [phone.strip() for phone in phones.split(";")]
        logger.info(f"Procesando mensaje para cliente {code_cli}: {message}")

        all_sent = True

        for phone in phones_list:
            logger.info(f"Enviando mensaje a {phone}...")
            success, obs = send_message_to_phone(db, phone, message)

            if not success:
                logger.error(f"No se pudo enviar mensaje a {phone}: {obs}")
                db.insert_obs(obs)
                all_sent = False

        if all_sent:
            db.mark_as_sent(msg_id)
        else:
            logger.warning(f"El mensaje {msg_id} no fue enviado correctamente a todos los destinatarios.")



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
    while True:
        try:
            routine()
        except Exception as e:
            logger.exception(f"Error en la ejecución principal: {e}")

        time.sleep(60)