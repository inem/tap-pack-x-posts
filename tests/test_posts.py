import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handler import lookup, post_id, save_bookmark
from reader import accept


class PostsTest(unittest.TestCase):
    def record(self, body, ts=1):
        return {'record_version':1,'record_id':'record-' + str(ts),'ts':ts,
                'url':'https://x.com/i/api/graphql/hash/HomeTimeline','status':200,
                'body_kept':True,'body':json.dumps(body)}

    def test_indexes_note_text_and_answers_by_canonical_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'data/readers/x.posts'
            context = {'output_dir':str(output)}
            body = {'data':{'result':{'__typename':'Tweet','rest_id':'42',
                    'legacy':{'full_text':'short'},'note_tweet':{'note_tweet_results':
                    {'result':{'text':'complete note text'}}}}}}
            self.assertEqual(accept(self.record(body), context, 'delivery-1'), 'indexed')
            self.assertEqual(lookup(temporary, 'https://x.com/alice/status/42'), 'complete note text')

    def test_lower_quality_observation_does_not_replace_note(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'data/readers/x.posts'
            context = {'output_dir':str(output)}
            note = {'__typename':'Tweet','rest_id':'42','note_tweet':{'note_tweet_results':
                    {'result':{'text':'complete'}}}}
            legacy = {'__typename':'Tweet','rest_id':'42','legacy':{'full_text':'later excerpt'}}
            accept(self.record(note, 1), context, 'delivery-1')
            accept(self.record(legacy, 2), context, 'delivery-2')
            self.assertEqual(lookup(temporary, 'https://x.com/a/status/42'), 'complete')

    def test_delivery_is_idempotent_and_foreign_records_are_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = {'output_dir':str(Path(temporary) / 'data/readers/x.posts')}
            body = {'__typename':'Tweet','rest_id':'42','legacy':{'full_text':'text'}}
            record = self.record(body)
            self.assertEqual(accept(record, context, 'same'), 'indexed')
            self.assertEqual(accept(record, context, 'same'), 'duplicate')
            record['url'] = 'https://example.com/i/api/graphql/hash/HomeTimeline'
            self.assertEqual(accept(record, context, 'foreign'), 'ignored')
            self.assertIsNone(post_id('https://example.com/a/status/42'))

    def test_saves_bookmark_markdown_and_rebuilds_local_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = save_bookmark(temporary, 'https://x.com/alice/status/42?tracking=1',
                                   'Complete post\n\nhttps://x.com/alice/status/42')
            self.assertEqual(result, {'saved':True, 'path':'readable/bookmarks/42.md'})
            root = Path(temporary) / 'data/readers/x.posts/readable'
            self.assertEqual((root / 'bookmarks/42.md').read_text(),
                             'Complete post\n\nhttps://x.com/alice/status/42\n')
            self.assertIn('(bookmarks/42.md)', (root / 'README.md').read_text())
            self.assertFalse(save_bookmark(temporary, 'https://x.com/alice/status/42',
                                           'Complete post\n\nhttps://x.com/alice/status/42')['saved'])

    def test_saved_bookmark_can_fall_back_to_captured_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'data/readers/x.posts'
            body = {'__typename':'Tweet','rest_id':'42','legacy':{'full_text':'captured text'}}
            accept(self.record(body), {'output_dir':str(output)}, 'delivery-1')
            save_bookmark(temporary, 'https://x.com/alice/status/42')
            self.assertEqual((output / 'readable/bookmarks/42.md').read_text(),
                             'captured text\n\nhttps://x.com/alice/status/42\n')


if __name__ == '__main__':
    unittest.main()
