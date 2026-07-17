import json
import base64
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

	# This request belongs to the phone: login_as() below queues Set-Cookie
	# headers for the NEW web session on this response, and mobile HTTP stacks
	# adopt them — after that the phone and the browser share one sid, and the
	# killswitch (or a browser logout) ends the phone's session too. Snapshot
	# the phone's session and response cookies, then restore them after.
	mobile_session = frappe.local.session
	mobile_session_obj = getattr(frappe.local, "session_obj", None)
	mobile_cookies = dict(frappe.local.cookie_manager.cookies)
	mobile_cookies_to_delete = list(frappe.local.cookie_manager.to_delete)

	login_manager = LoginManager()
	# Don't let deny_multiple_sessions evict the phone's own session while
	# creating the browser session.
	login_manager.clear_active_sessions = lambda: None
	login_manager.login_as(user)

	sid = frappe.session.sid

	frappe.local.session = mobile_session
	if mobile_session_obj is not None:
		frappe.local.session_obj = mobile_session_obj
	frappe.local.cookie_manager.cookies = mobile_cookies
	frappe.local.cookie_manager.to_delete = mobile_cookies_to_delete

	# Mark this session as QR-born so the desk asks for a killswitch duration
	frappe.cache.set_value(f"qr_session:{sid}", 1, expires_in_sec=7 * 24 * 3600)

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
	return {
		"qr_session": qr_session,
		"deadline": frappe.cache.get_value(_killswitch_key(sid)),
	}


@frappe.whitelist(methods=["POST"])
def set_killswitch(minutes):
	"""Arm the killswitch: log this session out `minutes` from now."""
	import time

	minutes = int(minutes)
	if minutes not in KILLSWITCH_OPTIONS:
		frappe.throw(_("Invalid duration"), frappe.ValidationError)

	sid = frappe.session.sid
	deadline = int(time.time()) + minutes * 60
	frappe.cache.set_value(
		_killswitch_key(sid), deadline, expires_in_sec=minutes * 60 + 24 * 3600
	)
	# Choice made — don't prompt again on reload
	frappe.cache.delete_value(f"qr_session:{sid}")
	return {"deadline": deadline}


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
