"""On-demand personal Telegram client. No message listeners or automatic replies."""
import argparse
import asyncio
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sys

HOME = Path(__file__).resolve().parent


def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def validate_alias(value):
    if not re.fullmatch(r'[a-z0-9_-]{1,40}', value):
        raise ValueError('Alias must contain 1–40 lowercase letters, numbers, _ or -')
    return value


def prepare_send(args, recipients, sent):
    if args.alias not in recipients:
        raise ValueError('Unknown recipient. Resolve and add the exact person or group first.')
    text = sys.stdin.read() if args.text_file == '-' else Path(args.text_file).read_text()
    if not text.strip() or len(text.encode('utf-16-le')) // 2 > 4096:
        raise ValueError('Message must contain 1–4096 UTF-16 code units')
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', args.request_id):
        raise ValueError('Use a stable unique request ID (8–100 letters, digits, _ or -)')
    fingerprint = hashlib.sha256(json.dumps([recipients[args.alias]['id'], text], ensure_ascii=False).encode()).hexdigest()
    previous = sent.get(args.request_id)
    if previous and previous['fingerprint'] != fingerprint:
        raise ValueError('Request ID already used for a different recipient or text')
    return text, fingerprint, previous


async def login_qr(client):
    import qrcode
    from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError

    if await client.is_user_authorized():
        return
    for attempt in range(3):
        login = await client.qr_login()
        qr = qrcode.QRCode(border=4)
        qr.add_data(login.url)
        qr.make(fit=True)
        print('\nTelegram on phone: Settings -> Devices -> Link Desktop Device')
        qr.print_ascii(invert=True)
        print('Scan this QR and approve the login on your phone.', flush=True)
        try:
            await login.wait()
            return
        except SessionPasswordNeededError:
            for password_attempt in range(3):
                try:
                    await client.sign_in(password=getpass.getpass('Telegram 2FA password: '))
                    return
                except PasswordHashInvalidError:
                    print('Incorrect 2FA password. Try again.', flush=True)
            raise ValueError('Too many incorrect 2FA passwords; run login again.')
        except asyncio.TimeoutError:
            if attempt < 2:
                print('QR expired; generating a new one.', flush=True)
    raise ValueError('QR login timed out. Run login --qr again when your phone is ready.')


