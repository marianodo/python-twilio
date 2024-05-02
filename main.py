import json
from time import gmtime, strftime
from email_wrapper import EmailAccount
from db import Database
import time
import os

ACCOUNT_SETTINGS = "account_settings.json"

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_DATABASE = os.getenv("DB_DATABASE")


def load_settings(file):
    with open(file) as settings:
        accounts = json.load(settings)
    return accounts


def send_message(accounts, email, message):
    print("Sending message...")
    for account in accounts:
        try:
            if account.send_to(email, message):
                print(f"Sent to {email} message {message}")
                return True
        except Exception as e:
            print(f"Error: {e}")
    return False


if __name__ == '__main__':
    settings = load_settings(ACCOUNT_SETTINGS)
    accounts = [EmailAccount(sett['email'],
                             sett['password'],
                             sett['server'],
                             sett['port']) for sett in settings]

    db = Database(DB_USER,
                  DB_PASSWORD,
                  DB_HOST,
                  DB_PORT,
                  DB_DATABASE)

    messages = db.get_unsent()
    if not messages:
        print("No hay mensajes nuevos")
    for message in messages:
        id = message[0]
        content = message[1]
        code_cli = message[3]
        emails = db.get_email_from_code(code_cli)
        print(message)
        if emails:
            emails = emails.split(";")
            emails = [email.strip() for email in emails]
        else:
            db.mark_as_sent(id)
            continue

        success = False
        for email in emails:
            success |= send_message(accounts, email, content)

        # if at least one was True, mark as sent
        if success:
            db.mark_as_sent(id)
    time.sleep(60)


