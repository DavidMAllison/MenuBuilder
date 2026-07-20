#!/usr/bin/env python3
"""
convert_recipes_to_md.py -- Convert recipe PDFs to Markdown.

Usage:
  python3 convert_recipes_to_md.py file1.pdf file2.pdf ...
  python3 convert_recipes_to_md.py --dry-run file1.pdf   # print MD, don't write
  python3 convert_recipes_to_md.py --all                 # convert all PDFs in recipes dir

After conversion: updates recipe_metadata.json filename field and deletes the PDF.
"""

import os
import re
import json
import sys
import argparse
import pypdf

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
with open(_CONFIG_PATH) as _f:
    _CONFIG = json.load(_f)

RECIPES_DIR = os.path.expanduser("~/Dropbox/LLMContext/cooking/recipes")
METADATA_PATH = os.path.expanduser(_CONFIG['metadata_path'])

FRACTION_CHARS = '¼½¾⅓⅔⅛⅜⅝⅞'
# Fraction followed by a space = quantity (e.g. "½ cup"); fraction+dash = size spec continuation (e.g. "½-inch")
QUANTITY_RE = re.compile(rf'^(\d|[{FRACTION_CHARS}]\s)')


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def extract_text(pdf_path):
    r = pypdf.PdfReader(pdf_path)
    return "\n".join(p.extract_text() or "" for p in r.pages)


def strip_footer(text):
    """Remove RECIPE METADATA block and URL/page-break lines anywhere in text."""
    # Strip === metadata block
    m = re.search(r'={20,}', text)
    if m:
        text = text[:m.start()].strip()
    # Strip "RECIPE METADATA" or "Metadata" header and everything after
    m2 = re.search(r'^(RECIPE METADATA|Metadata\s+Source:)', text, re.MULTILINE)
    if m2:
        text = text[:m2.start()].strip()
    # Strip URL lines and page indicator lines (e.g. "1/2", "2/2") from anywhere
    lines = text.splitlines()
    cleaned = []
    for l in lines:
        s = l.strip()
        if re.match(r'https?://', s):
            continue
        if re.match(r'^\d+/\d+/\d+,\s', s):  # "2/1/26, 12:02 PM ..."
            continue
        if re.match(r'^\d+/\d+$', s):  # page indicator like "1/2"
            continue
        cleaned.append(l)
    return '\n'.join(cleaned).strip()


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def detect_format(text):
    lines = text.splitlines()
    first300 = text[:400]
    # Type 3: has "INGREDIENTS" alone on a line + dash-prefixed ingredients
    if re.search(r'^INGREDIENTS\s*$', text, re.MULTILINE) and re.search(r'^- ', text, re.MULTILINE):
        return 'type3'
    # Type 2: has "Source:" in header area
    if re.search(r'^Source:', first300, re.MULTILINE):
        return 'type2'
    # Type 1: ATK web scrape (has "Published on" or "Time X" without colon)
    if 'Published on' in first300 or re.search(r'^Time\s+\S', first300, re.MULTILINE):
        return 'type1'
    return 'type2'


# ---------------------------------------------------------------------------
# Line-joining for word-wrapped PDFs
# ---------------------------------------------------------------------------

def is_new_ingredient(line):
    s = line.strip()
    if not s:
        return False
    if s.startswith('- '):  # already dash-prefixed (Type 3 format)
        return True
    if QUANTITY_RE.match(s):
        return True
    if s.isupper() and len(s) > 2:  # ALL CAPS subsection header
        return True
    # Words that commonly start ingredients without a quantity
    if re.match(r'^(Kosher|Salt|Pepper|Fresh|Dried|Ground|Pinch|Dash|Grated|Lime|Lemon)', s):
        return True
    return False


def join_ingredient_lines(raw_lines):
    """Join word-wrapped ingredient lines."""
    result = []
    for line in raw_lines:
        s = line.strip()
        if not s:
            continue
        if result and not is_new_ingredient(s):
            result[-1] = result[-1] + ' ' + s
        else:
            result.append(s)
    return result


