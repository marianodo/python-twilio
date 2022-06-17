import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json

DATABASE_FILE = "dbConf.json"

with open(DATABASE_FILE) as db:
	configFile = json.load(db)

class EmailAccount(object):
	def __init__(self, sender_email, passwd, server, port):
		self.sender_email = sender_email
		self.passwd = passwd
		self.server = server
		self.port = port

	def send_to(self, email, content):
		try:
			#Setup the MIME
			message = MIMEMultipart()
			message['From'] = self.sender_email
			message['To'] = email
			message['Subject'] = 'Sistema de Mensajes'   #The subject line
			#The body and the attachments for the mail
			message.attach(MIMEText(content, 'plain'))
			#Create SMTP session for sending the mail
			session = smtplib.SMTP(self.server, self.port) #use gmail with port
			session.starttls() #enable security
			session.login(self.sender_email, self.passwd) #login with mail_id and password
			text = message.as_string()
			session.sendmail(self.sender_email, email, text)
			session.quit()
			return True
		except Exception as e:
			print(e)
			raise e