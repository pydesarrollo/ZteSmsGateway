"""Prueba un envío real y muestra cómo responde el módem (estado del envío y borrado).

Uso (con la API detenida y sin otros envíos en curso):
    py diagnostico_envio.py <numero_destino> [ip] [password]
"""
import sys
import time

from zte_sms import SMS_CMD_DELETE, SMS_CMD_SEND, ZteSms

numero = sys.argv[1]
ip = sys.argv[2] if len(sys.argv) > 2 else "192.168.0.1"
password = sys.argv[3] if len(sys.argv) > 3 else "admin"

z = ZteSms(ip, password)
z.login()
print("1) SMS en memoria antes:", z.sms_total())

z._set("SEND_SMS", {
    "notCallback": "true",
    "Number": numero,
    "sms_time": time.strftime("%y;%m;%d;%H;%M;%S;+0"),
    "MessageBody": z._encode_ucs2("Prueba diagnostico gateway"),
    "ID": "-1",
    "encode_type": "UNICODE",
})
print("2) Estado del envio (1=en curso, 2=fallo, 3=ok):")
t0 = time.monotonic()
while time.monotonic() - t0 < 20:
    st = z.sms_cmd_status(SMS_CMD_SEND)
    print(f"   {time.monotonic() - t0:5.1f}s -> {st!r}")
    if st in ("2", "3"):
        break
    time.sleep(0.5)

print("3) SMS en memoria despues de enviar:", z.sms_total())
try:
    print("4) Borrados:", z.delete_all_sms())
except Exception as e:
    print("4) Error al borrar:", e)
print("   Estado del borrado:", repr(z.sms_cmd_status(SMS_CMD_DELETE)))
print("5) SMS en memoria al final:", z.sms_total())
z.logout()
