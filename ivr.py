from flask import Flask, request, Response
import os
app = Flask(__name__)

mensaje = "Hola, este es un mensaje automatizado. Si desea escuchar el mensaje nuevamente, presione 1."


@app.route("/", methods=['GET'])
def main():
    print("ACAAAAA")
    return "Hola mundo", 200

@app.route("/inicio", methods=['POST'])
def inicio():
    # Primer mensaje (cuando la llamada se conecta)
    response = f"""
    <Response>
        <Gather numDigits="1" action="/procesar_opcion" timeout="5" method="POST">
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Usa el puerto de Railway o 5000 por defecto
    app.run(host="0.0.0.0", port=port)
