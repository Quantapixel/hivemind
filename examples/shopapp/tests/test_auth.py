import unittest

from shop import auth


class AuthTests(unittest.TestCase):
    def test_password_hash_is_stable(self):
        self.assertEqual(auth.hash_password("pw"), auth.hash_password("pw"))

    def test_password_hash_differs_per_password(self):
        self.assertNotEqual(auth.hash_password("a"), auth.hash_password("b"))


if __name__ == "__main__":
    unittest.main()
