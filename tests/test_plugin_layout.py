"""Validate the distributable layout and exercise the hook as configured."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class PluginLayout(unittest.TestCase):
    def test_manifests(self):
        plugin = read_json(".claude-plugin/plugin.json")
        self.assertEqual(plugin["name"], "repo-waymark")
        self.assertEqual(plugin["version"], "0.1.1")
        self.assertEqual(plugin["license"], "MIT")
        for key in ("displayName", "description", "author", "repository", "homepage", "keywords"):
            self.assertTrue(plugin[key])
        self.assertEqual(plugin["author"], {"name": "jukkraputp", "url": "https://github.com/jukkraputp"})
        self.assertNotIn("engines", plugin)
        market = read_json(".claude-plugin/marketplace.json")
        self.assertEqual(market["name"], "repo-waymark")
        self.assertEqual(market["owner"], {"name": "jukkraputp"})
        self.assertTrue(market["description"])
        self.assertNotIn("version", market)
        self.assertEqual(len(market["plugins"]), 1)
        entry = market["plugins"][0]
        self.assertEqual((entry["name"], entry["source"]), ("repo-waymark", "./"))
        self.assertTrue(entry["description"])
        self.assertNotIn("version", entry)
        self.assertFalse((ROOT / "bin").exists())
        self.assertFalse((ROOT / "CLAUDE.md").exists())

    def test_skill_frontmatter(self):
        skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual({p.parent.name for p in skills}, {"workflow", "lookup", "audit", "sync"})
        for path in skills:
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                self.assertLess(len(text.splitlines()), 500)
                self.assertTrue(text.startswith("---\n"))
                front = text.split("---\n", 2)[1]
                descriptions = [line.partition(":")[2].strip() for line in front.splitlines() if line.startswith("description:")]
                self.assertEqual(len(descriptions), 1)
                self.assertTrue(descriptions[0])
                self.assertLess(len(descriptions[0]), 1536)
                self.assertFalse(any("$ARGUMENTS" in line for line in text.splitlines() if line.lstrip().startswith("!")))
                if path.parent.name in ("audit", "sync"):
                    self.assertIn("disable-model-invocation: true", front)
                if path.parent.name == "lookup":
                    self.assertIn("argument-hint: <question or identifier>", front)

    def test_hook_configuration_and_execution(self):
        config = read_json("hooks/hooks.json")
        hook = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertEqual(hook["command"], "python3")
        self.assertEqual(hook["args"], ["${CLAUDE_PLUGIN_ROOT}/scripts/prompt_hook.py"])
        self.assertEqual(hook["timeout"], 10)
        self.assertTrue(hook["statusMessage"])
        args = [arg.replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT)) for arg in hook["args"]]
        env = {k: v for k, v in os.environ.items() if not k.startswith("WAYMARK_") and k != "CLAUDE_PROJECT_DIR"}
        def run(cwd):
            return subprocess.run([sys.executable, *args], cwd=cwd, env=env, input=json.dumps({"prompt": "what is the status of the example topic"}), text=True, capture_output=True)
        result = run(ROOT / "templates/example-wiki")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<wiki-index-matches>", result.stdout)
        with tempfile.TemporaryDirectory() as empty:
            result = run(empty)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