async def run(args):
    from telethon import TelegramClient, functions, types, utils
    config_path = HOME / 'credentials.json'
    config = read_json(config_path, {})
    if args.command == 'login':
        if not sys.stdin.isatty():
            raise ValueError('Login requires an interactive terminal (docker exec -it)')
        if not config:
            api_id = int(input('Telegram api_id: ').strip())
            api_hash = getpass.getpass('Telegram api_hash: ').strip()
            if api_id <= 0 or not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash):
                raise ValueError('Invalid api_id or api_hash')
            config = {'api_id': api_id, 'api_hash': api_hash}
            write_json(config_path, config)
    if not config:
        print(json.dumps({'authorized': False, 'action': 'run login over SSH'}))
        return
    recipients_path = HOME / 'recipients.json'
    sent_path = HOME / 'sent.json'
    recipients = read_json(recipients_path, {})
    sent = read_json(sent_path, {})
    if args.command == 'send':
        text, fingerprint, previous = prepare_send(args, recipients, sent)
        if previous and previous.get('message_id') is not None:
            print(json.dumps({'already_sent': True, 'message_id': previous['message_id']}))
            return
    client = TelegramClient(str(HOME / 'account'), config['api_id'], config['api_hash'],
                            device_model='Hermes pi5 personal sender',
                            request_retries=0, connection_retries=2, timeout=15,
                            flood_sleep_threshold=0)
    try:
        await client.connect()
        if args.command == 'login':
            if args.qr:
                await login_qr(client)
            else:
                await client.start(phone=lambda: input('Phone (+country code): ').strip(),
                                   code_callback=lambda: getpass.getpass('Telegram login code: '),
                                   password=lambda: getpass.getpass('Telegram 2FA password: '))
            me = await client.get_me()
            if me.bot:
                raise ValueError('A personal user account is required, not a bot')
            print(json.dumps({'authorized': True, 'username': me.username, 'id': me.id}))
            return
        if not await client.is_user_authorized():
            raise ValueError('Not authorized. Run login interactively over SSH.')
        if args.command == 'status':
            me = await client.get_me()
            print(json.dumps({'authorized': True, 'username': me.username,
                              'recipients': list(recipients)}))
        elif args.command == 'contacts':
            contacts = await client(functions.contacts.GetContactsRequest(hash=0))
            users = [u for u in contacts.users if not u.bot and not u.deleted]
            query = args.query.casefold()
            result = [{'id': u.id, 'username': u.username,
                       'name': ' '.join(filter(None, [u.first_name, u.last_name]))}
                      for u in users if query in ' '.join(filter(None, [u.first_name, u.last_name, u.username])).casefold()]
            print(json.dumps(result[:20], ensure_ascii=False))
        elif args.command == 'groups' or (args.command == 'add-recipient' and args.group is not None):
            groups = []
            async for dialog in client.iter_dialogs(ignore_migrated=True):
                if not dialog.is_group or not isinstance(dialog.entity, (types.Chat, types.Channel)):
                    continue
                group = {'id': dialog.id, 'name': dialog.name}
                if args.command == 'groups':
                    if args.query.casefold() in dialog.name.casefold():
                        groups.append(group)
                        if len(groups) == 20:
                            break
                elif dialog.id == args.group:
                    recipients[args.alias] = {
                        **group, 'access_hash': getattr(dialog.input_entity, 'access_hash', None)}
                    write_json(recipients_path, recipients)
                    print(json.dumps({'alias': args.alias, **group}, ensure_ascii=False))
                    return
            if args.command == 'add-recipient':
                raise ValueError('Group ID not found in this account’s dialogs. Run groups --query NAME.')
            print(json.dumps(groups, ensure_ascii=False))
        elif args.command == 'add-recipient':
            entity = await client.get_entity(args.user)
            if not isinstance(entity, types.User) or entity.bot or entity.deleted:
                raise ValueError('Recipient must be an individual human Telegram user')
            peer = await client.get_input_entity(entity)
            recipients[args.alias] = {'id': entity.id, 'access_hash': peer.access_hash,
                                     'username': entity.username,
                                     'name': ' '.join(filter(None, [entity.first_name, entity.last_name]))}
            write_json(recipients_path, recipients)
            print(json.dumps({'alias': args.alias, 'id': entity.id, 'name': recipients[args.alias]['name']}, ensure_ascii=False))
        elif args.command == 'send':
            recipient = recipients[args.alias]
            peer_id, peer_type = utils.resolve_id(recipient['id'])
            if peer_type is types.PeerChat:
                peer = types.InputPeerChat(peer_id)
            elif peer_type is types.PeerChannel:
                peer = types.InputPeerChannel(peer_id, recipient['access_hash'])
            else:
                peer = types.InputPeerUser(peer_id, recipient['access_hash'])
            # Persist the Telegram random_id before sending: retries with the same
            # request ID use Telegram's own duplicate suppression, even after a crash.
            random_id = int.from_bytes(hashlib.sha256(args.request_id.encode()).digest()[:8], 'big', signed=True)
            sent[args.request_id] = {'fingerprint': fingerprint, 'message_id': None}
            write_json(sent_path, sent)
            result = await client(functions.messages.SendMessageRequest(
                peer=peer,
                message=text, random_id=random_id, no_webpage=True))
            message_id = getattr(result, 'id', None)
            for update in getattr(result, 'updates', []):
                if isinstance(update, types.UpdateMessageID) and update.random_id == random_id:
                    message_id = update.id
            sent[args.request_id]['message_id'] = message_id
            write_json(sent_path, sent)
            print(json.dumps({'sent': True, 'alias': args.alias, 'message_id': message_id}))
    finally:
        await client.disconnect()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    login = commands.add_parser('login')
    login.add_argument('--qr', action='store_true', help='Scan a QR in Telegram instead of requesting a login code')
    commands.add_parser('status')
    contacts = commands.add_parser('contacts')
    contacts.add_argument('--query', required=True)
    groups = commands.add_parser('groups')
    groups.add_argument('--query', required=True)
    add = commands.add_parser('add-recipient')
    add.add_argument('alias', type=validate_alias)
    target = add.add_mutually_exclusive_group(required=True)
    target.add_argument('--user', help='Exact @username or known numeric user ID')
    target.add_argument('--group', type=int, help='Negative group ID returned by groups')
    send = commands.add_parser('send')
    send.add_argument('alias', type=validate_alias)
    send.add_argument('--text-file', required=True, help='UTF-8 file, or - for stdin')
    send.add_argument('--request-id', required=True, help='Reuse this ID when retrying the same send')
    args = parser.parse_args()
    if args.command == 'add-recipient':
        if args.group is not None and args.group >= 0:
            add.error('--group requires a negative ID returned by groups')
        if args.user is not None and args.user.isdigit():
            args.user = int(args.user)
    with (HOME / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another personal Telegram command is running. Press Ctrl+C in the old login terminal first.', file=sys.stderr)
            raise SystemExit(1) from None
        try:
            asyncio.run(run(args))
        except Exception as exc:
            print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}), file=sys.stderr)
            raise SystemExit(1) from None


if __name__ == '__main__':
    main()
