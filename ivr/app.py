from flask import Flask, request, Response
import os

app = Flask(__name__)

@app.route('/')
def hello_world():
    print("AAAAAAAAAAAAAAAAA")
    return 'Hola Mundo'



@app.route("/inicio", methods=['POST'])
def inicio():
    print("BBBBBBBBBBBBBBBBBBB")
    mensaje = request.args.get('mensaje', 'Este es un mensaje por defecto.')
    print(mensaje)
    response = f"""
    <Response>
        <Gather numDigits="1" action="/procesar_opcion?mensaje={mensaje}", timeout="5" method="POST">
            <Say voice="alice" language="es-ES">{mensaje}</Say>
        </Gather>
        <Say voice="alice" language="es-ES">Gracias, adiós.</Say>
        <Hangup/>
    </Response>
    """
    return Response(response, content_type="text/xml")


@app.route("/procesar_opcion", methods=['POST'])
def procesar_opcion():
    digits = request.form.get('Digits')

    if digits == "1":
        # Si el usuario presiona 1, repetimos el mensaje
        response = f"""
        <Response>
            <Gather numDigits="1" action="/procesar_opcion" timeout="5" method="POST">
                <Say voice="alice" language="es-ES">{mensaje}</Say>
            </Gather>
            <Say voice="alice" language="es-ES">Gracias, adiós.</Say>
            <Hangup/>
        </Response>
        """
    else:
        # Si presiona cualquier otra cosa o no presiona nada
        response = """
        <Response>
            <Say voice="alice" language="es-ES">Gracias, adiós.</Say>
            <Hangup/>
        </Response>
        """

    return Response(response, content_type="text/xml")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
