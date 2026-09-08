from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import os
import tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import json
from types import SimpleNamespace
from unittest.mock import patch
from app.file_ops import move_document, rename_document
from app.attachments import import_attachment_file
from app.recovery import RecoveryStore
from app import session_state

base = Path.cwd() / "tmp" / "audit-reliability-20260908"
base.mkdir(parents=True, exist_ok=True)
root = Path(tempfile.mkdtemp(prefix="scenarios-", dir=base))
results = {}

source = root / 'move-source'
dest = root / 'move-destination'
source.mkdir(exist_ok=True)
dest.mkdir(exist_ok=True)
attachment = root / 'attachment.txt'
attachment.write_text('attachment content', encoding='utf-8')
note = source / 'note.md'
note.write_text('# note', encoding='utf-8')
relative = import_attachment_file(attachment, note)
note.write_text(f'[attachment]({relative})', encoding='utf-8')
valid_before = (note.parent / relative).is_file()
mapping = move_document(note, dest)
moved = Path(mapping[str(note)])
results['move_breaks_relative_attachment'] = {
    'relative_link': relative,
    'valid_before_move': valid_before,
    'valid_after_move': (moved.parent / relative).is_file(),
    'original_asset_still_exists': (source / relative).is_file(),
    'moved_document_text': moved.read_text(encoding='utf-8'),
}

sidecar_source = root / 'sidecar-source.md'
sidecar_target = root / 'sidecar-target.md'
sidecar_source.write_text('# source', encoding='utf-8')
source_notes = Path(str(sidecar_source) + '.notes.json')
target_notes = Path(str(sidecar_target) + '.notes.json')
source_notes.write_text('{"source_notes":true}', encoding='utf-8')
target_notes.write_text('{"unrelated_notes":true}', encoding='utf-8')
sidecar_mapping = rename_document(sidecar_source, sidecar_target)
results['sidecar_collision_silently_succeeds'] = {
    'operation_returned_mapping': sidecar_mapping,
    'document_moved': sidecar_target.exists(),
    'original_notes_stranded': source_notes.exists(),
    'destination_notes_unchanged': target_notes.read_text(encoding='utf-8'),
}

recovery_source = root / 'new-since-last-session.md'
recovery_source.write_text('disk version', encoding='utf-8')
store = RecoveryStore(root / 'isolated-recovery')
store.save(recovery_source, 'unsaved draft', encoding='utf-8', newline='\n', cursor=0, anchor=0, scroll=0)
opened = []
class Settings:
    def value(self, key, default=None):
        return {'open_tabs':'[]'}.get(key, default)
window = SimpleNamespace(_recovery_store=store, _open_file=lambda path: opened.append(path), _add_tab=lambda path, kind: opened.append(str(path)))
with patch.object(session_state, 'QSettings', return_value=Settings()), patch.object(session_state, 'restore_file_tree_state'):
    session_state.restore_last_session(window)
results['new_tab_crash_snapshot_not_discovered'] = {
    'valid_recovery_snapshots': len(store.list()),
    'restored_tabs': len(opened),
    'condition': 'snapshot belongs to a tab not present in last normally closed session',
}
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QTextDocument
from app import window as window_module
app = QApplication.instance() or QApplication([])
class RestoreDialog:
    RESTORE = "restore"
    DISCARD = "discard"
    def __init__(self, *args):
        self.choice = self.RESTORE
    def exec(self):
        return None
holder = SimpleNamespace(
    _recovery_store=store,
    _recovery_checked_paths=set(),
    _tab_state={},
    _editor=SimpleNamespace(create_buffer_document=lambda text: QTextDocument(text)),
)
with patch.object(window_module, "RecoveryDialog", RestoreDialog):
    window_module.MainWindow._prepare_recovery_state(holder, recovery_source, "markdown")
state = holder._tab_state[str(recovery_source)]
results["manual_open_recovery_preparation"] = {
    "restored_text": state["editor_document"].toPlainText(),
    "restored_as_modified": state["editor_document"].isModified(),
    "source_unchanged": recovery_source.read_text(encoding="utf-8"),
    "scope": "the recovery preparation function used when manually opening the same path; dialog choice stubbed to Restore",
}
output = json.dumps(results, ensure_ascii=False, indent=2)
Path(__file__).with_name('reliability-reproduction-results.json').write_text(output + '\n', encoding='utf-8')
print(output)