def join_instruction_lines(raw_lines):
    """Join word-wrapped instruction lines; each step starts with digit.
    Returns (instructions_list, notes_list) where notes captures trailing non-step lines."""
    instructions = []
    notes = []
    in_notes = False
    for line in raw_lines:
        s = line.strip()
        if not s:
            continue
        if re.match(r'^\d+[\.\)]\s', s):
            in_notes = False
            instructions.append(s)
        elif re.match(r'^(Kids|Note|Notes|Tip|Tips|Serving|Serve):', s):
            in_notes = True
            notes.append(s)
        elif in_notes:
            notes[-1] = notes[-1] + ' ' + s if notes else notes.append(s)
        elif instructions:
            instructions[-1] = instructions[-1] + ' ' + s
        else:
            instructions.append(s)
    return instructions, notes


def join_prose_lines(raw_lines):
    """Join word-wrapped prose (Before You Begin, Notes) into paragraphs.
    Lines starting with '- ' are kept as separate items."""
    result = []
    for line in raw_lines:
        s = line.strip()
        if not s:
            if result:
                result.append('')
        elif s.startswith('- '):
            result.append(s)
        elif result and result[-1] and not result[-1].startswith('- '):
            result[-1] = result[-1] + ' ' + s
        else:
            result.append(s)
    # Remove trailing empty strings
    while result and not result[-1]:
        result.pop()
    return result


# ---------------------------------------------------------------------------
# Format-specific parsers
# ---------------------------------------------------------------------------

def parse_type1(lines):
    """ATK web scrape: 'Time X', 'Yield X', word-wrapped ingredients."""
    lines = [l for l in lines if not re.match(r'Published on ', l.strip())]
    lines = [l for l in lines if not l.strip().startswith('http')]
    lines = [l for l in lines if not re.match(r'\d+/\d+/\d+,', l.strip())]

    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1

    # Title: lines until "By " / "Time" / "Yield" / "Ingredients"
    title_parts = []
    while i < len(lines):
        s = lines[i].strip()
        if re.match(r'^By\s', s) or re.match(r'^Time\b', s) or re.match(r'^Yield\b', s) or s == 'Ingredients':
            break
        title_parts.append(s)
        i += 1
    title = ' '.join(title_parts)

    author = source = time_str = yield_str = ''
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith('By '):
            author = s[3:]
        elif re.match(r'^Time\b', s):
            time_str = s[4:].strip()
        elif re.match(r'^Yield\b', s):
            yield_str = s[5:].strip()
        elif s == 'Ingredients':
            break
        i += 1

    return title, author, source, time_str, yield_str, lines[i:]


def parse_type2(lines):
    """Standard format: 'Source:', 'Time:', 'Yield:', indented ingredients."""
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1

    title_parts = []
    while i < len(lines):
        s = lines[i].strip()
        if re.match(r'^(By |Source:|Time:|Yield:|Ingredients)', s):
            break
        title_parts.append(s)
        i += 1
    title = ' '.join(title_parts)

    author = source = time_str = yield_str = ''
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith('By '):
            author = s[3:]
        elif s.startswith('Source:'):
            source = s[7:].strip()
        elif s.startswith('Time:'):
            time_str = s[5:].strip()
        elif s.startswith('Yield:') or s.startswith('Serves:'):
            yield_str = re.split(r':\s*', s, 1)[1].strip()
        elif s == 'Ingredients':
            break
        i += 1

    return title, author, source, time_str, yield_str, lines[i:]


def parse_type3(lines):
    """Custom format: 'INGREDIENTS', dash-prefixed, may have inline time."""
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1

    # Title on first line; may have subtitle after | or on next line
    title_line = lines[i].strip() if i < len(lines) else ''
    i += 1

    # Second line may have subtitle in parens or after |
    subtitle = ''
    if i < len(lines) and lines[i].strip() and not re.match(r'^(Source|Time|Prep|INGR)', lines[i].strip()):
        subtitle = lines[i].strip()
        i += 1

    author = source = time_str = yield_str = ''
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith('Source:'):
            source = s[7:].strip()
        elif re.match(r'^Time:', s):
            time_part = s[5:].strip()
            # Extract just the time before any | separator
            time_str = re.split(r'\s*\|\s*', time_part)[0].strip()
            # Also extract Servings/Yield if on same line
            if not yield_str:
                m = re.search(r'Servings?:\s*([\w\s\-]+?)(?:\s*\||\s*$)', s, re.IGNORECASE)
                if m:
                    yield_str = m.group(1).strip()
        elif re.match(r'^Prep:', s):
            # "Prep: 20 min  |  Cook: 25 min  |  Total: ~1 hr  |  Serves: 4-6"
            time_str = re.sub(r'\|.*', '', s).replace('Prep:', '').strip()
            m = re.search(r'Total:\s*([\w~\s]+?)(?:\s*\||\s*$)', s)
            if m:
                time_str = m.group(1).strip()
            m2 = re.search(r'Serves?:\s*([\w\s-]+?)(?:\s*\||\s*$)', s)
            if m2:
                yield_str = m2.group(1).strip()
        elif re.match(r'^Servings?:', s, re.IGNORECASE):
            yield_str = re.split(r':\s*', s, 1)[1].strip()
        elif s == 'INGREDIENTS' or s == 'Ingredients':
            break
        i += 1

    # Parse inline time/health from title line if present
    # e.g. "Time: 25 minutes   |   Servings: 4   |   Health: Moderate"
    if not time_str:
        m = re.search(r'Time:\s*([\w\s]+?)(?:\s*\||\s*$)', title_line)
        if m:
            time_str = m.group(1).strip()
            title_line = title_line[:title_line.find('Time:')].strip().rstrip('|').strip()

    return title_line, author, source, time_str, yield_str, lines[i:]


