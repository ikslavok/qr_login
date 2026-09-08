import time

import frappe
from frappe.tests.utils import FrappeTestCase

from qr_login import api


class TestQRLoginSettings(FrappeTestCase):
	def test_auto_logout_minutes_picks_shortest_matching_role(self):
		s = frappe.get_doc("QR Login Settings")
		s.auto_logout_roles = []
		s.append("auto_logout_roles", {"role": "System Manager", "minutes": 60})
		s.append("auto_logout_roles", {"role": "Administrator", "minutes": 15})
		s.save()
		self.assertEqual(api._auto_logout_minutes("Administrator"), 15)
		self.assertIsNone(api._auto_logout_minutes("Guest"))

	def test_trust_cookie_roundtrip(self):
		exp = str(int(time.time()) + 60)
		cookie = api._trust_cookie_value("a@b.c", exp)
		self.assertTrue(api._cookie_trusts("a@b.c", cookie))
		self.assertFalse(api._cookie_trusts("x@b.c", cookie))
		self.assertFalse(api._cookie_trusts("a@b.c", cookie[:-1] + "0"))
		expired = api._trust_cookie_value("a@b.c", str(int(time.time()) - 1))
		self.assertFalse(api._cookie_trusts("a@b.c", expired))


def demo():
	"""bench --site <site> execute qr_login.qr_login.doctype.qr_login_settings.test_qr_login_settings.demo"""
	s = frappe.get_doc("QR Login Settings")
	saved_roles = [r.as_dict() for r in s.auto_logout_roles]
	s.auto_logout_roles = []
	s.append("auto_logout_roles", {"role": "System Manager", "minutes": 60})
	s.append("auto_logout_roles", {"role": "Administrator", "minutes": 15})
	s.save()
	try:
		assert api._auto_logout_minutes("Administrator") == 15
		assert api._auto_logout_minutes("Guest") is None
		exp = str(int(time.time()) + 60)
		c = api._trust_cookie_value("a@b.c", exp)
		assert api._cookie_trusts("a@b.c", c)
		assert not api._cookie_trusts("x@b.c", c)
		assert not api._cookie_trusts("a@b.c", c[:-1] + ("0" if c[-1] != "0" else "1"))
		assert not api._cookie_trusts("a@b.c", api._trust_cookie_value("a@b.c", str(int(time.time()) - 1)))
		assert not api._cookie_trusts("a@b.c", None)
		assert not api._cookie_trusts("a@b.c", "nope")
	finally:
		s.auto_logout_roles = []
		for r in saved_roles:
			s.append("auto_logout_roles", {"role": r["role"], "minutes": r["minutes"]})
		s.save()
		frappe.db.commit()
	return "ok"
