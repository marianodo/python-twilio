import MySQLdb
import json

DATABASE_FILE = "dbConf.json"


INSERT_MESSAGE = "INSERT INTO mea_mensajes_alarma(mea_codigo_cliente, mea_grupo, mea_fecha, mea_hora, mea_contenido, mea_codigo_accion, mea_estado, mea_verificado) VALUES ({0}, 1, CURRENT_DATE(), CURRENT_TIME(), '{1}', 0, 0, 0)"
GET_CLIENT = "SELECT EXISTS(SELECT * FROM cli_clientes WHERE cli_codigo =  {0} )"
GET_CLAIM_ID = "SELECT men_id from men_mensajes WHERE men_origen_id = {0}"
GET_UNSENT = "SELECT * FROM mensaje_a_telegram WHERE men_status = 0"

class Database(object):
    __instance   = None
    __host       = None
    __user       = None
    __password   = None
    __database   = None
    __session    = None
    __connection = None


    def __init__(self, user, password, host, port, database):
        self.__user     = user
        self.__password = password
        self.__host     = host
        self.__port     = port
        self.__database = database
    ## End def __init__

    def __open(self):
        try:
            cnx = MySQLdb.connect(host = self.__host, user = self.__user, passwd = self.__password, db = self.__database, port = self.__port)
            self.__connection = cnx
            self.__session    = cnx.cursor()
        except MySQLdb.Error as e:
            print(f"Error {e}")
    ## End def __open
    def open(self):
        self.__open()

    def __close(self):
        self.__session.close()
        self.__connection.close()
    ## End def __close
    
    def __selectOneRow(self, query):
        self.__open()
        self.__session.execute(query)
        return self.__session.fetchone()[0]

    def __selectAll(self, query):
        self.__open()
        self.__session.execute(query)
        return self.__session.fetchall()

    def isCodeExists(self, code):
        return self.__selectOneRow(GET_CLIENT.format(code))

    def sendMessage(self, code, message):
        self.__open()
        self.__session.execute(INSERT_MESSAGE.format(code, message))
        self.__connection.commit()
        self.__close()
        return self.__session.lastrowid

    def getClaimId(self, messageId):
        return self.__selectOneRow(GET_CLAIM_ID.format(messageId))
        
    def get_unsent(self):
        return self.__selectAll(GET_UNSENT)
        
    def __enter__(self):
        return self

    def __exit__(self, ext_type, exc_value, traceback):
        self.cursor.close()
        if isinstance(exc_value, Exception):
            self.connection.rollback()
        else:
            self.connection.commit()
        self.connection.close()


# if __name__ == '__main__':
#     with open(DATABASE_FILE) as db:
#             configFile = json.load(db)
#     db = Database(configFile["user"], configFile["passwd"], configFile["host"], configFile["port"], configFile["database"])
