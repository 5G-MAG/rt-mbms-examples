"""Putting a document on the air through the rt-mbms BM-SC: provisioning the session and pushing files. [design]

The path of option 1 (deck slides 11 and 17): the assistance document is a file, delivered with FLUTE in a reserved MBMS session.
The session is an xMB Files session with Push ingest (TS 26.348 V19.0.0 clause 5.4.6: "In the Push mode, the file list is not used
since the BM-SC will monitor its push folder and send the files it receives on a first-come first-served basis."), created through
the application-provider portal; files then go in with an HTTP PUT to the BM-SC's xMB-U push URL.
"""
from __future__ import annotations

import base64
import http.client
import json
import ssl
import urllib.parse
from dataclasses import dataclass


class XmbError(RuntimeError):
    pass


def _connect(url: str, timeout: float, cert: str | None, key: str | None, insecure: bool, cafile: str | None):
    u = urllib.parse.urlsplit(url)
    if u.scheme == "https":
        ctx = ssl.create_default_context(cafile=cafile)
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        if cert:
            ctx.load_cert_chain(cert, key)
        return http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout, context=ctx), u
    return http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout), u


class XmbUClient:
    """xMB-U push: PUT /xmb-u/push/{session}/{path}, authenticated with a TLS client certificate."""

    def __init__(self, base_url: str, cert: str | None = None, key: str | None = None, insecure: bool = False,
                 cafile: str | None = None, timeout: float = 20.0):
        self._base, self._cert, self._key = base_url.rstrip("/"), cert, key
        self._insecure, self._cafile, self._timeout = insecure, cafile, timeout

    def push(self, session_id: str, path: str, body: bytes, content_type: str = "application/json") -> int:
        conn, u = _connect(self._base, self._timeout, self._cert, self._key, self._insecure, self._cafile)
        try:
            conn.request("PUT", f"{u.path}/xmb-u/push/{session_id}/{path.lstrip('/')}", body, {"Content-Type": content_type})
            resp = conn.getresponse()
            resp.read()
            if resp.status != 204:
                raise XmbError(f"push of {path} to session {session_id} answered HTTP {resp.status}")
            return resp.status
        finally:
            conn.close()


@dataclass
class SessionSpec:
    service_name: str
    tmgi_service_id: int
    tsi: int
    mcast_addr: str
    port: int
    c_teid: int
    tmgi_mcc: str = "901"
    tmgi_mnc: str = "56"
    service_area_code: int = 1
    mme_sm_peers: str = "127.0.0.1:2123"
    max_bitrate_bps: int = 1_000_000
    fec_enabled: bool = True
    service_class: str = "urn:example:positioning-assistance"
    display_base_url: str = ""  # the base URL the UE sees the files under (TS 26.348 V19.0.0 Table 5.4-5); announced as basePattern


class PortalClient:
    """The portal's xMB REST routes, as the demo scripts use them."""

    def __init__(self, base_url: str, user: str, password: str, timeout: float = 60.0):
        self._base, self._timeout = base_url.rstrip("/"), timeout
        self._auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self._location = ""

    def _id(self, body: dict, what: str) -> str:
        """The id of a created resource, found the way the demo scripts find it: a field of the answer, else the Location header."""
        for k in ("id", "serviceId", "sessionId"):
            if isinstance(body.get(k), str) and body[k]:
                return body[k]
        service = body.get("service")
        if isinstance(service, dict) and service.get("id"):
            return service["id"]
        if self._location:
            return self._location.rstrip("/").rsplit("/", 1)[-1]
        raise XmbError(f"{what}: the portal's answer carries no id: {str(body)[:200]}")

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        conn, u = _connect(self._base, self._timeout, None, None, False, None)
        try:
            data = None if body is None else json.dumps(body).encode()
            conn.request(method, u.path + path, data, {"Authorization": self._auth, "Content-Type": "application/json"})
            resp = conn.getresponse()
            raw = resp.read()
            self._location = resp.getheader("Location") or ""
            if resp.status >= 300:
                raise XmbError(f"{method} {path} answered HTTP {resp.status}: {raw[:200]!r}")
            return json.loads(raw) if raw else {}
        finally:
            conn.close()

    def create_push_session(self, spec: SessionSpec) -> tuple[str, str]:
        svc = self._call("POST", "/api/xmb/services", {
            "serviceClass": spec.service_class, "serviceNames": [spec.service_name], "serviceLanguages": ["eng"],
            "receiveOnlyMode": True, "serviceAnnouncementMode": "SACH"})
        service_id = self._id(svc, "service creation")
        sess = self._call("POST", f"/api/xmb/services/{service_id}/sessions", {
            "sessionType": "Files", "maxBitrate": spec.max_bitrate_bps, "fecEnabled": spec.fec_enabled,
            "mbmsBearer": {"tmgiMcc": spec.tmgi_mcc, "tmgiMnc": spec.tmgi_mnc, "tmgiServiceId": spec.tmgi_service_id,
                           "mcastAddr": spec.mcast_addr, "port": spec.port, "tsi": spec.tsi,
                           "serviceAreaCode": spec.service_area_code, "cTeid": spec.c_teid, "mmeSmPeers": spec.mme_sm_peers},
            "files": {"ingestMode": "Push", "carouselMode": "none",
                      **({"displayBaseURL": spec.display_base_url} if spec.display_base_url else {})}})
        return service_id, self._id(sess, "session creation")

    def set_state(self, service_id: str, session_id: str, state: str) -> None:
        """Idle or Active. The session is read back first and sent with the new state, as the portal's own button does; fields the
        portal rejects on input (the read-only push URL and the echoed file list) are dropped."""
        cur = self._call("GET", f"/api/xmb/services/{service_id}/sessions/{session_id}")
        cur.pop("ok", None)
        cur["state"] = state
        files = cur.get("files")
        if isinstance(files, dict):
            files.pop("fileList", None)
            files.pop("pushURL", None)
        self._call("PUT", f"/api/xmb/services/{service_id}/sessions/{session_id}", cur)

    def delete_session(self, service_id: str, session_id: str) -> None:
        self._call("DELETE", f"/api/xmb/services/{service_id}/sessions/{session_id}")
        self._call("DELETE", f"/api/xmb/services/{service_id}")
