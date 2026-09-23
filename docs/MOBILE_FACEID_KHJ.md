# KHBL Mobile — KHJ passkey bridge

## Architecture

KHBL LAN `http://192.168.1.6:8102/banle/mobile/` -> POST `/banle/mobile/auth/start/` -> KHJ public
`https://unopposable-parheliacal-waylon.ngrok-free.dev/banle/mobile/` -> fresh WebAuthn verification -> one-use code ->
KHBL `/banle/mobile/auth/callback/` -> `/banle/mobile/dashboard/`.

- Existing KHJ RP ID/public origin unchanged; no re-enrollment or copying passkeys.
- KHJ existing portal authentication views/cookies are unchanged. Separate bridge
  challenge and session keys do not grant access to Portal User or Admin.
- Backend start/redeem use loopback + HMAC SHA256 timestamp (30-second skew).
  Dedicated key file `C:/ProgramData/KHJ/auth-bridge/khbl.key`, outside git,
  restricted Windows ACL. Both apps must have read access. Never expose this key.
- LAN browser generates a random verifier retained in its server-side session;
  KHJ stores its SHA256 hash. This binds redemption to the initiating browser.
- Start ticket expires in 3 minutes, can open public login once; authorization
  code expires in 60 seconds, atomically redeemed once under a database lock.
- Only hashes of tickets/codes are persisted. Fixed callback, no arbitrary redirects.
- User selected separate HTTP mobile port 8102 (no phone CA installation). It uses
  HttpOnly/SameSite=Lax `khbl_mobile_http_session` without Secure. Face ID sessions
  persist for 365 days, renewed while used; password fallback remains 8 hours.
  Browsers may clear/limit cookies; private browsing cannot guarantee persistence.
  HTTP LAN session/data are unencrypted; Face ID authentication remains public HTTPS.
  Old HTTPS 8100 retains Secure `khbl_mobile_session`; desktop cookies are untouched.
  Desktop cookie remains untouched; QR requests from the mobile page use mobile
  session based on explicit X-KHBL-Mobile header or same-host mobile Referer.
- Fresh verification is required; existing KHJ login cookie alone is insufficient.
- Revocation/employee active status checked at verification and redemption, then
  at most every 5 minutes of session activity through signed loopback internal/status.
  A confirmed inactive/revoked result logs out the session. If KHJ is unavailable,
  protected requests fail closed with 503 until it returns (cookie retained).
  Disabled KHBL users/mappings are blocked locally. Deleted/changed mappings log out.

## Identity / permissions — approved all active passkey employees

New table `mobile_employee_identity`: unique KHJ employee ID -> unique KHBL user.
Manage at `/admin/pos/mobileemployeeidentity/`. Never assign shared admin identity
automatically. Identity is KHJ employee_id, NOT employee_pmv. After a verified backend
redemption, missing dedicated accounts are provisioned automatically with IN/QR access.
Missing/changed PMV code does not prevent login. A deliberate disabled user/mapping
is never re-enabled. Username collisions and non-mobile/admin mappings require review.

User approved dedicated per-employee IN/QR-only accounts. Provisioned 32 `khj_<id>`
users with unusable passwords, no staff/superuser flag, no shared PmvUser assignment.
Only CHUYEN_KHOAN view/edit granted; middleware additionally restricts them to mobile
dashboard/IN list, IN BANK QR create/reopen and automatic receipt verification.
OUT, CASH, management, admin, other application routes are denied server-side.
Staff filter uses verified employee_pmv from the mobile session, not shared PMV login.
Employees without PMV default to all IN transactions; UI explains that “Tôi” cannot
identify their transactions yet. No name matching or shared EmpID fallback is used.
Existing admin/kimhanh2/ketoan users and their mappings/permissions remain untouched.

Provisioning command (dry-run default, idempotent, never overwrites existing users):
`manage.py provision_mobile_faceid [--apply]`.

