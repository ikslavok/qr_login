// QR Login — inject QR code into the standard Frappe login page
(function () {
	var pollInterval = null;
	var countdownInterval = null;
	var currentToken = null;
	// Bumped on every cleanup; async callbacks capture it and bail if a newer
	// generation started, so a stale in-flight poll can't spawn a second loop.
	var generation = 0;

	frappe.ready(function () {
		$(document).on("login_rendered", function () {
			initQRLogin();
		});
	});

	function initQRLogin() {
		// Only run on login page, only for guests
		if (window.location.pathname !== "/login") return;
		if ($(".qr-login-section").length) return; // already injected

		var html =
			'<div class="qr-login-divider"><span>' + __("or") + '</span></div>' +
			'<div class="qr-login-section">' +
			'  <div id="qr-login-loading" class="qr-login-loading">' +
			"    <p>" + __("Loading QR code...") + "</p>" +
			"  </div>" +
			'  <img id="qr-login-img" style="display:none;" />' +
			'  <p class="qr-login-label">' + __("Scan with mobile app to log in") + '</p>' +
			'  <p class="qr-login-timer" id="qr-login-timer" style="display:none;">' +
			'    ' + __("Expires in") + ' <span id="qr-countdown"></span>' +
			"  </p>" +
			'  <button class="btn btn-xs btn-default qr-login-refresh" id="qr-login-refresh" style="display:none;">' +
			"    " + __("Refresh QR Code") +
			"  </button>" +
			"</div>";

		$(".for-login .login-content.page-card").append(html);

		$("#qr-login-refresh").on("click", function () {
			generateQR();
		});

		// Don't poll or regenerate while the tab is hidden — a login page left
		// in the background would otherwise keep hitting the server until it
		// trips the rate limit. Show a fresh QR when the user comes back.
		document.addEventListener("visibilitychange", function () {
			if (!$(".qr-login-section").length) return;
			if (document.hidden) {
				cleanup();
			} else {
				generateQR();
			}
		});

		generateQR();
	}

	function generateQR() {
		cleanup();
		if (document.hidden) return; // visibilitychange will start it when shown
		var gen = generation;

		$("#qr-login-loading").show().html("<p>" + __("Loading QR code...") + "</p>");
		$("#qr-login-img").hide();
		$("#qr-login-timer").hide();
		$("#qr-login-refresh").hide();

		frappe.xcall("qr_login.api.generate_token").then(function (data) {
			if (gen !== generation) return; // superseded meanwhile
			if (!data || !data.qr_image) {
				showError();
				return;
			}

			currentToken = data.token;

			$("#qr-login-img").attr("src", data.qr_image).show();
			$("#qr-login-loading").hide();
			$("#qr-login-timer").show();

			startCountdown(120);
			startPolling(data.token);
		}).catch(function () {
			if (gen !== generation) return;
			showError();
		});
	}

	function startPolling(token) {
		var gen = generation;
		pollInterval = setInterval(function () {
			frappe.xcall("qr_login.api.check_status", { token: token }).then(function (data) {
				if (gen !== generation) return; // stale response after cleanup
				if (!data) return;

				if (data.status === "confirmed" && data.login_token) {
					cleanup();
					// Redirect using Frappe's built-in login_via_token
					window.location.href =
						"/api/method/frappe.www.login.login_via_token?login_token=" +
						encodeURIComponent(data.login_token);
				} else if (data.status === "expired") {
					showExpired();
				}
			}).catch(function () {
				// Silently ignore poll errors
			});
		}, 2500);
	}

	function startCountdown(seconds) {
		var remaining = seconds;
		updateCountdownDisplay(remaining);

		countdownInterval = setInterval(function () {
			remaining--;
			if (remaining <= 0) {
				showExpired();
				return;
			}
			updateCountdownDisplay(remaining);
		}, 1000);
	}

	function updateCountdownDisplay(seconds) {
		var min = Math.floor(seconds / 60);
		var sec = seconds % 60;
		$("#qr-countdown").text(min + ":" + (sec < 10 ? "0" : "") + sec);
	}

	function showExpired() {
		cleanup();
		// Auto-refresh: generate a new QR code immediately
		generateQR();
	}

	function showError() {
		// Manual recovery only — an automatic retry here can turn a failing
		// endpoint (e.g. rate limit) into a request loop that keeps it failing.
		cleanup();
		$("#qr-login-loading").show().html("<p>" + __("Could not generate QR code") + "</p>");
		$("#qr-login-img").hide();
		$("#qr-login-timer").hide();
		$("#qr-login-refresh").show();
	}

	function cleanup() {
		generation++;
		if (pollInterval) {
			clearInterval(pollInterval);
			pollInterval = null;
		}
		if (countdownInterval) {
			clearInterval(countdownInterval);
			countdownInterval = null;
		}
		currentToken = null;
	}
})();
