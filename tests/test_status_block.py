import json

from helpers import DATE, PAGE, StatusBlockFixture, wl


class DuplicateFields(StatusBlockFixture):
    def setUp(self):
        super().setUp()
        page = PAGE.replace("- **Code:** committed", "- **Code:** uncommitted\n- **Code:** committed")
        wl.write_text(self.page, "---\ndescription: unchanged\n---\n" + page)

    def assert_invalid(self, output):
        self.assertIn("INVALID status block: duplicate field", output)
        self.assertIn("Code", output)

    def test_audit_flags_duplicate_even_with_no_git(self):
        run = self.run_tool("audit.py", "--no-git")
        self.assertEqual(run.returncode, 1)
        self.assertIn("ERROR S14 demo.md:", run.stdout)
        self.assert_invalid(run.stdout)

    def test_check_and_sync_refuse_to_render_duplicate(self):
        original = {path: wl.read_raw(path) for path in (self.page, self.index)}
        for action in ("check", "sync"):
            with self.subTest(action=action):
                run = self.run_tool("status.py", action)
                self.assertEqual(run.returncode, 1)
                self.assert_invalid(run.stdout + run.stderr)
                self.assertEqual(original, {path: wl.read_raw(path) for path in original})

    def test_lookup_and_hook_withhold_duplicate_status(self):
        for script in ("lookup.py", "prompt_hook.py"):
            with self.subTest(script=script):
                run = self.run_tool(script, "rc-2.4.1")
                self.assertEqual(run.returncode, 0)
                self.assert_invalid(run.stdout)
                for term in ("code committed", "Code: committed", "Code: uncommitted", "Built, not merged."):
                    self.assertNotIn(term, run.stdout)
        run = self.run_tool("lookup.py", "--json", "rc-2.4.1")
        self.assertEqual(run.returncode, 0)
        self.assert_invalid(run.stdout)
        hit = json.loads(run.stdout)["hits"][0]
        self.assertNotIn("status", hit)
        self.assertNotIn("Built, not merged.", run.stdout)

    def test_hook_body_fallback_withholds_stale_status(self):
        with open(self.page, "a", encoding="utf-8") as stream:
            stream.write("\nOnlyInBody\n")
        run = self.run_tool("prompt_hook.py", "OnlyInBody")
        self.assertEqual(run.returncode, 0)
        self.assert_invalid(run.stdout)
        self.assertNotIn("Built, not merged.", run.stdout)

    def test_lookup_body_only_result_labels_invalid_status(self):
        with open(self.page, "a", encoding="utf-8") as stream:
            stream.write("\nOnlyInBody\n")
        for flags in ((), ("--json",)):
            with self.subTest(flags=flags):
                run = self.run_tool("lookup.py", *flags, "OnlyInBody")
                self.assertEqual(run.returncode, 0)
                self.assert_invalid(run.stdout)


class StatusBlockFormat(StatusBlockFixture):
    def assert_rejected(self, page, errors):
        wl.write_text(self.page, "---\ndescription: unchanged\n---\n" + page)
        before = {path: wl.read_raw(path) for path in (self.page, self.index)}
        audit = self.run_tool("audit.py", "--no-git")
        self.assertEqual(audit.returncode, 1, audit.stdout + audit.stderr)
        for code, detail in errors:
            self.assertRegex(audit.stdout, f"ERROR {code} demo[.]md: [^\\n]*{detail}")
        self.assertNotIn("status block lacks required key(s)", audit.stdout)
        for script, args, returncode in (
            ("status.py", ("check",), 1),
            ("status.py", ("sync",), 1),
            ("lookup.py", ("rc-2.4.1",), 0),
            ("lookup.py", ("--json", "rc-2.4.1"), 0),
            ("prompt_hook.py", ("rc-2.4.1",), 0),
        ):
            run = self.run_tool(script, *args)
            output = run.stdout + run.stderr
            self.assertEqual(run.returncode, returncode, output)
            self.assertIn("INVALID status block", output)
            self.assertNotIn("Built, not merged.", output)
        self.assertEqual(before, {path: wl.read_raw(path) for path in before})

    def test_required_field_spelling_is_canonical(self):
        for variant in ("as of", "As  of", " As of "):
            with self.subTest(variant=variant):
                page = PAGE.replace("**As of:**", f"**{variant}:**")
                self.assert_rejected(page, [("S15", "non-canonical spelling of required field")])
                self.assertEqual(wl.as_of_date(wl.Page("demo.md", text=page)), DATE)

    def test_duplicate_field_names_use_normalized_spelling(self):
        page = PAGE.replace("- **As of:**", "- **AS  of:** unknown\n- **As of:**")
        self.assert_rejected(page, [("S14", "duplicate field As of"),
                                    ("S15", "non-canonical spelling of required field")])
        page = PAGE.replace("<!-- history -->", "- **Extra note:** a\n- **extra  NOTE:** b\n\n<!-- history -->")
        self.assert_rejected(page, [("S14", "duplicate field")])

    def test_unparseable_lines_report_their_location(self):
        for line in ("-  **Published:** x", "* **Published:** x", "- **Published**: x"):
            with self.subTest(line=line):
                page = PAGE.replace("<!-- history -->", line + "\n\n<!-- history -->")
                line_no = page.splitlines().index(line) + 4  # front matter adds three lines
                self.assert_rejected(page, [("S16", f"unparseable line in status block.*line {line_no}")])

    def test_required_field_cannot_be_hidden_in_child_bullet(self):
        for key in ("Published", "pUbLiShEd", "As  of"):
            with self.subTest(key=key):
                page = PAGE.replace("- **Tested:** none", f"- **Tested:** none\n  - **{key}:** hidden")
                self.assert_rejected(page, [("S14", "duplicate field")])

    def test_unknown_fields_children_continuations_and_history_are_legal(self):
        page = PAGE.replace("<!-- history -->",
                            "- **Extra note:** first line\n  continuation\n  - **Child note:** details\n\n"
                            "<!-- history -->")
        page += "\n- **Published:** old\n- **published:** older\n-  **Published:** malformed\n"
        wl.write_text(self.page, page)
        self.assertIsNone(wl.Page(self.page).status_error)
        run = self.run_tool("status.py", "check")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        run = self.run_tool("lookup.py", "--json", "rc-2.4.1")
        entry = json.loads(run.stdout)["hits"][0]
        self.assertEqual(entry["status"]["Extra note"],
                         {"value": "first line continuation", "children": ["**Child note:** details"]})
        self.assertEqual(entry["status"]["Published"], "no")


