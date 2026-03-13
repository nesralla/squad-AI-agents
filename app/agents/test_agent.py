import json
import logging
import re

from app.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOKENS = 12_000

TEST_SYSTEM_PROMPT = """Voce e um engenheiro de qualidade senior especializado em testes GoLang.
Sua funcao e gerar testes automatizados completos para codigo Go existente.

Regras obrigatorias:
- Use Go 1.22+ com o pacote testing da stdlib
- Gere testes unitarios para TODAS as camadas (handler, service, repository)
- Use table-driven tests (padrao Go idiomatico)
- Crie mocks/stubs para interfaces (sem frameworks de mock — use implementacoes manuais)
- Teste cenarios de sucesso E de erro
- Teste edge cases (nil, vazio, limites)
- Use testify/assert apenas se ja estiver nas dependencias, senao use stdlib
- Nomeie testes com padrao Test{Funcao}_{Cenario} (ex: TestCreateUser_Success)
- Inclua subtests com t.Run() para organizacao
- NUNCA gere testes que dependam de banco de dados real ou servicos externos
- Use context.Background() para testes

Formato de resposta OBRIGATORIO — retorne APENAS um JSON valido, sem texto antes ou depois:
{
  "test_files": [
    {
      "path": "internal/service/user_service_test.go",
      "content": "package service\\n\\nimport (...)\\n\\n..."
    }
  ],
  "coverage_estimate": "85%",
  "test_count": 12,
  "scenarios_covered": [
    "CreateUser com dados validos",
    "CreateUser com email duplicado",
    "GetUser com ID inexistente"
  ],
  "notes": "Observacoes sobre a estrategia de teste"
}
"""

FIX_TEST_PROMPT = """Os testes GoLang que voce gerou anteriormente falharam.

=== CODIGO FONTE (implementacao) ===
{source_code}

=== TESTES ANTERIORES ===
{previous_tests}

=== ERROS DE COMPILACAO/EXECUCAO ===
{errors}

Corrija TODOS os testes para que compilem e passem.
Retorne o JSON completo com TODOS os arquivos de teste, no mesmo formato obrigatorio.
"""


def _try_repair_json(raw: str) -> dict | None:
    start = raw.find("{")
    if start == -1:
        return None

    text = raw[start:]
    repairs = [
        text,
        text + '"}]}',
        text + '"}], "coverage_estimate": "unknown", "test_count": 0, "scenarios_covered": [], "notes": "truncated"}',
    ]
    for attempt in repairs:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue
    return None


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

    logger.warning("TestAgent JSON parsing failed, attempting repair...")
    repaired = _try_repair_json(raw)
    if repaired and repaired.get("test_files"):
        logger.info(f"JSON repair succeeded — {len(repaired['test_files'])} test file(s) recovered.")
        return repaired

    raise ValueError(f"TestAgent returned non-JSON response:\n{raw[:500]}")


class TestAgent(BaseAgent):
    def __init__(self):
        super().__init__("TestAgent", TEST_SYSTEM_PROMPT)

    def generate_tests(
        self,
        dev_output: dict,
        architecture: dict | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> dict:
        """Generate test files for the code produced by DevAgent."""
        parts: list[str] = []

        if architecture:
            parts.append(
                f"=== ARQUITETURA DO PROJETO ===\n"
                f"Test strategy: {architecture.get('test_strategy', 'N/A')}\n"
            )

        parts.append(
            f"Gere testes unitarios completos para o seguinte codigo GoLang:\n\n"
            f"{json.dumps(dev_output, indent=2, ensure_ascii=False)}"
        )

        prompt = "\n".join(parts)
        raw = super().run(prompt, max_tokens)
        return _extract_json(raw)

    def fix_tests(
        self,
        dev_output: dict,
        previous_test_output: dict,
        errors: str,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> dict:
        """Fix test files that failed compilation or execution."""
        prompt = FIX_TEST_PROMPT.format(
            source_code=json.dumps(dev_output, indent=2, ensure_ascii=False),
            previous_tests=json.dumps(previous_test_output, indent=2, ensure_ascii=False),
            errors=errors,
        )
        raw = super().run(prompt, max_tokens)
        return _extract_json(raw)
