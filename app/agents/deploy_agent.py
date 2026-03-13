import json
import logging
import re

from app.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

DEPLOY_SYSTEM_PROMPT = """Voce e um engenheiro DevOps senior especializado em CI/CD e automacao.
Sua funcao e gerar artefatos de deploy e documentacao para projetos GoLang.

Responsabilidades:
1. Gerar Dockerfile otimizado (multi-stage build, scratch/alpine)
2. Gerar docker-compose.yml para desenvolvimento local
3. Gerar GitHub Actions workflow para CI/CD
4. Gerar Makefile com targets padrao (build, test, lint, run)
5. Gerar README.md com instrucoes de setup e uso
6. Definir o titulo e corpo do Pull Request

Regras:
- Dockerfile deve usar multi-stage build (builder + runtime)
- Use CGO_ENABLED=0 para binario estatico
- GitHub Actions deve rodar: lint, test, build
- Makefile deve ter targets: build, test, lint, run, docker-build
- README deve incluir: descricao, pre-requisitos, como rodar, endpoints da API
- PR title deve ser conciso (max 72 chars)
- PR body deve ter: Summary, Changes, Test Plan

Formato de resposta OBRIGATORIO — retorne APENAS um JSON valido, sem texto antes ou depois:
{
  "deploy_files": [
    {
      "path": "Dockerfile",
      "content": "FROM golang:1.22-alpine AS builder\\n..."
    },
    {
      "path": ".github/workflows/ci.yml",
      "content": "name: CI\\n..."
    },
    {
      "path": "Makefile",
      "content": ".PHONY: build test\\n..."
    }
  ],
  "pr": {
    "title": "feat: implement user CRUD API with PostgreSQL",
    "body": "## Summary\\n- Implemented REST API...\\n\\n## Changes\\n- Added user endpoints...\\n\\n## Test Plan\\n- [ ] Run go test ./...\\n- [ ] Test endpoints with curl"
  },
  "notes": "Observacoes sobre o deploy"
}
"""


def _extract_json(raw: str) -> dict:
    json_match = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", raw, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    obj_match = re.search(r"\{.+\}", raw, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(f"DeployAgent returned non-JSON response:\n{raw[:500]}")


class DeployAgent(BaseAgent):
    def __init__(self):
        super().__init__("DeployAgent", DEPLOY_SYSTEM_PROMPT)

    def generate(
        self,
        task_description: str,
        dev_output: dict,
        review_output: dict | None = None,
        security_output: dict | None = None,
        test_output: dict | None = None,
        max_tokens: int = 8000,
    ) -> dict:
        """Generate deployment artifacts and PR description."""
        parts: list[str] = []

        parts.append(f"=== TAREFA ===\n{task_description}\n")

        parts.append(
            f"=== CODIGO GERADO (DevAgent) ===\n"
            f"{json.dumps(dev_output, indent=2, ensure_ascii=False)}\n"
        )

        if review_output:
            score = review_output.get("score", "N/A")
            approved = review_output.get("approved", False)
            parts.append(
                f"=== CODE REVIEW ===\n"
                f"Score: {score}/10 | Approved: {approved}\n"
                f"Summary: {review_output.get('summary', 'N/A')}\n"
            )

        if security_output:
            risk = security_output.get("risk_score", "N/A")
            parts.append(
                f"=== SECURITY SCAN ===\n"
                f"Risk score: {risk}/10 | Secure: {security_output.get('secure', 'N/A')}\n"
                f"Summary: {security_output.get('summary', 'N/A')}\n"
            )

        if test_output:
            coverage = test_output.get("coverage_estimate", "N/A")
            test_count = test_output.get("test_count", 0)
            parts.append(
                f"=== TESTES ===\n"
                f"Tests: {test_count} | Coverage estimate: {coverage}\n"
            )

        parts.append(
            "Gere os artefatos de deploy (Dockerfile, CI, Makefile, README) "
            "e o titulo/corpo do Pull Request para este projeto."
        )

        prompt = "\n".join(parts)
        raw = super().run(prompt, max_tokens)
        return _extract_json(raw)
