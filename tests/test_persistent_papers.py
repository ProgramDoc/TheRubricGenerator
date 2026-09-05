"""Regression coverage for the first live benchmark's lost-PDF failure."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backend import paper_files, storage
from tests.test_synthesis_api import mock_llm


def test_configured_upload_root_survives_process_workdir_change(tmp_path):
    persistent = tmp_path / "disk" / "uploads"
    module = Path(storage.__file__).resolve()
    env = {**os.environ, "LOCAL_UPLOAD_DIR": str(persistent), "AWS_S3_BUCKET": ""}
    script = ("import runpy; s=runpy.run_path(" + repr(str(module)) + "); "
              "print(s['upload_file'](b'%PDF retained', 'paper.pdf'))")
    path = subprocess.check_output([sys.executable, "-c", script], env=env, cwd=tmp_path, text=True).strip()
    next_release = tmp_path / "next-release"
    next_release.mkdir()
    check = "from pathlib import Path; assert Path(" + repr(path) + ").read_bytes() == b'%PDF retained'"
    subprocess.run([sys.executable, "-c", check], cwd=next_release, check=True)
    assert Path(path).parent == persistent


def test_s3_fallback_uses_configured_persistent_directory(monkeypatch, tmp_path):
    dest = tmp_path / "persistent" / "uploads"
    monkeypatch.setattr(storage, "LOCAL_UPLOAD_DIR", dest)
    monkeypatch.setattr(paper_files, "is_cloud_storage", lambda: True)
    monkeypatch.setattr(paper_files, "_STRICT_STORAGE", False)
    def unavailable(*a):
        raise OSError("test storage outage")
    monkeypatch.setattr(paper_files, "upload_file", unavailable)
    path = paper_files.write_paper_file(b"%PDF restored", "trial.pdf")
    assert Path(path).parent == dest
    assert Path(path).read_bytes() == b"%PDF restored"


@pytest.mark.parametrize("change", ["missing", "tampered"])
def test_duplicate_upload_restores_missing_file_without_new_record_or_credit(client, test_user, monkeypatch, tmp_path, change):
    import main
    monkeypatch.setattr(storage, "_USE_S3", False)
    monkeypatch.setattr(storage, "LOCAL_UPLOAD_DIR", tmp_path)
    cookie = {"rubricgen_session": test_user["cookie"]}
    content = b"%PDF-1.4 restore regression"
    response = client.post('/api/papers/upload', files={'file': ('trial.pdf', content, 'application/pdf')}, cookies=cookie)
    pid = response.json()['id']
    conn = main.get_db()
    row = conn.execute('SELECT storage_path FROM papers WHERE id=?', (pid,)).fetchone()
    if change == 'missing':
        Path(row['storage_path']).unlink()
    else:
        Path(row['storage_path']).write_bytes(b'%PDF corrupted stored object')
    conn.close()
    monkeypatch.setattr(main.member_mod, 'check_pdf_limit', lambda *a: {'allowed': False, 'used': 10, 'limit': 10})
    def no_increment(*a):
        raise AssertionError('Restoring a PDF must not consume another storage slot')
    monkeypatch.setattr(main.member_mod, 'increment_pdf_count', no_increment)
    restored = client.post('/api/papers/upload', files={'file': ('trial.pdf', content, 'application/pdf')}, cookies=cookie)
    assert restored.status_code == 201, restored.text
    assert restored.json()['id'] == pid and restored.json()['restored']
    assert client.get(f'/api/papers/{pid}/pdf', cookies=cookie).content == content
    conn = main.get_db()
    assert conn.execute('SELECT COUNT(*) AS n FROM papers WHERE sha256=?', (hashlib.sha256(content).hexdigest(),)).fetchone()['n'] == 1
    conn.close()


@pytest.mark.parametrize('change', ['missing', 'tampered'])
def test_unavailable_source_stops_benchmark_before_any_model_call(client, admin_user, monkeypatch, mock_llm, change):
    import main
    from backend import simulator as sim
    from tests.test_simulator import _launch
    _, body, _ = _launch(client, admin_user, monkeypatch, n=1)
    conn = main.get_db()
    job = sim.claim_job(conn, 'worker')
    row = conn.execute('SELECT disk_filename FROM papers WHERE id=?', (body['papers'][0]['paper_id'],)).fetchone()
    conn.close()
    path = main.PAPERS_DIR / row['disk_filename']
    if change == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'%PDF replaced without changing database hash')
    def forbidden(*a):
        raise AssertionError('Model call made before source validation')
    monkeypatch.setattr(sim.syn, 'derive_eligibility_criteria', forbidden)
    with pytest.raises(ValueError, match='Source PDF'):
        sim.process_job(main.get_db, main.PAPERS_DIR, job)
    conn = main.get_db()
    assert conn.execute('SELECT COUNT(*) AS n FROM synthesis_reviews').fetchone()['n'] == 0
    conn.close()
