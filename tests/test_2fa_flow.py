"""The two-factor sequence.

A code must be *requested* before it is validated. For a modern HSA2 account
request_2fa_code() performs Apple's trusted-device bridge handshake and records
the state validate_2fa_code() needs; without it validation uses the legacy
verifier and Apple rejects every code, however many times the prompt is approved
on the phone. That was a real failure, not a theoretical one.
"""
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")

S = tempfile.mkdtemp(prefix="obsisync-2fa-")
import config, state_db
config.CONFIG_DIR = S; config.CONFIG_FILE = os.path.join(S, "c.json")
state_db.DB_PATH = os.path.join(S, "d.db"); state_db.init()
import auth


class FakeApi:
    """Records the order of the calls obsisync makes."""
    def __init__(self, delivery="trusted_device", request_result=True, notice=None):
        self.calls = []
        self.requires_2fa = True
        self.is_trusted_session = False
        self.two_factor_delivery_method = delivery
        self.two_factor_delivery_notice = notice
        self._request_result = request_result
        self.validated_with = None

    def request_2fa_code(self):
        self.calls.append("request")
        return self._request_result

    def validate_2fa_code(self, code):
        self.calls.append("validate")
        self.validated_with = code
        # Apple only accepts a code when the handshake ran first.
        if "request" not in self.calls[: self.calls.index("validate")]:
            return False
        self.requires_2fa = False
        return code == "123456"

    def trust_session(self):
        self.calls.append("trust")
        self.is_trusted_session = True
        return True


def with_api(api):
    auth.PyiCloudService = lambda *a, **k: api
    return api


print("\n== the code is requested before it is validated ==")
api = with_api(FakeApi())
auth.authenticate("a@b.c", "pw", twofa_callback=lambda _a: "123456")
check("request_2fa_code is called", "request" in api.calls, str(api.calls))
check("it is called before validate_2fa_code",
      api.calls.index("request") < api.calls.index("validate"), str(api.calls))
check("the entered code reaches validation", api.validated_with == "123456")

print("\n== without the request, Apple rejects the code ==")
# Reproduces the reported failure: approving on the phone, code still 'wrong'.
class NoRequestApi(FakeApi):
    def request_2fa_code(self):
        return True          # pretend obsisync skipped the handshake
    def validate_2fa_code(self, code):
        self.calls.append("validate")
        return False
api2 = with_api(NoRequestApi())
try:
    auth.authenticate("a@b.c", "pw", twofa_callback=lambda _a: "123456")
    rejected = False
except RuntimeError as exc:
    rejected = "Invalid 2FA code" in str(exc)
check("a rejected code raises rather than looping silently", rejected)

print("\n== the delivery route is reported to the user ==")
api3 = with_api(FakeApi(delivery="trusted_device"))
seen = {}
auth.authenticate("a@b.c", "pw",
                  twofa_callback=lambda a: (seen.update(
                      notice=a.two_factor_delivery_notice,
                      method=a.two_factor_delivery_method), "123456")[1])
check("the delivery method is available to the prompt",
      seen.get("method") == "trusted_device", str(seen))

# For a known route obsisync writes its own message rather than relaying
# Apple's, because it has to say which of the two prompts to use.
api4 = with_api(FakeApi(delivery="sms", notice="A code was sent to +1 ... 1234"))
notice = auth._request_code_delivery(api4)
check("an SMS route produces SMS guidance", "text message" in (notice or ""), str(notice))

class UnknownRouteApi(FakeApi):
    pass
api4b = with_api(UnknownRouteApi(delivery="unknown", notice="Apple said something"))
check("an unrecognised route falls back to Apple's own wording",
      auth._request_code_delivery(api4b) == "Apple said something",
      str(auth._request_code_delivery(api4b)))
api5 = with_api(FakeApi(delivery="trusted_device", notice=None))
check("a trusted-device route is described even without a notice",
      "trusted device" in (auth._request_code_delivery(api5) or "").lower(),
      str(auth._request_code_delivery(api5)))

print("\n== the prompt says which code to type ==")
# Apple often shows a device prompt AND sends an SMS. Only one is accepted, so
# an ambiguous prompt means the user picks wrong and every attempt fails.
class SmsApi:
    two_factor_delivery_method = "sms"
    two_factor_delivery_notice = None
    _auth_data = {"phoneNumber": {"obfuscatedNumber": "•••-•••-••78"}}
notice = auth.delivery_notice(SmsApi())
check("an SMS route names the number", "••78" in notice, notice)
check("it says to use the text message", "text message" in notice, notice)
check("it says to ignore a device prompt", "ignore" in notice.lower(), notice)

class DeviceApi:
    two_factor_delivery_method = "trusted_device"
    two_factor_delivery_notice = None
    _auth_data = {}
