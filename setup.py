# -*- coding: utf-8 -*-
from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

from qr_login import __version__ as version

setup(
	name="qr_login",
	version=version,
	description="QR code-based login for Frappe — scan from mobile app to log in on web",
	author="Filip Ilic",
	author_email="filip@filipilic.com",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires,
)
