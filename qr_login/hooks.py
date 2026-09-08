app_name = "qr_login"
app_title = "QR Login"
app_publisher = "Filip Ilic"
app_description = "QR login app for frappe. Just scan with your mobile phone and login."
app_icon = "octicon octicon-key"
app_color = "blue"
app_email = "filip@filipilic.com"
app_license = "MIT"

# Inject QR login JS and CSS on the login page (web pages)
web_include_js = ["/assets/qr_login/js/qr_login.js"]
web_include_css = ["/assets/qr_login/css/qr_login.css"]

# Killswitch: duration prompt + auto-logout UI on the desk.
# Content hash in the URL busts nginx's 1-year asset cache without a bench build.
import hashlib as _hashlib
import pathlib as _pathlib

_killswitch_js = _pathlib.Path(__file__).parent / "public" / "js" / "qr_killswitch.js"
app_include_js = [
	f"/assets/qr_login/js/qr_killswitch.js?v={_hashlib.md5(_killswitch_js.read_bytes()).hexdigest()[:8]}"
]

# Enforce killswitch server-side on every request
auth_hooks = ["qr_login.api.enforce_killswitch"]