# ---------------------------------------------------------------------------
# Shared body parser (Ingredients + Instructions + sections)
# ---------------------------------------------------------------------------

SECTION_HEADERS = {
    'Before You Begin', 'Before you begin',
    'Instructions', 'INSTRUCTIONS',
    'Notes', 'NOTES',
    'Ingredients', 'INGREDIENTS',
}


def parse_body(raw_lines, fmt):
    """Parse ingredient/instruction/etc sections from body lines."""
    sections = {}
    current = None
    buf = []

    for line in raw_lines:
        s = line.strip()
        sl = s.lower()

        if sl in ('ingredients', 'before you begin', 'instructions', 'notes'):
            if current is not None:
                sections[current] = buf
            current = sl.replace(' ', '_')
            buf = []
        elif s in ('INGREDIENTS', 'INSTRUCTIONS', 'NOTES'):
            if current is not None:
                sections[current] = buf
            current = s.lower()
            buf = []
        elif s == 'Before You Begin':
            if current is not None:
                sections[current] = buf
            current = 'before_you_begin'
            buf = []
        else:
            if current is not None:
                buf.append(line)

    if current is not None:
        sections[current] = buf

    # Process each section
    result = {}

    ing = sections.get('ingredients', [])
    if fmt == 'type3':
        # Type 3 already has dash-prefixed items; just strip
        ing = [l.strip() for l in ing if l.strip()]
    elif fmt == 'type2':
        ing = [l.strip() for l in ing if l.strip()]
        ing = join_ingredient_lines(ing)
        # Normalize: prefix ingredient lines with '- ' so build_markdown can distinguish
        # items from ALL CAPS subsection headers (which already get bolded)
        normalized = []
        for l in ing:
            if l.isupper() and len(l) > 2:
                normalized.append(l)  # header, no prefix
            elif not l.startswith('- '):
                normalized.append('- ' + l)
            else:
                normalized.append(l)
        ing = normalized
    else:  # type1
        ing = join_ingredient_lines(ing)
        # Same normalization for type1
        normalized = []
        for l in ing:
            if l.isupper() and len(l) > 2:
                normalized.append(l)
            elif not l.startswith('- '):
                normalized.append('- ' + l)
            else:
                normalized.append(l)
        ing = normalized
    result['ingredients'] = ing

    bfr = sections.get('before_you_begin', [])
    result['before_you_begin'] = join_prose_lines(bfr)

    inst = sections.get('instructions', [])
    inst_steps, inst_notes = join_instruction_lines(inst)
    result['instructions'] = inst_steps

    notes = sections.get('notes', [])
    parsed_notes = join_prose_lines(notes)
    # Merge any notes lines captured from the instruction block
    if inst_notes:
        parsed_notes = inst_notes + ([''] if parsed_notes else []) + parsed_notes
    result['notes'] = parsed_notes

    return result


# ---------------------------------------------------------------------------
# Markdown generator
# ---------------------------------------------------------------------------

