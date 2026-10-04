"""Run the real Bash updater against fake Git/Docker functions; no host changes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "update-from-git.sh"
BASH = shutil.which("bash") or (r"C:\Program Files\Git\bin\bash.exe" if Path(r"C:\Program Files\Git\bin\bash.exe").is_file() else None)
HARNESS = r'''
git() {
  case "$1" in
    fetch|checkout) return 0 ;;
    reset) printf '%s\n' "$3" > "$SCENARIO/current" ;;
    rev-parse)
      case "$2" in
        --git-path) printf '%s/.git/%s\n' "$SCENARIO" "$3" ;;
        --verify) return 0 ;;
        HEAD) cat "$SCENARIO/current" ;;
        *) cat "$SCENARIO/target" ;;
      esac ;;
    *) return 91 ;;
  esac
}
flock() { return 0; }
docker() {
  printf '%s\n' "$*" >> "$SCENARIO/docker.log"
  if [ "$*" = 'compose build app' ] && [ -f "$SCENARIO/fail-build" ]; then
    rm "$SCENARIO/fail-build"; return 7
  fi
  if [ "$*" = 'compose ps -q app' ]; then printf '%s\n' synthetic-container; fi
  if [ "$1" = inspect ]; then printf '%s\n' "${OLD_USER:-}"; fi
  if [ "$2" = run ] && [ -f "$SCENARIO/fail-cache" ]; then
    rm "$SCENARIO/fail-cache"; return 8
  fi
  if [ "$2" = up ] && [ -f "$SCENARIO/fail-health" ]; then
    rm "$SCENARIO/fail-health"; return 9
  fi
  return 0
}
export -f git docker flock
bash "$1" "$SCENARIO" main
'''


@unittest.skipUnless(BASH, "Bash is required for updater shell logic tests")
class DeployUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.scenario = Path(self.directory.name)
        (self.scenario / ".git").mkdir()
        (self.scenario / "current").write_text("old\n", encoding="utf-8")
        (self.scenario / "target").write_text("new\n", encoding="utf-8")
        self.pending = self.scenario / ".git" / "reading-sound-game-deploy-pending"

    def run_update(self, user=""):
        return subprocess.run([BASH, "-c", HARNESS, "--", SCRIPT.as_posix()], env={**os.environ, "SCENARIO": self.scenario.as_posix(), "OLD_USER": user}, capture_output=True, text=True, timeout=15)

    def calls(self):
        path = self.scenario / "docker.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def test_unchanged_head_without_pending_marker_never_touches_docker(self):
        (self.scenario / "current").write_text("new\n", encoding="utf-8")
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.pending.exists())

    def test_failed_build_retries_even_after_head_was_reset_to_target(self):
        (self.scenario / "fail-build").touch()
        failed = self.run_update()
        self.assertEqual(failed.returncode, 7, failed.stderr)
        self.assertTrue(self.pending.exists())
        self.assertEqual((self.scenario / "current").read_text().strip(), "new")
        self.assertEqual(self.calls(), ["compose build app"])
        retried = self.run_update()
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertEqual(self.calls().count("compose build app"), 2)
        self.assertIn("compose stop app", self.calls())
        self.assertIn("compose run --rm --no-deps --user root --entrypoint chown app -R 10001:10001 /app/src/api/.cache", self.calls())
        self.assertIn("compose up -d --wait --wait-timeout 180", self.calls())
        self.assertFalse(self.pending.exists())

    def test_failed_health_retains_retry_marker_without_stopping_nonroot_app_early(self):
        (self.scenario / "fail-health").touch()
        result = self.run_update(user="appuser")
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertTrue(self.pending.exists())
        self.assertNotIn("compose stop app", self.calls())
        retried = self.run_update(user="appuser")
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertFalse(self.pending.exists())

    def test_failed_cache_migration_attempts_restart_of_old_root_app(self):
        (self.scenario / "fail-cache").touch()
        result = self.run_update()
        self.assertEqual(result.returncode, 8, result.stderr)
        self.assertTrue(self.pending.exists())
        self.assertIn("compose stop app", self.calls())
        self.assertIn("compose start app", self.calls())
        self.assertFalse(any(call.startswith("compose up ") for call in self.calls()))


if __name__ == "__main__":
    unittest.main()
