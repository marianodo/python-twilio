%load_ext autoreload

%autoreload 2
from dbSigesmen import Database
import json
DATABASE_FILE = "dbConf.json"
with open(DATABASE_FILE) as db:
    configFile = json.load(db)
db = Database(configFile["user"], configFile["passwd"], configFile["host"], configFile["port"], configFile["database"])

unsent = db.get_unsent()
for msg in unsent:
    id = msg[0]
    message = msg[1]
    phones = msg[2]
    code_id = msg[3]
    #db.mark_as_sent(id)


unsent = db.get_unsent()
print(unsent)