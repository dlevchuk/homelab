import requests
import os
import sys
import time
import datetime
import json
from dotenv import load_dotenv


load_dotenv()

API = 'https://api.notion.com/v1'
TIMEOUT = 30

timestamp = datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
folder = 'notionbackup_' + timestamp

notion_token = os.environ.get("NOTION_INTEGRATION_TOKEN")
if not notion_token:
  sys.exit("NOTION_INTEGRATION_TOKEN is not set")
if not notion_token.startswith('Bearer '):
  notion_token = f'Bearer {notion_token}'

headers = {
  'Authorization': notion_token,
  'Notion-Version': '2022-06-28',      #notion api version
  'Content-Type': 'application/json',
}


def request(method, url, **kwargs):
  """HTTP call with timeout and retry on rate limit (429)."""
  for _ in range(5):
    r = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)
    if r.status_code == 429:
      time.sleep(int(r.headers.get('Retry-After', 1)))
      continue
    r.raise_for_status()
    return r.json()
  raise RuntimeError(f'Too many retries: {url}')


def paginate(method, url, body=None):
  """Yield all results following Notion's cursor pagination."""
  cursor = None
  while True:
    if method == 'POST':
      payload = dict(body or {}, page_size=100)
      if cursor:
        payload['start_cursor'] = cursor
      data = request('POST', url, json=payload)
    else:
      params = {'page_size': 100}
      if cursor:
        params['start_cursor'] = cursor
      data = request('GET', url, params=params)
    yield from data['results']
    if not data.get('has_more'):
      return
    cursor = data['next_cursor']


def dump(path, obj):
  with open(path, 'w', encoding='utf-8') as file:
    file.write(json.dumps(obj, ensure_ascii=False))


os.mkdir(folder)

for block in paginate('POST', f'{API}/search'):
  dump(f'{folder}/{block["id"]}.json', block)

  # databases are not blocks and have no /children endpoint
  if block.get('object') != 'page':
    continue

  try:
    children = list(paginate('GET', f'{API}/blocks/{block["id"]}/children'))
  except requests.RequestException as e:
    print(f'WARN: cannot fetch children of {block["id"]}: {e}', file=sys.stderr)
    continue

  if children:
    os.mkdir(f'{folder}/{block["id"]}')
    for child in children:
      dump(f'{folder}/{block["id"]}/{child["id"]}.json', child)