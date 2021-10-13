# Download the helper library from https://www.twilio.com/docs/python/install
import os
from twilio.rest import Client


# Find your Account SID and Auth Token at twilio.com/console
# and set the environment variables. See http://twil.io/secure
account_sid = "ACbd15738c8ad5886c63a2b54b39e0f1f6"
auth_token = "13c338c2abf55ee5ba01e9294c548f08"
client = Client(account_sid, auth_token)

message = client.messages \
                .create(
                     body="oootro msj de prueba",
                     from_='whatsapp:+13203907561',
                     to='whatsapp:+5493517157848'
                 )

print(message.sid)