def build_markdown(title, author, source, time_str, yield_str, body):
    md = [f'# {title}', '']

    meta = []
    if source:
        meta.append(f'Source: {source}')
    elif author:
        meta.append('Source: America\'s Test Kitchen')
    if time_str:
        meta.append(f'Time: {time_str}')
    if yield_str:
        meta.append(f'Yield: {yield_str}')
    md.extend(meta)
    if meta:
        md.append('')

    ings = body.get('ingredients', [])
    if ings:
        md.append('## Ingredients')
        md.append('')
        for l in ings:
            if l.isupper() and len(l) > 2:
                md.append(f'**{l.title()}:**')
            elif l.startswith('**'):
                md.append(l)
            elif l.startswith('- '):
                md.append(l)
            else:
                # No dash prefix = subsection header (Type 3 style)
                if l:
                    md.append(f'**{l}**')
        md.append('')

    bfr = body.get('before_you_begin', [])
    if bfr:
        md.append('## Before You Begin')
        md.append('')
        md.extend(bfr)
        md.append('')

    insts = body.get('instructions', [])
    if insts:
        md.append('## Instructions')
        md.append('')
        for l in insts:
            m = re.match(r'^(\d+)[\.\)]\s+(.*)', l, re.DOTALL)
            if m:
                md.append(f'{m.group(1)}. {m.group(2)}')
            else:
                md.append(l)
        md.append('')

    notes = body.get('notes', [])
    if notes:
        md.append('## Notes')
        md.append('')
        md.extend(notes)
        md.append('')

    return '\n'.join(md).strip() + '\n'


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def convert_pdf(pdf_path, dry_run=False):
    filename = os.path.basename(pdf_path)
    stem = os.path.splitext(filename)[0]
    md_path = os.path.join(os.path.dirname(pdf_path), stem + '.md')

    raw = extract_text(pdf_path)
    raw = strip_footer(raw)
    lines = raw.splitlines()

    fmt = detect_format(raw)

    if fmt == 'type1':
        title, author, source, time_str, yield_str, body_lines = parse_type1(lines)
    elif fmt == 'type2':
        title, author, source, time_str, yield_str, body_lines = parse_type2(lines)
    else:
        title, author, source, time_str, yield_str, body_lines = parse_type3(lines)

    body = parse_body(body_lines, fmt)
    md = build_markdown(title, author, source, time_str, yield_str, body)

    if dry_run:
        print(f'\n{"="*60}\n{filename} (format: {fmt})\n{"="*60}')
        print(md)
        return True

    # Write MD
    with open(md_path, 'w') as f:
        f.write(md)

    # Update metadata
    with open(METADATA_PATH) as f:
        data = json.load(f)

    updated = False
    for key, entry in data['recipes'].items():
        if entry.get('filename') == filename or entry.get('pdf_filename') == filename:
            entry['filename'] = stem + '.md'
            if 'pdf_filename' in entry:
                del entry['pdf_filename']
            updated = True
            break

    if updated:
        with open(METADATA_PATH, 'w') as f:
            json.dump(data, f, indent=2)

    # Delete PDF
    os.remove(pdf_path)

    status = 'metadata updated' if updated else 'WARNING: no metadata entry found'
    print(f'  {filename} -> {stem}.md  [{fmt}] [{status}]')
    return True


def main():
    parser = argparse.ArgumentParser(description='Convert recipe PDFs to Markdown')
    parser.add_argument('files', nargs='*', help='PDF filenames or paths')
    parser.add_argument('--dry-run', action='store_true', help='Print output without writing')
    parser.add_argument('--all', action='store_true', help='Convert all PDFs in recipes dir')
    args = parser.parse_args()

    if args.all:
        pdfs = sorted(f for f in os.listdir(RECIPES_DIR) if f.endswith('.pdf'))
        paths = [os.path.join(RECIPES_DIR, f) for f in pdfs]
    else:
        paths = []
        for f in args.files:
            if os.path.isabs(f):
                paths.append(f)
            elif os.path.exists(f):
                paths.append(f)
            else:
                paths.append(os.path.join(RECIPES_DIR, f))

    if not paths:
        parser.print_help()
        sys.exit(1)

    print(f'Converting {len(paths)} PDF(s)...')
    ok = 0
    for p in paths:
        try:
            convert_pdf(p, dry_run=args.dry_run)
            ok += 1
        except Exception as e:
            print(f'  ERROR {os.path.basename(p)}: {e}')

    if not args.dry_run:
        print(f'\nDone. {ok}/{len(paths)} converted.')


if __name__ == '__main__':
    main()
