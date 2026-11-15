"""Validate sending guards without a Telegram session or network access."""
import importlib.util
from contextlib import redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'ansible/raspberry/roles/docker_apps/files/personal_telegram.py'
SPEC = importlib.util.spec_from_file_location('personal_telegram', SOURCE)
CLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENT)


class SendGuards(unittest.TestCase):
    def prepare(self, text='Hello', recipients=None, sent=None):
        args = SimpleNamespace(alias='oleh', text_file='-', request_id='request_123')
        with patch('sys.stdin', io.StringIO(text)):
            return CLIENT.prepare_send(args, recipients if recipients is not None else {'oleh': {'id': 42}}, sent or {})

    def test_unknown_recipient_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown recipient'):
            self.prepare(recipients={})

    def test_retries_preserve_identity_and_content(self):
        _, fingerprint, _ = self.prepare()
        sent = {'request_123': {'fingerprint': fingerprint, 'message_id': 7}}
        self.assertEqual(self.prepare(sent=sent)[2]['message_id'], 7)
        with self.assertRaisesRegex(ValueError, 'different recipient or text'):
            self.prepare(text='Different', sent=sent)
        with self.assertRaisesRegex(ValueError, 'different recipient or text'):
            self.prepare(recipients={'oleh': {'id': 99}}, sent=sent)

    def test_empty_or_oversized_messages_are_rejected(self):
        for text in ['', '   ', 'a' * 4097, '🙂' * 2049]:
            with self.subTest(length=len(text)), self.assertRaises(ValueError):
                self.prepare(text=text)
        self.prepare(text='🙂' * 2048)

    def test_alias_cannot_be_a_path(self):
        for alias in ['../session', 'Name With Spaces', '']:
            with self.assertRaises(ValueError):
                CLIENT.validate_alias(alias)


class QrLogin(unittest.IsolatedAsyncioTestCase):
    async def run_qr(self, waits, authorized=False):
        class PasswordNeeded(Exception):
            pass

        errors = SimpleNamespace(SessionPasswordNeededError=PasswordNeeded,
                                 PasswordHashInvalidError=type('InvalidPassword', (Exception,), {}))
        qr = SimpleNamespace(url='tg://login?token=TEST', wait=AsyncMock(side_effect=waits(PasswordNeeded)))
        client = SimpleNamespace(is_user_authorized=AsyncMock(return_value=authorized),
                                 qr_login=AsyncMock(return_value=qr), sign_in=AsyncMock())
        modules = {'qrcode': SimpleNamespace(QRCode=Mock()),
                   'telethon': SimpleNamespace(errors=errors), 'telethon.errors': errors}
        with patch.dict('sys.modules', modules), patch.object(CLIENT.getpass, 'getpass', return_value='test'), redirect_stdout(io.StringIO()):
            await CLIENT.login_qr(client)
        return client

    async def test_existing_session_does_not_request_qr(self):
        client = await self.run_qr(lambda _: [None], authorized=True)
        client.qr_login.assert_not_awaited()

    async def test_expired_qr_is_renewed(self):
        client = await self.run_qr(lambda _: [TimeoutError(), None])
        self.assertEqual(client.qr_login.await_count, 2)

    async def test_qr_2fa_is_entered_locally(self):
        client = await self.run_qr(lambda needed: [needed()])
        client.sign_in.assert_awaited_once_with(password='test')

    async def test_qr_expiry_has_bounded_retries(self):
        with self.assertRaisesRegex(ValueError, 'timed out'):
            await self.run_qr(lambda _: [TimeoutError()] * 3)


if __name__ == '__main__':
    unittest.main()
