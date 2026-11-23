"""Prueba un envío real y muestra cómo responde el módem (tiempo, estado y borrado).

Uso (sin otros envíos en curso: pausar el despachador antes):
    py diagnostico_envio.py <numero_destino> [corto|largo|largo-unicode] [ip] [password]

    corto          SMS de 1 parte
    largo          mensaje de cita real (~500 caracteres) en modo GSM, sin tildes (4 partes)
    largo-unicode  el mismo mensaje con tildes, en Unicode (8 partes), para comparar
"""
import sys
import time

from zte_sms import SMS_CMD_DELETE, ZteSms

LARGO = (
    "¡Hola! Queremos verte bien. DIRIS Lima Este te recuerda tu cita en MEDICINA FISICA Y "
    "REHABILITACION con el/la especialista ROGER DANTE JOSE ACOSTA RIOS.\r\n\r\n"
    "Fecha: 24/11/2026\r\nHora: 12:20 pm\r\nLugar: CENTRO REFERENCIAL ESPECIALIZADO EN REHABILITACION\r\n\r\n"
    "Ya está activo el chatbot WhatsApp Dr. Huellita. Consulta información sobre tu cita aquí:\r\n"
    "https://sigesav2.dirislimaeste.gob.pe/qr-drhuellita/chat\r\n\r\n"
    "También puedes consultar el portal web de Dr. Huellita:\r\nhttps://dr-huellita.dirislimaeste.gob.pe/"
)

numero = sys.argv[1]
modo = sys.argv[2] if len(sys.argv) > 2 else "corto"
ip = sys.argv[3] if len(sys.argv) > 3 else "192.168.0.1"
password = sys.argv[4] if len(sys.argv) > 4 else "admin"

texto = "Prueba diagnostico gateway" if modo == "corto" else LARGO
z = ZteSms(ip, password, gsm7=(modo != "largo-unicode"))
z.login()
print(f"Modo: {modo} ({len(texto)} caracteres)")
print("1) SMS en memoria antes:", z.sms_total())

t0 = time.monotonic()
try:
    z.send_sms(numero, texto)
    print(f"2) Enviado OK en {time.monotonic() - t0:.1f}s")
except Exception as e:
    print(f"2) Fallo tras {time.monotonic() - t0:.1f}s: {e}")

print("3) SMS en memoria despues de enviar:", z.sms_total())
try:
    print("4) Borrados:", z.delete_all_sms())
except Exception as e:
    print("4) Error al borrar:", e)
print("   Estado del borrado:", repr(z.sms_cmd_status(SMS_CMD_DELETE)))
print("5) SMS en memoria al final:", z.sms_total())
z.logout()
