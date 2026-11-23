"""
Cliente para enviar SMS a través de la API web (goform) de routers/MiFi ZTE,
como el ZTE MF920V con firmware Entel Perú (ufi/mf9xx, PASSWORD_ENCODE=true):
login por Base64 del password, sin LD/SHA256, sin token AD y sin cookie de
sesión (el router identifica la sesión por IP de origen).

Protocolo obtenido leyendo js/service.js y js/config/config.js servidos por
el propio router (192.168.0.1).
"""
import base64
import codecs
import time

import requests


class ZteApiError(Exception):
    pass


# sms_cmd_status_info (mismo mecanismo que usa la web del módem en js/service.js)
SMS_CMD_SEND = "4"
SMS_CMD_DELETE = "6"
SMS_STATUS_FAIL = "2"
SMS_STATUS_OK = "3"


class ZteSms:
    def __init__(
        self,
        router_ip: str = "192.168.0.1",
        password: str = "admin",
        timeout: int = 10,
        send_wait: float = 20,
    ):
        self.router_ip = router_ip
        self.password = password
        self.timeout = timeout
        self.send_wait = send_wait  # segundos máx. esperando que el módem termine un envío
        self.session = requests.Session()
        self.base_url = f"http://{router_ip}"
        self.headers = {
            "Referer": f"{self.base_url}/index.html",
            "Origin": self.base_url,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        }
        self._wa_inner_version = None
        self._cr_version = None

    # ---------- bajo nivel ----------

    def _get(self, cmd: str, extra: dict | None = None) -> dict:
        params = {"isTest": "false", "cmd": cmd, "multi_data": "1"}
        if extra:
            params.update(extra)
        r = self.session.get(
            f"{self.base_url}/goform/goform_get_cmd_process",
            params=params,
            headers=self.headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def _set(self, goform_id: str, data: dict | None = None) -> dict:
        payload = {"isTest": "false", "goformId": goform_id}
        if data:
            payload.update(data)
        r = self.session.post(
            f"{self.base_url}/goform/goform_set_cmd_process",
            data=payload,
            headers=self.headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def _load_versions(self):
        if self._wa_inner_version is None:
            info = self._get("cr_version,wa_inner_version")
            self._wa_inner_version = info.get("wa_inner_version", "")
            self._cr_version = info.get("cr_version", "")

    @staticmethod
    def _encode_ucs2(message: str) -> str:
        return codecs.encode(message.encode("utf-16-be"), "hex").decode("ascii")

    # ---------- alto nivel ----------

    def login(self) -> dict:
        encoded = base64.b64encode(self.password.encode()).decode()
        result = self._set("LOGIN", {"password": encoded})
        if result.get("result") not in ("0", "4"):
            raise ZteApiError(f"Login fallido: {result}")
        return result

    def is_logged_in(self) -> bool:
        info = self._get("loginfo")
        return info.get("loginfo") == "ok"

    def send_sms(self, phone_number: str, message: str) -> dict:
        """Envía y espera a que el módem termine.

        No consulta loginfo antes de cada envío (cada consulta a la web del
        módem tarda): si la sesión venció, SEND_SMS falla sin encolar nada, y
        entonces se inicia sesión y se reintenta una vez.
        """
        data = {
            "notCallback": "true",
            "Number": phone_number,
            "sms_time": time.strftime("%y;%m;%d;%H;%M;%S;+0"),
            "MessageBody": self._encode_ucs2(message),
            "ID": "-1",
            "encode_type": "UNICODE",
        }
        result = self._set("SEND_SMS", data)
        if result.get("result") not in ("success", "0"):
            self.login()
            result = self._set("SEND_SMS", data)
        if result.get("result") not in ("success", "0"):
            raise ZteApiError(f"Fallo al enviar SMS: {result}")
        # SEND_SMS solo encola: el módem sigue enviando unos segundos más. Mientras
        # tanto rechaza borrados, así que hay que esperar a que termine.
        status = self.wait_sms_cmd(SMS_CMD_SEND, self.send_wait)
        if status == SMS_STATUS_FAIL:
            raise ZteApiError("El modem no pudo enviar el SMS (sin saldo, sin señal o numero invalido)")
        return result

    def sms_cmd_status(self, sms_cmd: str) -> str:
        """Estado del último comando SMS del módem: '1'=en curso, '2'=falló, '3'=terminó bien."""
        r = self.session.get(
            f"{self.base_url}/goform/goform_get_cmd_process",
            params={"isTest": "false", "cmd": "sms_cmd_status_info", "sms_cmd": sms_cmd},
            headers=self.headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return str(r.json().get("sms_cmd_status_result", ""))

    def wait_sms_cmd(self, sms_cmd: str, timeout: float) -> str:
        """Espera a que el módem termine el comando. Devuelve '2', '3', o '' si no se supo a tiempo."""
        deadline = time.monotonic() + timeout
        # pausa inicial: que el módem alcance a marcar el comando nuevo como "en
        # curso" y no se lea el resultado del comando anterior
        time.sleep(0.3)
        while time.monotonic() < deadline:
            try:
                status = self.sms_cmd_status(sms_cmd)
            except Exception:
                status = ""
            if status in (SMS_STATUS_FAIL, SMS_STATUS_OK):
                return status
            time.sleep(0.2)
        return ""

    def sms_capacity(self) -> dict:
        # comando simple: sin multi_data
        r = self.session.get(
            f"{self.base_url}/goform/goform_get_cmd_process",
            params={"isTest": "false", "cmd": "sms_capacity_info"},
            headers=self.headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def list_sms_ids(self, tags: str = "10", limit: int = 500) -> list[str]:
        """IDs de SMS en el router. tags: 10=todos, 1=recibidos, 2=enviados, 3=borradores."""
        if not self.is_logged_in():
            self.login()
        # sms_data_total es un comando simple: sin multi_data, la respuesta trae "messages"
        r = self.session.get(
            f"{self.base_url}/goform/goform_get_cmd_process",
            params={
                "isTest": "false",
                "cmd": "sms_data_total",
                "page": "0",
                "data_per_page": str(limit),
                "mem_store": "1",
                "tags": tags,
                "order_by": "order by id desc",
            },
            headers=self.headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return [m["id"] for m in r.json().get("messages", [])]

    def delete_sms(self, ids: list[str]) -> dict:
        if not ids:
            return {"result": "success"}
        if not self.is_logged_in():
            self.login()
        result = self._set("DELETE_SMS", {"notCallback": "true", "msg_id": ";".join(ids) + ";"})
        if result.get("result") not in ("success", "0"):
            raise ZteApiError(f"Fallo al borrar SMS: {result}")
        return result

    def delete_all_sms(self) -> int:
        """Vacía la memoria de SMS. Devuelve cuántos había antes.

        El resultado se verifica con el contador de sms_capacity_info: la lista
        (sms_data_total) puede venir vacía cuando el módem está ocupado aunque
        tenga la memoria llena.
        """
        before = self.sms_total()
        if before == 0:
            return 0
        # ALL_DELETE_SMS falla si la bandeja está vacía, por eso se ignora el resultado
        for location in ("native_inbox", "native_outbox", "native_sentbox", "native_draftbox"):
            self._set("ALL_DELETE_SMS", {"notCallback": "true", "which_cgi": location})
        self.wait_sms_cmd(SMS_CMD_DELETE, 10)
        # lo que quede (p. ej. enviados) se borra por id
        for size in (100, 10, 10, 10):
            if self.sms_total() == 0:
                return before
            ids = self.list_sms_ids(limit=size)
            if ids:
                try:
                    self.delete_sms(ids)
                except ZteApiError:
                    pass
                self.wait_sms_cmd(SMS_CMD_DELETE, 10)
            time.sleep(1)
        remaining = self.sms_total()
        if remaining:
            raise ZteApiError(f"No se pudieron borrar {remaining} de {before} SMS")
        return before

    def sms_total(self) -> int:
        """Cantidad de SMS guardados en la memoria del router (recibidos + enviados + borradores).

        Ojo: sms_nv_total es la CAPACIDAD (100 en el MF920V), no lo ocupado.
        """
        if not self.is_logged_in():
            self.login()
        cap = self.sms_capacity()
        return sum(
            int(cap.get(k, 0) or 0)
            for k in ("sms_nv_rev_total", "sms_nv_send_total", "sms_nv_draftbox_total")
        )

    def trim_sms(self, max_sms: int) -> int:
        """Si la memoria del router llegó a max_sms, la vacía. Devuelve cuántos borró (0 si no hizo falta)."""
        if max_sms <= 0 or self.sms_total() < max_sms:
            return 0
        return self.delete_all_sms()

    def logout(self):
        try:
            self._set("LOGOUT")
        except Exception:
            pass
