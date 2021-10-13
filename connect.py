from dbSigesmen import Database
import json
DATABASE_FILE = "dbConf.json"
with open(DATABASE_FILE) as db:
    configFile = json.load(db)
db = Database(configFile["user"], configFile["passwd"], configFile["host"], configFile["port"], configFile["database"])

db.open()