from types import SimpleNamespace

from django.test import SimpleTestCase

from .signals import credential_username, request_ip_address, request_user_agent


class LoginEventSignalHelperTests(SimpleTestCase):
    def test_request_ip_address_uses_first_forwarded_address(self):
        request = SimpleNamespace(
            META={
                "HTTP_X_FORWARDED_FOR": "203.0.113.10, 10.0.0.5",
                "REMOTE_ADDR": "10.0.0.5",
            }
        )

        self.assertEqual(request_ip_address(request), "203.0.113.10")

    def test_request_ip_address_falls_back_to_remote_addr(self):
        request = SimpleNamespace(META={"REMOTE_ADDR": "127.0.0.1"})

        self.assertEqual(request_ip_address(request), "127.0.0.1")

    def test_request_user_agent_handles_missing_request(self):
        self.assertEqual(request_user_agent(None), "")

    def test_credential_username_ignores_password(self):
        self.assertEqual(
            credential_username({"password": "secret", "username": "alice"}),
            "alice",
        )