Live audit 2026-09-17: 32 active KHJ employees with passkeys; 23 have employee_pmv.
KHBL has admin, kimhanh2, ketoan, all mapped to EMP150400000001. This is insufficient
to identify other employees; dedicated mobile identities now solve this. No role inheritance from KHJ.

PMV missing but now allowed/provisioned (9): 24 MR GIANG; 25 Nguyễn Văn Trường;
26 Trần Trung Hiếu; 41 Nguyễn Tuấn Khanh; 42 Phạm Minh Đức; 46 Lý Dương Thiện;
53 Nguyễn Văn Thành; 99 Nguyễn Minh Triết; 100 Đặng Hằng Ni.
Do not confuse employee 24 MR GIANG with employee 50 Trương Ngọc Giang (already mapped).

## Operations

### Home Screen app (2026-09-17)

- `static/mobile/manifest.webmanifest` scopes the launcher to `/banle/mobile/`,
  starts at the login/dashboard router and uses standalone display. Local PNG
  launcher icons (180/192/512) are exported by `ops/build_mobile_icons.py` from
  `static/mobile/kh2-icon-master.png`. Display name: **KH2 mApp**. The user-provided
  `D:/images/logo0.png` was used with built-in imagegen for the gold-on-charcoal
  icon; no original logo was overwritten. Login and dedicated KHJ Face ID page
  now show the logo, app name and main action, without installation notes.
- iOS: Safari > Share > Add to Home Screen (Open as Web App when offered).
  Android: Chrome > Add to Home Screen. HTTP can yield a shortcut, not an installable
  full PWA. No service worker, offline payments, or cached financial screens added.
- Mobile-only safe-area/visual viewport handling accounts for notch, navigation,
  keyboard and landscape. No viewport zoom lock. Base desktop viewport unchanged.
- `/banle/mobile/health/` is a no-store, anonymous, read-only application liveness
  probe. JS checks every 30 seconds while visible, plus resume/retry/network events.
  The browser cannot identify the Wi-Fi SSID: errors instruct users to check LAN/server,
  never claim to have detected a specific network. Health probes do not renew auth.
- QR remains a local data PNG; no external image host. Native file sharing is
  offered only when secure context + canShare(files) support it. HTTP falls back to
  downloading PNG / long-press on iPhone with instructions for Photos or Files.

### Face ID with separate app/browser cookies

Normal Safari/Chrome tab: original redirect/callback/PKCE-like verifier flow unchanged.
Standalone: POST start with `mode=app` keeps the verifier solely in the original
server-side KHBL session and returns the ticket URL with `app=1`. The app prepares
this link on load, before the first tap, and refreshes unused tickets after 150s
while visible. The single “Xác nhận FACE ID” link opens HTTPS with native user-gesture
navigation; no second “Mở xác nhận” step, async popup, or popup-blocker workaround.
Only a non-secret launch timestamp is saved in sessionStorage to restore waiting UI.
After WebAuthn, KHJ shows “open the KHBL icon”; it does NOT send a login code to
Safari in this mode. The original app POSTs `/banle/mobile/auth/result/` with CSRF,
then KHBL calls signed loopback `internal/result/` using the original verifier.
KHJ locks and consumes the approved grant once and rechecks active employee/passkey.
Only that initiating KHBL session gets logged in. No secrets in localStorage,
URL fragments, postMessage or shared cookie assumption. No new database schema.

- Auth result polls every 2.5 seconds while visible; server limits to one call/2s
  per verifier. Resume/check button triggers a check. Hidden/success/expired stops.
- Start expires after 3 minutes and verified grant after 60 seconds as before;
  return to the KHBL icon promptly. Original app pending state is bounded to 240s.
- Automated tests use separate cookie jars and mock biometric verification only
  in isolated SQLite test settings. Real iPhone/Android installation, OS navigation,
  biometric prompt and Photos/share behavior require final testing on real phones.

Phone acceptance checklist:
1. Add icon from LAN URL, close Safari/Chrome, open icon. Confirm icon/name/layout.
2. From an unauthenticated icon session, tap Face ID once to open the external page,
   confirm, return to the same icon within 60s. Confirm employee name + IN-only rights.
