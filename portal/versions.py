"""Title-based version requirements, shared by preview and native Sonarr enforcement."""
import re


FIELDS = ('group', 'resolution', 'codec', 'source', 'platform', 'audio', 'subtitles')
NOT_GROUPS = {'dl', 'web', 'rip', 'hdtv', 'bluray', 'h264', 'h265', 'aac', 'dts', 'ddp', 'dd', 'flac', 'nogrp'}
ALIASES = {
    'resolution': {'1080p': r'1080p', '1080i': r'1080i', '4K': r'(?:2160p|4k)', '720p': r'720p'},
    'codec': {'H.264': r'(?:[xh][ ._-]?264|avc)', 'H.265': r'(?:[xh][ ._-]?265|hevc)', 'AV1': r'av1'},
    'source': {'WEB-DL': r'web[ ._-]?dl', 'WEBRip': r'web[ ._-]?rip', 'BDRemux': r'(?:bd[ ._-]?)?remux',
               'BluRay': r'blu[ ._-]?ray', 'BDRip': r'bdrip', 'HDTV': r'hdtv'},
    'platform': {'Netflix': r'(?:nf|nflx|netflix)', 'Amazon': r'(?:amzn|amazon)',
                 'Disney+': r'(?:dsnp|disney\+)', 'AppleTV+': r'(?:atvp|aptv)',
                 'iQIYI': r'(?:iq|iqiyi)', 'Tencent': r'(?:qq|tencent)', 'YOUKU': r'(?:youku|yk)',
                 'Bilibili': r'bilibili', 'B-Global': r'b[ ._-]?global', 'Bahamut': r'baha'},
}


def token(pattern):
    return r'(?<![A-Za-z0-9])(?:' + pattern + r')(?![A-Za-z0-9])'


def validate_profile(record, series, profiles):
    profile = next((p for p in profiles if p['id'] == record['profile_id']), {})
    _, pattern = rule(record['fields'])
    if (record['pattern'] != pattern or record['tag_id'] not in series.get('tags', [])
            or profile.get('name') != 'Cinema Version ' + str(series['tvdbId'])
            or profile.get('enabled') is not True or profile.get('indexerId') != 0
            or profile.get('tags') != [record['tag_id']] or profile.get('required') != [pattern] or profile.get('ignored')):
        raise ValueError('Sonarr 版本规则或专属标签已变更，请重新选择版本并保存，暂不发起下载。')
    return record


def group_pattern(group):
    return r'(?:^\[' + re.escape(group) + r'\]|[-.]' + re.escape(group) + r'(?:\.(?:mkv|mp4|torrent))?$)'


def details(title):
    result = {key: '' for key in FIELDS}
    for field, choices in ALIASES.items():
        result[field] = next((name for name, pattern in choices.items() if re.search(token(pattern), title, re.I)), '')
    match = re.search(r'-([A-Za-z0-9][A-Za-z0-9_@]{1,39})(?:\.(?:mkv|mp4|torrent))?$', title)
    if not match:
        match = re.match(r'^\[([^\[\]]{2,40})\]', title)
    if match and match[1].casefold() not in NOT_GROUPS:
        result['group'] = re.sub(r'\.(?:mkv|mp4|torrent)$', '', match[1], flags=re.I)
    audio = re.search(token(r'(?:DDP|E[ ._-]?AC3|DD|AC3|AAC|FLAC|TrueHD|DTS(?:[ ._-]?HD)?(?:[ ._-]?MA)?)(?:[ ._-]*[1-9][ ._-][01])?(?:[ ._-]*Atmos)?'), title, re.I)
    if audio:
        result['audio'] = re.sub(r'(?<=[A-Za-z])(?=\d)', '.', audio[0])
    count = re.search(token(r'\d{1,2}Audios?|Dual[ ._-]*Audio'), title, re.I)
    if count:
        result['audio'] = ','.join(filter(None, [result['audio'], count[0]]))
    result['subtitles'] = ','.join(dict.fromkeys(m[0] for m in re.finditer(token(r'CHS|CHT|ENGSUB|中字|简繁|简中|繁中'), title, re.I)))
    span = re.search(r'S\d{1,2}(?:[ ._-]*E\d{1,4}(?:-E?\d{1,4}|E\d{1,4})*)?|\d{4}[ ._-]\d{2}[ ._-]\d{2}|\[\d{1,4}(?:[-~]\d{1,4})?\]|第\s*\d+(?:[-~]\d+)?\s*[集期]', title, re.I)
    return result, span[0] if span else '集数待 Sonarr 识别'


def rule(fields):
    if not isinstance(fields, dict) or set(fields) - set(FIELDS):
        raise ValueError('请确认版本条件。')
    values = {}
    for key in FIELDS:
        value = fields.get(key, '')
        if not isinstance(value, str) or len(value) > 80 or any(ord(c) < 32 for c in value) or '/' in value or '\\' in value:
            raise ValueError('请填写有效的版本条件，不接受正则表达式或路径。')
        values[key] = value.strip()
        if values[key] and not re.search(r'[^\W_]', values[key]):
            raise ValueError('请填写包含文字或数字的版本标记。')
    if not values['group']:
        raise ValueError('请补充发布组；无法识别发布组时不能建立同版追更。')
    for key in ('resolution', 'codec'):
        if values[key] and values[key] not in ALIASES[key]:
            raise ValueError('请选择有效的清晰度与编码，或明确选择不限。')
    parts = []
    for key, value in values.items():
        if not value:
            continue
        terms = re.split('[,，]', value) if key in {'audio', 'subtitles'} else [value]
        if len(terms) > 6 or any(not term.strip() for term in terms):
            raise ValueError('请填写 1–6 个音轨／字幕标记，逗号表示必须全部满足。')
        for term in terms:
            term = term.strip()
            if key == 'group':
                pattern = group_pattern(term)
            else:
                literal = r'[ ._-]*'.join(re.escape(p) for p in re.split(r'[ ._-]+', term) if p)
                pattern = token(ALIASES.get(key, {}).get(term, literal))
            parts.append('(?=.*' + pattern + ')')
    # Sonarr ORs required entries. One expression keeps every field mandatory.
    pattern = '^' + ''.join(parts) + '.*$'
    return values, '/' + pattern + '/i'


def matches(pattern, title):
    return bool(re.search(pattern[1:-2], title, re.I))
