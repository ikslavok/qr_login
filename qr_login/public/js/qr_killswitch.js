// QR session killswitch — non-blocking side panel on the desk.
// Flow: QR-born session → ask "izloguj me nakon 5/15/30 min" → at deadline
// show 5s countdown → show QR, 10s to rescan (continues via a fresh QR
// login) → otherwise logout. Server enforces the deadline via auth_hooks,
// this script is only the UX.
(function () {
	var deadline = null; // epoch ms
	var qrDeadline = null; // epoch ms, rescan window
	var phase = "idle"; // idle | chooser | armed | warning | qr | done
	var tickInterval = null;
	var pollInterval = null;

	$(document).ready(function () {
		if (!window.frappe || !frappe.session || frappe.session.user === "Guest") return;

		frappe.xcall("qr_login.api.killswitch_status").then(function (s) {
			if (!s || (!s.qr_session && !s.deadline)) return;
			if (s.deadline) {
				deadline = s.deadline * 1000;
				phase = "armed";
				startTicking();
			} else {
				showChooser();
			}
		});
	});

	function panel() {
		var p = document.getElementById("qr-killswitch");
		if (!p) {
			p = document.createElement("div");
			p.id = "qr-killswitch";
			p.style.cssText =
				"position:fixed;bottom:20px;right:20px;z-index:10000;" +
				"background:var(--card-bg,#fff);border:1px solid var(--border-color,#d1d8dd);" +
				"border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,.15);" +
				"padding:12px 16px;max-width:230px;text-align:center;font-size:13px;";
			document.body.appendChild(p);
		}
		return p;
	}

	function removePanel() {
		var p = document.getElementById("qr-killswitch");
		if (p) p.remove();
	}

	function showChooser() {
		phase = "chooser";
		var p = panel();
		p.innerHTML =
			'<div style="margin-bottom:8px;font-weight:600;">Izloguj me nakon:</div>' +
			'<div style="display:flex;gap:6px;justify-content:center;"></div>';
		var row = p.lastChild;

		// auto-dismiss: no choice in 15s -> panel goes away, no killswitch
		var dismissTimer = setTimeout(function () {
			if (phase === "chooser") {
				phase = "idle";
				removePanel();
			}
		}, 15000);

		[5, 15, 30].forEach(function (m) {
			var b = document.createElement("button");
			b.className = "btn btn-sm btn-default";
			b.textContent = m + " min";
			b.onclick = function () {
				clearTimeout(dismissTimer);
				frappe.xcall("qr_login.api.set_killswitch", { minutes: m }).then(function (r) {
					deadline = r.deadline * 1000;
					phase = "armed";
					removePanel();
					startTicking();
				});
			};
			row.appendChild(b);
		});
	}

	function startTicking() {
		if (tickInterval) return;
		// wall-clock based so laptop sleep can't skip the deadline
		tickInterval = setInterval(tick, 1000);
		tick();
	}

	function tick() {
		var rem = deadline - Date.now();

		if (phase === "armed" && rem <= 5000) {
			phase = "warning";
		}

		if (phase === "warning") {
			if (rem > 0) {
				panel().innerHTML =
					'<div style="font-weight:600;color:var(--red-500,#c00);">' +
					"Odjava za " + Math.ceil(rem / 1000) + "s</div>";
			} else {
				showQR();
			}
		}

		if (phase === "qr" && qrDeadline) {
			var qrem = qrDeadline - Date.now();
			var t = document.getElementById("qr-killswitch-timer");
			if (t) t.textContent = Math.max(0, Math.ceil(qrem / 1000)) + "s";
			if (qrem <= 0) logout();
		}
	}

	function showQR() {
		phase = "qr";
		var p = panel();
		p.innerHTML = '<div style="font-weight:600;margin-bottom:6px;">Skeniraj za nastavak</div>';

		frappe
			.xcall("qr_login.api.generate_token")
			.then(function (data) {
				if (phase !== "qr" || !data || !data.qr_image) return logout();
				qrDeadline = Date.now() + 10000;
				p.innerHTML =
					'<div style="font-weight:600;margin-bottom:6px;">Skeniraj za nastavak</div>' +
					'<img src="' + data.qr_image + '" style="width:160px;height:160px;" />' +
					'<div style="margin-top:6px;">Odjava za <span id="qr-killswitch-timer">10s</span></div>';
				startPolling(data.token);
			})
			.catch(logout);
	}

	function startPolling(token) {
		pollInterval = setInterval(function () {
			frappe
				.xcall("qr_login.api.check_status", { token: token })
				.then(function (data) {
					if (data && data.status === "confirmed" && data.login_token) {
						phase = "done";
						cleanup();
						// fresh session via the standard QR flow; new session asks again
						window.location.href =
							"/api/method/frappe.www.login.login_via_token?login_token=" +
							encodeURIComponent(data.login_token);
					}
				})
				.catch(function () {});
		}, 2000);
	}

	function logout() {
		if (phase === "done") return;
		phase = "done";
		cleanup();
		removePanel();
		window.location.href = "/api/method/logout";
	}

	function cleanup() {
		if (tickInterval) clearInterval(tickInterval);
		if (pollInterval) clearInterval(pollInterval);
		tickInterval = pollInterval = null;
	}
})();
