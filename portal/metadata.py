"""Read NexusPHP title metadata once per search, using the existing indexer cookie."""
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from integration import Client


class Titles(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.items, self.current = [], {}, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get('class', '').split()
        parent = self.stack[-1] if self.stack else {}
        root = tag == 'table' and 'torrentname' in classes and self.current is None
        if root:
            self.current = {'id': None, 'subtitle': [], 'labels': [], 'after_break': False}
        label = parent.get('label')
        if self.current is not None and ({'torrent-tag', 'optiontag'} & set(classes)):
            label = []
            self.current['labels'].append(label)
        description = parent.get('description', False) or 'torrent-smalldescr' in classes
        # HDSky places the subtitle in the final span of its title cell.
        if self.current is not None and tag == 'br':
            self.current['after_break'] = True
        if self.current is not None and self.current['after_break'] and tag == 'span' and not classes and parent.get('tag') == 'td':
            description = True
        frame = {'tag': tag, 'root': root, 'description': description, 'label': label,
                 'skip': parent.get('skip', False) or tag in {'script', 'style'}}
        if self.current is not None:
            if tag == 'a':
                match = re.search(r'details\.php\?[^\s"\']*\bid=(\d+)', attrs.get('href', '') + ' ' + attrs.get('onmouseover', ''))
                if match:
                    self.current['id'] = match[1]
            if tag == 'img' and attrs.get('alt') in {'官方', '合集', '中字', 'CMCTV'}:
                self.current['labels'].append([attrs['alt']])
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(frame)

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i]['tag'] == tag), None)
        if index is None:
            return
        closed = self.stack[index:]
        del self.stack[index:]
        if self.current is not None and any(f['root'] for f in closed):
            item = self.current
            if item['id']:
                labels = list(dict.fromkeys(''.join(s).strip() for s in item['labels'] if ''.join(s).strip()))
                self.items[item['id']] = {'subtitle': ' '.join(item['subtitle']).strip()[:2000], 'labels': labels[:30]}
            self.current = None

    def handle_data(self, data):
        if self.current is None or not self.stack or self.stack[-1]['skip'] or not data.strip():
            return
        frame = self.stack[-1]
        if frame['label'] is not None:
            frame['label'].append(data.strip())
        elif frame['description']:
            self.current['subtitle'].append(data.strip())


def metadata(indexer, query, kind='movie'):
    fields = {f['name']: f.get('value') for f in indexer['fields']}
    definition = fields.get('definitionFile')
    if definition not in {'hdsky', 'springsunday'} or not fields.get('cookie'):
        return {}
    base = fields.get('baseUrl') or indexer['indexerUrls'][0]
    origin = urlsplit(base)
    if origin.scheme != 'https' or not origin.hostname or origin.username or origin.password or origin.query or origin.fragment:
        raise ValueError('Invalid indexer origin')
    params = {'search': query, 'incldead': 0, 'spstate': 2 if fields.get('freeleech') else 0,
              'search_area': 0, 'search_mode': 0, 'notnewword': 1}
    if kind == 'movie':
        params.update({'cat401': 1, 'cat410': 1} if definition == 'hdsky' else {'cat501': 1})
    if definition == 'springsunday':
        params.update(pick=0, sort=fields.get('sort') or 4, type=fields.get('type') or 'desc')
    client = Client(base)
    client.timeout = 20
    client.opener.addheaders = [('Cookie', fields['cookie']), ('User-Agent', 'Mozilla/5.0')]
    parser = Titles()
    # shortcut: one NexusPHP result page; add pagination when source results span additional pages.
    parser.feed(client.request('GET', '/torrents.php?' + urlencode(params)).decode('utf-8', errors='replace'))
    return parser.items


def resource_id(value):
    return parse_qs(urlsplit(value.get('infoUrl') or value.get('guid') or '').query).get('id', [''])[0]
