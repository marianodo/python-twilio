import os
import logging
import requests
import time
from dbSigesmen import Database
import json
import traceback
from flask import Flask, jsonify

app = Flask(__name__)

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_DATABASE = os.getenv("DB_DATABASE")
TOKEN = os.getenv("TOKEN")
SLEEP = int(os.getenv("SLEEP", "60"))
REBOOT_AFTER_ATTEMPS = int(os.getenv("REBOOT_AFTER_ATTEMPS", "60"))

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
    query = "SELECT * FROM mensaje_a_telegram WHERE men_status = 0"
    unsent_messages = db.get_unsent(query)
    logger.info(f"Total mensajes sin enviar: {len(unsent_messages)}")
    
    for msg in unsent_messages:
        logger.info(f"Mesanje: {msg}")
        msg_id, message, _, code_cli, _, _, _, _ = msg

        phones = db.get_phone_from_code(code_cli)[0]
        if not phones:
            logger.warning(f"No hay teléfonos registrados para el cliente {code_cli}. Marcando como enviado.")
            db.mark_as_sent(msg_id)
            continue
        logger.info(phones)
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

        db.mark_as_sent(msg_id)
        if not all_sent:
            logger.warning(f"El mensaje {msg_id} no fue enviado correctamente a todos los destinatarios.")



def send_message_to_phone(db, phone, message):
    """
    Envía un mensaje a un teléfono específico usando la API de Telegram.
    """
    try:
        last_num_phone = phone[-7:]
        chat_id = db.get_chat_id(last_num_phone)
        logger.info(chat_id)
        if chat_id:
            chat_id = chat_id[0]
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
    except Exception as e:
        return False, e


@app.route("/health", methods=['GET'])
def health_check():
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    # Inicia el servidor Flask en un hilo separado para no bloquear la tarea principal
    from threading import Thread
    thread = Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': 8080, 'debug': False, 'use_reloader': False})
    thread.daemon = True  # Para que el hilo se cierre cuando el programa principal termine
    thread.start()

    # Mantén el script principal en ejecución para que el servidor Flask siga activo
    while True:
        time.sleep(SLEEP) # Duerme por 1 minuto (o el intervalo de ejecución deseado)
        try:
            routine()
        except Exception as e:
            logger.exception(f"Error en la ejecución principal: {e}")