notice = auth.delivery_notice(DeviceApi())
check("a device route says to use the device prompt",
      "device prompt" in notice, notice)
check("it does not mention SMS", "sms" not in notice.lower(), notice)

class NoDataApi:
    two_factor_delivery_method = "sms"
    two_factor_delivery_notice = None
    _auth_data = None
check("a missing payload does not crash the notice",
      "SMS" in (auth.delivery_notice(NoDataApi()) or ""))

print("\n== failures explain themselves ==")
api6 = with_api(FakeApi(request_result=False))
try:
    auth.authenticate("a@b.c", "pw", twofa_callback=lambda _a: "123456")
    msg = ""
except auth.TwoFactorRequired as exc:
    msg = str(exc)
check("a security-key-only challenge says so", "security key" in msg.lower(), msg)

from icloudlite.exceptions import (
    PyiCloudNoTrustedNumberAvailable, PyiCloudTrustedDevicePromptException)

class NoNumberApi(FakeApi):
    def request_2fa_code(self):
        raise PyiCloudNoTrustedNumberAvailable("none")
try:
    auth._request_code_delivery(with_api(NoNumberApi())); msg = ""
except auth.TwoFactorRequired as exc:
    msg = str(exc)
check("a missing trusted number is explained", "trusted phone number" in msg, msg)

class PromptFailApi(FakeApi):
    def request_2fa_code(self):
        raise PyiCloudTrustedDevicePromptException("prompt refused")
try:
    auth._request_code_delivery(with_api(PromptFailApi())); msg = ""
except auth.TwoFactorRequired as exc:
    msg = str(exc)
check("a refused device prompt is explained", "trusted devices" in msg, msg)

print("\n== the wizard signs in once and keeps that session ==")
# The reported failure: submitting the code started a second sign-in, which made
# Apple send another code and checked the old one against the new session. It
# could never succeed, and it ran until Apple answered "tooManyCodesSent".
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
import gui.wizard as wiz

app = QApplication.instance() or QApplication([])

sessions = []
class SessionApi(FakeApi):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        sessions.append(self)
        self.code_sends = 0
    def request_2fa_code(self):
        self.code_sends += 1
        return super().request_2fa_code()

wiz.authenticate = lambda email, password, interactive=False, twofa_callback=None: (
    _drive(SessionApi(), twofa_callback))

def _drive(api, cb):
    """Mimic auth.authenticate: request a code, then block on the callback."""
    api.request_2fa_code()
    code = cb(api) if cb else None
    if not api.validate_2fa_code(code or ""):
        raise RuntimeError("Invalid 2FA code")
    return api

sessions.clear()
d = wiz.SetupDialog()
d.email.setText("a@b.c"); d.password.setText("pw")
d._advance()                                   # start the sign-in

# Wait for the worker to reach the 2FA prompt.
for _ in range(200):
    app.processEvents()
    if d.stack.currentIndex() == wiz.PAGE_TWOFA:
        break
    QApplication.processEvents()
    import time as _t; _t.sleep(0.01)
check("the wizard reaches the code page", d.stack.currentIndex() == wiz.PAGE_TWOFA,
      f"page {d.stack.currentIndex()}")
check("exactly one session was created so far", len(sessions) == 1, str(len(sessions)))
check("Apple was asked for exactly one code",
      sessions and sessions[0].code_sends == 1,
      str(sessions[0].code_sends if sessions else None))

d.code.setText("123456")
d._advance()                                   # submit the code
for _ in range(200):
    app.processEvents()
    if sessions[0].validated_with:
        break
    import time as _t; _t.sleep(0.01)

check("no second sign-in happened", len(sessions) == 1, f"{len(sessions)} sessions")
check("no second code was sent", sessions[0].code_sends == 1, str(sessions[0].code_sends))
check("the code was checked against the original session",
      sessions[0].validated_with == "123456", str(sessions[0].validated_with))

print("\n== apple's raw payload is not shown as an error ==")
blob = ('{ "trustedPhoneNumbers": [{"obfuscatedNumber": "***78"}], '
        '"securityCode": {"tooManyCodesSent": true}, "mode": "sms" }' + " x" * 120)
msg = wiz._readable(RuntimeError(blob))
check("a rate limit is explained in words", "rate-limiting" in msg, msg[:80])
check("the payload is not repeated on screen", "obfuscatedNumber" not in msg)
check("a short message is left alone", wiz._readable(RuntimeError("Invalid 2FA code"))
      == "Invalid 2FA code")

print("\n== the session is trusted afterwards ==")
api7 = with_api(FakeApi())
auth.authenticate("a@b.c", "pw", twofa_callback=lambda _a: "123456")
check("trust_session is called so the code is not asked for again",
      "trust" in api7.calls, str(api7.calls))

shutil.rmtree(S, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
