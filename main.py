import json
from email_wrapper import EmailAccount
from db import Database
import time
DATABASE_FILE = "dbConf.json"
ACCOUNT_SETTINGS = "account_settings.json"

with open(DATABASE_FILE) as db:
	configFile = json.load(db)


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
            print(e)
    return False

if __name__ == '__main__':
    settings = load_settings(ACCOUNT_SETTINGS)
    accounts = [EmailAccount(sett['email'],
                             sett['password'],
                             sett['server'],
                             sett['port']) for sett in settings]
                        
    
    db = Database(configFile["user"], 
                  configFile["passwd"], 
                  configFile["host"], 
                  configFile["port"], 
                  configFile["database"])

    while True:
        messages = db.get_unsent()
        print(messages, end="\r")
        for message in messages:
            id = message[0]
            content = message[1]
            code_cli = message[3]
            emails = db.get_email_from_code(code_cli)
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
        time.sleep(5)
            

