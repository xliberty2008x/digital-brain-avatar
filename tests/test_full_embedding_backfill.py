from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import full_embedding_backfill


class FullEmbeddingBackfillTests(TestCase):
    def test_neo4j_data_volume_uses_compose_project_name(self) -> None:
        with patch.object(full_embedding_backfill, "compose_project_name", return_value="avatar_digital_brain"):
            self.assertEqual(
                full_embedding_backfill.neo4j_data_volume(),
                "avatar_digital_brain_neo4j-data",
            )

    def test_sha256_file_hashes_file_content(self) -> None:
        path = Path("/tmp/avatar-digital-brain-sha-test.txt")
        path.write_text("abc", encoding="utf-8")
        try:
            self.assertEqual(
                full_embedding_backfill.sha256_file(path),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )
        finally:
            path.unlink(missing_ok=True)

    def test_create_offline_dump_dumps_system_and_neo4j_from_read_only_volume(self) -> None:
        with TemporaryDirectory() as temp_dir:
            backup_root = Path(temp_dir) / "neo4j"
            commands: list[list[str]] = []

            def fake_run_command(command: list[str], *, check: bool = True):
                commands.append(command)
                if "dump" in command:
                    database = command[command.index("dump") + 1]
                    backup_dir = next(backup_root.iterdir())
                    (backup_dir / f"{database}.dump").write_bytes(f"{database}-dump".encode("utf-8"))

            with (
                patch.object(full_embedding_backfill, "BACKUP_ROOT", backup_root),
                patch.object(full_embedding_backfill, "neo4j_data_volume", return_value="avatar_digital_brain_neo4j-data"),
                patch.object(full_embedding_backfill, "run_command", side_effect=fake_run_command),
                patch.object(full_embedding_backfill, "wait_for_neo4j_health"),
            ):
                backup_dir = full_embedding_backfill.create_offline_dump()

            self.assertTrue((backup_dir / "system.dump").exists())
            self.assertTrue((backup_dir / "neo4j.dump").exists())
            self.assertTrue((backup_dir / "SHA256SUMS").exists())
            dump_commands = [command for command in commands if "dump" in command]
            load_info_commands = [command for command in commands if "load" in command]
            self.assertEqual({command[command.index("dump") + 1] for command in dump_commands}, {"system", "neo4j"})
            self.assertEqual({command[command.index("load") + 1] for command in load_info_commands}, {"system", "neo4j"})
            self.assertTrue(
                any("avatar_digital_brain_neo4j-data:/data:ro" in command for command in dump_commands)
            )