3. Close/reopen icon; session persists. Logout inside app; app is logged out.
4. Cancel Face ID / let it expire / try a separate browser: no unauthorized login.
5. Create a QR only for an authorized real task; check amount, save PNG/long-press,
   verify saved image remains readable. On HTTP, no unsupported native share button.
6. With page already loaded, disconnect LAN; observe banner. Reconnect and Retry;
   verify banner clears. No auto-replay of payment mutations. A cold launch offline
   can show the browser's network error because HTTP has no service-worker fallback.

Icon prompt (built-in imagegen, reference `D:/images/logo0.png`):
“Edit the supplied gold logo into a polished square mobile app icon for a Vietnamese
jewelry shop. Preserve the identity and radial folded faceted gold emblem from the
reference: same number and arrangement of folded petal/ray arms, central convergence,
warm metallic gold. Remove the white outer border and the flat rectangular gold
background behind the emblem; isolate the gold emblem cleanly on a full-bleed very
dark warm charcoal background (#191711) with extremely subtle warm illumination.
Center the emblem, occupy approximately 68% of total square width so it survives
circular maskable cropping. Crisp clean luxury brand finish, clearer sculpted facet
definition but no redesign of the mark. Output one flat front-facing square 1024x1024
PNG icon asset, opaque background edge to edge, NO rounded outer corners (OS masks it),
NO mockup, NO device, NO text, NO letters, NO extra ornaments or border. This is the
actual production launcher icon and login centerpiece.”

- Migrations: KHJ employee_portal 0012; KHBL pos 0032/0033. New identity/grant tables only.
- Canonical mobile LAN origin `http://192.168.1.6:8102` (no CA required).
  HTTPS desktop 8100 remains unchanged. Port 8102 binds only 192.168.1.6 and accepts
  LAN 192.168.1.0/24, serving mobile/static/IN BANK QR routes only; others return 404.
- Public origin is the existing ngrok domain, fixed in bridge client. A domain change
  requires a deliberate passkey migration; do not silently change RP ID.
- KHJ 8000 + ngrok must run; KHCD 8200 unchanged. Public Caddy now forwards exactly
  `/banle/mobile/` to KHJ, not the KHBL business application. Options/verify remain
  at the existing `/portal-user/auth/khbl/` bridge routes.
- KHBL waitress trusts ONLY loopback X-Forwarded-Proto from Caddy. Startup command
  updated accordingly; live test previously found is_secure() false without this.
- Password fallback at `/banle/mobile/login/` uses existing KHBL credentials/rights,
  does not provision accounts, and uses the isolated mobile cookie.
- If login interrupted/offline/expired, restart from LAN login in the same browser.
- New verified employees receive dedicated mobile-only accounts on first login.
- Expired bridge grant rows are harmless but should be periodically purged according
  to chosen audit-retention policy (no recurring cleanup installed in this change).

## Verification

Only isolated SQLite settings used for automated tests, never live database test runner.
KHBL tests: mobile_auth + prior IN/QR suites. KHJ bridge tests cover HMAC gate,
ticket replay, origin mismatch, signature failure, PKCE mismatch, revoked passkeys,
expired code, one-use redemption. WebAuthn successful-signature unit tests use a mock;
real Face ID roundtrip still requires the user's phone and approved identity mapping.

Existing KHJ portal suite has 17 failures + 8 errors / 64 tests. Re-running with
HEAD network.py and urls.py loaded in memory (no file rollback) produces the same
failures. Those legacy unrelated tests were not changed in this implementation.

Deployment verified: 69 KHBL regression/security tests and 11 KHJ bridge tests pass;
JavaScript money formatting/polling tests pass. `tests/smoke_faceid_transport.py`
passed against actual LAN HTTP 8102, authenticated backend start, public ngrok
`/banle/mobile/` and CSRF-protected WebAuthn options. HTTPS 8100 separately returns 200.
It intentionally stops before
any biometric assertion. Login UI also opened successfully in browser.
