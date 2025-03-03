import mysql.connector as MySQLdb
import json

DATABASE_FILE = "dbConf.json"


INSERT_MESSAGE = "INSERT INTO mea_mensajes_alarma(mea_codigo_cliente, mea_grupo, mea_fecha, mea_hora, mea_contenido, mea_codigo_accion, mea_estado, mea_verificado) VALUES ({0}, 1, CURRENT_DATE(), CURRENT_TIME(), '{1}', 0, 0, 0)"
GET_CLIENT = "SELECT EXISTS(SELECT * FROM cli_clientes WHERE cli_codigo =  {0} )"
GET_CLIENT_PHONE = "SELECT CLI_CELULAR FROM cli_clientes WHERE cli_codigo = {0}" ###
GET_CLAIM_ID = "SELECT men_id from men_mensajes WHERE men_origen_id = {0}"
GET_UNSENT = "SELECT * FROM mensaje_a_telegram WHERE men_status = 0" ###
MARK_AS_SENT = "UPDATE mensaje_a_telegram SET men_status = 1 WHERE id = {0}" ###
GET_CHAT_ID = "SELECT chat_id FROM telegram_chat WHERE telefono like '%{0}'" ###
INSERT_CHAT_ID = "INSERT INTO telegram_chat(telefono, chat_id) VALUES({0}, {1})"
UPDATE_CHAT_ID = "UPDATE telegram_chat SET chat_id = {0} WHERE telefono = {1}"
INSERT_OBS = "INSERT INTO telegram_observaciones(fecha, observacion) VALUES(now(), '{0}')" ###
class Database(object):
    def __init__(self, user, password, host, port, database):
        self.__user = user
        self.__password = password
        self.__host = host
        self.__port = port
        self.__database = database
        self.__connection = None
        self.__session = None
    ## End def __init__

    def open(self, retries=3, delay=5):
        for attempt in range(retries):
            try:
                self.__connection = MySQLdb.connect(
                    host=self.__host,
                    user=self.__user,
                    passwd=self.__password,
                    db=self.__database,
                    port=self.__port
                )
                self.__session = self.__connection.cursor()
                return
            except MySQLdb.Error as e:
                print(f"Intento {attempt+1} - Error conectando a MySQL: {e}")
                if attempt < retries - 1:
                    sleep(delay)
                else:
                    raise

    def close(self):
        if self.__session:
            self.__session.close()
        if self.__connection:
            self.__connection.close()
    
    def __selectOneRow(self, query):
        self.__session.execute(query)
        result = self.__session.fetchone()
        
        return result[0] if result else []

    def __selectAll(self, query):
        self.__session.execute(query)
        return self.__session.fetchall()

    def isCodeExists(self, code):
        return self.__selectOneRow(GET_CLIENT.format(code))

    def sendMessage(self, code, message):
        self.__session.execute(INSERT_MESSAGE.format(code, message))
        self.__connection.commit()
        return self.__session.lastrowid

    def mark_as_sent(self, client_id):
        self.__session.execute(MARK_AS_SENT.format(client_id))
        self.__connection.commit()

    def insert_obs(self, obs):
        self.__session.execute(INSERT_OBS.format(obs))
        self.__connection.commit()

    def getClaimId(self, messageId):
        return self.__selectOneRow(GET_CLAIM_ID.format(messageId))
        
    def get_unsent(self):
        return self.__selectAll(GET_UNSENT)

    def get_phone_from_code(self, code):
        return self.__selectOneRow(GET_CLIENT_PHONE.format(code))

    def insert_chat_id(self, phone, chat_id):
        value = self.get_chat_id(phone)
        if value:
            self.update_chat_id(phone, chat_id)
        else:
            self.__session.execute(INSERT_CHAT_ID.format(phone, chat_id))
            self.__connection.commit()

        return self.__session.lastrowid

    def update_chat_id(self, phone, chat_id):
        self.__session.execute(UPDATE_CHAT_ID.format(chat_id, phone))
        self.__connection.commit()
    
    def get_chat_id(self, phone):
        return self.__selectOneRow(GET_CHAT_ID.format(phone))
        
