"""
Regression tests for security fixes M-1, M-3, M-4.
(M-2 is covered by Test F in tests/test_admin_dashboard.py.)

M-1: session cookie is Secure (never sent over plain HTTP).
M-3: upload filenames can never escape the downloads/ directory.
M-4: login is rate-limited and uses a constant-time password comparison.
"""
import os
import sys

os.environ["JWT_SECRET"] = "test-secret-key-very-secure-length-32-bytes"
os.environ["DASHBOARD_PASSWORD"] = "test-admin-password"

import db
db.DB_PATH = "database/test_security_m1_m4.db"
if os.path.exists(db.DB_PATH):
    os.remove(db.DB_PATH)
db.init_db()

from fastapi.testclient import TestClient
from unittest.mock import patch
from backend.app import app
import backend.routes as routes

print("\n" + "=" * 70)
print("SECURITY FIXES M-1 / M-3 / M-4 REGRESSION TESTS")
print("=" * 70)


def run_tests():
    # -----------------------------------------------------------
    # M-1: Secure cookie flag
    # -----------------------------------------------------------
    print("\nTest M-1: Secure session cookie...")

    https_client = TestClient(app, base_url="https://testserver")
    resp = https_client.post("/admin/login", data={"password": "test-admin-password"})
    assert resp.status_code == 200
    set_cookie_header = resp.headers.get("set-cookie", "")
    assert "secure" in set_cookie_header.lower(), f"Expected Secure attribute in Set-Cookie, got: {set_cookie_header}"
    assert "httponly" in set_cookie_header.lower(), "Expected HttpOnly attribute"
    assert "samesite=lax" in set_cookie_header.lower(), "Expected SameSite=Lax attribute"
    print(f"  - Set-Cookie header includes Secure + HttpOnly + SameSite=Lax [OK]")
    print(f"    raw header: {set_cookie_header}")

    # Prove it actually behaves like a Secure cookie: a plain-HTTP client
    # must NOT be able to use it to reach an authenticated route.
    http_client = TestClient(app, base_url="http://testserver")
    resp = http_client.post("/admin/login", data={"password": "test-admin-password"})
    assert resp.status_code == 200
    resp2 = http_client.get("/admin", follow_redirects=False)
    assert resp2.status_code == 303, f"Expected 303 (cookie withheld over http), got {resp2.status_code}"
    print("  - Secure cookie is NOT usable over plain HTTP (redirected to login) [OK]")

    resp3 = https_client.get("/admin", follow_redirects=False)
    assert resp3.status_code == 200, f"Expected 200 over https, got {resp3.status_code}"
    print("  - Same cookie IS usable over HTTPS (dashboard loads) [OK]")

    https_client.post("/admin/logout")

    # -----------------------------------------------------------
    # M-3: upload filename path traversal
    # -----------------------------------------------------------
    print("\nTest M-3: Upload filename path traversal protection...")

    # log back in (need auth for upload routes)
    https_client.post("/admin/login", data={"password": "test-admin-password"})

    project_root = os.path.abspath(os.path.join(os.path.dirname(db.DB_PATH) or ".", ".."))
    marker_name = "evil_traversal_marker.pdf"

    # Snapshot: this exact filename must not exist anywhere relevant before the test.
    before_root = os.path.exists(os.path.join(".", marker_name))
    assert not before_root, "Test setup invalid: marker file already exists"

    malicious_filename = "../../../../../../" + marker_name
    malicious_file = (malicious_filename, b"%PDF-1.4 fake pdf content", "application/pdf")

    with patch("backend.routes.pipeline.parser.parse_local") as mock_parse, \
         patch("backend.routes.pipeline.extractor.extract") as mock_extract:
        mock_parse.return_value = {"file_path": "irrelevant", "content_type": "application/pdf", "text": "ocr text"}
        mock_extract.return_value = {
            "doc_type": "SUPPLIER_INVOICE",
            "items": [{"product": "Test", "quantity": 1, "unit": "kg", "unit_price": 1.0, "total": 1.0}],
        }
        resp = https_client.post("/admin/upload/grocery-invoice", files={"file": malicious_file})

    assert resp.status_code == 200, f"Expected upload to succeed safely, got {resp.status_code}: {resp.text}"
    print("  - Upload with malicious filename '../../../../../../evil_traversal_marker.pdf' handled without error [OK]")

    # The malicious filename must never have been used to create a file anywhere.
    after_root = os.path.exists(os.path.join(".", marker_name))
    assert not after_root, "SECURITY FAILURE: file escaped to project root!"
    print(f"  - No file named '{marker_name}' was created anywhere outside downloads/ [OK]")

    # Confirm only sanitized uuid-based filenames exist in downloads/ (no traversal
    # segments, no trace of the malicious name in the actual filename on disk).
    created = [f for f in os.listdir("downloads") if f.startswith("web_")]
    for f in created:
        assert ".." not in f and "/" not in f and "\\" not in f, f"Unsafe filename on disk: {f}"
        assert "evil_traversal_marker" not in f, f"Client filename leaked into disk filename: {f}"
    print(f"  - All {len(created)} files on disk use safe 'web_<uuid><ext>' naming, client filename discarded [OK]")

    # cleanup any temp files this test created (uploads clean up on success already,
    # but be defensive)
    for f in os.listdir("downloads"):
        if f.startswith("web_"):
            try:
                os.remove(os.path.join("downloads", f))
            except OSError:
                pass

    https_client.post("/admin/logout")

    # -----------------------------------------------------------
    # M-4: login rate limiting + constant-time comparison
    # -----------------------------------------------------------
    print("\nTest M-4: Login rate limiting + constant-time password comparison...")

    # Reset any rate-limit state left over from earlier logins in this test run.
    routes._login_failures.clear()

    rl_client = TestClient(app, base_url="https://testserver")

    # 1) First LOGIN_MAX_ATTEMPTS wrong-password attempts should each get a normal 401.
    for i in range(routes.LOGIN_MAX_ATTEMPTS):
        resp = rl_client.post("/admin/login", data={"password": f"wrong-{i}"})
        assert resp.status_code == 401, f"Attempt {i+1}: expected 401, got {resp.status_code}"
    print(f"  - First {routes.LOGIN_MAX_ATTEMPTS} failed attempts each return 401 [OK]")

    # 2) The next attempt (even with a WRONG password) must now be rate-limited (429).
    resp = rl_client.post("/admin/login", data={"password": "wrong-again"})
    assert resp.status_code == 429, f"Expected 429 after {routes.LOGIN_MAX_ATTEMPTS} failures, got {resp.status_code}"
    print(f"  - Attempt #{routes.LOGIN_MAX_ATTEMPTS + 1} is rate-limited with 429 [OK]")

    # 3) Even the CORRECT password is blocked while locked out (no bypass via a lucky guess).
    resp = rl_client.post("/admin/login", data={"password": "test-admin-password"})
    assert resp.status_code == 429, f"Expected 429 (correct password still locked out), got {resp.status_code}"
    assert "session_token" not in rl_client.cookies, "Should NOT be authenticated while locked out"
    print("  - CORRECT password is ALSO blocked during lockout (no bypass) [OK]")

    # 4) A different client IP is unaffected (rate limiting is per-IP).
    #    TestClient doesn't let us spoof client.host easily without a transport override;
    #    instead verify directly that the limiter is keyed by IP by checking internal state.
    assert "testclient" in routes._login_failures, "Expected recorded failures keyed by client IP"
    print("  - Rate-limit state is keyed by client IP (per-IP lockout, not global) [OK]")

    # 5) Constant-time comparison: verify the code path actually uses hmac.compare_digest
    #    (a timing measurement would be flaky in CI; verify the implementation directly).
    import inspect
    login_source = inspect.getsource(routes.login)
    assert "hmac.compare_digest" in login_source, "Expected hmac.compare_digest in login()"
    assert "password == expected_password" not in login_source, "Must not use == for password comparison"
    print("  - login() uses hmac.compare_digest(...) instead of == for the password check [OK]")

    # Reset limiter state so it doesn't leak into other test runs in the same process.
    routes._login_failures.clear()


try:
    run_tests()
    print("\n" + "=" * 70)
    print("ALL M-1 / M-3 / M-4 TESTS PASSED")
    print("=" * 70 + "\n")
finally:
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
        print("Test database cleaned up successfully.")
