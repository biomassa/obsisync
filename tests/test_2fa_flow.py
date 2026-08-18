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

api4 = with_api(FakeApi(delivery="sms", notice="A code was sent to +1 ... 1234"))
notice = auth._request_code_delivery(api4)
check("an SMS notice is passed through", "1234" in (notice or ""), str(notice))
api5 = with_api(FakeApi(delivery="trusted_device", notice=None))
check("a trusted-device route is described even without a notice",
      "trusted device" in (auth._request_code_delivery(api5) or "").lower(),
      str(auth._request_code_delivery(api5)))

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

print("\n== the session is trusted afterwards ==")
api7 = with_api(FakeApi())
auth.authenticate("a@b.c", "pw", twofa_callback=lambda _a: "123456")
check("trust_session is called so the code is not asked for again",
      "trust" in api7.calls, str(api7.calls))

shutil.rmtree(S, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
