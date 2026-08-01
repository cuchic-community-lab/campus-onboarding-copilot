import unittest

from campus_copilot.preview_security import PreviewGuard, SlidingWindowLimiter, parse_login_body


class PreviewSecurityTest(unittest.TestCase):
    def setUp(self):
        self.guard = PreviewGuard("correct-horse-battery-staple", session_secret=b"s" * 32)

    def test_signed_cookie_is_required(self):
        self.assertFalse(self.guard.authorized({}))
        cookie = self.guard.session_cookie(secure=True)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Secure", cookie)
        self.assertTrue(self.guard.authorized({"Cookie": cookie.split(";", 1)[0]}))
        self.assertFalse(self.guard.authorized({"Cookie": "campus_preview_session=forged"}))

    def test_access_code_is_compared_and_short_codes_are_rejected(self):
        self.assertTrue(self.guard.valid_code("correct-horse-battery-staple"))
        self.assertFalse(self.guard.valid_code("wrong-code-that-is-long"))
        with self.assertRaises(ValueError):
            PreviewGuard("too-short")

    def test_login_form_has_strict_size_and_content_type_boundary(self):
        body = b"access_code=correct-horse-battery-staple"
        self.assertEqual(parse_login_body(body, "application/x-www-form-urlencoded"), "correct-horse-battery-staple")
        self.assertEqual(parse_login_body(body, "application/json"), "")
        self.assertEqual(parse_login_body(b"x" * 4097, "application/x-www-form-urlencoded"), "")

    def test_sliding_window_limiter_blocks_and_recovers(self):
        limiter = SlidingWindowLimiter()
        self.assertTrue(limiter.allow("ip", 2, 60, now=1))
        self.assertTrue(limiter.allow("ip", 2, 60, now=2))
        self.assertFalse(limiter.allow("ip", 2, 60, now=3))
        self.assertTrue(limiter.allow("ip", 2, 60, now=62))

    def test_origin_must_match_forwarded_host_when_present(self):
        self.assertTrue(self.guard.same_origin({"Host": "preview.example", "Origin": "https://preview.example"}))
        self.assertTrue(self.guard.same_origin({"Host": "preview.example"}))
        self.assertFalse(self.guard.same_origin({"Host": "preview.example", "Origin": "https://evil.example"}))


if __name__ == "__main__":
    unittest.main()
