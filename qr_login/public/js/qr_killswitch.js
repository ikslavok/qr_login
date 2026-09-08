// QR session killswitch on the desk.
// Flow: QR-born session → non-blocking corner panel "log me out after
// 5/15/30 min" (optional; for some roles the server already armed a
// mandatory deadline and the panel can only shorten it) → near the deadline
// a big centered modal warns, then shows a large QR to rescan (continues via
// a fresh QR login) → otherwise logout.
// Server enforces the deadline via auth_hooks; this script is only the UX.
// Keep WARNING/RESCAN in sync with KILLSWITCH_GRACE_SECONDS on the server.
(function () {
	var WARNING_SECONDS = 10; // heads-up countdown before the deadline
	var RESCAN_SECONDS = 25; // window to scan the continue-QR after the deadline

	var deadline = null; // epoch ms
	var canTrust = false; // forced deadline + settings allow "my device"
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
			}
			canTrust = !!s.can_trust;
			if (s.qr_session) showChooser();
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

	// Big centered modal used for the warning + continue-QR (blocking on
	// purpose: the session is about to end, they need to act). Returns the
	// inner card so callers can set its content.
	function modalCard() {
		var m = document.getElementById("qr-killswitch-modal");
		if (!m) {
			m = document.createElement("div");
			m.id = "qr-killswitch-modal";
			m.style.cssText =
				"position:fixed;inset:0;z-index:100000;display:flex;" +
				"align-items:center;justify-content:center;" +
				"background:rgba(0,0,0,.55);";
			var card = document.createElement("div");
			card.id = "qr-killswitch-card";
			card.style.cssText =
				"background:var(--card-bg,#fff);color:var(--text-color,#1f272e);" +
				"border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.35);" +
				"padding:28px 32px;max-width:360px;text-align:center;";
			m.appendChild(card);
			document.body.appendChild(m);
		}
		return document.getElementById("qr-killswitch-card");
	}

	function removeModal() {
		var m = document.getElementById("qr-killswitch-modal");
		if (m) m.remove();
	}

	function showChooser() {
		phase = "chooser";
		var p = panel();
		var note = deadline
			? '<div style="color:var(--text-muted,#8d99a6);margin-bottom:6px;">' +
			  __("Session ends in {0} min", [Math.round((deadline - Date.now()) / 60000)]) +
			  "</div>"
			: "";
		p.innerHTML =
			note +
			'<div style="margin-bottom:8px;font-weight:600;">' + __("Log me out after:") + "</div>" +
			'<div style="display:flex;gap:6px;justify-content:center;"></div>';
		var row = p.lastChild;

		// auto-dismiss: no choice in 15s -> panel goes away; a mandatory deadline stays
		var dismissTimer = setTimeout(function () {
			if (phase === "chooser") {
				phase = deadline ? "armed" : "idle";
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

		if (canTrust) {
			var t = document.createElement("button");
			t.className = "btn btn-sm btn-default";
			t.style.cssText = "margin-top:8px;width:100%;";
			t.textContent = __("My device – don't log me out");
			t.onclick = function () {
				clearTimeout(dismissTimer);
				frappe.xcall("qr_login.api.trust_device").then(function () {
					cleanup();
					deadline = null;
					phase = "idle";
					removePanel();
				});
			};
			p.appendChild(t);
		}
	}

	function startTicking() {
		if (tickInterval) return;
		// wall-clock based so laptop sleep can't skip the deadline
		tickInterval = setInterval(tick, 1000);
		tick();
	}

	function tick() {
		var rem = deadline - Date.now();

		if (phase === "armed" && rem <= WARNING_SECONDS * 1000) {
			phase = "warning";
		}

		if (phase === "warning") {
			if (rem > 0) {
				showWarning(Math.ceil(rem / 1000));
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

	function showWarning(secs) {
		var card = modalCard();
		// build once, then just update the number so the countdown doesn't flicker
		if (!document.getElementById("qr-ks-warn-secs")) {
			card.innerHTML =
				'<div style="font-size:44px;margin-bottom:6px;">⏳</div>' +
				'<div style="font-size:19px;font-weight:700;margin-bottom:6px;">' + __("Session is about to end") + "</div>" +
				'<div style="color:var(--text-muted,#8d99a6);margin-bottom:16px;">' + __("Get your phone ready to continue the session") + "</div>" +
				'<div style="font-size:15px;">' + __("Continue in") + " " +
				'<span id="qr-ks-warn-secs" style="font-weight:700;font-size:22px;color:var(--primary,#2490ef);"></span></div>';
		}
		document.getElementById("qr-ks-warn-secs").textContent = secs + "s";
	}

	function showQR() {
		phase = "qr";
		var card = modalCard();
		card.innerHTML =
			'<div style="font-size:19px;font-weight:700;margin-bottom:4px;">' + __("Continue session") + "</div>" +
			'<div style="color:var(--text-muted,#8d99a6);">' + __("Loading code…") + "</div>";

		frappe
			.xcall("qr_login.api.generate_token")
			.then(function (data) {
				if (phase !== "qr" || !data || !data.qr_image) return logout();
				qrDeadline = Date.now() + RESCAN_SECONDS * 1000;
				card.innerHTML =
					'<div style="font-size:19px;font-weight:700;margin-bottom:4px;">' + __("Continue session") + "</div>" +
					'<div style="color:var(--text-muted,#8d99a6);margin-bottom:16px;">' + __("Scan the code with your phone to continue") + "</div>" +
					'<img src="' + data.qr_image + '" style="width:260px;height:260px;" />' +
					'<div style="margin-top:16px;font-size:15px;">' + __("Logout in") + " " +
					'<span id="qr-killswitch-timer" style="font-weight:700;color:var(--red-500,#e24c4c);">' +
					RESCAN_SECONDS + 's</span></div>';
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
						removeModal();
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
		removeModal();
		// Proper logout that ends on the login page. callback+error both
		// redirect, so an already-killed session still lands cleanly instead
		// of dumping raw JSON from /api/method/logout.
		frappe.call({
			method: "logout",
			callback: function () {
				window.location.href = "/login";
			},
			error: function () {
				window.location.href = "/login";
			},
		});
	}

	function cleanup() {
		if (tickInterval) clearInterval(tickInterval);
		if (pollInterval) clearInterval(pollInterval);
		tickInterval = pollInterval = null;
	}
})();
