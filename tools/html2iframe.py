#!/usr/bin/env python3
"""Convert Colab code cells that embed an <iframe> through the %%html magic
into cells that use IPython.display.IFrame, keeping #@title / #@markdown /
#@param lines (and ordinary # comments) in place.

A cell like

    #@title 実験: 慣性の法則
    #@markdown note
    %%html
    <iframe width="560" height="315" src="https://www.youtube-nocookie.com/embed/ID?si=xxx"
            title="YouTube video player" frameborder="0" allow="..." allowfullscreen></iframe>

becomes

    #@title 実験: 慣性の法則
    #@markdown note
    from IPython.display import IFrame
    IFrame("https://www.youtube-nocookie.com/embed/ID", 560, 315,
           extras=['allow="accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture"',
                   'referrerpolicy="strict-origin-when-cross-origin"'])

Usage:
    python3 html2iframe.py .                # all *.ipynb under the current dir (recursive)
    python3 html2iframe.py -n .             # dry run: report only, write nothing
    python3 html2iframe.py --html .         # also convert the remaining %%html cells (see below)
    python3 html2iframe.py --keep-si .      # keep YouTube's ?si=... share-tracking parameter
    python3 html2iframe.py --clear-outputs .   # drop the saved outputs of converted cells
    python3 html2iframe.py --keep-outputs .    # leave the saved outputs untouched

Rules:
  * Only code cells containing a %%html line are considered. Lines starting
    with "#" before the HTML (and #@... form lines anywhere) are kept, in
    their original order, with #@title moved to the first line as Colab
    expects; %%html itself is removed.
  * If the HTML consists of <iframe> elements only, each becomes an
    IFrame(...) call. YouTube iframes (youtube.com / youtube-nocookie.com)
    get the fixed extras shown above, which is what the YouTube embedded
    player needs (referrerpolicy avoids "Error 153"). The host and any
    query parameters other than si (e.g. start=28) are kept; HTML entities
    such as &amp; in the URL are decoded; tabs/newlines inside a URL are
    removed (browsers ignore them, Python strings would not). Other
    iframes (e.g. PhET) keep their own attributes as extras, except
    src/width/height/frameborder/allowfullscreen, which the IFrame template
    already provides. Width/height come from the tag (integers when
    numeric); YouTube iframes without them default to 560 x 315, other
    iframes are skipped. Several iframes become one display(IFrame(...))
    per iframe.
  * Any other %%html content (<svg>, <img>, <video>, an iframe wrapped in
    <div>s, ...) is left unchanged and reported, unless --html is given:
    then it is wrapped verbatim as HTML(r\"\"\"...\"\"\") so the cell no longer
    needs the magic, and any YouTube iframe inside it gets referrerpolicy.
  * Saved outputs of converted cells are regenerated from the new code
    (the HTML that IPython 7.34, as used by Colab, would produce), so what
    the notebook shows before the cell is run matches the code and shows
    no stale "Error 153". --clear-outputs drops them instead;
    --keep-outputs leaves them as they are.
  * The notebook JSON is re-serialized with the original file's indentation,
    so a Colab- or Jupyter-saved notebook keeps its formatting (a note is
    printed if it would not). Cell metadata (e.g. cellView: "form") is
    untouched. Directories named .git and .ipynb_checkpoints are skipped.
Exit status is 0 on success, 1 if any file could not be processed.
"""
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

YOUTUBE_EXTRAS = [
    'allow="accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture"',
    'referrerpolicy="strict-origin-when-cross-origin"',
]
YOUTUBE_HOSTS = ('youtube.com', 'youtube-nocookie.com')
TEMPLATE_ATTRS = {'src', 'width', 'height', 'frameborder', 'allowfullscreen'}
SKIP_DIRS = {'.git', '.ipynb_checkpoints', 'node_modules'}

# IPython 7.34 (Colab) IFrame template, used to regenerate saved outputs
IFRAME_HTML = ('\n        <iframe\n            width="{width}"\n            height="{height}"\n'
               '            src="{src}"\n            frameborder="0"\n            allowfullscreen\n'
               '            {extras}\n        ></iframe>\n        ')
IFRAME_PLAIN = '<IPython.lib.display.IFrame at 0x7f0000000000>'
HTML_PLAIN = '<IPython.core.display.HTML object>'

IFRAME = re.compile(r'<iframe\b(?P<attrs>[^>]*)>\s*</iframe\s*>', re.IGNORECASE)
YT_IFRAME = re.compile(r'<iframe\b[^>]*youtube(?:-nocookie)?\.com/embed/[^>]*>', re.IGNORECASE)
ATTR = re.compile(r'''([A-Za-z_][\w-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?''')
HTML_MAGIC = re.compile(r'^\s*%%html\b')


