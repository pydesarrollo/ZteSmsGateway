"""Vacía la memoria de SMS del router. Uso: python vaciar_router.py [ip] [password]"""
import sys

from zte_sms import ZteSms

ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.1"
password = sys.argv[2] if len(sys.argv) > 2 else "admin"

z = ZteSms(ip, password)
z.login()
print("Borrados:", z.delete_all_sms())
print("Quedan:", len(z.list_sms_ids(limit=500)))
z.logout()