class StatusBlockStructure(StatusBlockFixture):
    def assert_invalid_structure(self, page):
        wl.write_text(self.page, page)
        original = {path: wl.read_raw(path) for path in (self.page, self.index)}
        audit = self.run_tool("audit.py", "--no-git")
        self.assertEqual(audit.returncode, 1, audit.stdout + audit.stderr)
        self.assertIn("ERROR S17 demo.md:", audit.stdout)
        for action in (("check",), ("sync",), ("sync", "demo.md")):
            with self.subTest(action=action):
                run = self.run_tool("status.py", *action)
                output = run.stdout + run.stderr
                self.assertEqual(run.returncode, 1, output)
                self.assertIn("INVALID status block", output)
        self.assertEqual(original, {path: wl.read_raw(path) for path in original})

        for script in ("lookup.py", "prompt_hook.py"):
            with self.subTest(script=script):
                run = self.run_tool(script, "rc-2.4.1")
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn("demo.md", run.stdout)
                self.assertIn("INVALID status block", run.stdout)
                self.assertNotIn("Built, not merged.", run.stdout)
                self.assertNotIn("code committed", run.stdout)
        run = self.run_tool("lookup.py", "--json", "rc-2.4.1")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        hit = next(hit for hit in json.loads(run.stdout)["hits"] if hit["page"].endswith("/demo.md"))
        self.assertIn("INVALID status block", hit["status_error"])
        self.assertNotIn("status", hit)
        self.assertNotIn("index_line", hit)

    def test_generated_entry_still_requires_marker_when_heading_is_present(self):
        self.assert_invalid_structure(PAGE.replace("<!-- wiki-status v1 -->\n", ""))

    def test_generated_entry_still_requires_block_when_marker_and_heading_are_removed(self):
        self.assert_invalid_structure("# Demo topic\n\n## History\nOnlyInBody\n")
        hook = self.run_tool("prompt_hook.py", "OnlyInBody")
        self.assertIn("INVALID status block", hook.stdout)
        self.assertNotIn("Built, not merged.", hook.stdout)

    def test_opt_in_marker_requires_heading(self):
        self.assert_invalid_structure(PAGE.replace("## Current status\n", ""))

    def test_opt_in_marker_requires_heading_with_legacy_index_entry(self):
        index = wl.read(self.index).splitlines()
        index[1] = "- [Demo topic](demo.md) — legacy note rc-2.4.1"
        wl.write_text(self.index, "\n".join(index) + "\n")
        self.assert_invalid_structure(PAGE.replace("## Current status\n", ""))

    def test_generated_entry_requires_parseable_fields(self):
        self.assert_invalid_structure(PAGE.replace("- **Published:**", "- **Published**:"))

    def test_marker_requires_valid_block_with_legacy_index_entry(self):
        index = wl.read(self.index).splitlines()
        index[1] = "- [Demo topic](demo.md) — legacy note rc-2.4.1"
        wl.write_text(self.index, "\n".join(index) + "\n")
        self.assert_invalid_structure(PAGE.replace("- **Published:**", "- **Published**:"))

    def test_unmarked_heading_with_legacy_index_remains_legacy(self):
        index = wl.read(self.index).splitlines()
        index[1] = "- [Demo topic](demo.md) — legacy note rc-2.4.1"
        wl.write_text(self.index, "\n".join(index) + "\n")
        wl.write_text(self.page, PAGE.replace(wl.MARKER + "\n", "") + "\nOnlyInBody\n")
        original = {path: wl.read_raw(path) for path in (self.page, self.index)}
        for script, args in (("audit.py", ("--no-git",)), ("status.py", ("check",)), ("status.py", ("sync",))):
            with self.subTest(script=script, args=args):
                run = self.run_tool(script, *args)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(original, {path: wl.read_raw(path) for path in original})
        for script in ("lookup.py", "prompt_hook.py"):
            with self.subTest(script=script):
                run = self.run_tool(script, "rc-2.4.1")
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn("legacy note rc-2.4.1", run.stdout)
                self.assertNotIn("INVALID status block", run.stdout)
                self.assertNotIn("Built, not merged.", run.stdout)
                body = self.run_tool(script, "OnlyInBody")
                self.assertIn("demo.md", body.stdout)
                self.assertNotIn("INVALID status block", body.stdout)
        run = self.run_tool("lookup.py", "--json", "rc-2.4.1")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        hit = json.loads(run.stdout)["hits"][0]
        self.assertNotIn("status", hit)
        self.assertNotIn("status_error", hit)
        self.assertIn("legacy note rc-2.4.1", hit["index_line"])