def parse_attrs(text):
    """Ordered dict attribute -> value (None for boolean attributes); first occurrence wins."""
    attrs = {}
    for m in ATTR.finditer(text):
        name = m.group(1).lower()
        value = next((g for g in m.groups()[1:] if g is not None), None)
        if value is not None:
            value = html.unescape(value)
            # browsers drop tabs/newlines inside URLs and ignore line breaks in attribute values
            value = re.sub(r'[\t\r\n]', '', value).strip() if name == 'src' else ' '.join(value.split())
        attrs.setdefault(name, value)
    return attrs


def is_youtube(url):
    host = (urlsplit(url).hostname or '').lower()
    return any(host == h or host.endswith('.' + h) for h in YOUTUBE_HOSTS)


def strip_si(url):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != 'si']
    return urlunsplit(parts._replace(query=urlencode(query)))


def size(value):
    if value is None:
        return None
    return int(value) if value.isdigit() else value


def py_str(s):
    """A double-quoted single-line Python literal."""
    if re.search(r'[\x00-\x1f]', s):
        raise ValueError(f'control character in {s!r}')
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def parse_iframe(attrs, keep_si):
    """Describe one iframe as {url, width, height, extras, youtube}, or raise ValueError."""
    if attrs.get('src') is None:
        raise ValueError('iframe without src')
    url = attrs['src']
    youtube = is_youtube(url)
    if youtube and not keep_si:
        url = strip_si(url)
    width, height = size(attrs.get('width')), size(attrs.get('height'))
    if width is None or height is None:
        if youtube:
            width, height = width or 560, height or 315
        else:
            raise ValueError('iframe without width/height')
    if youtube:
        extras = list(YOUTUBE_EXTRAS)
    else:
        extras = [name if value is None else f'{name}="{value}"'
                  for name, value in attrs.items() if name not in TEMPLATE_ATTRS]
    py_str(url)  # fail early on unprintable URLs
    return {'url': url, 'width': width, 'height': height, 'extras': extras, 'youtube': youtube}


def iframe_code(e):
    w = e['width'] if isinstance(e['width'], int) else py_str(e['width'])
    h = e['height'] if isinstance(e['height'], int) else py_str(e['height'])
    head = f'IFrame({py_str(e["url"])}, {w}, {h}'
    if not e['extras']:
        return head + ')'
    pad = ' ' * len('IFrame(')
    items = ',\n'.join((pad + ' ' * len('extras=[') if i else '') + repr(x) for i, x in enumerate(e['extras']))
    return f'{head},\n{pad}extras=[{items}])'


def iframe_html(e):
    return IFRAME_HTML.format(width=e['width'], height=e['height'], src=e['url'], extras=' '.join(e['extras']))


def html_literal(body):
    """body as a raw triple-quoted Python string literal, or raise ValueError."""
    for q in ('"""', "'''"):
        if q not in body:
            if body.endswith('\\'):
                body += '\n'
            return 'r' + q + body + q
    raise ValueError('HTML contains both \"\"\" and \'\'\'')


def convert_cell(src, keep_si, html_mode):
    """Return (new_source, outputs, info) or (None, None, reason).
    outputs is a list of (kind, html) describing what the new code displays."""
    lines = src.split('\n')
    if not any(HTML_MAGIC.match(l) for l in lines):
        return None, None, None  # not a %%html cell
    head, body, in_html = [], [], False
    for line in lines:
        if HTML_MAGIC.match(line):
            continue
        s = line.strip()
        if s.startswith('#@') or (s.startswith('#') and not in_html):
            head.append(s)
        else:
            if s:
                in_html = True
            body.append(line)
    html_text = '\n'.join(body).strip('\n')
    titles = [c for c in head if c.startswith('#@title')]
    out = titles + [c for c in head if not c.startswith('#@title')]
    tags = list(IFRAME.finditer(html_text))
    rest = IFRAME.sub('', html_text).replace('"', '').strip()

    if tags and not rest:  # pure iframe cell -> IFrame(...)
        embeds = []
        for t in tags:
            try:
                embeds.append(parse_iframe(parse_attrs(t.group('attrs')), keep_si))
            except ValueError as e:
                return None, None, f'{e} (left as %%html)'
        if len(embeds) == 1:
            out += ['from IPython.display import IFrame', iframe_code(embeds[0])]
        else:
            out.append('from IPython.display import IFrame, display')
            pad = ' ' * len('display(')
            out += ['display(' + iframe_code(e).replace('\n', '\n' + pad) + ')' for e in embeds]
        outputs = [('iframe', iframe_html(e)) for e in embeds]
        info = f'{len(embeds)} iframe(s): ' + ', '.join(
            'youtube' if e['youtube'] else (urlsplit(e['url']).hostname or '?') for e in embeds)
    elif html_mode and html_text:  # anything else -> HTML(r"""...""")
        patched = YT_IFRAME.sub(lambda m: m.group(0) if 'referrerpolicy' in m.group(0).lower()
                                else m.group(0)[:-1].rstrip() + ' referrerpolicy="strict-origin-when-cross-origin">',
                                html_text)
        try:
            literal = html_literal(patched)
        except ValueError as e:
            return None, None, f'{e} (left as %%html)'
        out += ['from IPython.display import HTML', 'HTML(' + literal + ')']
        outputs = [('html', patched)]
        what = re.match(r'<\w+', html_text.lstrip())
        info = 'HTML(...) verbatim' + (f' ({what.group(0)}...)' if what else '')
    elif tags:
        return None, None, f'other HTML besides <iframe> (left as %%html): {rest[:40]!r}'
    else:
        return None, None, 'no <iframe> (left as %%html)'

    new = '\n'.join(out)
    if src.endswith('\n'):
        new += '\n'
    return new, outputs, info


