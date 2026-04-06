"""Parser-level tests for darkfactory.spec.spec_model."""

import unittest

from darkfactory.spec.spec_model import parse_spec_model


class TestSpecModel(unittest.TestCase):
    def test_single_section_with_fenced_block(self) -> None:
        text = (
            "## Acceptance Criteria\n\n"
            "Some prose.\n\n"
            "```json\n"
            '{"ok": true}\n'
            "```\n"
        )
        m = parse_spec_model(text)
        self.assertEqual(len(m.sections), 1)
        self.assertEqual(m.sections[0].heading.text, "Acceptance Criteria")
        self.assertEqual(len(m.sections[0].fenced_blocks), 1)
        fb = m.sections[0].fenced_blocks[0]
        self.assertEqual(fb.language, "json")
        self.assertIn("Acceptance Criteria", fb.section_path)

    def test_nested_subheadings_document_order(self) -> None:
        text = (
            "## A\n\nfoo\n\n"
            "### A1\n\nbar\n\n"
            "## B\n\nbaz\n"
        )
        m = parse_spec_model(text)
        names = [s.heading.text for s in m.sections]
        self.assertEqual(names, ["A", "A1", "B"])
        levels = [s.heading.level for s in m.sections]
        self.assertEqual(levels, [2, 3, 2])

    def test_gfm_table(self) -> None:
        text = (
            "## AC\n\n"
            "| ID | Description |\n"
            "|----|-------------|\n"
            "| AC-1 | GIVEN x WHEN y THEN z |\n"
            "| AC-2 | GIVEN a WHEN b THEN c |\n"
        )
        m = parse_spec_model(text)
        self.assertEqual(len(m.all_tables), 1)
        t = m.all_tables[0]
        # 3 rows: header + 2 body
        self.assertEqual(len(t.rows), 3)
        self.assertEqual(len(t.rows[0].cells), 2)
        self.assertGreaterEqual(t.end_line, t.line_number)

    def test_two_tables_when_paragraph_interleaved(self) -> None:
        text = (
            "## AC\n\n"
            "| ID | Desc |\n"
            "|----|------|\n"
            "| AC-1 | GIVEN x WHEN y THEN z |\n\n"
            "Some interrupting paragraph.\n\n"
            "| ID | Desc |\n"
            "|----|------|\n"
            "| AC-2 | GIVEN a WHEN b THEN c |\n"
        )
        m = parse_spec_model(text)
        ac = m.find_section("AC")
        assert ac is not None
        self.assertEqual(len(ac.tables), 2)

    def test_inline_code_in_table_cell_not_a_fence(self) -> None:
        text = (
            "## A\n\n"
            "| ID | Desc |\n"
            "|----|------|\n"
            "| 1 | use `foo` here |\n"
        )
        m = parse_spec_model(text)
        self.assertEqual(len(m.all_fenced_blocks), 0)

    def test_wire_contracts_at_h2(self) -> None:
        text = (
            "## Wire Contracts\n\n"
            "### Error Envelope\n\n"
            "```json\n"
            '{"ok": false}\n'
            "```\n"
        )
        m = parse_spec_model(text)
        wc = m.wire_contracts()
        self.assertIn("error envelope", wc)

    def test_wire_contracts_nested_at_h3(self) -> None:
        text = (
            "## Error Handling\n\n"
            "### Wire Contracts\n\n"
            "#### Error Envelope\n\n"
            "```json\n"
            '{"ok": false}\n'
            "```\n"
        )
        m = parse_spec_model(text)
        wc = m.wire_contracts()
        self.assertIn("error envelope", wc)


    def test_section_end_lines_close_all_open_ancestors(self) -> None:
        # Regression: the close-prior-sections loop used to `break` after
        # closing one section, so deeper nested sections kept a tentative
        # end_line at EOF and over-included sibling content.
        text = (
            "# A\n"           # line 1
            "\n"              # 2
            "## B\n"          # 3
            "\n"              # 4
            "### C\n"         # 5
            "\n"              # 6
            "## D\n"          # 7
            "\n"              # 8
            "body of D\n"     # 9
        )
        m = parse_spec_model(text)
        by_name = {s.heading.text: s for s in m.sections}
        # B should end right before D (line 7), NOT at EOF.
        self.assertEqual(by_name["B"].end_line, 7)
        # C (nested inside B) should also close at D's line.
        self.assertEqual(by_name["C"].end_line, 7)


if __name__ == "__main__":
    unittest.main()
