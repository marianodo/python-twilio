import mysql.connector as MySQLdb
import json

DATABASE_FILE = "dbConf.json"


GET_UNSENT = "SELECT * FROM mensaje_a_python_email WHERE men_status = 0"
MARK_AS_SENT = "UPDATE mensaje_a_python_email SET men_status = 1 WHERE id = {0}"
GET_CLIENT_EMAIL = "SELECT cli_mail FROM cli_clientes WHERE cli_codigo = {0}"

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
        result = self.__session.fetchone()
        
        return result[0] if result else []

    def __selectAll(self, query):
        self.__open()
        self.__session.execute(query)
        return self.__session.fetchall()


    def mark_as_sent(self, id):
        self.__open()
        self.__session.execute(MARK_AS_SENT.format(id))
        self.__connection.commit()
        self.__close()

    def get_email_from_code(self, code):
        return self.__selectOneRow(GET_CLIENT_EMAIL.format(code))

    # def insert_obs(self, obs):
    #     self.__open()
    #     self.__session.execute(INSERT_OBS.format(obs))
    #     self.__connection.commit()
    #     self.__close()

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
