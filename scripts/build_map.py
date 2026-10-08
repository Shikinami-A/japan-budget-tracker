"""Convert reviewed Geolonia polygon geometry from an excluded local cache.

No network calls. Run after downloading the pinned map-full.svg described in
public/map/README.md to .cache/map/map-full.svg. Budget builds do not need it.
"""
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA256 = '3b4b9aef5c6282675dc8f04fc1002af13c09e2feec7c1d130cb4038856c64497'


def build():
    raw = (ROOT / '.cache/map/map-full.svg').read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('Map source differs from the reviewed original')
    root = ET.fromstring(raw)
    ns = {'s': 'http://www.w3.org/2000/svg'}
    prefectures = []
    for group in root.findall('.//s:g[@data-code]', ns):
        code = group.attrib['data-code'].zfill(2)
        title = group.find('s:title', ns).text.split(' / ')[0]
        name = title if code == '01' else title + ('都' if code == '13' else '府' if code in ('26', '27') else '県')
        # All prefecture polygons use translations under the same two matrices.
        transform = group.attrib.get('transform', 'translate(0, 0)')
        match = re.fullmatch(r'translate\(([-\d.]+),\s*([-\d.]+)\)', transform)
        if not match:
            raise ValueError('Unexpected transform')
        dx, dy = map(float, match.groups())
        paths = []
        for polygon in group.findall('s:polygon', ns):
            numbers = [float(n) for n in polygon.attrib['points'].split()]
            points = [(numbers[i], numbers[i + 1])
                      for i in range(0, len(numbers), 2)]
            paths.append('M' + 'L'.join(f'{x:.2f},{y:.2f}' for x, y in points) + 'Z')
        for path in group.findall('s:path', ns):
            d = path.attrib['d']
            if re.search(r'[^MLZ\d.,\s-]', d):
                raise ValueError('Unexpected path command')
            paths.append(d)
        if not paths:
            raise ValueError('Empty geometry')
        matrix = f'matrix(1.028807 0 0 1.028807 {(dx + 6) * 1.028807 - 47.544239:.6f} {(dy + 18) * 1.028807 - 28.806583:.6f})'
        prefectures.append({'code': code, 'name': name, 'path': ''.join(paths), 'transform': matrix})
    prefectures.sort(key=lambda p: p['code'])
    assert [p['code'] for p in prefectures] == [f'{i:02}' for i in range(1, 48)]
    assert len({p['name'] for p in prefectures}) == 47
    output = {'viewBox': '0 0 1000 1000', 'prefectures': prefectures}
    (ROOT / 'public/map/japan.json').write_text(json.dumps(output, ensure_ascii=False, separators=(',', ':')) + '\n')


if __name__ == '__main__':
    build()
