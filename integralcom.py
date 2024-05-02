import os
import logging
import requests
import time
from dbSigesmen import Database
import json

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_DATABASE = os.getenv("DB_DATABASE")
TOKEN = os.getenv("TOKEN")
db = Database(DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_DATABASE)
API = f"https://api.telegram.org/bot{TOKEN}"

# Enable logging
logging.basicConfig(
	format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

logger = logging.getLogger(__name__)

def routine():
	unsent_msg = db.get_unsent()
	logger.info(f"Total mensajes sin enviar: {len(unsent_msg)}")
	for msg in unsent_msg:
		id = msg[0]
		message = msg[1]
		code_cli = msg[3]
		phones = db.get_phone_from_code(code_cli)
		if not phones:
			return
		phones_list = phones.split(";")
		logger.info(f"Procesando mensaje {message}")
		for phone in phones_list:
			logger.info(f"Enviando mensaje a {phone}")
			success, obs = send_message_to_phone(phone.strip(), message)
			if not success:
				logger.error(f"Mensaje al telefono {phone} no enviado")
				db.insert_obs(obs)
		db.mark_as_sent(id) # TODO: we should mark as sent when we really sent the message


def send_message_to_phone(phone, message):
	last_num_phone = phone[-7:]
	chat_id = db.get_chat_id(last_num_phone)
	if chat_id:
		SEND_MESSAGE = f"{API}/sendMessage?chat_id={chat_id}&text={message}"
		r = requests.get(SEND_MESSAGE)
		if r.status_code == 200:
			return True, ""
		else:
			return False, f"Codigo de error: {r.status_code}. Texto: {r.text}"
	else:
		error = f"El teléfono {phone} no está registrado"
		return False, error


if __name__ == '__main__':
	try:
		routine()
	except Exception as e:
		print(f"Error al tratar de enviar un mensaje: {e}")
	time.sleep(60)
