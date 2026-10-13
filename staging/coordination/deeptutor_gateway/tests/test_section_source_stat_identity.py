"""Windows source identity keeps real changes while normalizing filename execute bits."""
from hashlib import sha256
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import pytest
from integrations.deeptutor_shchem_v1 import desktop_section_source_stage as stages

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows path/descriptor mode semantics")

def named_handle_evidence(path):
    before = path.stat()
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        native = stages._FileBasicInfo()
        assert stages._kernel.GetFileInformationByHandleEx(stages.msvcrt.get_osfhandle(stream.fileno()),
            0, stages.ctypes.byref(native), stages.ctypes.sizeof(native))
    names = ['st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns']
    return {'path': str(path), 'path_stat': {name: getattr(before, name) for name in names},
        'handle_stat': {name: getattr(opened, name) for name in names},
        'xor_mode': before.st_mode ^ opened.st_mode, 'native_attributes': native.FileAttributes,
        'native_change_time': native.ChangeTime}


@pytest.mark.parametrize('name', ['original.exe', 'original.com', 'original.bat', 'original.cmd'])
def test_unchanged_extension_synthetic_execute_bits_keep_full_byte_proof(tmp_path, name):
    source = tmp_path / name
    raw = b'Complete owned material; executable suffix must not exempt original bytes.' * 1700
    source.write_bytes(raw)
    evidence = named_handle_evidence(source)
    assert evidence['xor_mode'] == 0o111
    proof = stages.SourceFileSet()
    try:
        with proof.capture():
            assert source.read_bytes() == raw
        fingerprint = proof.files[os.path.normcase(str(source))][1]
        assert fingerprint['sha256'] == sha256(raw).hexdigest()
        assert fingerprint['identity'][2] == stat.S_IFREG | 0o666
        assert proof.statistics['source_bytes_hashed'] == len(raw)
        proof.verify()
        assert proof.statistics['source_bytes_hashed'] == len(raw) * 2
    finally:
        proof.close()


def test_windows_identity_preserves_type_readwrite_device_inode_size_and_mtime():
    native = {'st_dev': 10, 'st_ino': 20, 'st_mode': stat.S_IFREG | 0o777, 'st_size': 50, 'st_mtime_ns': 70}
    original = stages.SourceFileSet._identity(SimpleNamespace(**native))
    assert original[2] == stat.S_IFREG | 0o666
    for field, value in [('st_mode', stat.S_IFDIR | 0o777), ('st_mode', stat.S_IFREG | 0o444),
            ('st_dev', 11), ('st_ino', 21), ('st_size', 51), ('st_mtime_ns', 71)]:
        changed = {**native, field: value}
        assert stages.SourceFileSet._identity(SimpleNamespace(**changed)) != original


@pytest.mark.parametrize('change', ['replace_same_bytes', 'readonly_attribute'])
def test_change_between_path_stat_and_open_is_rejected(tmp_path, monkeypatch, change):
    source = tmp_path / 'source.exe'
    raw = b'complete owned selected source bytes'
    source.write_bytes(raw)
    prior = source.stat()
    replacement = tmp_path / 'replacement.exe'
    replacement.write_bytes(raw)
    os.utime(replacement, ns=(prior.st_atime_ns, prior.st_mtime_ns))
    native_open = Path.open
    armed = [True]
    def raced_open(path, mode='r', *args, **kwargs):
        if armed[0] and path == source and mode == 'rb':
            armed[0] = False
            if change == 'replace_same_bytes':
                replacement.replace(source)
            else:
                source.chmod(stat.S_IREAD)
        return native_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', raced_open)
    proof = stages.SourceFileSet()
    try:
        with pytest.raises(ValueError, match='打开期间变化'):
            proof._fingerprint(source)
    finally:
        proof.close()
        source.chmod(stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.parametrize('change', ['bytes', 'restored_bytes_mtime', 'readonly_attribute', 'restored_readonly_attribute'])
def test_change_after_open_during_hash_is_rejected(tmp_path, monkeypatch, change):
    source = tmp_path / 'source.exe'
    raw = b'complete owned selected source bytes'
    source.write_bytes(raw)
    prior = source.stat()
    native_open = Path.open
    mutation = [False]
    class RacingStream:
        def __init__(self, actual):
            self.actual = actual
        def __enter__(self):
            self.actual.__enter__()
            return self
        def __exit__(self, *args):
            return self.actual.__exit__(*args)
        def fileno(self):
            return self.actual.fileno()
        def read(self, *args):
            chunk = self.actual.read(*args)
            if not mutation[0]:
                mutation[0] = True
                if change in {'bytes', 'restored_bytes_mtime'}:
                    with native_open(source, 'wb') as writer:
                        writer.write(b'changed owned source bytes')
                    if change == 'restored_bytes_mtime':
                        with native_open(source, 'wb') as writer:
                            writer.write(raw)
                        os.utime(source, ns=(prior.st_atime_ns, prior.st_mtime_ns))
                else:
                    source.chmod(stat.S_IREAD)
                    if change == 'restored_readonly_attribute':
                        source.chmod(stat.S_IREAD | stat.S_IWRITE)
            return chunk
    def raced_open(path, mode='r', *args, **kwargs):
        actual = native_open(path, mode, *args, **kwargs)
        return RacingStream(actual) if path == source and mode == 'rb' else actual
    monkeypatch.setattr(Path, 'open', raced_open)
    proof = stages.SourceFileSet()
    try:
        with pytest.raises(ValueError, match='读取期间变化'):
            proof._fingerprint(source)
    finally:
        proof.close()
        source.chmod(stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.parametrize('restore', [False, True])
def test_captured_readonly_attribute_change_or_aba_is_rejected(tmp_path, restore):
    source = tmp_path / 'captured.exe'
    source.write_bytes(b'owned bytes retained across attribute change')
    proof = stages.SourceFileSet()
    try:
        with proof.capture():
            source.read_bytes()
        source.chmod(stat.S_IREAD)
        if restore:
            source.chmod(stat.S_IREAD | stat.S_IWRITE)
        with pytest.raises(ValueError, match='变化'):
            proof.verify()
    finally:
        proof.close()
        source.chmod(stat.S_IREAD | stat.S_IWRITE)
