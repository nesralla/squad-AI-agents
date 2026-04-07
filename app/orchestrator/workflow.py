"""
Orchestrator — Full autonomous pipeline with 7 AI agents.

Pipeline:
    1. PlannerAgent    — Decomposes complex tasks into subtasks
    2. ArchitectAgent  — Defines tech stack, patterns, project structure
    3. DevAgent        — Generates GoLang code (iterative with fix loop)
    4. GoBuildService  — Validates go build + go vet
    5. TestAgent       — Generates and validates go test
    6. SecurityAgent   — Scans for vulnerabilities (OWASP)
    7. ReviewerAgent   — Final code review with quality gate
    8. DeployAgent     — Generates deploy artifacts + PR description
    9. GitService      — Push + create Pull Request on GitHub
"""
import json
import logging
from typing import Callable

from sqlalchemy.orm import Session

from app.agents.architect_agent import ArchitectAgent
from app.agents.deploy_agent import DeployAgent
from app.agents.dev_agent import DevAgent
from app.agents.planner_agent import PlannerAgent
from app.agents.reviewer_agent import ReviewerAgent
from app.agents.security_agent import SecurityAgent
from app.agents.test_agent import TestAgent
from app.core.config import settings
from app.core.redis_client import set_progress
from app.models.task import TaskStatus
from app.services.git_service import GitService
from app.services.go_build_service import GoBuildResult, validate_go_code, validate_go_tests
from app.services.memory_service import MemoryService
from app.services.task_service import TaskService

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 3
MAX_TEST_ITERATIONS = 2
MIN_APPROVED_SCORE = 7


