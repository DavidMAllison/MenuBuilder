# Spec: sniff image magic bytes instead of trusting the file extension

**Where**: `/Users/davidallison/projects/personal/GroceryAgent/receipt_parser.py`
**Why**: Leftover item from `architecture_review_and_fix_plan.md` Phase 4.6, split out of
the sms-assistant hygiene pass since this file lives in a separate project
(`groceryagent_bridge.py` on the sms-assistant side just calls into it as a subprocess —
not touched by this spec). The other half of 4.6 ("strip to first `{...}` block before
parsing") is already implemented at lines 113–120 — doc was stale on that part, nothing
to do there.

**The actual bug**: `parse_receipt()` picks the `media_type` sent to Claude Vision purely
from the file extension:
```python
media_type = MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")
```
If a file is misnamed (e.g. a PNG saved with a `.jpg` extension — common if it passed
through an image tool, or a texted photo gets an arbitrary temp filename before Keanu's
image-handling code writes it to disk), this sends the wrong `media_type` label paired
with the real bytes, which the Vision API may reject or silently mis-decode.

## Fix — content-based sniffing with extension as fallback

Add a `_sniff_media_type()` helper that reads the first few bytes of the file and
matches known image signatures, falling back to the current extension-based lookup only
when the bytes don't match anything recognized (e.g. a corrupt file, or a real format
not in the signature list — same safety net as today, not a regression).

Insert after the `MEDIA_TYPES` dict (after line 40):
```python
def _sniff_media_type(path: Path) -> str:
    """Determine image media type from magic bytes, falling back to the file
    extension only if content sniffing is inconclusive. Guards against a
    mislabeled/renamed file being sent to Claude Vision with the wrong media_type."""
    try:
        header = path.read_bytes()[:12]
    except OSError:
        header = b""

    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if header[4:8] == b"ftyp" and header[8:12] in (b"heic", b"heix", b"mif1", b"heim", b"heis"):
        return "image/jpeg"  # HEIC container -- see note below, this label is a known lie

    # Content didn't match a known signature -- fall back to extension (today's behavior)
    return MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")
```

In `parse_receipt()`, replace:
```python
    media_type = MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")
```
with:
```python
    media_type = _sniff_media_type(path)
```

No other changes needed — `MEDIA_TYPES` stays as-is, still used as the fallback.

## Flag while you're in there: the HEIC line is already broken, separately

`MEDIA_TYPES[".heic"] = "image/jpeg"` — this labels HEIC files as JPEG, but a real HEIC
file's bytes are **not** JPEG-encoded; relabeling the media_type doesn't transcode the
image. The Anthropic Vision API's `media_type` field only accepts
`image/jpeg|png|gif|webp` — there's no valid label for actual HEIC bytes, so a genuine
HEIC receipt photo would either fail to decode or silently produce garbage OCR right
now, sniffed or not. My new `_sniff_media_type()` preserves this same (broken) behavior
for HEIC rather than fixing it, since a real fix needs actual image transcoding (e.g.
via `sips` — it's already on macOS — or Pillow with HEIF support), which is a bigger
change than "pick the right label."

Whether this matters in practice depends on whether photos texted through iMessage
arrive as HEIC or get auto-converted to JPEG before Keanu's image-handling code writes
them to disk — worth checking that path before deciding if this is worth fixing now or
can wait. Not attempting the transcoding fix here — flagging so it doesn't get lost.

## Verify

```bash
cd /Users/davidallison/projects/personal/GroceryAgent
python3 test_receipt_parser.py <path to a real receipt photo>
```
Confirm the parsed JSON still comes back correctly (same as before this change) —
`_sniff_media_type()` should return the same result as the old extension-based lookup
for any correctly-named file, so no behavior change is expected for normal receipts.
