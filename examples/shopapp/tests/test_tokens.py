import time
import unittest

from shop import tokens


class TokenTests(unittest.TestCase):
    def test_issued_token_validates(self):
        self.assertEqual(tokens.validate_token(tokens.issue_token("u1")), "u1")

    def test_tampered_token_is_rejected(self):
        token = tokens.issue_token("u1")
        self.assertIsNone(tokens.validate_token(token[:-1] + ("0" if token[-1] != "0" else "1")))

    def test_expired_token_is_rejected(self):
        self.assertIsNone(tokens.validate_token(tokens.issue_token("u1", ttl=-5)))

    def test_garbage_is_rejected(self):
        self.assertIsNone(tokens.validate_token("not-a-token"))


if __name__ == "__main__":
    unittest.main()
