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

	login_manager = LoginManager()
	login_manager.login_as(user)

	sid = frappe.session.sid

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
