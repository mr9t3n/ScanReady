import csv
import hashlib
import pytest
from tools.stage_release_data import StageError, stage_dataset_view

FIELDS = ['image_id','file','source_page_id','split','origin','release_allowed']
def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
def fixture(root):
    image=root/'data/sources/page.png'; image.parent.mkdir(parents=True); image.write_bytes(b'normalized fixture')
    notice=root/'data/raw/sources/NOTICE.md'; notice.parent.mkdir(parents=True); notice.write_bytes(b'exact approved notice\n')
    row=dict(zip(FIELDS,['one','data/sources/page.png','page-one','heldout','scholarly','true']))
    for name in ('manifest.csv','heldout.csv'): write_csv(root/'data'/name,FIELDS,[row])
    write_csv(root/'data/calibration.csv',FIELDS,[])
    write_csv(root/'data/sources.csv',['source_page_id','license_file','license','redistribution_allowed'],[{'source_page_id':'page-one','license_file':'data/raw/sources/NOTICE.md','license':'MIT','redistribution_allowed':'true'}])
    return row

def test_stage_preserves_heldout_bytes_images_notices_and_working_files(tmp_path):
    root=tmp_path/'source'; stage=tmp_path/'stage'; fixture(root)
    before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    hashes=stage_dataset_view(root,stage)
    assert (stage/'data/heldout.csv').read_bytes()==before['data/heldout.csv']
    assert (stage/'data/sources/page.png').read_bytes()==before['data/sources/page.png']
    assert any(p.read_bytes()==b'exact approved notice\n' for p in (stage/'data/licenses').rglob('*.md'))
    assert hashes['heldout.csv']==hashlib.sha256(before['data/heldout.csv']).hexdigest()
    assert {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}==before
    assert list(csv.DictReader((stage/'data/manifest.csv').open()))[0]['source_page_id']=='page-one'

@pytest.mark.parametrize('failure',['missing_image','false_flag','missing_notice','missing_heldout_in_manifest','overlapping_roots'])
def test_stage_rejects_invalid_heldout_before_writing(tmp_path,failure):
    root=tmp_path/'source'; stage=tmp_path/'stage'; row=fixture(root)
    if failure=='missing_image': (root/row['file']).unlink()
    if failure=='false_flag':
        row['release_allowed']='false'
        for name in ('manifest.csv','heldout.csv'): write_csv(root/'data'/name,FIELDS,[row])
    if failure=='missing_notice': (root/'data/raw/sources/NOTICE.md').unlink()
    if failure=='missing_heldout_in_manifest': write_csv(root/'data/manifest.csv',FIELDS,[])
    if failure=='overlapping_roots': stage=root/'staging'
    with pytest.raises(StageError): stage_dataset_view(root,stage)
    assert not stage.exists()

def test_stage_omits_local_and_generated_calibration(tmp_path):
    root=tmp_path/'source'; stage=tmp_path/'stage'; held=fixture(root)
    generated={**held,'image_id':'generated','source_page_id':'calibration-page','split':'calibration','origin':'generated','release_allowed':'false','file':'data/generated/a.png'}
    local={**held,'image_id':'local','source_page_id':'local-page','split':'calibration','release_allowed':'false','file':'data/generated/local-only/b.png'}
    write_csv(root/'data/manifest.csv',FIELDS,[held,generated,local])
    write_csv(root/'data/calibration.csv',FIELDS,[generated,local])
    stage_dataset_view(root,stage)
    assert len(list(csv.DictReader((stage/'data/manifest.csv').open())))==1
    assert list(csv.DictReader((stage/'data/calibration.csv').open()))==[]
    assert not (stage/'data/generated').exists()
