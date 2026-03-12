"""Tests for darkfactory.deployment.migration_check — expand-contract enforcement."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from darkfactory.deployment.migration_check import (
    _is_migration_file,
    _scan_file,
    check_migrations,
    suggest_split,
    _compile_patterns,
    ALL_DESTRUCTIVE,
    ALL_ADDITIVE,
)
from darkfactory.utils.subprocess_runner import RunResult


class TestPatternDetection(unittest.TestCase):
    """Test individual pattern detection on file content."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.destructive = _compile_patterns(ALL_DESTRUCTIVE)
        self.additive = _compile_patterns(ALL_ADDITIVE)

    def _write(self, name: str, content: str) -> Path:
        p = Path(self.tmpdir) / name
        p.write_text(content)
        return p

    def test_sql_drop_table(self):
        f = self._write("m.sql", "DROP TABLE users;")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertTrue(d)
        self.assertFalse(a)

    def test_sql_create_table(self):
        f = self._write("m.sql", "CREATE TABLE users (id INT);")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertFalse(d)
        self.assertTrue(a)

    def test_sql_add_column(self):
        f = self._write("m.sql", "ALTER TABLE users ADD COLUMN email VARCHAR(255);")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertFalse(d)
        self.assertTrue(a)

    def test_sql_drop_column(self):
        f = self._write("m.sql", "ALTER TABLE users DROP COLUMN old_email;")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertTrue(d)
        self.assertFalse(a)

    def test_sql_truncate(self):
        f = self._write("m.sql", "TRUNCATE TABLE logs;")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertTrue(d)

    def test_sql_mixed(self):
        f = self._write("m.sql", "ALTER TABLE t ADD COLUMN x INT;\nDROP TABLE old_t;")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertTrue(d)
        self.assertTrue(a)

    def test_python_orm_remove_field(self):
        f = self._write("m.py", "migrations.RemoveField(model_name='user', name='old')")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertTrue(d)

    def test_python_orm_add_field(self):
        f = self._write("m.py", "migrations.AddField(model_name='user', name='email')")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertFalse(d)
        self.assertTrue(a)

    def test_alembic_op_drop(self):
        f = self._write("m.py", "op.drop_column('users', 'old_col')")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertTrue(d)

    def test_alembic_op_add(self):
        f = self._write("m.py", "op.add_column('users', sa.Column('email', sa.String))")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertFalse(d)
        self.assertTrue(a)

    def test_js_orm_drop_table(self):
        f = self._write("m.js", "await queryInterface.dropTable('users');")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertTrue(d)

    def test_js_orm_create_table(self):
        f = self._write("m.js", "await queryInterface.createTable('users', {});")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertFalse(d)
        self.assertTrue(a)

    def test_case_insensitive(self):
        f = self._write("m.sql", "drop table USERS;")
        d, a = _scan_file(f, self.destructive, self.additive)
        self.assertTrue(d)


class TestIsMigrationFile(unittest.TestCase):
    def test_matches_dir(self):
        self.assertTrue(_is_migration_file("migrations/001.sql", ["migrations/"]))

    def test_no_match(self):
        self.assertFalse(_is_migration_file("src/app.py", ["migrations/"]))

    def test_nested_match(self):
        self.assertTrue(_is_migration_file("db/migrate/001.rb", ["db/migrate/"]))


class TestCheckMigrations(unittest.TestCase):
    """Integration tests for check_migrations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.migration_dir = Path(self.tmpdir) / "migrations"
        self.migration_dir.mkdir()

    @patch("darkfactory.deployment.migration_check._get_changed_files")
    def test_safe_additive_only(self, mock_changed):
        mock_changed.return_value = ["migrations/001.sql"]
        (self.migration_dir / "001.sql").write_text("CREATE TABLE users (id INT);")
        result = check_migrations(self.tmpdir, ["migrations/"])
        self.assertTrue(result.safe)
        self.assertFalse(result.mixed)

    @patch("darkfactory.deployment.migration_check._get_changed_files")
    def test_safe_destructive_only(self, mock_changed):
        mock_changed.return_value = ["migrations/001.sql"]
        (self.migration_dir / "001.sql").write_text("DROP TABLE old_users;")
        result = check_migrations(self.tmpdir, ["migrations/"])
        self.assertTrue(result.safe)
        self.assertFalse(result.mixed)

    @patch("darkfactory.deployment.migration_check._get_changed_files")
    def test_unsafe_mixed(self, mock_changed):
        mock_changed.return_value = ["migrations/001.sql"]
        (self.migration_dir / "001.sql").write_text(
            "ALTER TABLE t ADD COLUMN x INT;\nDROP TABLE old_t;"
        )
        result = check_migrations(self.tmpdir, ["migrations/"])
        self.assertFalse(result.safe)
        self.assertTrue(result.mixed)

    @patch("darkfactory.deployment.migration_check._get_changed_files")
    def test_no_migration_files(self, mock_changed):
        mock_changed.return_value = ["src/app.py"]
        result = check_migrations(self.tmpdir, ["migrations/"])
        self.assertTrue(result.safe)

    @patch("darkfactory.deployment.migration_check._get_changed_files")
    def test_no_changed_files(self, mock_changed):
        mock_changed.return_value = []
        result = check_migrations(self.tmpdir, ["migrations/"])
        self.assertTrue(result.safe)

    @patch("darkfactory.deployment.migration_check._get_changed_files")
    def test_extra_patterns(self, mock_changed):
        mock_changed.return_value = ["migrations/001.sql"]
        (self.migration_dir / "001.sql").write_text(
            "CUSTOM_DROP stuff;\nCUSTOM_CREATE stuff;"
        )
        result = check_migrations(
            self.tmpdir, ["migrations/"],
            extra_patterns={
                "destructive": [r"\bCUSTOM_DROP\b"],
                "additive": [r"\bCUSTOM_CREATE\b"],
            },
        )
        self.assertFalse(result.safe)
        self.assertTrue(result.mixed)

    @patch("darkfactory.deployment.migration_check._get_changed_files")
    def test_files_checked_tracked(self, mock_changed):
        mock_changed.return_value = ["migrations/001.sql"]
        (self.migration_dir / "001.sql").write_text("CREATE TABLE t (id INT);")
        result = check_migrations(self.tmpdir, ["migrations/"])
        self.assertEqual(result.files_checked, ["migrations/001.sql"])


class TestSuggestSplit(unittest.TestCase):
    def test_suggests_both(self):
        result = suggest_split(
            "migrations/001.sql",
            ["DROP TABLE old_t"],
            ["ADD COLUMN email"],
        )
        self.assertIn("Expand", result)
        self.assertIn("Contract", result)
        self.assertIn("001.sql", result)

    def test_suggests_destructive_only(self):
        result = suggest_split("m.sql", ["DROP TABLE t"], [])
        self.assertIn("Contract", result)
        self.assertNotIn("Expand", result)


if __name__ == "__main__":
    unittest.main()
