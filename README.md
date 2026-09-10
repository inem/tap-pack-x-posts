# X Posts

`x.posts` passively indexes complete X post text already present in TAP Core
capture records. It performs no extra requests to X.

The local bridge answers exact post lookups for page packs such as `x.ui`. When
that UI observes a successful native Bookmark action, it can persist the projected
Markdown under:

```text
<profile>/data/readers/x.posts/readable/bookmarks/<post-id>.md
```

`readable/README.md` is rebuilt as a local index. Saving the same content again is
idempotent. Unbookmarking on X deliberately does not delete local material.

Build and validate with the TAP Pack SDK. The pack uses Core's current
`python-jsonl-v1` reader and handler contracts.
