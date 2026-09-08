import hmac
import json
import base64
import time
from io import BytesIO

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=60)
def generate_token():
	"""Generate a QR login token and return its QR code as base64 PNG."""
	import qrcode

	token = frappe.generate_hash(length=32)

	frappe.cache.set_value(
		f"qr_login:{token}",
		json.dumps({"status": "pending"}),
		expires_in_sec=120,
	)

	# QR code content: JSON with token and site URL
	qr_data = json.dumps({"token": token, "url": frappe.utils.get_url()})

	qr = qrcode.QRCode(version=1, box_size=8, border=2)
	qr.add_data(qr_data)
	qr.make(fit=True)

	img = qr.make_image(fill_color="black", back_color="white")
	buffer = BytesIO()
	img.save(buffer, format="PNG")
	qr_base64 = base64.b64encode(buffer.getvalue()).decode()

	return {
		"token": token,
		"qr_image": f"data:image/png;base64,{qr_base64}",
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def check_status(token):
	"""Check the status of a QR login token. Returns pending/confirmed/expired."""
	if not token or not isinstance(token, str):
		frappe.throw(_("Invalid token"), frappe.ValidationError)

	data = frappe.cache.get_value(f"qr_login:{token}")
	if not data:
		return {"status": "expired"}

	data = json.loads(data)

	if data["status"] == "confirmed":
		# One-time use: delete after the browser reads it
		frappe.cache.delete_value(f"qr_login:{token}")
		return {
			"status": "confirmed",
			"login_token": data["login_token"],
		}

	return {"status": "pending"}


@frappe.whitelist(methods=["POST"])
def confirm_login(token):
	"""Confirm QR login from the mobile app. Creates a web session for the authenticated user."""
	if not token or not isinstance(token, str):
		frappe.throw(_("Invalid token"), frappe.ValidationError)

	data = frappe.cache.get_value(f"qr_login:{token}")
	if not data:
		frappe.throw(_("QR code has expired. Please scan a new one."), frappe.AuthenticationError)

	data = json.loads(data)

	if data["status"] != "pending":
		frappe.throw(_("This QR code has already been used."), frappe.ValidationError)

	user = frappe.session.user

	# Create a new session for the web browser
	from frappe.auth import LoginManager

	login_manager = LoginManager()
	login_manager.login_as(user)

	sid = frappe.session.sid

	# Mark this session as QR-born so the desk asks for a killswitch duration
	frappe.cache.set_value(f"qr_session:{sid}", 1, expires_in_sec=7 * 24 * 3600)
	# Roles configured in QR Login Settings get a mandatory deadline;
	# the chooser on the desk can only shorten it (or a trusted device lifts it)
	minutes = _auto_logout_minutes(user)
	if minutes:
		_arm_killswitch(sid, minutes)

	# Create a one-time login token (Frappe's native pattern)
	login_token = frappe.generate_hash(length=32)
	frappe.cache.set_value(f"login_token:{login_token}", sid, expires_in_sec=120)

	# Update QR token status
	frappe.cache.set_value(
		f"qr_login:{token}",
		json.dumps({
			"status": "confirmed",
			"login_token": login_token,
			"user": user,
		}),
		expires_in_sec=120,
	)

	frappe.db.commit()

	return {"status": "confirmed", "user": user}


# --- Session killswitch (auto-logout timer for QR-born sessions) ---

KILLSWITCH_OPTIONS = (5, 15, 30)  # minutes
# after the deadline the client shows a 25s QR-rescan window; keep grace a bit
# above it so the desk's own requests don't 401 mid-window (must stay in sync
# with RESCAN_SECONDS in qr_killswitch.js)
KILLSWITCH_GRACE_SECONDS = 35


def _killswitch_key(sid):
	return f"qr_killswitch:{sid}"


def _auto_logout_minutes(user):
	"""Shortest forced deadline configured for any of the user's roles, or None."""
	by_role = {
		r.role: r.minutes for r in frappe.get_cached_doc("QR Login Settings").auto_logout_roles
	}
	matching = [by_role[r] for r in frappe.get_roles(user) if r in by_role]
	return min(matching) if matching else None


def _arm_killswitch(sid, minutes):
	deadline = int(time.time()) + minutes * 60
	frappe.cache.set_value(
		_killswitch_key(sid), deadline, expires_in_sec=minutes * 60 + 24 * 3600
	)
	return deadline


@frappe.whitelist(methods=["POST"])
def killswitch_status():
	"""State for the current session: is it QR-born, and is a killswitch armed?

	The QR-born marker is consumed on first read, so the chooser is offered
	exactly once per session — a refresh won't re-prompt.
	"""
	sid = frappe.session.sid
	qr_session = bool(frappe.cache.get_value(f"qr_session:{sid}"))
	if qr_session:
		frappe.cache.delete_value(f"qr_session:{sid}")
	deadline = frappe.cache.get_value(_killswitch_key(sid))
	# a deadline present on the first read is the forced one from confirm_login
	forced = bool(qr_session and deadline)
	trust_allowed = bool(frappe.get_cached_doc("QR Login Settings").allow_trusted_devices)
	if forced and trust_allowed and _device_trusted(frappe.session.user):
		frappe.cache.delete_value(_killswitch_key(sid))
		deadline = None
	return {
		"qr_session": qr_session,
		"deadline": deadline,
		"can_trust": forced and trust_allowed and deadline is not None,
	}


@frappe.whitelist(methods=["POST"])
def set_killswitch(minutes):
	"""Arm the killswitch: log this session out `minutes` from now.

	Only shortens: a forced deadline can't be pushed out past its configured value.
	"""
	minutes = int(minutes)
	if minutes not in KILLSWITCH_OPTIONS:
		frappe.throw(_("Invalid duration"), frappe.ValidationError)

	sid = frappe.session.sid
	deadline = _arm_killswitch(sid, minutes)
	# Choice made — don't prompt again on reload
	frappe.cache.delete_value(f"qr_session:{sid}")
	return {"deadline": deadline}


# --- Trusted devices (opt out of the forced deadline on the user's own computer) ---

TRUST_COOKIE = "qr_trusted_device"


def _trust_secret():
	"""Per-site HMAC secret, generated once and kept in the __Auth store."""
	from frappe.utils.password import get_decrypted_password, set_encrypted_password

	args = ("QR Login Settings", "QR Login Settings")
	secret = get_decrypted_password(*args, fieldname="trust_secret", raise_exception=False)
	if not secret:
		secret = frappe.generate_hash(length=64)
		set_encrypted_password(*args, secret, fieldname="trust_secret")
	return secret


def _trust_cookie_value(user, exp):
	sig = hmac.new(_trust_secret().encode(), f"{user}|{exp}".encode(), "sha256").hexdigest()
	return f"{user}|{exp}|{sig}"


def _cookie_trusts(user, cookie):
	try:
		c_user, exp, sig = cookie.rsplit("|", 2)
	except (AttributeError, ValueError):
		return False
	if c_user != user or int(exp) < time.time():
		return False
	return hmac.compare_digest(cookie, _trust_cookie_value(user, exp))


def _device_trusted(user):
	request = getattr(frappe.local, "request", None)
	return bool(request) and _cookie_trusts(user, request.cookies.get(TRUST_COOKIE))


@frappe.whitelist(methods=["POST"])
def trust_device():
	"""Mark this browser as the user's own: future QR logins here skip the forced deadline."""
	settings = frappe.get_cached_doc("QR Login Settings")
	if not settings.allow_trusted_devices:
		frappe.throw(_("Trusted devices are disabled"), frappe.PermissionError)

	user = frappe.session.user
	days = int(settings.trusted_device_days or 90)
	exp = str(int(time.time()) + days * 86400)
	frappe.local.cookie_manager.set_cookie(
		TRUST_COOKIE, _trust_cookie_value(user, exp), max_age=days * 86400, httponly=True
	)
	sid = frappe.session.sid
	frappe.cache.delete_value(_killswitch_key(sid))
	frappe.cache.delete_value(f"qr_session:{sid}")
	return {"trusted_until": int(exp)}


def enforce_killswitch():
	"""auth_hooks: reject any request on a session whose killswitch has expired.

	Runs on every authenticated request, costs one cache read for sessions
	without a killswitch. The grace period keeps the session alive just long
	enough for the client's 5s warning + 10s QR-rescan window.
	"""
	import time

	sid = getattr(frappe.session, "sid", None)
	if not sid or frappe.session.user in ("", "Guest"):
		return

	deadline = frappe.cache.get_value(_killswitch_key(sid))
	if not deadline:
		return

	if time.time() > int(deadline) + KILLSWITCH_GRACE_SECONDS:
		frappe.cache.delete_value(_killswitch_key(sid))
		frappe.local.login_manager.logout()
		frappe.db.commit()
		raise frappe.AuthenticationError("Session ended by killswitch")
