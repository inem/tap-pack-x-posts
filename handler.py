"""Return captured X posts and persist user-bookmarked Markdown locally."""
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit


MAX_SUBTITLE_BYTES = 2 * 1024 * 1024

def post_id(value):
    if not isinstance(value, str):
        return None
    url = urlsplit(value)
    if url.scheme != 'https' or url.netloc not in ('x.com', 'www.x.com'):
        return None
    match = re.fullmatch(r'/[^/]+/status/(\d+)', url.path.rstrip('/'))
    return match.group(1) if match else None


def lookup(profile_root, link):
    value = post_id(link)
    if value is None:
        raise ValueError('invalid_post')
    database = Path(profile_root) / 'data/readers/x.posts/posts.sqlite3'
    try:
        with sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT text FROM posts WHERE id=?', (value,)).fetchone()
    except sqlite3.OperationalError as error:
        if 'unable to open' in str(error):
            return None
        raise
    return row[0] if row else None


def subtitle_material(profile_root, value):
    """Resolve native caption artifacts for an observed X post without changing them."""
    root = Path(profile_root) / 'data' / 'readers' / 'x.subtitles'
    database = root / 'index.sqlite3'
    try:
        with sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True) as db:
            rows = db.execute('''
                SELECT ct.track_id, ct.current_sha256, ct.vtt_url
                FROM post_media AS pm
                JOIN media_entities AS me ON me.media_key = pm.media_key
                JOIN caption_tracks AS ct ON ct.media_id = me.media_id
                WHERE pm.post_id = ?
                ORDER BY ct.track_id
            ''', (value,)).fetchall()
    except sqlite3.OperationalError as error:
        if 'unable to open' in str(error):
            return []
        raise

    materials = []
    for track_id, sha256, source_url in rows:
        path = root / 'artifacts' / 'sha256' / f'{sha256}.vtt'
        try:
            if path.stat().st_size > MAX_SUBTITLE_BYTES:
                continue
            vtt = path.read_text(encoding='utf-8')
        except OSError:
            continue
        materials.append({
            'track_id': track_id,
            'address': f'sha256:{sha256}',
            'source_url': source_url,
            'vtt': vtt,
        })
    return materials


def chat_copy(profile_root, link):
    value = post_id(link)
    if value is None:
        raise ValueError('invalid_post')
    post = lookup(profile_root, link)
    if post is None:
        raise LookupError('not_found')

    captions = subtitle_material(profile_root, value)
    if not captions:
        return {'text': post, 'captions': []}

    blocks = [post, '---', '## Captured video subtitles']
    for caption in captions:
        blocks.extend([
            '',
            f'Native VTT · track {caption["track_id"]} · artifact `{caption["address"]}`',
            f'Source: {caption["source_url"]}',
            '',
            '```vtt',
            caption['vtt'].rstrip(),
            '```',
        ])
    return {
        'text': '\n'.join(blocks) + '\n',
        'captions': [{key: caption[key] for key in ('track_id', 'address', 'source_url')} for caption in captions],
    }


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '.tmp-' + str(os.getpid()))
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


def canonical_link(link):
    value = post_id(link)
    if value is None:
        raise ValueError('invalid_post')
    parsed = urlsplit(link)
    handle = parsed.path.strip('/').split('/')[0]
    return value, f'https://x.com/{handle}/status/{value}'


def normalize_markdown(markdown, link):
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError('invalid_markdown')
    if len(markdown.encode('utf-8')) > 256 * 1024:
        raise ValueError('markdown_over_limit')
    text = markdown.replace('\r\n', '\n').replace('\r', '\n').strip()
    return text if text.endswith(link) else text + '\n\n' + link


def rebuild_index(root):
    bookmarks = root / 'bookmarks'
    rows = []
    for path in bookmarks.glob('*.md'):
        try:
            body = path.read_text(encoding='utf-8')
        except OSError:
            continue
        first = next((line.strip().lstrip('#').strip() for line in body.splitlines()
                      if line.strip() and not line.lstrip().startswith('![')), path.stem)
        label = first[:120].replace('[', '\\[').replace(']', '\\]') or path.stem
        rows.append((path.stat().st_mtime_ns, f'- [{label}](bookmarks/{path.name})'))
    rows.sort(reverse=True)
    content = '# Saved X posts\n\n' + ('\n'.join(row for _, row in rows) if rows else '_No saved posts yet._') + '\n'
    atomic_write(root / 'README.md', content)


def save_bookmark(profile_root, link, markdown=None):
    value, canonical = canonical_link(link)
    if markdown is None:
        text = lookup(profile_root, canonical)
        if text is None:
            raise LookupError('not_found')
        markdown = text
    body = normalize_markdown(markdown, canonical) + '\n'
    root = Path(profile_root) / 'data/readers/x.posts/readable'
    destination = root / 'bookmarks' / f'{value}.md'
    previous = None
    try:
        previous = destination.read_text(encoding='utf-8')
    except OSError:
        pass
    if previous != body:
        atomic_write(destination, body)
    rebuild_index(root)
    return {'saved':previous != body, 'path':f'readable/bookmarks/{value}.md'}


def main():
    context = json.loads(os.environ['TAP_PACK_CONTEXT'])
    request = json.loads(sys.stdin.readline())
    args = request.get('args')
    link = args.get('link') if isinstance(args, dict) else None
    action = args.get('action', 'lookup') if isinstance(args, dict) else 'lookup'
    try:
        if action == 'save_bookmark':
            result = {'ok':True, 'value':save_bookmark(
                context['profile_root'], link, args.get('markdown'))}
        elif action == 'chat_copy':
            result = {'ok':True, 'value':chat_copy(context['profile_root'], link)}
        elif action == 'lookup':
            text = lookup(context['profile_root'], link)
            if text is None:
                result = {'ok':False,'error':{'code':'not_found','message':'Post has not been observed with complete text'}}
            else:
                result = {'ok':True,'value':{'text':text}}
        else:
            result = {'ok':False,'error':{'code':'unsupported_action','message':'Unsupported X posts action'}}
    except LookupError:
        result = {'ok':False,'error':{'code':'not_found','message':'Post has not been observed with complete text'}}
    except ValueError as error:
        code = str(error)
        message = 'A canonical X post link is required' if code == 'invalid_post' else 'Valid post Markdown is required'
        result = {'ok':False,'error':{'code':code,'message':message}}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error':type(error).__name__}), file=sys.stderr)
        sys.exit(1)
