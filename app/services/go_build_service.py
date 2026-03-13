"""
Go Build Service — validates generated Go code inside the worker container.

Writes files to a temp directory, runs `go build ./...`, `go vet ./...`,
and optionally `go test ./...`, returning structured results so the
Orchestrator can feed errors back to the DevAgent/TestAgent for correction.
"""
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Timeout for go commands (seconds)
_GO_CMD_TIMEOUT = 60
_GO_TEST_TIMEOUT = 90


class GoBuildResult:
    """Holds the outcome of a Go build + vet + test run."""

    def __init__(
        self,
        success: bool,
        build_output: str,
        vet_output: str,
        test_output: str = "",
        test_success: bool | None = None,
    ):
        self.success = success
        self.build_output = build_output
        self.vet_output = vet_output
        self.test_output = test_output
        self.test_success = test_success  # None = tests not run

    @property
    def errors(self) -> str:
        """Combined error output for feeding back to the DevAgent."""
        parts = []
        if self.build_output:
            parts.append(f"go build errors:\n{self.build_output}")
        if self.vet_output:
            parts.append(f"go vet warnings:\n{self.vet_output}")
        return "\n\n".join(parts)

    @property
    def test_errors(self) -> str:
        """Test-specific error output for feeding back to the TestAgent."""
        if self.test_output and not self.test_success:
            return f"go test failures:\n{self.test_output}"
        return ""

    def __repr__(self) -> str:
        return f"GoBuildResult(success={self.success}, test_success={self.test_success})"


def _run_go(args: list[str], cwd: str, timeout: int = _GO_CMD_TIMEOUT) -> tuple[int, str]:
    """Run a go command and return (returncode, combined output)."""
    try:
        result = subprocess.run(
            ["go", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"HOME": "/tmp", "GOPATH": "/tmp/gopath", "PATH": "/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, "go command timed out"
    except FileNotFoundError:
        return 1, "go binary not found in container"


def _write_files_to_dir(files: list[dict], target_dir: str, module_name: str = "") -> None:
    """Write file dicts to a target directory, ensuring go.mod exists."""
    has_go_mod = False
    for f in files:
        fpath = Path(target_dir) / f["path"]
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(f["content"], encoding="utf-8")
        if f["path"].endswith("go.mod"):
            has_go_mod = True

    if not has_go_mod:
        mod = module_name or "github.com/ia-squad/generated"
        go_mod_path = Path(target_dir) / "go.mod"
        go_mod_path.write_text(f"module {mod}\n\ngo 1.22\n", encoding="utf-8")
        logger.info("Created missing go.mod")


def validate_go_code(files: list[dict], module_name: str = "") -> GoBuildResult:
    """
    Write the generated files to a temp directory and run go build + go vet.

    Args:
        files: List of {"path": "...", "content": "..."} from the DevAgent.
        module_name: Go module name. If empty, extracted from go.mod in files.

    Returns:
        GoBuildResult with success flag and error output.
    """
    tmpdir = tempfile.mkdtemp(prefix="ia_squad_go_")

    try:
        _write_files_to_dir(files, tmpdir, module_name)

        # Run go mod tidy to fetch dependencies
        tidy_rc, tidy_out = _run_go(["mod", "tidy"], tmpdir)
        if tidy_rc != 0:
            logger.warning(f"go mod tidy issues: {tidy_out[:300]}")

        # Run go build
        build_rc, build_out = _run_go(["build", "./..."], tmpdir)
        build_errors = build_out if build_rc != 0 else ""

        # Run go vet (only if build succeeded)
        vet_errors = ""
        if build_rc == 0:
            vet_rc, vet_out = _run_go(["vet", "./..."], tmpdir)
            vet_errors = vet_out if vet_rc != 0 else ""

        success = build_rc == 0
        result = GoBuildResult(success=success, build_output=build_errors, vet_output=vet_errors)

        logger.info(f"Go validation: success={success}, build_errors={len(build_errors)} chars, vet_errors={len(vet_errors)} chars")
        return result

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def validate_go_tests(
    source_files: list[dict],
    test_files: list[dict],
    module_name: str = "",
) -> GoBuildResult:
    """
    Write source + test files to a temp directory and run go test ./...

    Args:
        source_files: Source code files from DevAgent.
        test_files: Test files from TestAgent.
        module_name: Go module name.

    Returns:
        GoBuildResult with test_success and test_output populated.
    """
    tmpdir = tempfile.mkdtemp(prefix="ia_squad_gotest_")

    try:
        # Write source files first
        _write_files_to_dir(source_files, tmpdir, module_name)

        # Write test files on top
        for f in test_files:
            fpath = Path(tmpdir) / f["path"]
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(f["content"], encoding="utf-8")

        # Run go mod tidy
        tidy_rc, tidy_out = _run_go(["mod", "tidy"], tmpdir)
        if tidy_rc != 0:
            logger.warning(f"go mod tidy issues (tests): {tidy_out[:300]}")

        # Run go build first to check test compilation
        build_rc, build_out = _run_go(["build", "./..."], tmpdir)
        if build_rc != 0:
            return GoBuildResult(
                success=False,
                build_output=build_out,
                vet_output="",
                test_output=f"Tests failed to compile:\n{build_out}",
                test_success=False,
            )

        # Run go test
        test_rc, test_out = _run_go(["test", "-v", "-count=1", "./..."], tmpdir, timeout=_GO_TEST_TIMEOUT)
        test_success = test_rc == 0

        logger.info(
            f"Go test validation: test_success={test_success}, "
            f"output={len(test_out)} chars"
        )

        return GoBuildResult(
            success=True,
            build_output="",
            vet_output="",
            test_output=test_out if not test_success else "",
            test_success=test_success,
        )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
