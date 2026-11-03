"""Regression checks for stale simulator app installs, using temporary bundles only."""

import importlib.util
import os
from pathlib import Path
import plistlib
import shutil
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_simulator_install.py"
SPEC = importlib.util.spec_from_file_location("verify_simulator_install_under_test", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Cannot load simulator installation verifier: {SCRIPT}")
VERIFIER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VERIFIER
SPEC.loader.exec_module(VERIFIER)


class CompareAppsTests(unittest.TestCase):
    ARCHIVE_TIMESTAMP = 315532800  # 1980-01-01 UTC, as in reproducible app archives.
    MAIN = "Telegram"
    CORE = "Frameworks/TelegramCore.framework/TelegramCore"
    UI = "Frameworks/TelegramUIFramework.framework/TelegramUIFramework"
    NESTED = (
        "Frameworks/TelegramUIFramework.framework/Frameworks/Composer.framework/"
        "ComposerRuntime"
    )

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.artifact = self.root / "artifact" / "Telegram.app"
        self.installed = self.root / "installed" / "Telegram.app"
        self._write_bundle(
            self.artifact,
            self.MAIN,
            b"telegram-main-binary",
            CFBundleIdentifier="org.showtime.TelegramQuickAttach",
            CFBundleShortVersionString="13.1.2",
            CFBundleVersion="100101",
        )
        for relative, payload in (
            (self.CORE, b"telegram-core-binary"),
            (self.UI, b"telegram-ui-framework"),
            (self.NESTED, b"nested-composer-binary"),
        ):
            executable = self.artifact / relative
            self._write_bundle(executable.parent, executable.name, payload)
        for path in self.artifact.rglob("*"):
            if path.is_file():
                os.utime(path, (self.ARCHIVE_TIMESTAMP, self.ARCHIVE_TIMESTAMP))
        shutil.copytree(self.artifact, self.installed)

    @staticmethod
    def _write_bundle(bundle, executable_name, payload, **metadata):
        bundle.mkdir(parents=True, exist_ok=True)
        with (bundle / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleExecutable": executable_name, **metadata}, stream)
        executable = bundle / executable_name
        executable.write_bytes(payload)
        executable.chmod(0o755)

    def _compare(self):
        report = VERIFIER.compare_apps(self.artifact, self.installed)
        self.assertIsInstance(report["passed"], bool)
        self.assertIsInstance(report["metadata"], list)
        self.assertIsInstance(report["binaries"], list)
        paths = [entry["path"] for entry in report["binaries"]]
        self.assertEqual(len(paths), len(set(paths)), "Each executable must appear once")
        for entry in report["binaries"]:
            self.assertFalse(Path(entry["path"]).is_absolute())
            self.assertNotIn("..", Path(entry["path"]).parts)
            self.assertIsInstance(entry["match"], bool)
        return report, {entry["path"]: entry["match"] for entry in report["binaries"]}

    def test_identical_apps_pass_and_include_all_declared_executables(self):
        report, binaries = self._compare()

        self.assertTrue(report["passed"])
        self.assertEqual(set(binaries), {self.MAIN, self.CORE, self.UI, self.NESTED})
        self.assertTrue(all(binaries.values()))

    def test_stale_extension_and_dylib_fail(self):
        for app in (self.artifact, self.installed):
            self._write_bundle(app / "PlugIns/Share.appex", "Share", b"extension")
            (app / "Frameworks/extra.dylib").write_bytes(b"library")
        for relative in ("PlugIns/Share.appex/Share", "Frameworks/extra.dylib"):
            (self.installed / relative).write_bytes(b"old")
        report, binaries = self._compare()
        self.assertFalse(report["passed"])
        self.assertFalse(binaries["PlugIns/Share.appex/Share"])
        self.assertFalse(binaries["Frameworks/extra.dylib"])

    def test_changed_framework_bytes_fail_despite_identical_version_size_and_mtime(self):
        # A stale framework used to look current because its metadata and archive time matched.
        # The nested framework also deliberately has a different executable and bundle name.
        for relative in (self.CORE, self.UI, self.NESTED):
            with self.subTest(executable=relative):
                source = self.artifact / relative
                target = self.installed / relative
                original = source.read_bytes()
                target.write_bytes(bytes([original[0] ^ 0xFF]) + original[1:])
                os.utime(target, (self.ARCHIVE_TIMESTAMP, self.ARCHIVE_TIMESTAMP))
                try:
                    self.assertEqual(source.stat().st_size, target.stat().st_size)
                    self.assertEqual(source.stat().st_mtime_ns, target.stat().st_mtime_ns)
                    self.assertEqual(
                        (self.artifact / "Info.plist").read_bytes(),
                        (self.installed / "Info.plist").read_bytes(),
                    )

                    report, binaries = self._compare()

                    self.assertFalse(report["passed"])
                    self.assertFalse(binaries[relative])
                    self.assertTrue(all(match for path, match in binaries.items() if path != relative))
                finally:
                    shutil.copy2(source, target)

    def test_changed_main_executable_fails(self):
        (self.installed / self.MAIN).write_bytes(b"different-main-code")

        report, binaries = self._compare()

        self.assertFalse(report["passed"])
        self.assertFalse(binaries[self.MAIN])
        self.assertTrue(all(match for path, match in binaries.items() if path != self.MAIN))

    def test_missing_embedded_framework_fails(self):
        shutil.rmtree((self.installed / self.CORE).parent)

        report, binaries = self._compare()

        self.assertFalse(report["passed"])
        self.assertFalse(binaries[self.CORE])

    def test_extra_embedded_framework_fails(self):
        extra = "Frameworks/Unexpected.framework/UnexpectedExecutable"
        executable = self.installed / extra
        self._write_bundle(executable.parent, executable.name, b"unexpected-framework")

        report, binaries = self._compare()

        self.assertFalse(report["passed"])
        self.assertFalse(binaries[extra])

    def test_app_identity_version_or_build_mismatch_fails_with_identical_binaries(self):
        info_path = self.installed / "Info.plist"
        original = info_path.read_bytes()
        for key, value in (
            ("CFBundleIdentifier", "org.example.OtherApp"),
            ("CFBundleShortVersionString", "13.1.1"),
            ("CFBundleVersion", "100100"),
        ):
            with self.subTest(metadata=key):
                metadata = plistlib.loads(original)
                metadata[key] = value
                info_path.write_bytes(plistlib.dumps(metadata))
                try:
                    report, binaries = self._compare()

                    self.assertFalse(report["passed"])
                    self.assertTrue(all(binaries.values()))
                finally:
                    info_path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