def lines_of(text):
    return text.splitlines(keepends=True)


def build_outputs(outputs, execution_count):
    """Cell outputs in Colab's layout, as IPython 7.34 would have saved them."""
    if len(outputs) == 1:  # last expression -> execute_result
        kind, h = outputs[0]
        plain = HTML_PLAIN if kind == 'html' else IFRAME_PLAIN
        return [{'output_type': 'execute_result',
                 'data': {'text/plain': [plain], 'text/html': lines_of(h)},
                 'metadata': {}, 'execution_count': execution_count}]
    return [{'output_type': 'display_data',
             'data': {'text/html': lines_of(h), 'text/plain': [IFRAME_PLAIN]},
             'metadata': {}} for _, h in outputs]


def dumps_like(obj, original):
    """Serialize obj as JSON using the indentation and trailing newline of the original text."""
    m = re.search(r'\n( +)"', original)
    indent = len(m.group(1)) if m else 2
    out = json.dumps(obj, ensure_ascii=False, indent=indent)
    return out + ('\n' if original.endswith('\n') else '')


def process(path, opts):
    """Convert one notebook. Returns (n_converted, n_skipped)."""
    text = path.read_text(encoding='utf-8')
    nb = json.loads(text)
    converted = skipped = 0
    for i, cell in enumerate(nb.get('cells', [])):
        if cell.get('cell_type') != 'code':
            continue
        source = cell.get('source', '')
        src = ''.join(source) if isinstance(source, list) else source
        new, outputs, info = convert_cell(src, opts['keep_si'], opts['html'])
        if new is None:
            if info:
                skipped += 1
                print(f'{path} cell {i}: SKIP {info}')
            continue
        cell['source'] = lines_of(new) if isinstance(source, list) else new
        if opts['outputs'] == 'clear':
            cell['outputs'] = []
            cell['execution_count'] = None
        elif opts['outputs'] == 'regenerate':
            cell['outputs'] = build_outputs(outputs, cell.get('execution_count'))
        converted += 1
        title = next((l.strip() for l in src.split('\n') if l.lstrip().startswith('#@title')), '')
        print(f'{path} cell {i}: {info}   {title[:60]}')
    if converted:
        if dumps_like(json.loads(text), text) != text:
            print(f'{path}: note: JSON formatting will be normalized', file=sys.stderr)
        out = dumps_like(nb, text)
        json.loads(out)  # must still be valid JSON
        if not opts['dry_run']:
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.write(out)
    return converted, skipped


def collect(args):
    for a in args:
        p = Path(a)
        if p.is_dir():
            for f in sorted(p.rglob('*.ipynb')):
                if not (SKIP_DIRS & set(f.parts)):
                    yield f
        elif p.is_file():
            yield p
        else:
            print(f'{p}: not found, skipped', file=sys.stderr)


def main(argv):
    flags = {'-n', '--dry-run', '--keep-si', '--html', '--clear-outputs', '--keep-outputs'}
    opts = {'dry_run': '-n' in argv or '--dry-run' in argv,
            'keep_si': '--keep-si' in argv,
            'html': '--html' in argv,
            'outputs': 'clear' if '--clear-outputs' in argv else 'keep' if '--keep-outputs' in argv else 'regenerate'}
    targets = [a for a in argv if a not in flags]
    if not targets:
        print(__doc__)
        return 1
    files = cells = skipped = failed = 0
    for p in collect(targets):
        try:
            c, s = process(p, opts)
        except Exception as e:  # unreadable / invalid notebook
            print(f'{p}: FAILED ({e})', file=sys.stderr)
            failed += 1
            continue
        files += bool(c)
        cells += c
        skipped += s
    verb = 'would be converted' if opts['dry_run'] else 'converted'
    print(f'Summary: {cells} cell(s) in {files} file(s) {verb}, {skipped} %%html cell(s) left unchanged, {failed} file(s) failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