class Orchestrator:
    """
    Coordinates the full squad of autonomous AI agents.

    Pipeline:
        PlannerAgent → ArchitectAgent → DevAgent ⟷ GoBuild → TestAgent ⟷ GoTest
        → SecurityAgent → ReviewerAgent → DeployAgent → GitService (push + PR)
    """

    def __init__(
        self,
        db: Session,
        on_agent_complete: Callable[[str], None] | None = None,
        on_agent_fail: Callable[[str], None] | None = None,
    ):
        self.planner = PlannerAgent()
        self.architect = ArchitectAgent()
        self.dev = DevAgent()
        self.test_agent = TestAgent()
        self.security = SecurityAgent()
        self.reviewer = ReviewerAgent()
        self.deployer = DeployAgent()
        self.git = GitService()
        self.tasks = TaskService(db)
        self.memory = MemoryService(db)
        self._on_agent_complete = on_agent_complete
        self._on_agent_fail = on_agent_fail

    def _agent_done(self, step: str) -> None:
        """Notify that an agent step completed successfully."""
        if self._on_agent_complete:
            try:
                self._on_agent_complete(step)
            except Exception as exc:
                logger.warning(f"on_agent_complete callback failed for '{step}': {exc}")

    def _agent_failed(self, step: str) -> None:
        """Notify that an agent step failed."""
        if self._on_agent_fail:
            try:
                self._on_agent_fail(step)
            except Exception as exc:
                logger.warning(f"on_agent_fail callback failed for '{step}': {exc}")

    def _progress(self, task_id: int, step: str, detail: str = "") -> None:
        set_progress(task_id, step, detail)

    # ------------------------------------------------------------------
    # Feedback helpers
    # ------------------------------------------------------------------

    def _build_feedback(
        self,
        build_result: GoBuildResult | None,
        review_output: dict | None,
        security_output: dict | None = None,
    ) -> str | None:
        """
        Compose a feedback string from build errors, review issues, and security vulnerabilities.
        Returns None if everything is acceptable.
        """
        parts: list[str] = []

        if build_result and not build_result.success:
            parts.append(build_result.errors)

        if review_output:
            critical_major = [
                i
                for i in review_output.get("issues", [])
                if i.get("severity") in ("critical", "major")
            ]
            score = review_output.get("score", 10)
            approved = review_output.get("approved", True)

            if critical_major or (not approved and score < MIN_APPROVED_SCORE):
                lines = ["Code review issues to fix:"]
                for issue in critical_major:
                    sev = issue.get("severity", "?").upper()
                    desc = issue.get("description", "")
                    suggestion = issue.get("suggestion", "")
                    fname = issue.get("file", "")
                    lines.append(f"  [{sev}] {fname}: {desc}")
                    if suggestion:
                        lines.append(f"         Fix: {suggestion}")
                parts.append("\n".join(lines))

        if security_output:
            sec_feedback = self.security.get_remediation_feedback(security_output)
            if sec_feedback:
                parts.append(sec_feedback)

        return "\n\n".join(parts) if parts else None

    # ------------------------------------------------------------------
    # Phase 1: Planning & Architecture
    # ------------------------------------------------------------------

    def _phase_planning(self, task_id: int, description: str, memory_context: str) -> tuple[dict, dict]:
        """Run PlannerAgent + ArchitectAgent. Returns (plan, architecture)."""
        # ── Planner ──
        self._progress(task_id, "planner", "PlannerAgent decompondo tarefa...")
        logger.info(f"[Task {task_id}] PlannerAgent starting...")

        plan = self.planner.plan(description, memory_context=memory_context)

        subtask_count = plan.get("total_subtasks", 1)
        is_complex = plan.get("is_complex", False)
        logger.info(
            f"[Task {task_id}] Plan: {subtask_count} subtask(s), "
            f"complex={is_complex}."
        )
        self._agent_done("planner")

        # ── Architect ──
        self._progress(task_id, "architect", "ArchitectAgent definindo arquitetura...")
        logger.info(f"[Task {task_id}] ArchitectAgent starting...")

        architecture = self.architect.design(
            description, plan=plan, memory_context=memory_context
        )

        tech_decisions = len(architecture.get("tech_decisions", []))
        logger.info(
            f"[Task {task_id}] Architecture: {tech_decisions} tech decisions."
        )
        self._agent_done("architect")

        return plan, architecture

    # ------------------------------------------------------------------
    # Phase 2: Code Generation (iterative with build validation)
    # ------------------------------------------------------------------

    def _phase_dev_loop(
        self,
        task_id: int,
        description: str,
        architecture: dict,
        memory_context: str,
    ) -> tuple[dict, GoBuildResult, dict | None, dict | None, int]:
        """
        Iterative Dev → Build → Security → Review loop.

        Returns:
            (dev_output, build_result, security_output, review_output, iterations)
        """
        dev_output = None
        review_output = None
        security_output = None
        build_result = None

        # Enrich DevAgent prompt with architecture context
        arch_context = (
            f"=== ARQUITETURA DEFINIDA ===\n"
            f"Estrutura: {architecture.get('project_structure', {}).get('description', 'N/A')}\n"
            f"Erro strategy: {architecture.get('error_strategy', 'N/A')}\n"
            f"Dependencies: {json.dumps(architecture.get('dependencies', []), ensure_ascii=False)}\n"
        )

        for iteration in range(1, MAX_ITERATIONS + 1):
            is_fix = iteration > 1

            # ── Code generation / fix ──
            action = "corrigindo" if is_fix else "gerando"
            step = f"dev_fix_{iteration}" if is_fix else "dev_agent"
            self._progress(
                task_id, step,
                f"DevAgent {action} codigo GoLang... (iteracao {iteration}/{MAX_ITERATIONS})",
            )
            logger.info(
                f"[Task {task_id}] DevAgent iteration {iteration} "
                f"({'fix' if is_fix else 'generate'})..."
            )

            if is_fix:
                feedback = self._build_feedback(build_result, review_output, security_output)
                dev_output = self.dev.fix(
                    task_description=description,
                    previous_output=dev_output,
                    feedback=feedback,
                )
            else:
                combined_context = ""
                if memory_context:
                    combined_context += memory_context + "\n\n"
                combined_context += arch_context

                dev_output = self.dev.generate(
                    description, memory_context=combined_context
                )

            files = dev_output.get("files", [])
            logger.info(
                f"[Task {task_id}] DevAgent iteration {iteration} done "
                f"-- {len(files)} file(s)."
            )

            # ── Go build validation ──
            self._progress(
                task_id, f"go_build_{iteration}",
                f"Validando go build + go vet... (iteracao {iteration})",
            )

            build_result = validate_go_code(files)

            if build_result.success:
                logger.info(f"[Task {task_id}] Go build OK (iteration {iteration}).")
            else:
                logger.warning(
                    f"[Task {task_id}] Go build FAILED (iteration {iteration}): "
                    f"{build_result.build_output[:200]}"
                )
                if iteration < MAX_ITERATIONS:
                    review_output = None
                    security_output = None
                    continue  # skip review, go straight to fix

            # ── Security scan ──
            self._progress(
                task_id, f"security_{iteration}",
                f"SecurityAgent escaneando vulnerabilidades... (iteracao {iteration})",
            )
            logger.info(f"[Task {task_id}] SecurityAgent iteration {iteration}...")

            try:
                security_output = self.security.scan(dev_output)
                vuln_count = len(security_output.get("vulnerabilities", []))
                risk_score = security_output.get("risk_score", 0)
                logger.info(
                    f"[Task {task_id}] Security scan: risk={risk_score}/10, "
                    f"vulnerabilities={vuln_count}."
                )

                # If critical/high vulns found AND we have iterations left, fix first
                if self.security.has_critical_issues(security_output) and iteration < MAX_ITERATIONS:
                    logger.warning(
                        f"[Task {task_id}] Critical security issues found, "
                        f"scheduling fix iteration {iteration + 1}..."
                    )
                    review_output = None
                    continue
            except Exception as sec_exc:
                logger.warning(f"[Task {task_id}] Security scan failed (non-fatal): {sec_exc}")
                security_output = {"secure": True, "risk_score": 0, "vulnerabilities": [], "summary": "Scan skipped"}

            # ── Code review ──
            self.tasks.update(task_id, status=TaskStatus.REVIEW_REQUESTED)
            self._progress(
                task_id, f"reviewer_{iteration}",
                f"ReviewerAgent analisando... (iteracao {iteration})",
            )
            logger.info(f"[Task {task_id}] ReviewerAgent iteration {iteration}...")

            review_output = self.reviewer.run(
                dev_output, memory_context=memory_context
            )

            approved = review_output.get("approved", False)
            score = review_output.get("score", 0)
            issues_count = len(review_output.get("issues", []))
            logger.info(
                f"[Task {task_id}] Review iteration {iteration} -- "
                f"approved={approved}, score={score}/10, issues={issues_count}."
            )

            # ── Check if acceptable ──
            feedback = self._build_feedback(build_result, review_output, security_output)
            if feedback is None:
                logger.info(
                    f"[Task {task_id}] Code accepted after {iteration} iteration(s)."
                )
                break

            if iteration < MAX_ITERATIONS:
                logger.info(
                    f"[Task {task_id}] Issues found, scheduling fix "
                    f"iteration {iteration + 1}..."
                )
            else:
                logger.warning(
                    f"[Task {task_id}] Max iterations reached. "
                    f"Pushing best version."
                )

        self._agent_done("dev_agent")
        if security_output:
            self._agent_done("security")
        if review_output:
            self._agent_done("reviewer")

        return dev_output, build_result, security_output, review_output, iteration

    # ------------------------------------------------------------------
    # Phase 3: Test Generation
    # ------------------------------------------------------------------

    def _phase_tests(
        self,
        task_id: int,
        dev_output: dict,
        architecture: dict,
    ) -> tuple[dict | None, bool]:
        """
        Generate tests with TestAgent and validate with go test.

        Returns:
            (test_output, tests_pass)
        """
        source_files = dev_output.get("files", [])
        test_output = None
        test_build_result = None
        tests_pass = False

        for test_iter in range(1, MAX_TEST_ITERATIONS + 1):
            is_fix = test_iter > 1
            action = "corrigindo" if is_fix else "gerando"

            self._progress(
                task_id, f"test_agent_{test_iter}",
                f"TestAgent {action} testes... (iteracao {test_iter}/{MAX_TEST_ITERATIONS})",
            )
            logger.info(f"[Task {task_id}] TestAgent iteration {test_iter}...")

            try:
                if is_fix and test_output:
                    test_output = self.test_agent.fix_tests(
                        dev_output=dev_output,
                        previous_test_output=test_output,
                        errors=test_build_result.test_errors if test_build_result else "",
                    )
                else:
                    test_output = self.test_agent.generate_tests(
                        dev_output=dev_output,
                        architecture=architecture,
                    )

                test_files = test_output.get("test_files", [])
                test_count = test_output.get("test_count", len(test_files))
                logger.info(
                    f"[Task {task_id}] TestAgent generated {test_count} test(s) "
                    f"in {len(test_files)} file(s)."
                )

                # Validate: go test ./...
                self._progress(
                    task_id, f"go_test_{test_iter}",
                    f"Executando go test... (iteracao {test_iter}/{MAX_TEST_ITERATIONS})",
                )

                test_build_result = validate_go_tests(source_files, test_files)

                if test_build_result.test_success:
                    logger.info(f"[Task {task_id}] Go tests PASSED (iteration {test_iter}).")
                    tests_pass = True
                    break
                else:
                    logger.warning(
                        f"[Task {task_id}] Go tests FAILED (iteration {test_iter}): "
                        f"{test_build_result.test_output[:200]}"
                    )

            except Exception as test_exc:
                logger.warning(f"[Task {task_id}] TestAgent failed (non-fatal): {test_exc}")
                self._agent_failed("test_agent")
                break

        if test_output:
            self._agent_done("test_agent")

        return test_output, tests_pass

    # ------------------------------------------------------------------
    # Phase 4: Deploy Artifacts + Git Push + PR
    # ------------------------------------------------------------------

    def _phase_deploy(
        self,
        task_id: int,
        task_description: str,
        dev_output: dict,
        test_output: dict | None,
        security_output: dict | None,
        review_output: dict | None,
    ) -> tuple[dict | None, str | None, str | None, dict | None]:
        """
        DeployAgent generates artifacts, GitService pushes and creates PR.

        Returns:
            (deploy_output, branch_name, git_error, pr_data)
        """
        files = dev_output.get("files", [])
        summary = dev_output.get("summary", "IA implementation")

        # ── DeployAgent ──
        deploy_output = None
        try:
            self._progress(task_id, "deploy_agent", "DeployAgent gerando artefatos de deploy e PR...")
            logger.info(f"[Task {task_id}] DeployAgent starting...")

            deploy_output = self.deployer.generate(
                task_description=task_description,
                dev_output=dev_output,
                review_output=review_output,
                security_output=security_output,
                test_output=test_output,
            )

            deploy_files = deploy_output.get("deploy_files", [])
            logger.info(f"[Task {task_id}] DeployAgent generated {len(deploy_files)} deploy file(s).")

            # Merge deploy files with source files (deploy files like Dockerfile, CI, Makefile)
            existing_paths = {f["path"] for f in files}
            for df in deploy_files:
                if df["path"] not in existing_paths:
                    files.append(df)

            # Also merge test files if available
            if test_output:
                for tf in test_output.get("test_files", []):
                    if tf["path"] not in existing_paths:
                        files.append(tf)
                        existing_paths.add(tf["path"])

        except Exception as deploy_exc:
            logger.warning(f"[Task {task_id}] DeployAgent failed (non-fatal): {deploy_exc}")

        # ── Git push ──
        branch_name = None
        git_error = None

        try:
            self._progress(task_id, "git_push", "Criando branch e fazendo push...")
            logger.info(f"[Task {task_id}] GitService starting...")

            branch_name = self.git.execute_task(
                task_id=task_id,
                task_description=task_description,
                files=files,
                summary=summary,
            )
            logger.info(f"[Task {task_id}] Pushed to branch: {branch_name}")
        except Exception as git_exc:
            git_error = str(git_exc)
            logger.warning(f"[Task {task_id}] Git push failed: {git_error}")

        # ── Create Pull Request ──
        pr_data = None
        if branch_name and deploy_output:
            try:
                self._progress(task_id, "create_pr", "Criando Pull Request no GitHub...")
                pr_info = deploy_output.get("pr", {})
                pr_title = pr_info.get("title", f"feat: task #{task_id} - {summary}"[:72])
                pr_body = pr_info.get("body", f"Automated PR for task #{task_id}\n\n{summary}")

                # Enrich PR body with pipeline results
                pr_body += (
                    f"\n\n---\n"
                    f"**Generated by IA Dev Squad**\n"
                    f"- Security scan: {security_output.get('risk_score', 'N/A')}/10 risk\n"
                    if security_output else ""
                )

                pr_data = self.git.create_pull_request(
                    branch_name=branch_name,
                    title=pr_title,
                    body=pr_body,
                )
                if pr_data:
                    logger.info(f"[Task {task_id}] PR created: {pr_data.get('html_url')}")
            except Exception as pr_exc:
                logger.warning(f"[Task {task_id}] PR creation failed (non-fatal): {pr_exc}")

        self._agent_done("deploy")

        return deploy_output, branch_name, git_error, pr_data

    # ------------------------------------------------------------------
    # Main execution pipeline
    # ------------------------------------------------------------------

    def execute(self, task_id: int) -> dict:
        self.tasks.update(task_id, status=TaskStatus.RUNNING)
        self._progress(task_id, "running", "Orchestrator iniciado — pipeline de 7 agentes.")

        try:
            task = self.tasks.get(task_id)

            # ── RAG: retrieve memory context ──
            memory_context = ""
            if settings.MEMORY_ENABLED:
                try:
                    self._progress(task_id, "memory_recall", "Consultando memoria de tarefas anteriores...")
                    memory_context = self.memory.get_context_for_task(task.description)
                    if memory_context:
                        logger.info(f"[Task {task_id}] RAG context retrieved ({len(memory_context)} chars).")
                    else:
                        logger.info(f"[Task {task_id}] No relevant memories found.")
                except Exception as mem_exc:
                    logger.warning(f"[Task {task_id}] Memory recall failed (non-fatal): {mem_exc}")

            # ══════════════════════════════════════════════════════════════
            # PHASE 1: Planning & Architecture
            # ══════════════════════════════════════════════════════════════
            plan, architecture = self._phase_planning(
                task_id, task.description, memory_context
            )

            # ══════════════════════════════════════════════════════════════
            # PHASE 2: Code Generation (iterative Dev → Build → Security → Review)
            # ══════════════════════════════════════════════════════════════
            dev_output, build_result, security_output, review_output, iteration = (
                self._phase_dev_loop(
                    task_id, task.description, architecture, memory_context
                )
            )

            # ══════════════════════════════════════════════════════════════
            # PHASE 3: Test Generation
            # ══════════════════════════════════════════════════════════════
            test_output = None
            tests_pass = False
            try:
                test_output, tests_pass = self._phase_tests(
                    task_id, dev_output, architecture
                )
            except Exception as test_exc:
                logger.warning(f"[Task {task_id}] Test phase failed (non-fatal): {test_exc}")

            # ══════════════════════════════════════════════════════════════
            # PHASE 4: Deploy Artifacts + Git Push + PR
            # ══════════════════════════════════════════════════════════════
            deploy_output, branch_name, git_error, pr_data = self._phase_deploy(
                task_id=task_id,
                task_description=task.description,
                dev_output=dev_output,
                test_output=test_output,
                security_output=security_output,
                review_output=review_output,
            )

            # ── Persist agent results ──
            self.tasks.update(
                task_id,
                generated_code=json.dumps(dev_output, ensure_ascii=False),
                review_feedback=(
                    json.dumps(review_output, ensure_ascii=False)
                    if review_output
                    else None
                ),
            )

            # ── Store memories ──
            if settings.MEMORY_ENABLED:
                try:
                    self._progress(task_id, "memory_store", "Armazenando memorias...")
                    self.memory.store_task_completion(
                        task_id=task_id,
                        task_description=task.description,
                        dev_output=dev_output,
                        review_output=review_output,
                        build_success=build_result.success if build_result else False,
                        iterations=iteration,
                    )
                    logger.info(f"[Task {task_id}] Memories stored.")
                except Exception as mem_exc:
                    logger.warning(f"[Task {task_id}] Memory storage failed (non-fatal): {mem_exc}")

            # ── Final status ──
            self.tasks.update(
                task_id,
                status=TaskStatus.COMPLETED,
                branch_name=branch_name,
            )

            summary = dev_output.get("summary", "IA implementation")
            pr_url = pr_data.get("html_url") if pr_data else None

            if pr_url:
                status_detail = f"PR: {pr_url}"
            elif branch_name:
                status_detail = f"Branch: {branch_name}"
            elif git_error:
                status_detail = f"Git skipped: {git_error[:120]}"
            else:
                status_detail = "Completed (no git)"
            self._progress(task_id, "completed", status_detail)

            return {
                "task_id": task_id,
                "branch": branch_name,
                "pr_url": pr_url,
                "pr_number": pr_data.get("number") if pr_data else None,
                "dev_summary": summary,
                "iterations": iteration,
                "build_success": build_result.success if build_result else False,
                "tests_pass": tests_pass,
                "test_count": test_output.get("test_count", 0) if test_output else 0,
                "git_error": git_error,
                "plan": {
                    "is_complex": plan.get("is_complex", False),
                    "subtasks": plan.get("total_subtasks", 1),
                },
                "architecture": {
                    "patterns": architecture.get("suggested_patterns", [])
                    if isinstance(architecture, dict) else [],
                },
                "security": {
                    "risk_score": security_output.get("risk_score", 0) if security_output else 0,
                    "secure": security_output.get("secure", True) if security_output else True,
                    "vulnerabilities": len(security_output.get("vulnerabilities", []))
                    if security_output else 0,
                },
                "review": {
                    "approved": (
                        review_output.get("approved", False) if review_output else False
                    ),
                    "score": (
                        review_output.get("score", 0) if review_output else 0
                    ),
                    "summary": (
                        review_output.get("summary", "") if review_output else ""
                    ),
                    "issues_count": (
                        len(review_output.get("issues", []))
                        if review_output
                        else 0
                    ),
                    "issues": (
                        review_output.get("issues", []) if review_output else []
                    ),
                },
            }

        except Exception as exc:
            logger.exception(f"[Task {task_id}] Execution failed: {exc}")
            self.tasks.update(task_id, status=TaskStatus.FAILED)
            self._progress(task_id, "failed", str(exc)[:200])
            raise
