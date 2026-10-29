"""Stage an approved dataset view without changing the working population.

Generated and local-only calibration rows are omitted. Heldout bytes and
membership are preserved exactly; any ineligible heldout row aborts staging.
"""
from __future__ import annotations
import csv
import hashlib
import io
import json
from pathlib import Path

class StageError(ValueError):
    """The requested dataset cannot be staged self-contained."""

def _read(root: Path, name: str):
    path = root / 'data' / name
    if not path.is_file():
        raise StageError(f'Missing dataset CSV: {name}')
    with path.open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        return fields, list(reader)

def _file(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or '\\' in relative:
        raise StageError(f'Unsafe dataset path: {relative}')
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise StageError(f'Symlink dataset path: {relative}')
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise StageError(f'Missing dataset file: {relative}')
    return path

def stage_dataset_view(source_root: Path, staging_root: Path) -> dict[str, str]:
    source_root = source_root.resolve()
    staging_root = staging_root.resolve()
    if source_root == staging_root or staging_root.is_relative_to(source_root) or source_root.is_relative_to(staging_root):
        raise StageError('Source and staging roots must not overlap.')
    if (staging_root / 'data').exists():
        raise StageError('Staging data already exists; use a fresh dataset destination.')
    fields, manifest = _read(source_root, 'manifest.csv')
    calibration_fields, calibration = _read(source_root, 'calibration.csv')
    heldout_fields, heldout = _read(source_root, 'heldout.csv')
    required = {'image_id','file','source_page_id','split','origin','release_allowed'}
    if not required <= set(fields) or calibration_fields != fields or heldout_fields != fields:
        raise StageError('Dataset CSV schemas disagree or lack provenance.')
    if any(row['split'] != 'calibration' for row in calibration) or any(row['split'] != 'heldout' for row in heldout):
        raise StageError('Dataset split CSV contains the wrong split.')
    manifest_ids = [row['image_id'] for row in manifest]
    if len(set(manifest_ids)) != len(manifest_ids):
        raise StageError('Duplicate manifest identity.')
    if any(row['split'] not in {'calibration','heldout'} for row in manifest):
        raise StageError('Unknown manifest split.')
    by_id = {row['image_id']:row for row in manifest}
    split_rows = calibration + heldout
    if len(split_rows) != len(manifest) or len({row['image_id'] for row in split_rows}) != len(split_rows):
        raise StageError('Split membership does not match manifest.')
    if any(by_id.get(row['image_id']) != row for row in split_rows):
        raise StageError('Split rows do not match manifest.')
    if {r['source_page_id'] for r in calibration} & {r['source_page_id'] for r in heldout}:
        raise StageError('Source pages overlap across splits.')
    def approved(row):
        return row['release_allowed'] == 'true' and row['origin'] != 'generated'
    if any(not approved(row) for row in heldout):
        raise StageError('Heldout contains an unapproved or generated row.')
    selected = [row for row in manifest if approved(row)]
    provenance = []
    for name in ('sources.csv','captured_labels.csv'):
        if (source_root / 'data' / name).is_file():
            provenance.extend(_read(source_root,name)[1])
    copies: dict[str, bytes] = {}
    notice_index = []
    for row in selected:
        relative = row['file']
        if not relative.startswith(('data/sources/','data/captured/')) or Path(relative).suffix.lower() not in {'.png','.jpg','.jpeg'}:
            raise StageError(f'Normalized dataset path is not releasable: {relative}')
        copies[relative] = _file(source_root,relative).read_bytes()
        entries = [entry for entry in provenance if entry.get('source_page_id') == row['source_page_id']]
        if not entries or any(entry.get('redistribution_allowed') != 'true' or not entry.get('license') or not entry.get('license_file') for entry in entries):
            raise StageError(f'Missing approved license provenance: {row["source_page_id"]}')
        for entry in entries:
            notice = _file(source_root,entry['license_file']).read_bytes()
            digest = hashlib.sha256(notice).hexdigest()
            suffix = Path(entry['license_file']).suffix.lower()
            if suffix not in {'.md','.txt'}:
                raise StageError('Retained notice must be Markdown or text.')
            destination = f'data/licenses/{digest}{suffix}'
            copies[destination] = notice
            item = {'source_page_id':row['source_page_id'], 'license':entry['license'], 'file':destination, 'sha256':digest}
            if item not in notice_index:
                notice_index.append(item)
    for name, rows in (('manifest.csv',selected),('calibration.csv',[r for r in calibration if approved(r)])):
        buffer=io.StringIO(newline='')
        writer=csv.DictWriter(buffer,fieldnames=fields,lineterminator='\n')
        writer.writeheader(); writer.writerows(rows)
        copies['data/'+name] = buffer.getvalue().encode('utf-8')
    copies['data/heldout.csv'] = (source_root/'data/heldout.csv').read_bytes()
    copies['data/licenses/notices.json'] = (json.dumps({'notices':notice_index,'omitted_calibration':'generated and local-only rows'},indent=2)+'\n').encode('utf-8')
    # Validate everything before the first write.
    for relative, content in copies.items():
        target = staging_root / relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(content)
    return {name:hashlib.sha256(copies['data/'+name]).hexdigest() for name in ('manifest.csv','calibration.csv','heldout.csv')}
