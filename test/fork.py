"""Fork export regression: synthetic sessions only; no model or tool execution."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("snap", ROOT / "snap.py")
snap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)


def message(eid, parent, role, content, **extra):
    return dict(type="message", id=eid, parentId=parent,
                message=dict(role=role, content=content, **extra))


class ForkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pi snap fork ")
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source.jsonl"
        self.output = Path(self.temp.name) / "fork output.jsonl"
        self.entries = [
            dict(type="session", version=3, id="original", timestamp="2026-01-01T00:00:00Z", cwd="/project"),
            dict(type="model_change", id="model", parentId=None, provider="test", modelId="old"),
            message("user", "model", "user", [{"type": "text", "text": "你好"}]),
            message("call", "user", "assistant", [
                {"type": "thinking", "thinking": "preserved"},
                {"type": "toolCall", "id": "tc1", "name": "read", "arguments": {"path": "x"}},
                {"type": "toolCall", "id": "tc2", "name": "read", "arguments": {"path": "y"}}]),
            message("result1", "call", "toolResult", [{"type": "text", "text": "one"}], toolCallId="tc1"),
            message("result2", "result1", "toolResult", [{"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"}], toolCallId="tc2", isError=True),
            message("abandoned", "result2", "user", "old branch"),
            dict(type="label", id="label", parentId="result2", targetId="user", label="checkpoint"),
            dict(type="compaction", id="compact", parentId="label", firstKeptEntryId="user", summary="summary", tokensBefore=100),
            message("future", "compact", "user", "must not leak"),
        ]
        self.save()

    def save(self):
        self.source.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.entries), encoding="utf-8")

    def run_cli(self, entry="compact", *flags):
        return subprocess.run([sys.executable, str(ROOT / "snap.py"), str(self.source), entry,
                               "--fork", str(self.output), *flags], capture_output=True, encoding="utf-8")

    def test_branch_preserves_raw_content_and_source(self):
        before = self.source.read_bytes()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        exported = snap.load_entries(self.output)
        self.assertNotEqual(exported[0]["id"], "original")
        # Export preserves the supplied absolute path, not a realpath. macOS /var
        # aliases /private/var; resolving here would assert a different contract.
        self.assertEqual(exported[0]["parentSession"], str(self.source.absolute()))
        self.assertEqual(exported[0]["cwd"], "/project")
        self.assertEqual(exported[1:], self.entries[1:6] + self.entries[7:9])
        self.assertEqual(self.source.read_bytes(), before)
        viewed = subprocess.run([sys.executable, str(ROOT / "snap.py"), str(self.output), "result2", "--json"], capture_output=True, encoding="utf-8")
        self.assertEqual(json.loads(viewed.stdout), self.entries[5])

    def test_abandoned_branch_and_prefix(self):
        result = self.run_cli("aband")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snap.load_entries(self.output)[1:], self.entries[1:7])

    def test_incomplete_tools_rejected(self):
        for entry in ("call", "result1"):
            with self.subTest(entry=entry):
                result = self.run_cli(entry)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("工具", result.stderr)
                self.assertFalse(self.output.exists())

    def test_no_overwrite_including_source(self):
        for target in (self.output, self.source):
            if target == self.output:
                target.write_text("keep", encoding="utf-8")
            self.output = target
            before = target.read_bytes()
            self.assertNotEqual(self.run_cli().returncode, 0)
            self.assertEqual(target.read_bytes(), before)

    def test_bad_input_does_not_export(self):
        original = copy.deepcopy(self.entries)
        for case in ("duplicate", "cycle", "missing_parent", "missing_kept", "version", "orphan_result", "nonobject", "bad_json"):
            with self.subTest(case=case):
                self.entries = copy.deepcopy(original)
                if case == "duplicate": self.entries.append(copy.deepcopy(self.entries[1]))
                if case == "cycle": self.entries[1]["parentId"] = "compact"
                if case == "missing_parent": self.entries[1]["parentId"] = "missing"
                if case == "missing_kept": self.entries[8]["firstKeptEntryId"] = "future"
                if case == "version": self.entries[0]["version"] = 2
                if case == "orphan_result": self.entries[4]["message"]["toolCallId"] = "missing"
                if case == "nonobject": self.entries.append(None)
                self.save()
                if case == "bad_json":
                    with self.source.open("a", encoding="utf-8") as file: file.write('{"partial":')
                result = self.run_cli()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())
                self.assertNotIn("Traceback", result.stderr)

    def test_incompatible_arguments(self):
        for flags in (("--json",), ("--all",), ("--tail", "2")):
            self.assertEqual(self.run_cli("user", *flags).returncode, 2)
        for args in (("--fork", str(self.output)), (str(self.source), "--fork", str(self.output))):
            result = subprocess.run([sys.executable, str(ROOT / "snap.py"), *args], capture_output=True)
            self.assertEqual(result.returncode, 2)
        self.assertFalse(self.output.exists())

    def test_large_chain(self):
        self.entries = self.entries[:1] + [message(f"u{i}", f"u{i-1}" if i else None, "user", "x" * 100) for i in range(20000)]
        self.save()
        result = self.run_cli("u19999")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(snap.load_entries(self.output)), 20001)


if __name__ == "__main__":
    unittest.main()
