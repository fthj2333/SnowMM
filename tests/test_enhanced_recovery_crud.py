"""Manager-level regressions for interrupted deployment and offline library editing."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_enhanced as fixtures


class RecoveryAndLibraryTests(unittest.TestCase):
    setUp = fixtures.LibraryTests.setUp
    tearDown = fixtures.LibraryTests.tearDown
    mod = fixtures.LibraryTests.mod

    def interrupted(self):
        first, second = self.mod("first.pak"), self.mod("second.pak", b"second")
        self.assertEqual(self.manager.set_enabled([first], True), (1, []))
        deployment = self.manager.deployment
        old = deployment.state()
        identity = "a" * 32
        folder = deployment.root / (".snowmm-txn-" + identity)
        (folder / "old").mkdir(parents=True)
        (folder / "new").mkdir()
        (folder / "owner.json").write_text(json.dumps({"owner": "SnowMMEnhanced", "id": identity}))
        (folder / "journal.json").write_text(json.dumps({"id": identity, "old": old, "new": {"files": []}}))
        (deployment.root / first.filename).replace(folder / "old" / first.filename)
        return first, second, folder

    def test_enable_after_interruption_retains_existing_mod(self):
        first, second, folder = self.interrupted()
        self.assertEqual(self.manager.set_enabled([second], True), (1, []))
        self.assertEqual(self.manager.deployment.enabled_ids(), {first.id, second.id})
        self.assertFalse(folder.exists())

    def test_disable_unrelated_after_interruption_retains_existing_mod(self):
        first, second, _ = self.interrupted()
        self.assertEqual(self.manager.set_enabled([second], False), (1, []))
        self.assertEqual(self.manager.deployment.enabled_ids(), {first.id})

    def test_refresh_and_preset_capture_recovered_state(self):
        first, _, _ = self.interrupted()
        self.manager.refresh_states()
        self.assertTrue(first.enabled)
        self.manager.save_preset("recovered")
        self.assertEqual(self.store.presets["recovered"]["ids"], [first.id])

    def test_rename_unrelated_retains_recovered_deployment(self):
        first, second, _ = self.interrupted()
        self.assertIsNone(self.manager.rename_stem(second, "new name"))
        self.assertEqual(self.manager.deployment.enabled_ids(), {first.id})

    def test_rename_enabled_mod_recovers_and_redeploys_from_new_folder(self):
        first, _, _ = self.interrupted()
        self.assertIsNone(self.manager.rename_stem(first, "renamed"))
        self.assertEqual(self.manager.deployment.enabled_ids(), {first.id})
        self.assertTrue(first.disk_path.exists())

    def test_version_switch_retains_recovered_enablement(self):
        first, _, _ = self.interrupted()
        update = self.root / "update" / first.filename
        update.parent.mkdir()
        update.write_bytes(b"updated")
        revision = self.manager.add_revision(first, [update])
        self.manager.select_version(first, revision)
        self.assertEqual(self.manager.deployment.enabled_ids(), {first.id})
        self.assertEqual((self.manager.deployment.root / first.filename).read_bytes(), b"updated")

    def test_delete_unrelated_retains_recovered_deployment(self):
        first, second, _ = self.interrupted()
        self.assertEqual(self.manager.delete([second]), (1, []))
        self.assertEqual(self.manager.deployment.enabled_ids(), {first.id})

    def test_recovery_error_does_not_queue_or_apply_request(self):
        first, second, folder = self.interrupted()
        backup = folder / "old" / first.filename
        backup.write_bytes(b"externally modified")
        self.assertTrue(self.manager.set_enabled([second], True)[1])
        self.assertFalse(self.settings.has_pending)
        self.assertEqual(backup.read_bytes(), b"externally modified")
        self.assertFalse((self.manager.deployment.root / second.filename).exists())

    def test_game_running_blocks_recovery_without_changing_files(self):
        first, second, folder = self.interrupted()
        self.manager.running_check = lambda: True
        self.assertTrue(self.manager.set_enabled([second], True)[1])
        self.assertFalse(self.settings.has_pending)
        self.assertTrue((folder / "old" / first.filename).exists())
        self.assertFalse((self.manager.deployment.root / first.filename).exists())

    def test_pending_selection_is_preserved_across_recovery(self):
        first, second, _ = self.interrupted()
        self.settings.pending_ids = [first.id]
        self.settings.has_pending = True
        self.assertEqual(self.manager.set_enabled([second], True), (1, []))
        self.assertEqual(self.manager.deployment.enabled_ids(), {first.id, second.id})

    def test_game_running_recovery_error_does_not_modify_pending_version(self):
        first, _, folder = self.interrupted()
        self.settings.has_pending = True
        self.settings.pending_ids = [first.id]
        self.manager.running_check = lambda: True
        with self.assertRaisesRegex(ValueError, "退出游戏"):
            self.manager.select_version(first, first.current_version)
        self.assertEqual(self.settings.pending_revisions, {})
        self.assertTrue((folder / "old" / first.filename).exists())

    def offline(self):
        mod = self.mod()
        self.settings.deploy_path = ""
        return mod

    def test_unconfigured_library_rename_does_not_deploy(self):
        mod = self.offline()
        with patch.object(self.manager, "_apply_ids", side_effect=AssertionError("must not deploy")):
            self.assertIsNone(self.manager.rename_stem(mod, "本地改名"))
        self.assertTrue(mod.disk_path.is_file())
        self.assertEqual(mod.name, "本地改名")
        self.assertFalse((self.settings._home / "unconfigured-deployment").exists())

    def test_unconfigured_category_move_does_not_deploy(self):
        mod = self.offline()
        previous = mod.category_parts[:]
        with patch.object(self.manager, "_apply_ids", side_effect=AssertionError("must not deploy")):
            self.manager.change_category(previous, ["插画", "移动"])
        self.assertEqual(mod.category_parts, ["插画", "移动"])
        self.assertTrue(mod.disk_path.is_file())

    def test_unconfigured_trash_retains_payload(self):
        mod = self.offline()
        with patch.object(self.manager, "_apply_ids", side_effect=AssertionError("must not deploy")):
            self.assertEqual(self.manager.delete([mod]), (1, []))
        payloads = list((self.store.root / ".回收站").rglob(mod.filename))
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].read_bytes(), b"content")

    def test_unconfigured_library_operations_ignore_unrelated_game_process(self):
        mod = self.offline()
        self.manager.running_check = lambda: True
        self.assertIsNone(self.manager.rename_stem(mod, "offline"))
        self.manager.change_category(mod.category_parts[:], ["杂项", "offline"])
        self.assertEqual(self.manager.delete([mod]), (1, []))

    def test_unconfigured_rename_failure_rolls_back(self):
        mod = self.offline()
        original = mod.folder
        replace = Path.rename

        def fail(path, destination):
            if path == Path(original):
                raise OSError("injected move failure")
            return replace(path, destination)

        with patch.object(Path, "rename", fail):
            self.assertIsNotNone(self.manager.rename_stem(mod, "failed name"))
        self.assertEqual(mod.folder, original)
        self.assertTrue(mod.disk_path.is_file())
