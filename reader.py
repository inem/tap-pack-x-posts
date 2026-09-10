"""Passively index full X post text from Core capture records."""
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlsplit

SCHEMA = '''
CREATE TABLE IF NOT EXISTS deliveries(
  delivery TEXT PRIMARY KEY, record_id TEXT NOT NULL, observed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS posts(
  id TEXT PRIMARY KEY, text TEXT NOT NULL, quality INTEGER NOT NULL,
  observed_at REAL NOT NULL, record_id TEXT NOT NULL);
'''
MAX_BODY = 16 * 1024 * 1024


def tweets(value, depth=0):
    if depth > 80:
        return
    if isinstance(value, dict):
        if value.get('__typename') == 'Tweet' and isinstance(value.get('rest_id'), str):
            yield value
        for child in value.values():
            yield from tweets(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from tweets(child, depth + 1)


def tweet_text(tweet):
    note = tweet.get('note_tweet')
    if isinstance(note, dict):
        results = note.get('note_tweet_results')
        result = results.get('result') if isinstance(results, dict) else None
        text = result.get('text') if isinstance(result, dict) else None
        if isinstance(text, str) and text:
            return text, 2
    legacy = tweet.get('legacy')
    text = legacy.get('full_text') if isinstance(legacy, dict) else None
    return (text, 1) if isinstance(text, str) and text else (None, 0)


def accept(record, context, delivery):
    if record.get('record_version') != 1 or type(record.get('record_version')) is not int:
        raise ValueError('Expected Core capture record v1')
    url = urlsplit(record.get('url', ''))
    if url.scheme != 'https' or url.netloc != 'x.com' or not url.path.startswith('/i/api/graphql/'):
        return 'ignored'
    if record.get('status') != 200 or not record.get('body_kept') or not isinstance(record.get('body'), str):
        return 'unavailable'
    if len(record['body'].encode('utf-8')) > MAX_BODY:
        return 'over_limit'
    body = json.loads(record['body'])
    found = []
    for tweet in tweets(body):
        post_id = tweet['rest_id']
        text, quality = tweet_text(tweet)
        if post_id.isdigit() and text:
            found.append((post_id, text, quality))
    if not delivery or not isinstance(record.get('record_id'), str):
        raise ValueError('Core delivery and record IDs required')
    observed = record.get('ts')
    if type(observed) not in (int, float):
        raise ValueError('Capture timestamp required')
    output = Path(context['output_dir'])
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    with sqlite3.connect(output / 'posts.sqlite3') as db:
        db.executescript(SCHEMA)
        with db:
            inserted = db.execute('INSERT OR IGNORE INTO deliveries VALUES(?,?,?)',
                                  (delivery, record['record_id'], observed)).rowcount
            if not inserted:
                return 'duplicate'
            for post_id, text, quality in found:
                db.execute('''INSERT INTO posts VALUES(?,?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET text=excluded.text, quality=excluded.quality,
                    observed_at=excluded.observed_at, record_id=excluded.record_id
                  WHERE excluded.quality > posts.quality OR
                    (excluded.quality = posts.quality AND excluded.observed_at >= posts.observed_at)''',
                  (post_id, text, quality, observed, record['record_id']))
    return 'indexed' if found else 'empty'


def main():
    context = json.loads(os.environ['TAP_PACK_CONTEXT'])
    delivery = os.environ.get('TAP_READER_DELIVERY_ID')
    for line in sys.stdin:
        print(json.dumps({'state':accept(json.loads(line), context, delivery)}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error':type(error).__name__}), file=sys.stderr)
        sys.exit(1)
