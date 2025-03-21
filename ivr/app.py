from flask import Flask, request, Response
import requests

# Texto a convertir a voz
mensaje = 'Este es un mensaje de prueba. Presiona 1 para escucharlo nuevamente.'

app = Flask(__name__)

@app.route('/voice', methods=['POST'])
def voice():
    """Genera el TwiML para la llamada."""
    mensaje = request.args.get('mensaje', 'Mensaje no proporcionado') #Obtiene el parametro mensaje
    mensaje_codificado = requests.utils.quote(mensaje)
    twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Say voice="alice" language="es-ES" rate="70%">{mensaje}</Say>
        <Gather input="dtmf" numDigits="1" action="/gather?mensaje={mensaje_codificado}">
            <Say voice="alice" language="es-ES">Presiona 1 para escuchar el mensaje nuevamente.</Say>
        </Gather>
        <Say voice="alice" language="es-ES">Gracias por su atencion.</Say>
    </Response>
    '''
    return Response(twiml, mimetype='application/xml')

@app.route('/gather', methods=['POST', 'GET'])
def gather():
    """Maneja la entrada del usuario."""
    mensaje = request.args.get('mensaje', 'Mensaje no proporcionado')
    mensaje_codificado = requests.utils.quote(mensaje)
    if 'Digits' in request.form and request.form['Digits'] == '1':
        twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say voice="alice" language="es-ES" rate="70%">{mensaje}</Say>
            <Gather input="dtmf" numDigits="1" action="/gather?mensaje={mensaje_codificado}">
                <Say voice="alice" language="es-ES">Presiona 1 para escuchar el mensaje nuevamente.</Say>
            </Gather>
            <Say voice="alice" language="es-ES">Gracias por su atencion.</Say>
        </Response>
        '''
    else:
        twiml = '''<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say language="es-ES" rate="85%">Gracias por su atencion.</Say>
        </Response>
        '''
    return Response(twiml, mimetype='application/xml')



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)