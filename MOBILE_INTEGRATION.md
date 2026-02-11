# QR Login — Mobile App Integration Guide

## Overview

The QR Login app adds a QR code to the Frappe login page. When a user scans the QR code from the mobile app (already authenticated), they are instantly logged in on the web browser. Works like WhatsApp Web, Discord, or Steam.

## Flow

1. User opens the web login page → a QR code is displayed
2. User opens the mobile app → taps "Scan to Login"
3. Mobile app scans the QR code → gets a token and site URL
4. Mobile app shows confirmation: "Log in as [user] on [site]?"
5. User confirms → mobile app calls the `confirm_login` API
6. Web browser auto-redirects → user is logged in

## What the QR Code Contains

The QR code encodes a JSON string:

```json
{
  "token": "a1b2c3d4e5f6...",
  "url": "https://your-site.com"
}
```

- `token` — unique 32-character hex string, valid for 2 minutes
- `url` — the Frappe site URL where the login is happening

## API Endpoint

### `POST /api/method/qr_login.api.confirm_login`

Confirms the QR login and creates a web session for the authenticated mobile user.

**Authentication**: Required. The mobile app must send its existing session credentials (cookie or token).

**Request:**

```
POST {url}/api/method/qr_login.api.confirm_login
Content-Type: application/json
Cookie: sid=<mobile_session_id>

{
  "token": "a1b2c3d4e5f6..."
}
```

Or with token-based auth:

```
POST {url}/api/method/qr_login.api.confirm_login
Content-Type: application/json
Authorization: token <api_key>:<api_secret>

{
  "token": "a1b2c3d4e5f6..."
}
```

**Response (success):**

```json
{
  "message": {
    "status": "confirmed",
    "user": "user@example.com"
  }
}
```

**Response (token expired):**

```json
{
  "exc_type": "AuthenticationError",
  "exception": "QR code has expired. Please scan a new one."
}
```

**Response (already used):**

```json
{
  "exc_type": "ValidationError",
  "exception": "This QR code has already been used."
}
```

## Implementation Steps

### 1. Add a "Scan to Login" Button

Add a button in the mobile app (e.g., in settings, profile, or main menu) that opens the device camera as a QR scanner.

### 2. Parse the QR Code

When a QR code is scanned, parse the JSON content:

```dart
// Dart / Flutter example
final qrData = jsonDecode(qrContent);
final token = qrData['token'];  // "a1b2c3d4e5f6..."
final url = qrData['url'];      // "https://your-site.com"
```

```kotlin
// Kotlin / Android example
val qrData = JSONObject(qrContent)
val token = qrData.getString("token")
val url = qrData.getString("url")
```

```swift
// Swift / iOS example
let qrData = try JSONDecoder().decode(QRLoginData.self, from: qrContent.data(using: .utf8)!)
let token = qrData.token
let url = qrData.url
```

### 3. Show Confirmation Dialog

Before confirming, show the user what they're approving:

```
Log in as john@example.com on your-site.com?

[Cancel]  [Confirm]
```

### 4. Call the API

On confirmation, make an authenticated POST request:

```dart
// Dart / Flutter example
final response = await http.post(
  Uri.parse('$url/api/method/qr_login.api.confirm_login'),
  headers: {
    'Content-Type': 'application/json',
    'Cookie': 'sid=$sessionId',
  },
  body: jsonEncode({'token': token}),
);

if (response.statusCode == 200) {
  // Show success message
} else {
  // Show error (expired or already used)
}
```

### 5. Show Feedback

- **Success**: "Logged in successfully!" (optionally with a checkmark animation)
- **Expired**: "QR code has expired. Please refresh and try again."
- **Error**: "Something went wrong. Please try again."

## Security Notes

- Tokens expire after **2 minutes**
- Tokens are **one-time use** — once confirmed, they cannot be reused
- The `confirm_login` endpoint **requires authentication** — only logged-in mobile users can confirm
- The web session created is a standard Frappe session with the same user and permissions as the mobile session

## Testing

You can test the flow manually using curl:

```bash
# 1. Get a token (simulating what the login page does)
curl -X POST https://your-site.com/api/method/qr_login.api.generate_token

# 2. Confirm login (simulating what the mobile app does)
curl -X POST https://your-site.com/api/method/qr_login.api.confirm_login \
  -H "Content-Type: application/json" \
  -H "Cookie: sid=your_mobile_session_id" \
  -d '{"token": "the_token_from_step_1"}'
```
