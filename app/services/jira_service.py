"""
Jira Cloud Service — REST API client for bidirectional integration.

Responsibilities:
  1. Fetch issues from a Jira project (filtered by label + status)
  2. Transition issue status (To Do → In Progress → Done)
  3. Add comments with pipeline results (code review, PR link, security scan)
  4. Update custom fields (branch name, PR URL)

Authentication: Atlassian API Token (Basic Auth with email:token).
API Docs: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
"""
import base64
import json
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0


class JiraService:
    """Client for Jira Cloud REST API v3."""

    def __init__(self):
        self.base_url = settings.JIRA_BASE_URL.rstrip("/")
        self.project_key = settings.JIRA_PROJECT_KEY
        self.label_trigger = settings.JIRA_LABEL_TRIGGER
        self._auth_header = self._build_auth_header()

    def _build_auth_header(self) -> str:
        """Build Basic Auth header from email:token."""
        email = settings.JIRA_USER_EMAIL
        token = settings.JIRA_API_TOKEN
        if not email or not token:
            return ""
        credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
        return f"Basic {credentials}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _api_url(self, path: str) -> str:
        return f"{self.base_url}/rest/api/3/{path.lstrip('/')}"

    def is_configured(self) -> bool:
        """Check if Jira integration is properly configured."""
        return bool(
            settings.JIRA_ENABLED
            and self.base_url
            and self._auth_header
            and self.project_key
        )

    # ------------------------------------------------------------------
    # Fetch issues
    # ------------------------------------------------------------------

    def get_new_issues(self) -> list[dict]:
        """
        Fetch issues in the target project that:
          - Have the trigger label (e.g. "ai-squad")
          - Are in "To Do" status
          - Are of type Story or Task

        Returns a list of simplified issue dicts.
        """
        if not self.is_configured():
            return []

        jql = (
            f'project = "{self.project_key}" '
            f'AND labels = "{self.label_trigger}" '
            f'AND status = "{settings.JIRA_STATUS_TODO}" '
            f"AND issuetype in (Story, Task, Bug) "
            f"ORDER BY created ASC"
        )

        try:
            logger.info(f"Jira JQL query: {jql}")

            # Jira Cloud deprecated /rest/api/3/search (410).
            # New endpoint: POST /rest/api/3/search/jql
            resp = httpx.post(
                self._api_url("search/jql"),
                headers=self._headers(),
                json={
                    "jql": jql,
                    "maxResults": 10,
                    "fields": [
                        "summary",
                        "description",
                        "issuetype",
                        "priority",
                        "labels",
                        "status",
                        "assignee",
                    ],
                },
                timeout=_TIMEOUT,
            )

            if resp.status_code != 200:
                logger.warning(f"Jira search failed ({resp.status_code}): {resp.text[:300]}")
                return []

            data = resp.json()
            logger.info(f"Jira response: total={data.get('total', '?')}, issues={len(data.get('issues', []))}, raw_keys={[i.get('key') for i in data.get('issues', [])]}")
            issues = []
            for item in data.get("issues", []):
                fields = item.get("fields", {})
                # Extract plain text from Atlassian Document Format (ADF)
                description = self._extract_text_from_adf(fields.get("description"))

                issues.append({
                    "key": item["key"],
                    "url": f"{self.base_url}/browse/{item['key']}",
                    "summary": fields.get("summary", ""),
                    "description": description,
                    "issue_type": fields.get("issuetype", {}).get("name", ""),
                    "priority": fields.get("priority", {}).get("name", ""),
                    "labels": fields.get("labels", []),
                    "status": fields.get("status", {}).get("name", ""),
                })

            logger.info(f"Jira: Found {len(issues)} new issue(s) in {self.project_key}.")
            return issues

        except Exception as exc:
            logger.warning(f"Jira fetch failed: {exc}")
            return []

    def get_issue(self, issue_key: str) -> dict | None:
        """Fetch a single Jira issue by key."""
        if not self.is_configured():
            return None

        try:
            resp = httpx.get(
                self._api_url(f"issue/{issue_key}"),
                headers=self._headers(),
                params={"fields": "summary,description,status,labels,priority"},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"Jira issue fetch {issue_key} failed ({resp.status_code})")
            return None
        except Exception as exc:
            logger.warning(f"Jira issue fetch {issue_key} failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # Transition (status change)
    # ------------------------------------------------------------------

    def transition_issue(self, issue_key: str, target_status: str) -> bool:
        """
        Transition a Jira issue to a target status name.

        Jira requires you to first fetch available transitions,
        then POST the transition ID.
        """
        if not self.is_configured():
            return False

        try:
            # 1. Get available transitions
            resp = httpx.get(
                self._api_url(f"issue/{issue_key}/transitions"),
                headers=self._headers(),
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.warning(f"Jira transitions fetch failed ({resp.status_code})")
                return False

            transitions = resp.json().get("transitions", [])
            target = next(
                (t for t in transitions if t["name"].lower() == target_status.lower()),
                None,
            )
            if not target:
                available = [t["name"] for t in transitions]
                logger.warning(
                    f"Jira transition '{target_status}' not found for {issue_key}. "
                    f"Available: {available}"
                )
                return False

            # 2. Execute transition
            resp = httpx.post(
                self._api_url(f"issue/{issue_key}/transitions"),
                headers=self._headers(),
                json={"transition": {"id": target["id"]}},
                timeout=_TIMEOUT,
            )

            if resp.status_code == 204:
                logger.info(f"Jira: {issue_key} transitioned to '{target_status}'.")
                return True

            logger.warning(f"Jira transition failed ({resp.status_code}): {resp.text[:200]}")
            return False

        except Exception as exc:
            logger.warning(f"Jira transition failed for {issue_key}: {exc}")
            return False

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def add_comment(self, issue_key: str, body: str) -> bool:
        """Add a comment to a Jira issue using Atlassian Document Format."""
        if not self.is_configured():
            return False

        # Jira Cloud v3 requires ADF (Atlassian Document Format) for comments
        adf_body = {
            "body": {
                "version": 1,
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": body}],
                    }
                ],
            }
        }

        try:
            resp = httpx.post(
                self._api_url(f"issue/{issue_key}/comment"),
                headers=self._headers(),
                json=adf_body,
                timeout=_TIMEOUT,
            )

            if resp.status_code == 201:
                logger.info(f"Jira: Comment added to {issue_key}.")
                return True

            logger.warning(f"Jira comment failed ({resp.status_code}): {resp.text[:200]}")
            return False

        except Exception as exc:
            logger.warning(f"Jira comment failed for {issue_key}: {exc}")
            return False

    def comment_pipeline_result(self, issue_key: str, result: dict) -> bool:
        """Format and post the full pipeline result as a Jira comment."""
        review = result.get("review", {})
        security = result.get("security", {})
        plan = result.get("plan", {})

        pr_line = f"PR: {result['pr_url']}" if result.get("pr_url") else ""
        branch_line = f"Branch: {result.get('branch', 'N/A')}"

        body = (
            f"=== IA Dev Squad — Pipeline Completo ===\n\n"
            f"{pr_line or branch_line}\n"
            f"Implementacao: {result.get('dev_summary', 'N/A')}\n\n"
            f"--- Pipeline (7 agentes) ---\n"
            f"Planner: {'Complexa' if plan.get('is_complex') else 'Simples'} "
            f"({plan.get('subtasks', 1)} subtask(s))\n"
            f"Iteracoes: {result.get('iterations', 1)}/3\n"
            f"Go build: {'OK' if result.get('build_success') else 'FALHOU'}\n"
            f"Go test: {'OK' if result.get('tests_pass') else 'FALHOU'} "
            f"({result.get('test_count', 0)} testes)\n"
            f"Security: risk {security.get('risk_score', 'N/A')}/10 "
            f"({security.get('vulnerabilities', 0)} vulns)\n\n"
            f"--- Code Review ---\n"
            f"Aprovado: {'Sim' if review.get('approved') else 'Nao'}\n"
            f"Score: {review.get('score', 0)}/10\n"
            f"Issues: {review.get('issues_count', 0)}\n\n"
            f"{review.get('summary', '')}"
        )

        return self.add_comment(issue_key, body)

    def comment_failure(self, issue_key: str, error: str) -> bool:
        """Post a failure comment on a Jira issue."""
        body = (
            f"=== IA Dev Squad — Falha no Pipeline ===\n\n"
            f"Erro: {error[:500]}\n\n"
            f"Verifique os logs do worker para detalhes."
        )
        return self.add_comment(issue_key, body)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_text_from_adf(self, adf: dict | None) -> str:
        """
        Extract plain text from Atlassian Document Format (ADF).
        Jira Cloud v3 returns description as ADF JSON, not plain text.
        """
        if not adf:
            return ""
        if isinstance(adf, str):
            return adf

        texts: list[str] = []
        self._walk_adf(adf, texts)
        return "\n".join(texts).strip()

    def _walk_adf(self, node: Any, texts: list[str]) -> None:
        """Recursively walk ADF nodes and extract text."""
        if isinstance(node, dict):
            if node.get("type") == "text":
                texts.append(node.get("text", ""))
            for child in node.get("content", []):
                self._walk_adf(child, texts)
        elif isinstance(node, list):
            for item in node:
                self._walk_adf(item, texts)

    def build_task_description(self, issue: dict) -> str:
        """
        Build a task description from a Jira issue for the DevAgent.
        Combines summary + description into a clear prompt.
        """
        summary = issue.get("summary", "")
        description = issue.get("description", "")
        issue_type = issue.get("issue_type", "")
        priority = issue.get("priority", "")

        parts = [f"[{issue['key']}] {summary}"]
        if description:
            parts.append(f"\nDetalhes:\n{description}")
        if priority:
            parts.append(f"\nPrioridade: {priority}")

        return "\n".join(parts)
