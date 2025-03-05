from flask import Flask, request, Response
import requests

# Texto a convertir a voz
mensaje = 'Este es un mensaje de prueba. Presiona 1 para escucharlo nuevamente.'

app = Flask(__name__)

@app.route('/voice', methods=['POST'])
def voice():
    """Genera el TwiML para la llamada."""
    mensaje = request.args.get('mensaje', 'Mensaje no proporcionado') #Obtiene el parametro mensaje
    twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Say voice="alice" language="es-ES">{mensaje}</Say>
        <Gather input="dtmf" numDigits="1" action="https://python-twilio-production.up.railway.app/gather?mensaje={mensaje}">
            <Say voice="alice" language="es-ES">Presiona 1 para escuchar el mensaje nuevamente.</Say>
        </Gather>
        <Say voice="alice" language="es-ES">Gracias por tu llamada.</Say>
    </Response>
    '''
    print("voice: ",twiml)
    return Response(twiml, mimetype='application/xml')

@app.route('/gather', methods=['POST', 'GET'])
def gather():
    """Maneja la entrada del usuario."""
    mensaje = request.args.get('mensaje', 'Mensaje no proporcionado')
    mensaje = requests.utils.quote(mensaje)
    print(f"Digits: {request.form.get('Digits')}") #Agrega esta linea
    if 'Digits' in request.form and request.form['Digits'] == '1':
        print("El usuario presionó 1") #Agrega esta linea
        twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say voice="alice" language="es-ES">{mensaje}</Say>
            <Gather input="dtmf" numDigits="1" action="https://python-twilio-production.up.railway.app/gather?mensaje={mensaje}">
                <Say voice="alice" language="es-ES">Presiona 1 para escuchar el mensaje nuevamente.</Say>
            </Gather>
            <Say voice="alice" language="es-ES">Gracias por tu llamada.</Say>
        </Response>
        '''
    else:
        print("El usuario no presionó 1") #Agrega esta linea
        twiml = '''<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say language="es-ES" rate="85%">Gracias por tu llamada.</Say>
        </Response>
        '''
    print("gather: ",twiml)
    return Response(twiml, mimetype='application/xml')



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)



# from flask import Flask, request, Response
# import os

# app = Flask(__name__)

# @app.route('/')
# def hello_world():
#     print("AAAAAAAAAAAAAAAAA")
#     return 'Hola Mundo'



# @app.route("/inicio", methods=['POST', 'GET'])
# def inicio():
#     print("BBBBBBBBBBBBBBBBBBB")
#     mensaje = request.args.get('mensaje', 'Este es un mensaje por defecto.')
#     print(mensaje)
#     response = f"""
#     <Response>
#         <Gather numDigits="1" action="/procesar_opcion?mensaje={mensaje}", timeout="5" method="POST">
#             <Say voice="alice" language="es-ES">{mensaje}</Say>
#         </Gather>
#         <Say voice="alice" language="es-ES">Gracias, adiós.</Say>
#         <Hangup/>
#     </Response>
#     """
#     return Response(response, content_type="text/xml")


# @app.route("/procesar_opcion", methods=['POST'])
# def procesar_opcion():
#     digits = request.form.get('Digits')

#     if digits == "1":
#         # Si el usuario presiona 1, repetimos el mensaje
#         response = f"""
#         <Response>
#             <Gather numDigits="1" action="/procesar_opcion" timeout="5" method="POST">
#                 <Say voice="alice" language="es-ES">{mensaje}</Say>
#             </Gather>
#             <Say voice="alice" language="es-ES">Gracias, adiós.</Say>
#             <Hangup/>
#         </Response>
#         """
#     else:
#         # Si presiona cualquier otra cosa o no presiona nada
#         response = """
#         <Response>
#             <Say voice="alice" language="es-ES">Gracias, adiós.</Say>
#             <Hangup/>
#         </Response>
#         """

#     return Response(response, content_type="text/xml")


# if __name__ == '__main__':
#     app.run(host='0.0.0.0', port=5000)
